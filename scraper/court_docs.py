from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "surplus_leads.json"
INDEX_PATH = ROOT / "index.html"
EMBED_START = '<script type="application/json" id="embedded-data">'
EMBED_END = "</script>"
REQUEST_DELAY_SECONDS = 3
REQUEST_TIMEOUT = 15


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_payload() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"leads": []}
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def write_payload(payload: dict[str, Any]) -> None:
    DATA_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    update_embedded_dashboard_data(payload)


def update_embedded_dashboard_data(payload: dict[str, Any]) -> None:
    if not INDEX_PATH.exists():
        return
    text = INDEX_PATH.read_text(encoding="utf-8")
    start = text.find(EMBED_START)
    if start == -1:
        return
    start_content = start + len(EMBED_START)
    end = text.find(EMBED_END, start_content)
    if end == -1:
        return
    embedded = json.dumps(payload, separators=(",", ":"))
    INDEX_PATH.write_text(text[:start_content] + embedded + text[end:], encoding="utf-8")


class CourtSession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.last_request = 0.0
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 surplus-intel/1.0 (+https://github.com/208-ndo/-surplus-intel)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        elapsed = time.monotonic() - self.last_request
        if self.last_request and elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        response = self.session.get(url, **kwargs)
        self.last_request = time.monotonic()
        response.raise_for_status()
        return response


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_money(value: str) -> float | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", value or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def qpublic_url(parcel_id: str, county: str) -> str:
    app_id = "1070"
    layer_id = "22624"
    return (
        "https://qpublic.schneidercorp.com/Application.aspx?"
        f"AppID={app_id}&LayerID={layer_id}&PageTypeID=4&KeyValue={quote_plus(parcel_id)}"
    )


def extract_field(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*:?\s+(.+?)(?=\s+[A-Z][A-Za-z /#()]+:|\s{{2,}}|$)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return ""


def fetch_property_details(parcel_id: str, county: str, session: CourtSession | None = None) -> dict[str, Any]:
    session = session or CourtSession()
    parcel = str(parcel_id or "").strip()
    if not parcel:
        return {}

    url = qpublic_url(parcel, county)
    try:
        response = session.get(url)
        text = clean_text(response.text)
        details: dict[str, Any] = {
            "property_address": extract_field(text, ["Property Address", "Situs Address", "Location Address", "Address"]),
            "owner_name_on_record": extract_field(text, ["Owner", "Owner Name", "Current Owner"]),
            "year_built": extract_field(text, ["Year Built", "Actual Year Built"]),
            "sq_footage": extract_field(text, ["Living Area", "Total Area", "Square Feet", "Sq Ft"]),
            "bedrooms": extract_field(text, ["Bedrooms", "Beds"]),
            "bathrooms": extract_field(text, ["Bathrooms", "Baths"]),
            "neighborhood": extract_field(text, ["Neighborhood", "Subdivision", "Subdivision Name"]),
            "last_sale_date": extract_field(text, ["Last Sale Date", "Sale Date"]),
            "source": "qpublic",
            "qpublic_url": url,
            "last_checked": now_iso(),
        }
        assessed = extract_field(text, ["Assessed Value", "Total Value", "Fair Market Value"])
        sale_price = extract_field(text, ["Last Sale Price", "Sale Price", "Deed Amount"])
        details["assessed_value"] = parse_money(assessed)
        details["last_sale_price"] = parse_money(sale_price)
        return {key: value for key, value in details.items() if value not in ("", None)}
    except Exception as exc:
        print(f"Warning: qPublic property details failed for {parcel}: {exc}")

    try:
        fallback_url = f"https://www.propertyshark.com/mason/api/?parcel={quote_plus(parcel)}&county={quote_plus(county)}"
        response = session.get(fallback_url)
        text = clean_text(response.text)
        return {
            "source": "propertyshark",
            "property_address": extract_field(text, ["Address", "Property Address"]),
            "owner_name_on_record": extract_field(text, ["Owner", "Owner Name"]),
            "last_checked": now_iso(),
        }
    except Exception as exc:
        print(f"Warning: property details fallback failed for {parcel}: {exc}")
        return {}


def gsccca_search_url(parcel_id: str, owner_name: str, county: str) -> str:
    query = quote_plus(f"{parcel_id} {owner_name}".strip())
    county_value = quote_plus(str(county or "").replace(" GA", ""))
    return f"https://search.gsccca.org/RealEstateIndex/default.aspx?searchTerm={query}&county={county_value}"


def classify_doc(doc: dict[str, str]) -> str:
    doc_type = doc.get("doc_type", "").upper()
    if "ASSIGNMENT OF EXCESS" in doc_type or "ASSIGNMENT OF SURPLUS" in doc_type:
        return "assignment"
    if "DEED OF REDEMPTION" in doc_type or "REDEMPTION" in doc_type:
        return "redemption"
    if "TAX DEED" in doc_type or "TAX SALE" in doc_type:
        return "tax_sale"
    if "SATISFACTION" in doc_type or "CANCELLATION" in doc_type or "CANCEL" in doc_type:
        return "satisfaction"
    if "SECURITY DEED" in doc_type or "DEED TO SECURE DEBT" in doc_type:
        return "security_deed"
    if "QCD" in doc_type or "QUITCLAIM" in doc_type or "QUIT CLAIM" in doc_type:
        return "quitclaim"
    return "other"


def parse_gsccca_rows(raw_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw_html, flags=re.IGNORECASE | re.DOTALL):
        cells = [clean_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        joined = " ".join(cells).upper()
        if "DOCUMENT" in joined and "TYPE" in joined:
            continue
        doc = {
            "doc_type": cells[0] if cells else "",
            "recorded_date": cells[1] if len(cells) > 1 else "",
            "book": cells[2] if len(cells) > 2 else "",
            "page": cells[3] if len(cells) > 3 else "",
            "grantor": cells[4] if len(cells) > 4 else "",
            "grantee": cells[5] if len(cells) > 5 else "",
        }
        if any(doc.values()):
            rows.append(doc)
    return rows[:50]


def scan_gsccca_documents(parcel_id: str, owner_name: str, county: str, session: CourtSession | None = None) -> dict[str, Any]:
    session = session or CourtSession()
    url = gsccca_search_url(parcel_id, owner_name, county)
    result: dict[str, Any] = {
        "tax_sale_deed_found": False,
        "tax_sale_date": "",
        "open_lien_found": False,
        "open_lien_holder": "",
        "open_lien_amount": "",
        "assignment_filed": False,
        "redemption_filed": False,
        "lien_satisfied": False,
        "quitclaim_after_sale": False,
        "all_docs": [],
        "doc_count": 0,
        "last_checked": now_iso(),
        "gsccca_search_url": url,
    }
    try:
        response = session.get(url)
        docs = parse_gsccca_rows(response.text)
    except Exception as exc:
        print(f"Warning: GSCCCA document scan failed for {parcel_id}: {exc}")
        return result

    security_deeds: list[dict[str, str]] = []
    satisfactions = 0
    tax_sale_date = ""
    for doc in docs:
        doc_class = classify_doc(doc)
        doc["classification"] = doc_class
        if doc_class == "tax_sale":
            result["tax_sale_deed_found"] = True
            tax_sale_date = doc.get("recorded_date", tax_sale_date)
        elif doc_class == "security_deed":
            security_deeds.append(doc)
        elif doc_class == "satisfaction":
            result["lien_satisfied"] = True
            satisfactions += 1
        elif doc_class == "assignment":
            result["assignment_filed"] = True
        elif doc_class == "redemption":
            result["redemption_filed"] = True
        elif doc_class == "quitclaim":
            result["quitclaim_after_sale"] = True

    if security_deeds and satisfactions < len(security_deeds):
        holder = security_deeds[-1].get("grantee") or security_deeds[-1].get("grantor") or "Unknown lien holder"
        result["open_lien_found"] = True
        result["open_lien_holder"] = holder
        result["open_lien_amount"] = "Unknown - verify with county"

    result["tax_sale_date"] = tax_sale_date
    result["all_docs"] = docs
    result["doc_count"] = len(docs)
    return result


def tag_lead(lead: dict[str, Any], tag: str) -> None:
    tags = lead.setdefault("tags", [])
    if not isinstance(tags, list):
        tags = []
        lead["tags"] = tags
    if tag not in tags:
        tags.append(tag)


def adjust_score_and_tags(lead: dict[str, Any], docs: dict[str, Any]) -> None:
    score = int(lead.get("score") or 0)
    if docs.get("assignment_filed"):
        score = 0
        tag_lead(lead, "Skip - Assignment Filed")
    if docs.get("redemption_filed"):
        score = max(0, score - 40)
        tag_lead(lead, "Verify - Redemption Found")
    if docs.get("open_lien_found"):
        tag_lead(lead, "Has Open Lien - Check Amount")
    if docs.get("tax_sale_deed_found"):
        score = min(100, score + 5)
        tag_lead(lead, "Tax Sale Deed Found")
    if not docs.get("assignment_filed") and not docs.get("redemption_filed") and not docs.get("open_lien_found"):
        tag_lead(lead, "Clean Title Check")
    lead["score"] = score


def checklist_status(status: str, source: str, note: str) -> dict[str, str]:
    return {"status": status, "source": source, "note": note}


def build_document_checklist(lead: dict[str, Any]) -> dict[str, Any]:
    docs = lead.get("gsccca_docs") if isinstance(lead.get("gsccca_docs"), dict) else {}
    details = lead.get("property_details") if isinstance(lead.get("property_details"), dict) else {}
    tax_note = "Tax sale deed not found in automated scan"
    for doc in docs.get("all_docs") or []:
        if doc.get("classification") == "tax_sale":
            tax_note = f"Recorded {doc.get('recorded_date') or 'unknown date'} Book {doc.get('book') or '-'} Page {doc.get('page') or '-'}"
            break
    lien_note = "Lien status unknown - verify with county"
    lien_status = "unknown"
    if docs.get("open_lien_found"):
        lien_status = "has_liens"
        lien_note = f"Open lien: {docs.get('open_lien_holder') or 'Unknown'} - verify amount with county"
    elif docs.get("doc_count", 0) > 0:
        lien_status = "clear"
        lien_note = "No open security deeds found in automated scan"

    detail_note = "Property details not pulled"
    if details:
        address = details.get("property_address") or lead.get("property_address") or "address found"
        value = details.get("assessed_value")
        value_note = f" assessed at ${value:,.0f}" if isinstance(value, (int, float)) else ""
        detail_note = f"{address}{value_note}"

    return {
        "tax_sale_deed": checklist_status("found" if docs.get("tax_sale_deed_found") else "unknown", "gsccca", tax_note),
        "excess_funds_confirmation": checklist_status("found", "county_pdf", f"On county excess funds list as of {lead.get('first_seen_date') or now_iso()[:10]}"),
        "lien_check": checklist_status(lien_status, "gsccca", lien_note),
        "property_details": checklist_status("found" if details else "not_found", details.get("source", "qpublic") if details else "qpublic", detail_note),
        "owner_id": checklist_status("needed", "owner", "Request from owner after contract signed"),
        "signed_contract": checklist_status("needed", "docusign", "Send via DocuSign after verbal agreement"),
        "attorney_petition": checklist_status("needed", "attorney", "Attorney files via eFileGA after all docs collected"),
    }


def eligible(lead: dict[str, Any]) -> bool:
    return int(lead.get("score") or 0) >= 50 and bool(str(lead.get("parcel_id") or "").strip())


def run() -> dict[str, int]:
    payload = load_payload()
    leads = payload.get("leads") or []
    session = CourtSession()
    stats = {"processed": 0, "clean": 0, "liens": 0, "assignments": 0, "property_details": 0, "flagged": 0}
    for index, lead in enumerate(leads, start=1):
        if not eligible(lead):
            continue
        parcel = str(lead.get("parcel_id") or "").strip()
        county = str(lead.get("county_name") or lead.get("county") or "").strip()
        owner = str(lead.get("owner_name") or "").strip()
        print(f"Court docs {index}/{len(leads)}: {owner}")
        stats["processed"] += 1

        details = fetch_property_details(parcel, county, session=session)
        lead["property_details"] = details
        if details:
            stats["property_details"] += 1
            if details.get("property_address") and (not lead.get("property_address") or lead.get("needs_address_lookup")):
                lead["property_address"] = details["property_address"]
                lead["needs_address_lookup"] = False

        docs = scan_gsccca_documents(parcel, owner, county, session=session)
        lead["gsccca_docs"] = docs
        adjust_score_and_tags(lead, docs)
        lead["document_checklist"] = build_document_checklist(lead)

        if docs.get("assignment_filed"):
            stats["assignments"] += 1
        if docs.get("open_lien_found"):
            stats["liens"] += 1
        if docs.get("assignment_filed") or docs.get("redemption_filed") or docs.get("open_lien_found"):
            stats["flagged"] += 1
        else:
            stats["clean"] += 1

    payload["court_docs_meta"] = {
        "last_checked": now_iso(),
        "processed_leads": stats["processed"],
        "clean_leads": stats["clean"],
        "flagged_leads": stats["flagged"],
        "open_lien_leads": stats["liens"],
        "assignment_leads": stats["assignments"],
        "property_details_pulled": stats["property_details"],
    }
    write_payload(payload)
    print(
        "Court docs complete: "
        f"{stats['processed']} processed | {stats['clean']} clean | {stats['liens']} with liens | "
        f"{stats['assignments']} assignments | {stats['property_details']} property details pulled"
    )
    return stats


if __name__ == "__main__":
    run()
