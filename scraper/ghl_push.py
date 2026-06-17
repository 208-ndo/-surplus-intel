from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "surplus_leads.json"
GHL_URL = "https://rest.gohighlevel.com/v1/contacts/"


def split_name(owner_name: str) -> tuple[str, str]:
    owner = re.sub(r"\s+", " ", str(owner_name or "").strip())
    if not owner:
        return "", ""
    if owner.upper().startswith("ESTATE OF "):
        owner = owner[10:].strip()
    parts = owner.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def load_leads(path: Path = DATA_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("leads") or []


def eligible_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        lead
        for lead in leads
        if int(lead.get("score") or 0) >= 60 and float(lead.get("surplus_amount") or 0) >= 20000
    ]


def build_contact_payload(lead: dict[str, Any]) -> dict[str, Any]:
    first_name, last_name = split_name(lead.get("owner_name", ""))
    county = lead.get("county_name", "")
    tier = lead.get("tier", "")
    return {
        "firstName": first_name,
        "lastName": last_name,
        "tags": ["Surplus Lead", "Surplus-GA", "GA", county, tier],
        "customField": {
            "surplus_amount": lead.get("surplus_amount"),
            "your_cut_30pct": lead.get("your_cut_30pct"),
            "sale_date": lead.get("sale_date"),
            "county": county,
            "property_address": lead.get("property_address", ""),
            "parcel_id": lead.get("parcel_id", ""),
            "lead_score": lead.get("score"),
            "tier": tier,
            "attorney_required": lead.get("attorney_required", True),
            "claim_deadline": lead.get("claim_deadline", ""),
            "days_to_claim": lead.get("days_to_claim"),
            "claim_status": lead.get("claim_status", ""),
            "is_national_creditor": lead.get("is_national_creditor", False),
        },
    }


def push_lead(lead: dict[str, Any], api_key: str) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return requests.post(GHL_URL, headers=headers, json=build_contact_payload(lead), timeout=30)


def push_all(path: Path = DATA_PATH) -> dict[str, int]:
    api_key = os.getenv("GHL_API_KEY", "").strip()
    if not api_key:
        print("GHL_API_KEY not set; skipping CRM push.")
        return {"eligible": 0, "pushed": 0, "failed": 0}

    leads = eligible_leads(load_leads(path))
    pushed = 0
    failed = 0
    for lead in leads:
        try:
            response = push_lead(lead, api_key)
            if 200 <= response.status_code < 300:
                pushed += 1
            else:
                failed += 1
                print(f"GHL push failed {response.status_code}: {response.text[:250]}")
        except requests.RequestException as exc:
            failed += 1
            print(f"GHL push exception: {exc}")
    print(f"GHL push complete: {pushed} pushed | {failed} failed | {len(leads)} eligible")
    return {"eligible": len(leads), "pushed": pushed, "failed": failed}


if __name__ == "__main__":
    push_all()
