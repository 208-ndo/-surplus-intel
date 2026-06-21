from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pdfplumber

try:
    from .collier_score import score_collier_leads
except ImportError:  # pragma: no cover
    from collier_score import score_collier_leads


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "collier_leads.json"
SOURCE_PAGE = "https://www.collierclerk.com/tax-deed-sales/tax-deed-surplus/"
PDF_URL = "https://www.collierclerk.com/wp-content/uploads/Tax-Deed-Sales-Excess-Proceeds-List-2.pdf"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()
    return text


def parse_money(value: Any) -> float:
    text = clean(value)
    text = text.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_date(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def record_id(record: dict[str, Any]) -> str:
    base = f"{record.get('owner_name', '')}_{record.get('parcel_id', '')}_{int(record.get('surplus_amount') or 0)}"
    return re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")


def first_money_cell(row: list[Any]) -> tuple[int, str]:
    for idx, cell in enumerate(row):
        text = clean(cell)
        if "$" in text or re.search(r"\d[\d,\s]*\.\d{2}", text):
            amount = parse_money(text)
            if amount:
                return idx, text
    return -1, ""


def parse_table_rows(table: list[list[Any]]) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    current_tda = ""
    for raw in table[1:]:
        row = list(raw or [])
        row += [""] * max(0, 16 - len(row))
        sale_date = parse_date(row[0])
        if not sale_date:
            if leads and clean(row[8]):
                leads[-1]["claims_filed"].append(clean(row[8]))
            continue

        tda_cell = clean(row[2])
        if tda_cell:
            current_tda = tda_cell.splitlines()[0].strip()
        amount_idx, amount_text = first_money_cell(row)
        amount = parse_money(amount_text)
        deadline = ""
        for cell in row[7:9]:
            parsed = parse_date(cell)
            if parsed:
                deadline = parsed
                break
        owner = clean(row[9])
        address = clean(row[11])
        city = clean(row[12]).title()
        state_zip = clean(row[13])
        parcel = clean(row[14])
        legal = clean(row[15])
        claims = [clean(row[8])] if clean(row[8]) else []

        if not owner or not amount:
            continue

        lead = {
            "source": "collier_tax_deed_excess_proceeds_pdf",
            "source_url": PDF_URL,
            "source_page": SOURCE_PAGE,
            "state": "FL",
            "county_name": "Collier FL",
            "county": "Collier",
            "owner_name": owner,
            "property_address": address,
            "mailing_address": address,
            "city": city,
            "zip": state_zip,
            "parcel_id": parcel,
            "property_id": parcel,
            "legal_description": legal,
            "tda_number": tda_cell.splitlines()[0].strip() if tda_cell else current_tda,
            "sale_date": sale_date,
            "notice_date": deadline,
            "surplus_amount": amount,
            "claims_filed": claims,
            "claim_pending": bool(claims),
            "first_seen_date": datetime.now(timezone.utc).date().isoformat(),
            "last_seen_date": datetime.now(timezone.utc).date().isoformat(),
        }
        lead["lead_id"] = record_id(lead)
        leads.append(lead)
    return leads


def extract_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                leads.extend(parse_table_rows(table))
    deduped: dict[str, dict[str, Any]] = {}
    for lead in leads:
        key = lead.get("lead_id") or record_id(lead)
        if key in deduped:
            deduped[key]["claims_filed"].extend(lead.get("claims_filed") or [])
        else:
            deduped[key] = lead
    return list(deduped.values())


async def fetch_pdf(session: aiohttp.ClientSession, output_path: Path) -> None:
    headers = {"User-Agent": "Mozilla/5.0 surplus-intel/1.0"}
    async with session.get(PDF_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=45)) as response:
        response.raise_for_status()
        output_path.write_bytes(await response.read())


def build_payload(leads: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    scored = score_collier_leads(leads)
    total = sum(float(lead.get("surplus_amount") or 0) for lead in scored)
    active = [lead for lead in scored if not lead.get("is_expired")]
    return {
        "generated_at": generated_at,
        "source": PDF_URL,
        "source_page": SOURCE_PAGE,
        "state": "FL",
        "counties": ["Collier FL"],
        "lead_count": len(scored),
        "active_lead_count": len(active),
        "total_surplus_amount": round(total, 2),
        "total_potential_fee_30pct": round(total * 0.30, 2),
        "total_potential_fee_15pct": round(total * 0.15, 2),
        "fire_lead_count": sum(1 for lead in scored if lead.get("tier") == "FIRE"),
        "critical_count": sum(1 for lead in scored if lead.get("fl_urgency") == "CRITICAL"),
        "hot_fee_window_count": sum(1 for lead in scored if lead.get("fee_tier") == "HIGH_URGENCY_15PCT"),
        "expired_count": sum(1 for lead in scored if lead.get("is_expired")),
        "leads": scored,
    }


async def run() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()
    tmp_dir = DATA_DIR / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = tmp_dir / "collier_excess_proceeds.pdf"
    async with aiohttp.ClientSession() as session:
        await fetch_pdf(session, pdf_path)
    leads = extract_pdf(pdf_path)
    try:
        pdf_path.unlink()
    except OSError:
        pass
    payload = build_payload(leads, generated_at)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Collier scrape complete: {payload['lead_count']} leads | "
        f"{payload['active_lead_count']} active | ${payload['total_surplus_amount']:,.0f} total"
    )
    return payload


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        fallback = {
            "generated_at": now_iso(),
            "source": PDF_URL,
            "source_page": SOURCE_PAGE,
            "state": "FL",
            "counties": ["Collier FL"],
            "lead_count": 0,
            "active_lead_count": 0,
            "total_surplus_amount": 0,
            "total_potential_fee_30pct": 0,
            "total_potential_fee_15pct": 0,
            "fire_lead_count": 0,
            "critical_count": 0,
            "hot_fee_window_count": 0,
            "expired_count": 0,
            "error": str(exc),
            "leads": [],
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(fallback, indent=2) + "\n", encoding="utf-8")
        print(f"Collier scrape failed gracefully: {exc}")


if __name__ == "__main__":
    main()
