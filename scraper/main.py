from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import clayton_ga, dekalb_ga
    from .score import score_leads
except ImportError:
    import clayton_ga
    import dekalb_ga
    from score import score_leads


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "surplus_leads.json"
INDEX_PATH = ROOT / "index.html"
EMBED_START = '<script type="application/json" id="embedded-data">'
EMBED_END = "</script>"


async def run_scrapers() -> list[dict[str, Any]]:
    results = await asyncio.gather(
        clayton_ga.scrape(),
        dekalb_ga.scrape(),
        return_exceptions=True,
    )
    leads: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        leads.extend(result)
    if errors:
        print("Scraper warnings:")
        for error in errors:
            print(f"- {error}")
    return leads


def clean_lead_identity(lead: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(lead)
    owner = re.sub(r"\s+", " ", str(cleaned.get("owner_name") or "")).strip()
    parcel = str(cleaned.get("parcel_id") or "").strip()
    address = str(cleaned.get("property_address") or "").strip()
    city = str(cleaned.get("city") or "").strip()
    zip_code = str(cleaned.get("zip") or "").strip()

    if address and not re.search(r"\d", address) and re.search(r"\d", city):
        owner = f"{owner} {address}".strip()
        address = city
        city = zip_code.title() if zip_code and not re.search(r"\d", zip_code) else ""
        zip_code = zip_code if re.fullmatch(r"\d{5}(?:-\d{4})?", zip_code) else ""

    if re.fullmatch(r"\d{5}(?:-\d{4})?", address) and not city:
        zip_code = address
        address = ""

    clayton_parcel = re.search(r"\b(\d{5}[A-Z]\s+[A-Z]\d{3})\b", owner, flags=re.IGNORECASE)
    if clayton_parcel:
        if not parcel:
            parcel = clayton_parcel.group(1).upper()
        owner = (owner[: clayton_parcel.start()] + owner[clayton_parcel.end() :]).strip()

    street_match = re.search(
        r"\b(\d{2,6}\s+[A-Za-z0-9][A-Za-z0-9\s.'#-]+?\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ct|Court|Cir|Circle|Ln|Lane|Way|Pl|Place|Pkwy|Parkway|Trl|Trail|Hwy|Highway|Blvd|Terrace|Ter)\b.*)$",
        owner,
        flags=re.IGNORECASE,
    )
    if street_match:
        if not address:
            address = street_match.group(1).strip()
        owner = owner[: street_match.start()].strip()

    owner = re.sub(r"\s+", " ", owner).strip(" ,-")
    tokens = owner.split()
    if len(tokens) >= 2 and tokens[-1].upper() == tokens[-2].upper():
        owner = " ".join(tokens[:-1])

    cleaned["owner_name"] = owner.title()
    cleaned["parcel_id"] = parcel.upper()
    cleaned["property_address"] = address.title()
    cleaned["city"] = city.title()
    cleaned["zip"] = zip_code
    cleaned["needs_address_lookup"] = not bool(address)
    cleaned["attorney_required"] = True
    return cleaned


def dedupe_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float, str]] = set()
    for lead in leads:
        key = (
            str(lead.get("county_name", "")).upper(),
            str(lead.get("owner_name", "")).upper(),
            float(lead.get("surplus_amount") or 0),
            str(lead.get("parcel_id", "")).upper(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(lead)
    return deduped


def lead_identity_key(lead: dict[str, Any]) -> str:
    return "|".join(
        [
            str(lead.get("county_name", "")).upper().strip(),
            str(lead.get("parcel_id", "")).upper().strip(),
            str(lead.get("owner_name", "")).upper().strip(),
            str(float(lead.get("surplus_amount") or 0)),
        ]
    )


def existing_first_seen(output_path: Path = OUTPUT_PATH) -> dict[str, str]:
    if not output_path.exists():
        return {}
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    existing: dict[str, str] = {}
    for lead in payload.get("leads") or []:
        first_seen = str(lead.get("first_seen_date") or "").strip()
        if first_seen:
            existing[lead_identity_key(lead)] = first_seen
    return existing


def build_payload(leads: list[dict[str, Any]]) -> dict[str, Any]:
    scored = score_leads(dedupe_leads([clean_lead_identity(lead) for lead in leads]))
    total_amount = sum(float(lead.get("surplus_amount") or 0) for lead in scored)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]
    seen_dates = existing_first_seen()
    for lead in scored:
        lead["first_seen_date"] = seen_dates.get(lead_identity_key(lead), today)
    return {
        "generated_at": now,
        "source": "Georgia surplus funds public records",
        "counties": ["Clayton GA", "DeKalb GA"],
        "lead_count": len(scored),
        "total_surplus_amount": round(total_amount, 2),
        "total_potential_fee_30pct": round(total_amount * 0.30, 2),
        "fire_lead_count": sum(1 for lead in scored if lead.get("tier") == "FIRE"),
        "leads": scored,
    }


def write_payload(payload: dict[str, Any], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_dashboard_fallback(payload: dict[str, Any], index_path: Path = INDEX_PATH) -> None:
    if not index_path.exists():
        return
    html = index_path.read_text(encoding="utf-8")
    safe_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    block = f'{EMBED_START}{safe_json}{EMBED_END}'
    start = html.find(EMBED_START)
    if start == -1:
        html = html.replace("  <script>", f"  {block}\n  <script>", 1)
    else:
        end = html.find(EMBED_END, start)
        if end == -1:
            raise ValueError("Embedded dashboard data block is missing closing script tag")
        html = html[:start] + block + html[end + len(EMBED_END):]
    index_path.write_text(html, encoding="utf-8")


async def main_async(output: Path = OUTPUT_PATH) -> dict[str, Any]:
    leads = await run_scrapers()
    payload = build_payload(leads)
    write_payload(payload, output)
    write_dashboard_fallback(payload)
    print(
        f"Wrote {payload['lead_count']} leads | "
        f"${payload['total_surplus_amount']:,.0f} total surplus | "
        f"{payload['generated_at']}"
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Georgia surplus funds PDFs.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(Path(args.output)))


if __name__ == "__main__":
    main()
