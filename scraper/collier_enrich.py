from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "collier_leads.json"
PROBATE_URL = "https://collierclerk.com/court-divisions/probate/"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def skip_trace_links(lead: dict[str, Any]) -> dict[str, str]:
    name = str(lead.get("owner_name") or "").strip()
    city = str(lead.get("city") or "").strip()
    state = str(lead.get("state") or "FL").strip() or "FL"
    name_dash = quote_plus(name.replace(" ", "-"))
    city_state = quote_plus(f"{city} {state}".strip())
    return {
        "truepeoplesearch": f"https://www.truepeoplesearch.com/results?name={quote_plus(name)}&citystatezip={city_state}",
        "fastpeoplesearch": f"https://www.fastpeoplesearch.com/name/{name_dash}_{quote_plus((city + '-' + state).strip('-'))}",
        "zabasearch": f"https://www.zabasearch.com/people/{quote_plus(name.replace(' ', '+'))}/",
        "google": f"https://www.google.com/search?q={quote_plus(chr(34) + name + chr(34) + ' ' + city + ' ' + state)}",
        "ssdi": f"https://stevemorse.org/ssdi/ssdi.html?name={quote_plus(name)}",
        "collier_probate": f"{PROBATE_URL}?search={quote_plus(name)}",
        "batchskiptracing": "https://batchskiptracing.com",
    }


def call_script(lead: dict[str, Any]) -> str:
    amount = f"${float(lead.get('surplus_amount') or 0):,.0f}"
    owner = lead.get("owner_name") or "the former owner"
    parcel = lead.get("parcel_id") or "the parcel"
    deadline = lead.get("claim_deadline") or "the county deadline"
    return (
        f"Hi, I am calling about Collier County tax deed excess proceeds connected to {owner} "
        f"and parcel {parcel}. County records show possible surplus funds of about {amount}. "
        f"I wanted to verify you received the Clerk's notice and see if you need help preparing "
        f"the notarized claim packet before the claim deadline, currently tracked as {deadline}."
    )


def text_script(lead: dict[str, Any]) -> str:
    amount = f"${float(lead.get('surplus_amount') or 0):,.0f}"
    return (
        f"Hi, this is Michael with 229 Holdings. Collier County records show possible tax deed "
        f"surplus funds of about {amount} tied to {lead.get('owner_name') or 'your name'}. "
        f"I can help you verify and prepare the claim paperwork. Is this the right number?"
    )


def enrich_lead(lead: dict[str, Any]) -> dict[str, Any]:
    enrichment = lead.get("enrichment") if isinstance(lead.get("enrichment"), dict) else {}
    enrichment.update(
        {
            "enriched_at": now_iso(),
            "checks_run": list(dict.fromkeys([*(enrichment.get("checks_run") or []), "collier_deadline", "collier_skip_trace_links", "lead_prep"])),
            "skip_trace_links": skip_trace_links(lead),
            "simple_call_script": call_script(lead),
            "short_text_script": text_script(lead),
            "urgent": lead.get("fl_urgency") in {"CRITICAL", "URGENT"},
            "critical": lead.get("fl_urgency") == "CRITICAL",
            "days_remaining": lead.get("days_to_claim"),
            "urgent_note": (
                f"{lead.get('days_to_claim')} days until Collier 120-day deadline."
                if lead.get("days_to_claim") is not None and int(lead.get("days_to_claim") or 0) >= 0
                else "Collier claim deadline appears expired or requires manual review."
            ),
        }
    )
    lead["enrichment"] = enrichment
    return lead


def run(path: Path = DATA_PATH) -> dict[str, int]:
    if not path.exists():
        print("data/collier_leads.json not found; skipping Collier enrichment.")
        return {"enriched": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    leads = payload.get("leads") if isinstance(payload.get("leads"), list) else []
    for lead in leads:
        enrich_lead(lead)
    payload["enrichment_meta"] = {"generated_at": now_iso(), "enriched": len(leads)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Collier enrichment complete: {len(leads)} leads")
    return {"enriched": len(leads)}


if __name__ == "__main__":
    run()
