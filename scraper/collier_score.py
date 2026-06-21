from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

try:
    from .score import tier_for_amount
except ImportError:  # pragma: no cover
    from score import tier_for_amount


ENTITY_TERMS = (
    " LLC",
    " INC",
    " CORP",
    " CORPORATION",
    " COMPANY",
    " CO ",
    " BANK",
    " TRUST",
    " ASSOCIATION",
    " LP",
    " LLP",
    " LTD",
)


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def money_float(value: Any) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def is_estate_owner(owner_name: str) -> bool:
    return bool(re.search(r"\b(ESTATE OF|EST OF|EST\b|HEIRS?|C/O)\b", owner_name or "", re.I))


def is_entity_owner(owner_name: str) -> bool:
    upper = f" {str(owner_name or '').upper()} "
    return any(term in upper for term in ENTITY_TERMS)


def years_between(start: date | None, end: date | None = None) -> float:
    if not start:
        return 0.0
    current = end or date.today()
    return round(max((current - start).days, 0) / 365.25, 1)


def florida_urgency(days_remaining: int | None, sale_date: date | None, current: date | None = None) -> str:
    today = current or date.today()
    if days_remaining is not None and days_remaining < 0:
        return "EXPIRED"
    if days_remaining is not None and days_remaining < 20:
        return "CRITICAL"
    if days_remaining is not None and days_remaining <= 45:
        return "URGENT"
    if sale_date and today <= sale_date + timedelta(days=90):
        return "HOT"
    return "WARM"


def score_collier_lead(lead: dict[str, Any], current: date | None = None) -> dict[str, Any]:
    today = current or date.today()
    owner = str(lead.get("owner_name") or "")
    amount = money_float(lead.get("surplus_amount"))
    sale_dt = parse_date(lead.get("sale_date"))
    notice_dt = parse_date(lead.get("notice_date")) or sale_dt
    date_estimated = not bool(parse_date(lead.get("notice_date")))
    claim_deadline = notice_dt + timedelta(days=120) if notice_dt else None
    fee_tier_deadline = sale_dt + timedelta(days=90) if sale_dt else None
    days_remaining = (claim_deadline - today).days if claim_deadline else None
    urgency = florida_urgency(days_remaining, sale_dt, today)
    fee_tier = "HIGH_URGENCY_15PCT" if sale_dt and today <= sale_dt + timedelta(days=90) else "NEGOTIABLE"
    amount_tier = tier_for_amount(amount)

    score = 0
    if amount >= 100000:
        score += 40
    elif amount >= 50000:
        score += 30
    elif amount >= 20000:
        score += 20
    else:
        score += 5

    if urgency == "CRITICAL":
        score += 35
    elif urgency == "URGENT":
        score += 25
    elif urgency == "HOT":
        score += 15
    elif urgency == "EXPIRED":
        score -= 45

    if is_estate_owner(owner):
        score += 10
    elif is_entity_owner(owner):
        score -= 10
    else:
        score += 15

    if sale_dt and years_between(sale_dt, today) >= 1:
        score += 5

    score = max(0, min(100, int(score)))

    tags = list(dict.fromkeys([*(lead.get("tags") or [])]))
    tags.extend(["FL", "Collier", "No Attorney Required", f"Fee Tier: {fee_tier.replace('_', ' ')}"])
    if urgency in {"CRITICAL", "URGENT", "HOT", "WARM", "EXPIRED"}:
        tags.append(urgency)
    if amount_tier in {"FIRE", "HOT", "WARM"}:
        tags.append(amount_tier)
    if is_estate_owner(owner):
        tags.append("Estate / Heirs")
    elif is_entity_owner(owner):
        tags.append("LLC / Corp")
    else:
        tags.append("Individual")
    if amount >= 20000 and score >= 60 and urgency != "EXPIRED":
        tags.append("GHL Eligible")

    lead.update(
        {
            "state": "FL",
            "county_name": "Collier FL",
            "county": "Collier",
            "surplus_amount": amount,
            "your_cut_30pct": round(amount * 0.30, 2),
            "your_cut_15pct": round(amount * 0.15, 2),
            "tier": amount_tier,
            "score": score,
            "sale_date": iso(sale_dt),
            "notice_date": iso(notice_dt),
            "claim_deadline": iso(claim_deadline),
            "fee_tier_deadline": iso(fee_tier_deadline),
            "fee_tier": fee_tier,
            "fl_urgency": urgency,
            "claim_status": urgency,
            "days_to_claim": days_remaining,
            "date_estimated": date_estimated,
            "years_unclaimed": years_between(sale_dt, today),
            "attorney_required": False,
            "is_estate_owner": is_estate_owner(owner),
            "is_estate": is_estate_owner(owner),
            "is_entity_owner": is_entity_owner(owner),
            "is_individual_owner": not is_estate_owner(owner) and not is_entity_owner(owner),
            "is_expired": urgency == "EXPIRED",
            "tags": list(dict.fromkeys(tag for tag in tags if tag)),
            "score_reasons": build_score_reasons(amount_tier, urgency, fee_tier, owner, date_estimated),
        }
    )
    return lead


def build_score_reasons(tier: str, urgency: str, fee_tier: str, owner: str, date_estimated: bool) -> list[str]:
    reasons = [f"{tier} surplus tier", f"Florida urgency: {urgency}", f"Fee tier: {fee_tier}"]
    if is_estate_owner(owner):
        reasons.append("Estate/heir lead")
    elif is_entity_owner(owner):
        reasons.append("Entity owner penalty")
    else:
        reasons.append("Individual owner")
    if date_estimated:
        reasons.append("Notice date estimated from sale date")
    return reasons


def score_collier_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [score_collier_lead(lead) for lead in leads]
