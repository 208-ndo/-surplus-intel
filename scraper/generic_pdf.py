from __future__ import annotations

import importlib
import io
import re
from pathlib import Path
from typing import Any

import aiohttp
import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "counties.yml"


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    return text


def load_county_configs(path: Path = CONFIG_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    counties: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "counties:":
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                counties.append(current)
            current = {}
            remainder = stripped[2:].strip()
            if remainder and ":" in remainder:
                key, value = remainder.split(":", 1)
                current[key.strip()] = parse_scalar(value)
            continue
        if current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(value)
    if current:
        counties.append(current)
    return counties


async def download_pdf(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=90)
    headers = {"User-Agent": "Mozilla/5.0 surplus-intel/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()


def pdf_text(pdf_bytes: bytes) -> str:
    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text(x_tolerance=1, y_tolerance=3) or "")
    return "\n".join(chunks)


def parse_money(value: str) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_date(value: str) -> str:
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", str(value or ""))
    if not match:
        return ""
    month, day, year = match.groups()
    if len(year) == 2:
        year = f"20{year}" if int(year) < 50 else f"19{year}"
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def parse_generic_line(line: str, config: dict[str, Any]) -> dict[str, Any] | None:
    clean = re.sub(r"\s+", " ", line or "").strip()
    if not clean:
        return None
    date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean)
    money_match = re.search(r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})", clean)
    parcel_match = re.search(r"\b\d{1,3}(?:\s?\d{2,3}){1,4}[A-Z0-9\-]*\b", clean, re.IGNORECASE)
    if not date_match or not money_match:
        return None
    owner = clean[: money_match.start()].strip(" |:-")
    if parcel_match and parcel_match.start() < money_match.start():
        owner = clean[: parcel_match.start()].strip(" |:-")
    if not owner:
        return None
    return {
        "county_name": config.get("county_name") or "",
        "source_url": config.get("source_url") or "",
        "owner_name": owner.title(),
        "parcel_id": parcel_match.group(0).upper() if parcel_match else "",
        "surplus_amount": parse_money(money_match.group(0)),
        "sale_date": normalize_date(date_match.group(0)),
        "property_address": "",
        "city": "",
        "zip": "",
    }


def parse_generic_pdf(pdf_bytes: bytes, config: dict[str, Any]) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float]] = set()
    for line in pdf_text(pdf_bytes).splitlines():
        lead = parse_generic_line(line, config)
        if not lead or lead["surplus_amount"] <= 0:
            continue
        key = (lead["owner_name"].upper(), lead["parcel_id"], lead["surplus_amount"])
        if key in seen:
            continue
        seen.add(key)
        leads.append(lead)
    return leads


async def scrape_configured_county(config: dict[str, Any]) -> list[dict[str, Any]]:
    module_name = str(config.get("parser_module") or "").strip()
    if module_name:
        try:
            module = importlib.import_module(f"scraper.{module_name}")
        except ModuleNotFoundError:
            module = importlib.import_module(module_name)
        scrape = getattr(module, "scrape")
        return await scrape()
    source_url = str(config.get("source_url") or "").strip()
    if not source_url:
        return []
    return parse_generic_pdf(await download_pdf(source_url), config)
