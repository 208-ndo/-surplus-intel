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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "collier_leads.json"
OUTPUT_DIR = ROOT / "packets" / "collier"

NAVY = colors.HexColor("#1a1f2e")
GREEN = colors.HexColor("#1D9E75")
AMBER = colors.HexColor("#d97706")
LIGHT_GRAY = colors.HexColor("#f3f4f6")
MID_GRAY = colors.HexColor("#d1d5db")
BODY = colors.HexColor("#111827")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("&", "and").strip()


def money(value: Any) -> str:
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def lead_id(lead: dict[str, Any]) -> str:
    owner = re.sub(r"[^a-z0-9]+", "_", clean(lead.get("owner_name")).lower()).strip("_") or "lead"
    amount = str(int(float(lead.get("surplus_amount") or 0)))
    parcel = re.sub(r"[^a-z0-9]+", "_", clean(lead.get("parcel_id")).lower()).strip("_")
    return f"{owner}_{amount}_{parcel}"[:140].strip("_")


def p(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(clean(text), style)


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=NAVY, alignment=1),
        "brand": ParagraphStyle("Brand", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=NAVY),
        "right": ParagraphStyle("Right", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, textColor=BODY, alignment=2),
        "section": ParagraphStyle("Section", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=NAVY, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.4, leading=10.5, textColor=BODY),
        "small": ParagraphStyle("Small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.2, leading=9, textColor=colors.HexColor("#374151")),
        "footer": ParagraphStyle("Footer", parent=base["BodyText"], fontName="Helvetica", fontSize=6.5, leading=8, textColor=colors.HexColor("#4b5563")),
    }


def simple_table(rows: list[list[Any]], widths: list[float], style: ParagraphStyle) -> Table:
    table = Table([[p(cell, style) for cell in row] for row in rows], colWidths=widths)
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


def packet_links(lead: dict[str, Any]) -> list[str]:
    parcel = quote_plus(str(lead.get("parcel_id") or ""))
    owner = quote_plus(str(lead.get("owner_name") or ""))
    return [
        f"Collier Official Records: https://cor.collierclerk.com/coraccess/Search/NameSearch.aspx?SearchName={owner}",
        f"Collier Property Appraiser: https://www.collierappraiser.com/main_search/RecordDetail.html?folio={parcel}",
        "Collier Tax Deed Surplus: https://www.collierclerk.com/tax-deed-sales/tax-deed-surplus/",
        f"Collier Probate: https://www.collierclerk.com/court-divisions/probate/?search={owner}",
    ]


def doc_rows(lead: dict[str, Any]) -> list[list[str]]:
    estate = bool(lead.get("is_estate_owner") or lead.get("is_estate"))
    rows = [
        ["Status", "Document", "Note"],
        ["Needed", "Notarized Claim Affidavit", "Required for Collier tax deed surplus claim."],
        ["Needed", "Government Photo ID", "Valid claimant ID."],
        ["Needed", "IRS W-9", "Required for payment processing."],
        ["Needed", "Power of Attorney", "Needed if claimant is represented by another person."],
    ]
    if estate:
        rows.extend([
            ["Needed", "Death Certificate", "Required if titleholder is deceased."],
            ["Needed", "Probate / Heirship Documents", "Verify legal authority before filing claim."],
        ])
    rows.append(["Reference", "F.S. 197.582", "Florida tax deed surplus distribution statute."])
    return rows


def build_story(lead: dict[str, Any]) -> list[Any]:
    s = styles()
    generated = datetime.now(timezone.utc).strftime("%b %d, %Y")
    amount = float(lead.get("surplus_amount") or 0)
    liens = float(lead.get("open_liens_total") or 0)
    net = max(amount - liens, 0)
    story: list[Any] = [
        Table(
            [[p("229 HOLDINGS LLC", s["brand"]), p(f"Generated {generated}", s["right"])]],
            colWidths=[4.4 * inch, 2.2 * inch],
            style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, NAVY), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]),
        ),
        Spacer(1, 8),
        p("SURPLUS FUNDS CLAIM - FLORIDA ATTORNEY INTAKE PACKET", s["title"]),
        Spacer(1, 8),
        p("CLAIM SUMMARY", s["section"]),
        simple_table(
            [
                ["Owner / Estate Name", lead.get("owner_name") or "Unknown"],
                ["Property Address", ", ".join(x for x in [lead.get("property_address"), lead.get("city"), lead.get("zip")] if x) or "See Collier parcel lookup"],
                ["County", "Collier, Florida"],
                ["Parcel ID", lead.get("parcel_id") or "Not listed"],
                ["Surplus Amount", money(amount)],
                ["Sale Date", lead.get("sale_date") or "Not listed"],
                ["Notice / Deadline Basis", lead.get("notice_date") or "Estimated from sale date"],
                ["Claim Deadline", lead.get("claim_deadline") or "Verify with Clerk"],
                ["Days Remaining", lead.get("days_to_claim") if lead.get("days_to_claim") is not None else "Verify with Clerk"],
                ["Fee Tier", lead.get("fee_tier") or "Verify"],
                ["Source", "Collier County Clerk Tax Deed Sales Excess Proceeds List"],
            ],
            [1.9 * inch, 4.7 * inch],
            s["small"],
        ),
        p("DOCUMENT CHECKLIST", s["section"]),
        simple_table(doc_rows(lead), [0.9 * inch, 2.1 * inch, 3.6 * inch], s["small"]),
        p("FINANCIAL BREAKDOWN", s["section"]),
        simple_table(
            [
                ["Gross Surplus", money(amount)],
                ["Estimated Liens", money(liens)],
                ["Net Surplus Estimate", money(net)],
                ["15% Fee Window", money(net * 0.15)],
                ["30% Negotiated Fee Reference", money(net * 0.30)],
                ["Legal Note", "Florida claims are governed by F.S. 197.582. Attorney filing is not required for the claim itself."],
            ],
            [2.25 * inch, 4.35 * inch],
            s["small"],
        ),
        p("VERIFICATION LINKS", s["section"]),
    ]
    for link in packet_links(lead):
        story.append(p(link, s["small"]))
    story.extend([
        Spacer(1, 8),
        p(
            "Prepared by 229 Holdings LLC. This packet is for informational purposes based on public county records. "
            "Recipient should independently verify all figures before filing. 229 Holdings LLC is not a law firm and does not provide legal advice.",
            s["footer"],
        ),
    ])
    return story


def generate_packet(lead: dict[str, Any], output_path: str) -> bool:
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
            title=f"Collier Attorney Intake Packet - {clean(lead.get('owner_name'))}",
        )
        doc.build(build_story(lead))
        return True
    except Exception as error:
        print(f"Collier packet generation failed for {lead.get('owner_name')}: {error}")
        traceback.print_exc()
        return False


def generate_all_packets(leads_json_path: str = str(DATA_PATH), output_dir: str = str(OUTPUT_DIR), min_score: int = 50) -> dict[str, int]:
    path = Path(leads_json_path)
    if not path.exists():
        print("data/collier_leads.json not found; skipping Collier packets.")
        return {"generated": 0, "failed": 0, "skipped": 0}
    payload = json.loads(path.read_text(encoding="utf-8"))
    leads = payload.get("leads") if isinstance(payload.get("leads"), list) else []
    output_root = Path(output_dir)
    generated_at = now_iso()
    summary = {"generated": 0, "failed": 0, "skipped": 0}
    for lead in leads:
        score = int(float(lead.get("score") or 0))
        if score < min_score:
            summary["skipped"] += 1
            continue
        packet_name = f"{lead_id(lead)}.pdf"
        lead["packet_path"] = f"packets/collier/{packet_name}"
        lead["packet_generated_at"] = generated_at
        if generate_packet(lead, str(output_root / packet_name)):
            summary["generated"] += 1
        else:
            lead.pop("packet_path", None)
            lead.pop("packet_generated_at", None)
            summary["failed"] += 1
    payload["packet_meta"] = {"generated_at": generated_at, "min_score": min_score, **summary}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    summary = generate_all_packets()
    print(f"Collier packet generation complete: {summary['generated']} generated | {summary['failed']} failed | {summary['skipped']} skipped")


if __name__ == "__main__":
    main()
