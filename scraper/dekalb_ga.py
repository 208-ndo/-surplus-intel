from __future__ import annotations

import asyncio
import io
import re
from typing import Any

import aiohttp
import pdfplumber


COUNTY_NAME = "DeKalb GA"
SOURCE_URL = "https://dekalbtax.org/wp-content/uploads/Excess-Funds-List.pdf"


def parse_money(value: str) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_date(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if not match:
        return ""
    month, day, year = match.groups()
    if len(year) == 2:
        year = f"20{year}" if int(year) < 50 else f"19{year}"
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


async def download_pdf(url: str = SOURCE_URL) -> bytes:
    timeout = aiohttp.ClientTimeout(total=90)
    headers = {"User-Agent": "Mozilla/5.0 surplus-intel/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()


def extract_tables(pdf_bytes: bytes) -> list[list[str]]:
    rows: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables or []:
                for row in table or []:
                    values = [re.sub(r"\s+", " ", str(cell or "").strip()) for cell in row]
                    if any(values):
                        rows.append(values)
            if tables:
                continue
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            for line in text.splitlines():
                rows.append([re.sub(r"\s+", " ", line).strip()])
    return rows


def parse_table_row(row: list[str]) -> dict[str, Any] | None:
    values = [value for value in row if value]
    joined = " ".join(values)
    if not joined or "parcel" in joined.lower() and "amount" in joined.lower():
        return None

    money_candidates = [(index, value) for index, value in enumerate(values) if re.search(r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})", value)]
    date_candidates = [(index, value) for index, value in enumerate(values) if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", value)]
    if not money_candidates or not date_candidates:
        return parse_text_line(joined)

    amount_index, amount_text = money_candidates[0]
    date_index, date_text = date_candidates[0]
    parcel_id = values[0] if values else ""
    first_name = values[date_index + 1] if date_index + 1 < len(values) else ""
    last_name = values[date_index + 2] if date_index + 2 < len(values) else ""
    owner_name = f"{first_name} {last_name}".strip()
    if not owner_name:
        owner_name = " ".join(values[date_index + 1 : date_index + 3]).strip()

    address_parts = values[date_index + 3 :]
    property_address = address_parts[0] if address_parts else ""
    city = address_parts[1] if len(address_parts) > 1 else ""
    zip_code = address_parts[2] if len(address_parts) > 2 else ""

    return {
        "county_name": COUNTY_NAME,
        "source_url": SOURCE_URL,
        "owner_name": owner_name.title(),
        "parcel_id": parcel_id.upper(),
        "surplus_amount": parse_money(amount_text),
        "sale_date": normalize_date(date_text),
        "property_address": property_address.title(),
        "city": city.title(),
        "zip": zip_code,
    }


def parse_text_line(line: str) -> dict[str, Any] | None:
    clean = re.sub(r"\s+", " ", line or "").strip()
    date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)
    money_match = re.search(r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})", clean)
    if not (date_match and money_match):
        return None
    before = clean[: money_match.start()].strip()
    after_date = clean[date_match.end() :].strip()
    parcel_id = before.split()[0] if before else ""
    tokens = after_date.split()
    owner_name = " ".join(tokens[:2]).title() if len(tokens) >= 2 else after_date.title()
    address = " ".join(tokens[2:-2]).title() if len(tokens) > 4 else ""
    city = tokens[-2].title() if len(tokens) > 3 else ""
    zip_code = tokens[-1] if len(tokens) > 3 else ""
    return {
        "county_name": COUNTY_NAME,
        "source_url": SOURCE_URL,
        "owner_name": owner_name,
        "parcel_id": parcel_id.upper(),
        "surplus_amount": parse_money(money_match.group(0)),
        "sale_date": normalize_date(date_match.group(0)),
        "property_address": address,
        "city": city,
        "zip": zip_code,
    }


def parse_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for row in extract_tables(pdf_bytes):
        lead = parse_table_row(row)
        if not lead or lead["surplus_amount"] <= 0:
            continue
        key = (lead["owner_name"].upper(), lead["parcel_id"], lead["surplus_amount"])
        if key in seen:
            continue
        seen.add(key)
        leads.append(lead)
    return leads


async def scrape() -> list[dict[str, Any]]:
    pdf_bytes = await download_pdf()
    return parse_pdf(pdf_bytes)


def scrape_sync() -> list[dict[str, Any]]:
    return asyncio.run(scrape())


if __name__ == "__main__":
    for item in scrape_sync():
        print(item)
