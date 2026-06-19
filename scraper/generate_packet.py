from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEADS_PATH = ROOT / "data" / "surplus_leads.json"
DEFAULT_OUTPUT_DIR = ROOT / "packets"
INDEX_PATH = ROOT / "index.html"
EMBED_START = '<script type="application/json" id="embedded-data">'
EMBED_END = "</script>"

NAVY = colors.HexColor("#1a1f2e")
GREEN = colors.HexColor("#1D9E75")
AMBER = colors.HexColor("#d97706")
LIGHT_GRAY = colors.HexColor("#f3f4f6")
MID_GRAY = colors.HexColor("#d1d5db")
BODY = colors.HexColor("#111827")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_label() -> str:
    return datetime.now(timezone.utc).strftime("%b %d, %Y")


def money(value: Any) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def short_money(value: Any) -> str:
    try:
        return f"${float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "$0"


def clean_text(value: Any) -> str:
    text = str(value or "").replace("&", "and")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def lead_id(lead: dict[str, Any]) -> str:
    owner = clean_text(lead.get("owner_name") or "unknown")
    amount = str(int(float(lead.get("surplus_amount") or 0)))
    parcel = clean_text(lead.get("parcel_id") or "")
    county = clean_text(lead.get("county_name") or lead.get("county") or "")
    slug = re.sub(r"[^a-z0-9]+", "_", owner.lower()).strip("_")
    suffix = re.sub(r"[^a-z0-9]+", "_", f"{county}_{parcel}".lower()).strip("_")
    return f"{slug}_{amount}_{suffix}"[:150].strip("_")


def is_estate(lead: dict[str, Any]) -> bool:
    if lead.get("is_estate_owner") or lead.get("is_estate"):
        return True
    return bool(re.search(r"\b(ESTATE OF|EST OF|EST PERS REP|HEIRS)\b", str(lead.get("owner_name") or ""), flags=re.I))


def owner_type(lead: dict[str, Any]) -> str:
    if is_estate(lead):
        return "Estate-Heirs"
    if lead.get("is_entity_owner"):
        return "Entity / LLC-Corp"
    return "Individual"


def address_label(lead: dict[str, Any]) -> str:
    pieces = [lead.get("property_address"), lead.get("city"), lead.get("zip")]
    text = ", ".join(clean_text(piece) for piece in pieces if piece)
    return text or "See parcel lookup"


def county_short(lead: dict[str, Any]) -> str:
    return clean_text(lead.get("county_name") or lead.get("county") or "Georgia").replace(" GA", "")


def gsccca_url(lead: dict[str, Any]) -> str:
    docs = lead.get("gsccca_docs") if isinstance(lead.get("gsccca_docs"), dict) else {}
    if docs.get("manual_search_url") or docs.get("gsccca_search_url"):
        return str(docs.get("manual_search_url") or docs.get("gsccca_search_url"))
    query = quote_plus(" ".join(str(x or "") for x in (lead.get("parcel_id"), lead.get("owner_name"))).strip())
    county = quote_plus(county_short(lead))
    return f"https://search.gsccca.org/RealEstateIndex/NameSearch.aspx?searchTerm={query}&county={county}"


def qpublic_url(lead: dict[str, Any]) -> str:
    details = lead.get("property_details") if isinstance(lead.get("property_details"), dict) else {}
    if details.get("manual_search_url") or details.get("qpublic_url"):
        return str(details.get("manual_search_url") or details.get("qpublic_url"))
    parcel = quote_plus(str(lead.get("parcel_id") or ""))
    return f"https://qpublic.schneidercorp.com/Application.aspx?AppID=1070&LayerID=22624&PageTypeID=4&KeyValue={parcel}"


def clerk_url(lead: dict[str, Any]) -> str:
    county = county_short(lead).lower()
    if "clayton" in county:
        return "https://www.claytoncountyga.gov/government/courts/clerk-of-superior-court/"
    if "dekalb" in county:
        return "https://dksuperiorclerk.com/"
    query = quote_plus(f"{county_short(lead)} Georgia clerk superior court records")
    return f"https://www.google.com/search?q={query}"


def probate_url(lead: dict[str, Any]) -> str:
    owner = str(lead.get("owner_name") or "")
    cleaned = re.sub(r"\b(ESTATE OF|EST OF|ESTATE|EST|PERS|REP|HEIRS|OF|THE)\b", " ", owner, flags=re.I)
    parts = [part.strip(" ,.&") for part in cleaned.split() if part.strip(" ,.&")]
    last = parts[-1] if parts else owner
    return f"https://georgiaprobaterecords.com/?county={quote_plus(county_short(lead).lower())}&search={quote_plus(last)}"


def checklist_mark(done: bool) -> str:
    return "✓" if done else "○"


def doc_statuses(lead: dict[str, Any]) -> list[tuple[bool, str, str]]:
    docs = lead.get("gsccca_docs") if isinstance(lead.get("gsccca_docs"), dict) else {}
    enrichment = lead.get("enrichment") if isinstance(lead.get("enrichment"), dict) else {}
    tax_found = bool(docs.get("tax_sale_deed_found"))
    open_lien = bool(docs.get("open_lien_found"))
    assignment = bool(docs.get("assignment_filed") or enrichment.get("assignment_filed"))
    rows = [
        (tax_found, "Tax Sale Deed", docs.get("tax_sale_date") or ("Found" if tax_found else "Not yet verified - see verification links")),
        (not open_lien if docs.get("status") == "checked_clean" else False, "Open Liens", "Open lien found - verify amount" if open_lien else "Not yet verified"),
        (not assignment if docs.get("status") == "checked_clean" else False, "Assignment of Excess Funds Filed", "Assignment found - do not work until verified" if assignment else "Not yet verified - recommend checking before filing"),
    ]
    if is_estate(lead):
        probate = bool(enrichment.get("probate_found"))
        note = enrichment.get("probate_note") or ("Probate record found" if probate else "Not yet verified - required before claim can be filed")
        rows.append((probate, "Probate Status", note))
    return rows


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY, alignment=1),
        "brand": ParagraphStyle("Brand", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=NAVY),
        "right": ParagraphStyle("Right", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, textColor=BODY, alignment=2),
        "section": ParagraphStyle("Section", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=NAVY, spaceBefore=7, spaceAfter=4),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.2, leading=10.5, textColor=BODY),
        "small": ParagraphStyle("Small", parent=base["BodyText"], fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#374151")),
        "footer": ParagraphStyle("Footer", parent=base["BodyText"], fontName="Helvetica", fontSize=6.5, leading=8, textColor=colors.HexColor("#4b5563")),
    }


def p(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(clean_text(text), style)


def summary_table(lead: dict[str, Any], generated_at: str, s: dict[str, ParagraphStyle]) -> Table:
    rows = [
        ["Owner / Estate Name", clean_text(lead.get("owner_name") or "Unknown")],
        ["Property Address", address_label(lead)],
        ["County", f"{county_short(lead)}, Georgia"],
        ["Parcel ID", clean_text(lead.get("parcel_id") or "Not listed")],
        ["Surplus Amount", short_money(lead.get("surplus_amount"))],
        ["Sale Date", clean_text(lead.get("sale_date") or "Not listed")],
        ["Years Unclaimed", clean_text(lead.get("years_unclaimed") or "Unknown")],
        ["Claim Deadline", clean_text(lead.get("claim_deadline") or "Verify with county")],
        ["Days Remaining", clean_text(lead.get("days_to_claim") if lead.get("days_to_claim") is not None else "Verify with county")],
        ["Owner Type", owner_type(lead)],
        ["Source", f"{county_short(lead)} County Excess Funds List, scraper updated {generated_at[:10] if generated_at else today_label()}"],
    ]
    table = Table([[p(a, s["small"]), p(b, s["small"])] for a, b in rows], colWidths=[1.85 * inch, 4.75 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
        ("BOX", (0, 0), (-1, -1), 0.5, MID_GRAY),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.white),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def simple_table(rows: list[list[Any]], widths: list[float], s: dict[str, ParagraphStyle]) -> Table:
    table = Table([[p(cell, s["small"]) for cell in row] for row in rows], colWidths=widths)
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.4, MID_GRAY),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, MID_GRAY),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GRAY),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def build_story(lead: dict[str, Any]) -> list[Any]:
    s = styles()
    generated_at = str(lead.get("packet_generated_at") or now_iso())
    story: list[Any] = []
    header = Table(
        [[p("229 HOLDINGS LLC", s["brand"]), p(f"Generated {today_label()}", s["right"])]],
        colWidths=[4.5 * inch, 2.0 * inch],
    )
    story.append(header)
    story.append(p("SURPLUS FUNDS CLAIM — ATTORNEY INTAKE PACKET", s["title"]))
    story.append(Spacer(1, 0.08 * inch))
    story.append(summary_table(lead, str(lead.get("generated_at") or generated_at), s))

    story.append(p("CLAIM SUMMARY", s["section"]))
    story.append(p("This packet summarizes public-record surplus funds data for attorney review and filing workflow planning.", s["body"]))

    status_rows = [["", "Item", "Status"]]
    for done, label, note in doc_statuses(lead):
        status_rows.append([checklist_mark(done), label, note])
    story.append(p("VERIFICATION STATUS", s["section"]))
    story.append(simple_table(status_rows, [0.25 * inch, 2.1 * inch, 4.15 * inch], s))

    details = lead.get("property_details") if isinstance(lead.get("property_details"), dict) else {}
    property_rows = [["Field", "Value"]]
    if details and not details.get("manual_check_required"):
        property_rows.extend([
            ["Assessed Value", money(details.get("assessed_value")) if details.get("assessed_value") else "Not listed"],
            ["Year Built", details.get("year_built") or "Not listed"],
            ["Square Footage", details.get("sq_footage") or "Not listed"],
            ["Last Sale Price", money(details.get("last_sale_price")) if details.get("last_sale_price") else "Not listed"],
        ])
    else:
        property_rows.append(["Property Details", "Property details not yet retrieved. See verification links below."])
    story.append(p("PROPERTY DETAILS", s["section"]))
    story.append(simple_table(property_rows, [1.8 * inch, 4.7 * inch], s))

    surplus = float(lead.get("surplus_amount") or 0)
    lien_amount = 0.0
    docs = lead.get("gsccca_docs") if isinstance(lead.get("gsccca_docs"), dict) else {}
    if isinstance(docs.get("open_lien_amount"), (int, float)):
        lien_amount = float(docs.get("open_lien_amount") or 0)
    net = max(0.0, surplus - lien_amount)
    story.append(p("FINANCIAL BREAKDOWN", s["section"]))
    story.append(simple_table([
        ["Gross Surplus", money(surplus)],
        ["Estimated Liens", money(lien_amount) if lien_amount else "Unknown / not yet verified"],
        ["Net Surplus Estimate", money(net)],
        ["Recovery Fee Structure", "30% contingency, no upfront cost to claimant"],
        ["Attorney Filing Requirement", "Georgia surplus claims must be filed by claimant or licensed GA attorney; finder cannot file directly"],
    ], [2.0 * inch, 4.5 * inch], s))

    story.append(p("CLIENT CONTACT STATUS", s["section"]))
    story.append(simple_table([
        ["Status", clean_text(lead.get("pipeline_status") or "")],
        ["Contract Signed", "Yes" if lead.get("contract_signed") else "No"],
        ["Assigned Attorney", clean_text(lead.get("assigned_attorney") or "")],
    ], [2.0 * inch, 4.5 * inch], s))

    links = [
        f"GA Superior Court Records (GSCCCA): {gsccca_url(lead)}",
        f"Property Records (qPublic): {qpublic_url(lead)}",
        f"{county_short(lead)} Clerk of Superior Court: {clerk_url(lead)}",
    ]
    if is_estate(lead):
        links.append(f"Georgia Probate Records: {probate_url(lead)}")
    story.append(p("VERIFICATION LINKS", s["section"]))
    story.append(p("For independent verification, the following public records searches are available:", s["small"]))
    for link in links:
        story.append(p(link, s["footer"]))

    story.append(Spacer(1, 0.08 * inch))
    story.append(p(
        f"Prepared by 229 Holdings LLC — {today_label()}<br/>"
        "This packet is for informational purposes based on public county records. Recipient should independently verify all figures before filing. "
        "229 Holdings LLC is not a law firm and does not provide legal advice.",
        s["footer"],
    ))
    return story


def generate_packet(lead: dict[str, Any], output_path: str) -> bool:
    """
    Generates a single-lead PDF packet.
    lead: full lead dict from surplus_leads.json including enrichment, gsccca_docs, property_details.
    output_path: where to save, e.g. packets/{sanitized_lead_id}.pdf
    Returns True on success, False on failure.
    """
    try:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(output),
            pagesize=letter,
            rightMargin=0.45 * inch,
            leftMargin=0.45 * inch,
            topMargin=0.45 * inch,
            bottomMargin=0.45 * inch,
            title=f"Attorney Intake Packet - {clean_text(lead.get('owner_name'))}",
        )
        doc.build(build_story(lead))
        return True
    except Exception as error:
        print(f"Packet generation failed for {lead.get('owner_name')}: {error}")
        traceback.print_exc()
        return False


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


def generate_all_packets(leads_json_path: str, output_dir: str, min_score: int = 50) -> dict[str, int]:
    """
    Batch generates packets for all eligible leads.
    Adds packet_path to each eligible lead and saves the JSON back to disk.
    """
    path = Path(leads_json_path)
    output_root = Path(output_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    leads = payload.get("leads") if isinstance(payload.get("leads"), list) else []
    summary = {"generated": 0, "failed": 0, "skipped": 0}
    generated_at = now_iso()
    for lead in leads:
        try:
            score = int(float(lead.get("score") or 0))
        except (TypeError, ValueError):
            score = 0
        if score < min_score:
            summary["skipped"] += 1
            continue
        packet_name = f"{lead_id(lead)}.pdf"
        output_path = output_root / packet_name
        lead["packet_path"] = f"packets/{packet_name}"
        lead["packet_generated_at"] = generated_at
        if generate_packet(lead, str(output_path)):
            summary["generated"] += 1
        else:
            lead.pop("packet_path", None)
            lead.pop("packet_generated_at", None)
            summary["failed"] += 1
    payload["packet_meta"] = {
        "generated_at": generated_at,
        "min_score": min_score,
        **summary,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if path.resolve() == DEFAULT_LEADS_PATH.resolve():
        update_embedded_dashboard_data(payload)
    return summary


def main() -> None:
    summary = generate_all_packets(str(DEFAULT_LEADS_PATH), str(DEFAULT_OUTPUT_DIR), min_score=50)
    print(f"Packet generation complete: {summary['generated']} generated | {summary['failed']} failed | {summary['skipped']} skipped")


if __name__ == "__main__":
    main()
