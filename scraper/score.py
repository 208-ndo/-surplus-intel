from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any


ENTITY_TERMS = (
    " LLC",
    " L.L.C",
    " INC",
    " CORP",
    " CORPORATION",
    " COMPANY",
    " CO ",
    " BANK",
    " MORTGAGE",
    " FUND",
    " TRUST",
    " TRUSTEE",
    " HOLDINGS",
    " PROPERTIES",
    " INVESTMENTS",
    " LP",
    " LLP",
    " LTD",
    " AUTHORITY",
    " COUNTY",
    " CITY OF",
    " STATE OF",
)

NATIONAL_CREDITOR_TERMS = (
    "US BANK",
    "U S BANK",
    "WELLS FARGO",
    "BANK OF AMERICA",
    "BMO",
    "CHASE",
    "JPMORGAN",
    "DEUTSCHE BANK",
    "FANNIE MAE",
    "FREDDIE MAC",
    "FEDERAL NATIONAL",
    "PENNYMAC",
    "ROCKET MORTGAGE",
    "NATIONSTAR",
    "MR COOPER",
)

LOWER_INCOME_ZIPS = {
    "30032",
    "30034",
    "30035",
    "30038",
    "30058",
    "30083",
    "30088",
    "30236",
    "30238",
    "30260",
    "30273",
    "30274",
    "30297",
    "30310",
    "30311",
    "30315",
    "30316",
    "30331",
    "30344",
    "30349",
}


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%b %d %Y",
        "%B %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text.replace(",", ""), fmt).date()
        except ValueError:
            continue
    return None


def years_unclaimed(sale_date: Any, today: date | None = None) -> float:
    parsed = parse_date(sale_date)
    if not parsed:
        return 0.0
    today = today or date.today()
    return max(0.0, (today - parsed).days / 365.25)


def clean_owner_name(owner_name: str) -> str:
    return re.sub(r"\s+", " ", str(owner_name or "").strip())


def is_entity_owner(owner_name: str) -> bool:
    owner = f" {clean_owner_name(owner_name).upper()} "
    return any(term in owner for term in ENTITY_TERMS)


def is_national_creditor(owner_name: str) -> bool:
    owner = clean_owner_name(owner_name).upper()
    return any(term in owner for term in NATIONAL_CREDITOR_TERMS)


def is_estate_owner(owner_name: str) -> bool:
    owner = clean_owner_name(owner_name).upper()
    return "ESTATE OF" in owner or owner.startswith("ESTATE ")


def is_individual_owner(owner_name: str) -> bool:
    owner = clean_owner_name(owner_name)
    if not owner:
        return False
    return not is_entity_owner(owner)


def tier_for_amount(amount: Any) -> str:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value >= 100000:
        return "FIRE"
    if value >= 50000:
        return "HOT"
    if value >= 20000:
        return "WARM"
    return "LOW"


def claim_deadline(sale_date: Any, years: int = 5) -> date | None:
    parsed = parse_date(sale_date)
    if not parsed:
        return None
    try:
        return parsed.replace(year=parsed.year + years)
    except ValueError:
        return parsed + timedelta(days=years * 365)


def days_to_claim(sale_date: Any, today: date | None = None) -> int | None:
    deadline = claim_deadline(sale_date)
    if not deadline:
        return None
    today = today or date.today()
    return (deadline - today).days


def claim_status(days: int | None) -> str:
    if days is None:
        return "Review deadline"
    if days < 0:
        return "Deadline review"
    if days <= 180:
        return "Urgent"
    if days <= 365:
        return "Under 1 year"
    return "Open"


def score_lead(lead: dict[str, Any]) -> dict[str, Any]:
    amount = float(lead.get("surplus_amount") or 0)
    owner_name = clean_owner_name(lead.get("owner_name", ""))
    score = 0
    reasons: list[str] = []

    if amount >= 100000:
        score += 40
        reasons.append("FIRE surplus $100k+")
    elif amount >= 50000:
        score += 30
        reasons.append("HOT surplus $50k+")
    elif amount >= 20000:
        score += 20
        reasons.append("WARM surplus $20k+")
    else:
        score += 5
        reasons.append("LOW surplus under $20k")

    age = years_unclaimed(lead.get("sale_date"))
    if age > 2:
        score += 20
        reasons.append("Older than 2 years unclaimed")
    elif age > 1:
        score += 10
        reasons.append("Older than 1 year unclaimed")

    individual = is_individual_owner(owner_name)
    entity = is_entity_owner(owner_name)
    national_creditor = is_national_creditor(owner_name)

    if individual:
        score += 20
        reasons.append("Individual owner")
    if is_estate_owner(owner_name):
        score += 15
        reasons.append("Estate/heir lead")
    if age > 2 and individual:
        score += 15
        reasons.append("2+ years unclaimed + individual")
    zip_code = str(lead.get("zip") or "").strip()[:5]
    if zip_code in LOWER_INCOME_ZIPS:
        score += 10
        reasons.append("Priority ZIP")
    if national_creditor:
        score -= 20
        reasons.append("National creditor")
    elif entity:
        score -= 5
        reasons.append("Entity owner")

    claim_days = days_to_claim(lead.get("sale_date"))
    deadline = claim_deadline(lead.get("sale_date"))

    lead["owner_name"] = owner_name
    lead["tier"] = tier_for_amount(amount)
    lead["score"] = max(0, min(100, score))
    lead["score_reasons"] = reasons
    lead["years_unclaimed"] = round(age, 2)
    lead["your_cut_30pct"] = round(amount * 0.30, 2)
    lead["is_entity_owner"] = entity
    lead["is_individual_owner"] = individual
    lead["is_estate_owner"] = is_estate_owner(owner_name)
    lead["is_national_creditor"] = national_creditor
    lead["attorney_required"] = True
    lead["claim_deadline"] = deadline.isoformat() if deadline else ""
    lead["days_to_claim"] = claim_days
    lead["claim_status"] = claim_status(claim_days)
    return lead


def score_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = [score_lead(dict(lead)) for lead in leads]
    return sorted(scored, key=lambda item: (item.get("score", 0), item.get("surplus_amount", 0)), reverse=True)
