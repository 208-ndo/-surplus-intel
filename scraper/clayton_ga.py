from __future__ import annotations

import asyncio
import io
import re
from typing import Any

import aiohttp
import pdfplumber


COUNTY_NAME = "Clayton GA"
SOURCE_URL = "https://publicaccess.claytoncountyga.gov/content/PDF/DQ759GA.pdf"


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


def text_from_pdf(pdf_bytes: bytes) -> str:
    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
    return "\n".join(chunks)


def parse_line(line: str) -> dict[str, Any] | None:
    clean = re.sub(r"\s+", " ", line or "").strip()
    if not clean or "parcel" in clean.lower() and "amount" in clean.lower():
        return None

    date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)
    money_match = re.search(r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})", clean)
    parcel_match = re.search(r"\b\d{2,3}[A-Z]?\d{2,4}[A-Z]?\d{2,4}[A-Z0-9\-]*\b", clean, re.IGNORECASE)
    if not (date_match and money_match):
        return None

    date_text = date_match.group(0)
    amount_text = money_match.group(0)
    parcel_id = parcel_match.group(0).strip() if parcel_match else ""

    owner_part = clean[: money_match.start()].strip(" |:-")
    if parcel_match and parcel_match.start() < money_match.start():
        owner_part = clean[: parcel_match.start()].strip(" |:-")
    owner_part = re.sub(r"\b(NAME|OWNER)\b", "", owner_part, flags=re.IGNORECASE).strip(" |:-")
    if not owner_part:
        return None

    return {
        "county_name": COUNTY_NAME,
        "source_url": SOURCE_URL,
        "owner_name": owner_part.title(),
        "parcel_id": parcel_id.upper(),
        "surplus_amount": parse_money(amount_text),
        "sale_date": normalize_date(date_text),
        "property_address": "",
        "city": "",
        "zip": "",
    }


def parse_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for line in text_from_pdf(pdf_bytes).splitlines():
        lead = parse_line(line)
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
