from __future__ import annotations

import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LEADS_PATH = DATA_DIR / "surplus_leads.json"
PREVIOUS_PATH = DATA_DIR / "previous_leads.json"
REMOVED_PATH = DATA_DIR / "removed_leads.json"
DIGEST_PATH = DATA_DIR / "daily_digest.json"
INDEX_PATH = ROOT / "index.html"
EMBED_START = '<script type="application/json" id="embedded-data">'
EMBED_END = "</script>"
REQUEST_TIMEOUT = 15
REQUEST_DELAY_SECONDS = 3
MIN_NETWORK_SCORE = 60
GSCCCA_URL = "https://www.gsccca.org/search"
PROBATE_URL = "https://www.georgiaprobaterecords.com/search"
SERP_API_URL = "https://serpapi.com/search.json"
DEFAULT_BATCH_API_URL = "https://api.batchskiptracing.com/api/v1/skip-trace"


class RateLimitedSession:
    def __init__(self, delay_seconds: int = REQUEST_DELAY_SECONDS) -> None:
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.last_request_at = 0.0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        elapsed = time.monotonic() - self.last_request_at
        if self.last_request_at and elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        response = self.session.request(method, url, **kwargs)
        self.last_request_at = time.monotonic()
        response.raise_for_status()
        return response


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Warning: could not read {path}: {error}")
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def lead_key(lead: dict[str, Any]) -> str:
    return "|".join(
        [
            str(lead.get("county_name") or lead.get("county") or "").upper().strip(),
            str(lead.get("parcel_id") or "").upper().strip(),
            str(lead.get("owner_name") or "").upper().strip(),
            str(float(lead.get("surplus_amount") or 0)),
        ]
    )


def default_enrichment() -> dict[str, Any]:
    return {
        "deceased": False,
        "deceased_note": "",
        "redemption_filed": False,
        "assignment_filed": False,
        "satisfaction_filed": False,
        "status_flag": "",
        "obituary_hit": False,
        "obituary_note": "",
        "probate_found": False,
        "probate_note": "",
        "urgent": False,
        "urgent_note": "",
        "days_remaining": None,
        "removed_last_run": False,
        "removed_note": "",
        "enriched_at": "",
        "checks_run": [],
        "tags": [],
    }


def ensure_enrichment(lead: dict[str, Any], enriched_at: str) -> dict[str, Any]:
    existing = lead.get("enrichment")
    enrichment = default_enrichment()
    if isinstance(existing, dict):
        enrichment.update(existing)
    enrichment["enriched_at"] = enriched_at
    enrichment["checks_run"] = []
    enrichment["tags"] = list(dict.fromkeys(enrichment.get("tags") or []))
    lead["enrichment"] = enrichment
    lead.setdefault("tags", [])
    if not isinstance(lead["tags"], list):
        lead["tags"] = []
    return enrichment


def add_check(enrichment: dict[str, Any], check_name: str) -> None:
    checks = enrichment.setdefault("checks_run", [])
    if check_name not in checks:
        checks.append(check_name)


def add_tag(lead: dict[str, Any], tag: str) -> None:
    if not tag:
        return
    lead_tags = lead.setdefault("tags", [])
    if not isinstance(lead_tags, list):
        lead_tags = []
        lead["tags"] = lead_tags
    if tag not in lead_tags:
        lead_tags.append(tag)
    enrichment = lead.setdefault("enrichment", default_enrichment())
    enrichment_tags = enrichment.setdefault("tags", [])
    if tag not in enrichment_tags:
        enrichment_tags.append(tag)


def first_name(owner_name: str) -> str:
    cleaned = re.sub(r"\b(estate|of|the|heirs|llc|inc|corp|corporation|company|co)\b", " ", owner_name, flags=re.IGNORECASE)
    parts = [part.strip(" ,.&") for part in cleaned.split() if part.strip(" ,.&")]
    return parts[0].title() if parts else "there"


def county_display(lead: dict[str, Any]) -> str:
    return str(lead.get("county_name") or lead.get("county") or "Georgia").replace(" GA", " County")


def amount_label(value: Any) -> str:
    return f"${float(value or 0):,.0f}"


def lead_address_label(lead: dict[str, Any]) -> str:
    address = ", ".join(str(lead.get(key) or "").strip() for key in ("property_address", "city", "zip") if lead.get(key))
    if address:
        return address
    if lead.get("parcel_id") and lead.get("county_name") == "Clayton GA":
        return f"Clayton County parcel {lead.get('parcel_id')} - address lookup needed in qPublic."
    return "Address pending"


def urgency_label(lead: dict[str, Any]) -> str:
    days = lead.get("enrichment", {}).get("days_remaining", lead.get("days_to_claim"))
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        return "deadline needs county verification"
    if days_int < 0:
        return f"{abs(days_int)} days past the expected 5-year window"
    if days_int == 0:
        return "deadline is today"
    return f"{days_int} days before expected 5-year deadline"


def build_personalized_sms(lead: dict[str, Any]) -> str:
    owner = first_name(str(lead.get("owner_name") or ""))
    county = county_display(lead)
    surplus = amount_label(lead.get("surplus_amount"))
    parcel = str(lead.get("parcel_id") or "").strip()
    parcel_note = f" for parcel {parcel}" if parcel else ""
    return (
        f"Hi {owner}, this is Michael with 229 Holdings LLC. "
        f"I found public {county} records showing possible excess funds of about {surplus}{parcel_note} "
        "from a prior tax sale. If you are the former owner, I can help verify whether the funds are still available. "
        "No obligation - reply YES and I can send the details."
    )


def build_precall_brief(lead: dict[str, Any]) -> dict[str, Any]:
    owner = str(lead.get("owner_name") or "Unknown owner")
    county = county_display(lead)
    surplus = amount_label(lead.get("surplus_amount"))
    fee = amount_label(lead.get("your_cut_30pct"))
    parcel = str(lead.get("parcel_id") or "no parcel listed")
    address = lead_address_label(lead)
    urgency = urgency_label(lead)
    talking_points = [
        f"Public records show a possible {surplus} surplus tied to {county}.",
        f"Parcel/reference: {parcel}. Property/address context: {address}.",
        "First call goal is verification, not selling. Confirm they are the former owner or connected heir.",
        "Ask whether they have already filed a claim or spoken with the county.",
        "Explain that Georgia claims usually need a Georgia-licensed attorney before any filing."
    ]
    if lead.get("is_estate_owner"):
        talking_points.append("Estate/heir angle: ask who is authorized to speak for the estate or whether probate has an executor.")
    if lead.get("is_entity_owner"):
        talking_points.append("Entity owner angle: ask for the managing member, registered agent, or person authorized to sign.")
    objections = [
        {
            "objection": "I do not believe this is real.",
            "response": f"That is fair. Tell them to call {county} directly and verify excess funds for parcel {parcel} before signing anything."
        },
        {
            "objection": "I already have an attorney.",
            "response": "Ask whether the attorney has confirmed the current balance and deadline. Offer to coordinate only if they want help moving it faster."
        },
        {
            "objection": "How do you get paid?",
            "response": f"Explain the fee is success-based and estimated at 30% only if funds are recovered. Potential fee pool on this lead is about {fee}."
        },
    ]
    return {
        "opening_line": (
            f"Hi {first_name(owner)}, this is Michael with 229 Holdings LLC. "
            f"I am calling about possible excess funds listed in {county} records under {owner}."
        ),
        "key_talking_points": talking_points,
        "likely_objections": objections,
        "urgency_angle": f"Timing note: {urgency}. Verify the exact claim deadline with the county before promising availability.",
        "voicemail_script": (
            f"Hi {first_name(owner)}, this is Michael with 229 Holdings LLC. "
            f"I found a public record in {county} that may show excess funds connected to you. "
            "I need to verify a few details before giving you exact numbers. Please call or text me back when you have a minute."
        ),
        "special_notes": [
            "Do not promise funds are available until the county confirms no claim has been filed.",
            "Verify open liens, assignments, redemption filings, and attorney requirements before signing an agreement.",
            "If owner appears deceased, pivot to heir/executor research before outreach."
        ],
    }


def build_contract_text(lead: dict[str, Any]) -> str:
    generated_date = datetime.now(timezone.utc).date().isoformat()
    owner = str(lead.get("owner_name") or "Former Property Owner")
    county = county_display(lead)
    parcel = str(lead.get("parcel_id") or "Parcel to be verified")
    address = lead_address_label(lead)
    surplus = amount_label(lead.get("surplus_amount"))
    fee = amount_label(float(lead.get("surplus_amount") or 0) * 0.30)
    sale_date = str(lead.get("sale_date") or "Sale date to be verified")
    deadline = str(lead.get("claim_deadline") or "Claim deadline to be verified")
    return f"""ASSET RECOVERY AGREEMENT

This Asset Recovery Agreement ("Agreement") is prepared on {generated_date} by and between:

Claimant / Former Owner:
{owner}

Recovery Specialist:
229 Holdings LLC

Property / Claim Information:
County: {county}
Parcel ID: {parcel}
Property Address: {address}
Tax Sale Date: {sale_date}
Estimated Excess Funds / Surplus Amount: {surplus}
Expected Claim Deadline: {deadline}

1. Purpose
Claimant authorizes 229 Holdings LLC to assist with research, coordination, document preparation support, and recovery workflow related to potential excess funds, surplus funds, or excess proceeds connected to the property and parcel listed above.

2. No Guarantee
Claimant understands that the surplus amount, claim deadline, lien priority, redemption status, assignment status, and claim eligibility must be verified directly with the county, court, or appropriate government office. 229 Holdings LLC does not guarantee that funds are available or recoverable.

3. Attorney Requirement
Claimant understands that Georgia surplus fund claims may require review, preparation, or filing by a Georgia-licensed attorney. 229 Holdings LLC may coordinate with an attorney partner, but does not provide legal advice.

4. Specialist Fee
If funds are successfully recovered, Claimant agrees that 229 Holdings LLC may receive a success fee equal to 30% of recovered funds unless a different written fee is agreed by the parties and permitted by applicable law. Based on the current estimated surplus amount, the estimated 30% fee would be {fee}.

5. Claimant Cooperation
Claimant agrees to provide identification, ownership/heirship documents, probate documents if applicable, signatures, and other reasonable information needed to verify and pursue the claim.

6. No Upfront Fee
No upfront fee is charged by 229 Holdings LLC under this Agreement. Any attorney fees, filing costs, or third-party costs must be separately disclosed and approved.

7. Limited Authorization
Claimant authorizes 229 Holdings LLC to communicate with county offices, title researchers, and attorney partners for the limited purpose of verifying and assisting with this surplus funds claim.

8. Signatures

Claimant Signature: _______________________________ Date: _______________

Printed Name: {owner}

229 Holdings LLC Representative: __________________ Date: _______________

Internal Verification Checklist:
- County confirmed funds still available: Yes / No
- No prior claim filed: Yes / No
- No assignment of excess funds found: Yes / No
- Redemption status verified: Yes / No
- Georgia attorney reviewed filing path: Yes / No
"""


def apply_local_lead_prep(lead: dict[str, Any]) -> None:
    enrichment = lead["enrichment"]
    enrichment["personalized_sms"] = build_personalized_sms(lead)
    enrichment["precall_brief"] = build_precall_brief(lead)
    enrichment["contract_text"] = build_contract_text(lead)
    add_check(enrichment, "lead_prep")
    add_check(enrichment, "contract")


def build_digest(payload: dict[str, Any], removed_count: int, enriched_at: str) -> dict[str, Any]:
    leads = payload.get("leads") or []
    urgent = [lead for lead in leads if lead.get("enrichment", {}).get("urgent")]
    critical = [
        lead for lead in leads
        if isinstance(lead.get("enrichment", {}).get("days_remaining"), int)
        and lead["enrichment"]["days_remaining"] < 30
    ]
    top_leads = sorted(leads, key=lambda lead: (float(lead.get("score") or 0), float(lead.get("surplus_amount") or 0)), reverse=True)[:10]
    return {
        "generated_at": enriched_at,
        "lead_count": len(leads),
        "urgent_count": len(urgent),
        "critical_count": len(critical),
        "removed_leads_count": removed_count,
        "total_surplus_amount": payload.get("total_surplus_amount", 0),
        "total_potential_fee_30pct": payload.get("total_potential_fee_30pct", 0),
        "summary": (
            f"{len(leads)} active surplus leads. "
            f"{len(urgent)} urgent, {len(critical)} critical, {removed_count} removed since previous run."
        ),
        "top_leads": [
            {
                "owner_name": lead.get("owner_name") or "",
                "county": lead.get("county_name") or "",
                "surplus_amount": lead.get("surplus_amount") or 0,
                "your_cut_30pct": lead.get("your_cut_30pct") or 0,
                "score": lead.get("score") or 0,
                "days_remaining": lead.get("enrichment", {}).get("days_remaining"),
                "recommended_next_step": "Call county to verify balance, deadline, and whether a claim has been filed.",
            }
            for lead in top_leads
        ],
    }


def is_network_candidate(lead: dict[str, Any]) -> bool:
    return int(float(lead.get("score") or 0)) >= MIN_NETWORK_SCORE


def is_individual_owner(lead: dict[str, Any]) -> bool:
    return bool(lead.get("is_individual_owner") or lead.get("is_individual"))


def parse_iso_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def add_years(date_value: datetime, years: int) -> datetime:
    try:
        return date_value.replace(year=date_value.year + years)
    except ValueError:
        return date_value.replace(month=2, day=28, year=date_value.year + years)


def recalculate_deadline_urgency(lead: dict[str, Any], today: datetime) -> None:
    enrichment = lead["enrichment"]
    sale_date = parse_iso_date(str(lead.get("sale_date") or ""))
    if not sale_date:
        add_check(enrichment, "deadline")
        return
    deadline = add_years(sale_date, 5)
    days_remaining = (deadline.date() - today.date()).days
    enrichment["days_remaining"] = days_remaining
    lead["claim_deadline"] = deadline.date().isoformat()
    lead["days_to_claim"] = days_remaining
    if days_remaining < 90:
        enrichment["urgent"] = True
        enrichment["urgent_note"] = (
            f"URGENT: {days_remaining} days until funds transfer to GA DOR. "
            "Call owner immediately."
        )
        add_tag(lead, "URGENT - < 90 days")
        lead["score"] = min(int(float(lead.get("score") or 0)) + 25, 100)
    if days_remaining < 30:
        add_tag(lead, "CRITICAL - < 30 days")
        lead["score"] = 100
    add_check(enrichment, "deadline")


def parse_batch_deceased(payload: Any) -> bool:
    if isinstance(payload, dict):
        if payload.get("deceased") is True:
            return True
        for key in ("result", "data", "person", "owner"):
            if key in payload and parse_batch_deceased(payload[key]):
                return True
        if isinstance(payload.get("records"), list):
            return any(parse_batch_deceased(item) for item in payload["records"])
    if isinstance(payload, list):
        return any(parse_batch_deceased(item) for item in payload)
    return False


def run_batch_deceased_check(lead: dict[str, Any], session: RateLimitedSession, api_key: str) -> None:
    enrichment = lead["enrichment"]
    add_check(enrichment, "deceased")
    if not api_key or not is_network_candidate(lead):
        return
    payload = {
        "owner_name": lead.get("owner_name") or "",
        "property_address": lead.get("property_address") or "",
        "city": lead.get("city") or "",
        "state": "GA",
        "zip": lead.get("zip") or "",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        url = os.getenv("BATCH_API_URL", DEFAULT_BATCH_API_URL)
        response = session.request("POST", url, json=payload, headers=headers)
        result = response.json()
        if parse_batch_deceased(result):
            enrichment["deceased"] = True
            enrichment["deceased_note"] = (
                "Owner flagged deceased by skip trace. "
                "Locate heirs via Georgia Probate Records."
            )
            add_tag(lead, "Estate - Find Heirs")
    except Exception as error:
        print(f"Warning: Batch deceased check failed for {lead.get('owner_name')}: {error}")


def run_gsccca_check(lead: dict[str, Any], session: RateLimitedSession) -> None:
    enrichment = lead["enrichment"]
    add_check(enrichment, "gsccca")
    if not is_network_candidate(lead) or not lead.get("parcel_id"):
        return
    params = {
        "searchTerm": lead.get("parcel_id") or "",
        "parcelId": lead.get("parcel_id") or "",
        "county": str(lead.get("county_name") or "").replace(" GA", ""),
    }
    try:
        response = session.request(
            "GET",
            GSCCCA_URL,
            params=params,
            headers={"User-Agent": "SurplusIntelBot/1.0"},
        )
        text = response.text
        if re.search(r"DEED\s+OF\s+REDEMPTION", text, flags=re.IGNORECASE):
            enrichment["redemption_filed"] = True
            enrichment["status_flag"] = (
                "REDEMPTION FILED - owner reclaimed property. "
                "Surplus may be void. Verify with county."
            )
            add_tag(lead, "Verify - Redemption Found")
        if re.search(r"ASSIGNMENT\s+OF\s+EXCESS", text, flags=re.IGNORECASE):
            enrichment["assignment_filed"] = True
            enrichment["status_flag"] = (
                "ASSIGNMENT FOUND - another agent may have secured rights. "
                "Call county immediately."
            )
            add_tag(lead, "Verify - Assignment Found")
            lead["score"] = max(int(float(lead.get("score") or 0)) - 30, 0)
        if re.search(r"SATISFACTION", text, flags=re.IGNORECASE):
            enrichment["satisfaction_filed"] = True
    except Exception as error:
        print(f"Warning: GSCCCA check failed for {lead.get('parcel_id')}: {error}")


def run_obituary_check(lead: dict[str, Any], session: RateLimitedSession, api_key: str) -> None:
    enrichment = lead["enrichment"]
    add_check(enrichment, "obituary")
    if not api_key or not is_network_candidate(lead) or not is_individual_owner(lead):
        return
    query = f"{lead.get('owner_name') or ''} obituary OR death OR funeral Georgia"
    params = {"engine": "google", "q": query, "api_key": api_key, "num": 5}
    try:
        response = session.request("GET", SERP_API_URL, params=params)
        payload = response.json()
        pattern = re.compile(r"obituary|passed away|in loving memory|funeral home", re.IGNORECASE)
        for item in payload.get("organic_results") or []:
            title = str(item.get("title") or "")
            snippet = str(item.get("snippet") or "")
            if pattern.search(f"{title} {snippet}"):
                enrichment["obituary_hit"] = True
                enrichment["obituary_note"] = f"Possible obituary found. Check: {item.get('link') or ''}"
                add_tag(lead, "Possible Deceased - Verify")
                break
    except Exception as error:
        print(f"Warning: obituary search failed for {lead.get('owner_name')}: {error}")


def run_probate_check(lead: dict[str, Any], session: RateLimitedSession) -> None:
    enrichment = lead["enrichment"]
    if not (enrichment.get("deceased") or enrichment.get("obituary_hit")):
        return
    add_check(enrichment, "probate")
    params = {"q": lead.get("owner_name") or "", "search": lead.get("owner_name") or ""}
    try:
        response = session.request(
            "GET",
            PROBATE_URL,
            params=params,
            headers={"User-Agent": "SurplusIntelBot/1.0"},
        )
        html = response.text
        owner_tokens = [token for token in re.split(r"\s+", str(lead.get("owner_name") or "")) if len(token) > 2]
        matches_owner = any(re.search(re.escape(token), html, flags=re.IGNORECASE) for token in owner_tokens)
        matches_estate = re.search(r"estate|probate|executor|administrator", html, flags=re.IGNORECASE)
        if matches_owner and matches_estate:
            enrichment["probate_found"] = True
            enrichment["probate_note"] = (
                "Probate record found. Estate may have an executor - "
                "contact them as the claimant."
            )
            add_tag(lead, "Probate Found - Contact Executor")
            lead["score"] = min(int(float(lead.get("score") or 0)) + 15, 100)
    except Exception as error:
        print(f"Warning: probate check failed for {lead.get('owner_name')}: {error}")


def removed_entry_from_lead(lead: dict[str, Any], removal_date: str) -> dict[str, Any]:
    return {
        "owner_name": lead.get("owner_name") or "",
        "surplus_amount": lead.get("surplus_amount") or 0,
        "county": lead.get("county_name") or lead.get("county") or "",
        "parcel_id": lead.get("parcel_id") or "",
        "last_seen": lead.get("enrichment", {}).get("enriched_at")
        or lead.get("generated_at")
        or lead.get("first_seen_date")
        or "",
        "removal_date": removal_date,
        "note": (
            "Removed from county PDF - may have been claimed, paid, or transferred to state. "
            "Call county to verify."
        ),
        "enrichment": {
            **default_enrichment(),
            "removed_last_run": True,
            "removed_note": (
                "Removed from county PDF - may have been claimed, paid, or transferred to state. "
                "Call county to verify."
            ),
            "enriched_at": removal_date,
            "checks_run": ["diff"],
        },
    }


def diff_removed_leads(current_leads: list[dict[str, Any]], removal_date: str) -> list[dict[str, Any]]:
    previous_payload = load_json(PREVIOUS_PATH, {})
    previous_leads = previous_payload.get("leads") if isinstance(previous_payload, dict) else previous_payload
    if not isinstance(previous_leads, list):
        previous_leads = []
    current_keys = {lead_key(lead) for lead in current_leads}
    removed = [removed_entry_from_lead(lead, removal_date) for lead in previous_leads if lead_key(lead) not in current_keys]
    write_json(REMOVED_PATH, {"generated_at": removal_date, "removed_count": len(removed), "leads": removed})
    return removed


def refresh_dashboard_embed(payload: dict[str, Any]) -> None:
    if not INDEX_PATH.exists():
        return
    html = INDEX_PATH.read_text(encoding="utf-8")
    safe_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    block = f"{EMBED_START}{safe_json}{EMBED_END}"
    start = html.find(EMBED_START)
    if start == -1:
        html = html.replace("  <script>", f"  {block}\n  <script>", 1)
    else:
        end = html.find(EMBED_END, start)
        if end == -1:
            raise ValueError("Embedded dashboard data block is missing closing script tag")
        html = html[:start] + block + html[end + len(EMBED_END) :]
    INDEX_PATH.write_text(html, encoding="utf-8")


def enrich_payload(payload: dict[str, Any]) -> dict[str, Any]:
    enriched_at = now_iso()
    today = datetime.now(timezone.utc)
    leads = payload.get("leads") or []
    if not isinstance(leads, list):
        leads = []
        payload["leads"] = leads

    removed = diff_removed_leads(leads, enriched_at)
    batch_key = os.getenv("BATCH_API_KEY", "").strip()
    serp_key = os.getenv("SERP_API_KEY", "").strip()
    if not batch_key:
        print("Warning: BATCH_API_KEY not set; skipping BatchSkipTracing deceased checks.")
    if not serp_key:
        print("Warning: SERP_API_KEY not set; skipping obituary quick searches.")

    session = RateLimitedSession()
    for index, lead in enumerate(leads, start=1):
        try:
            enrichment = ensure_enrichment(lead, enriched_at)
            add_check(enrichment, "diff")
            recalculate_deadline_urgency(lead, today)
            if is_network_candidate(lead):
                print(f"Enriching {index}/{len(leads)}: {lead.get('owner_name')}")
            run_batch_deceased_check(lead, session, batch_key)
            run_gsccca_check(lead, session)
            run_obituary_check(lead, session, serp_key)
            run_probate_check(lead, session)
            apply_local_lead_prep(lead)
        except Exception as error:
            print(f"Warning: enrichment failed for {lead.get('owner_name')}: {error}")
            continue

    total_amount = sum(float(lead.get("surplus_amount") or 0) for lead in leads)
    payload["lead_count"] = len(leads)
    payload["total_surplus_amount"] = round(total_amount, 2)
    payload["total_potential_fee_30pct"] = round(total_amount * 0.30, 2)
    payload["fire_lead_count"] = sum(1 for lead in leads if lead.get("tier") == "FIRE")
    payload["enriched_at"] = enriched_at
    payload["removed_leads_count"] = len(removed)
    payload["removed_leads"] = removed
    digest = build_digest(payload, len(removed), enriched_at)
    write_json(DIGEST_PATH, digest)
    payload["enrichment_meta"] = {
        "enriched_at": enriched_at,
        "removed_leads_count": len(removed),
        "batch_deceased_check": bool(batch_key),
        "serp_obituary_check": bool(serp_key),
        "network_score_minimum": MIN_NETWORK_SCORE,
        "digest_path": str(DIGEST_PATH.relative_to(ROOT)).replace("\\", "/"),
        "digest": digest,
    }
    return payload


def save_previous_snapshot(payload: dict[str, Any]) -> None:
    snapshot = deepcopy(payload)
    snapshot.pop("removed_leads", None)
    write_json(PREVIOUS_PATH, snapshot)


def main() -> None:
    payload = load_json(LEADS_PATH, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("leads"), list):
        raise SystemExit(f"No valid surplus lead payload found at {LEADS_PATH}")
    enriched = enrich_payload(payload)
    write_json(LEADS_PATH, enriched)
    refresh_dashboard_embed(enriched)
    save_previous_snapshot(enriched)
    print(
        f"Enriched {enriched.get('lead_count', 0)} leads | "
        f"removed last run: {enriched.get('removed_leads_count', 0)} | "
        f"{enriched.get('enriched_at')}"
    )


if __name__ == "__main__":
    main()
