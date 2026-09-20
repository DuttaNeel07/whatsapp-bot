"""Standalone SIH 2026 table scraper.

No Neonize, no Postgres. Used by:
  - features/sih.py (bot ingest / optional in-process poller)
  - scripts/sih_scrape.py (GitHub Actions / CLI)

Table layout (SIH 2026 dataTablePS, direct <td> children of each row):
  td[0] serial  td[1] org  td[2] title (contains a nested modal table!)
  td[3] category  td[4] PS number  td[5] "submitted/limit"  td[6] theme
  td[7] deadline
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_URL = "https://sih.gov.in/sih2026PS"
MAX_RETRIES = 4
REQUEST_TIMEOUT = 45.0

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.6261.94 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


def parse_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else 0

_SUBMISSION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
PS_NUMBER_RE = re.compile(r"^SIH\d+$", re.I)


def parse_submission(value: Any) -> tuple[int, int | None]:
    """Parse '225/500' -> (225, 500). A bare '225' -> (225, None)."""
    text = clean_text(value)
    if not text:
        return 0, None
    match = _SUBMISSION_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    if text.isdigit():
        return int(text), None
    raise ValueError(f"Unrecognised submission cell: {text!r}")


def candidate_urls(primary: str | None = None) -> list[str]:
    urls = [primary or "", DEFAULT_URL, "https://www.sih.gov.in/sih2026PS"]
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def parse_problem_statements(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="dataTablePS") or soup.find(
        "table", id=re.compile(r"dataTable", re.I)
    )
    if not table:
        return []

    # recursive=False everywhere: each title cell embeds a modal with its own
    # <table>/<tr>/<td>s, which must not be mistaken for outer rows or cells.
    tbody = table.find("tbody", recursive=False) or table
    rows: list[dict[str, Any]] = []
    for row in tbody.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 7:
            continue

        # Anchor from the right so an optional leading serial column doesn't shift fields.
        serial = clean_text(cells[-8].get_text()) if len(cells) >= 8 else ""
        (org_cell, title_cell, category_cell, ps_cell,
         count_cell, theme_cell, deadline_cell) = cells[-7:]

        ps_number = clean_text(ps_cell.get_text())
        if not PS_NUMBER_RE.match(ps_number):
            log.warning("SIH skipping row with unexpected PS number %r", ps_number)
            continue

        # Never fall back to title_cell.get_text(): it contains the whole modal.
        title_link = title_cell.find("a")
        if title_link:
            title = clean_text(title_link.get_text())
        else:
            title = clean_text(next(title_cell.stripped_strings, ""))

        try:
            submitted, limit = parse_submission(count_cell.get_text())
        except ValueError as exc:
            log.warning("SIH skipping %s: %s", ps_number, exc)
            continue

        rows.append(
            {
                "ps_number": ps_number,
                "serial_number": serial,
                "organization": clean_text(org_cell.get_text()),
                "title": title,
                "category": clean_text(category_cell.get_text()),
                "submitted_ideas_count": submitted,
                "submitted_ideas_limit": limit,
                "theme": clean_text(theme_cell.get_text()),
                "deadline": clean_text(deadline_cell.get_text()),
            }
        )
    return rows

def fetch_html(url: str) -> str:
    delay = 1.0
    last_error: Exception | None = None
    with httpx.Client(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
    ) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.get(url)
                response.raise_for_status()
                if "dataTablePS" in response.text or "<table" in response.text:
                    return response.text
                last_error = RuntimeError(f"{url} returned HTML without a PS table")
            except Exception as exc:
                last_error = exc
                log.warning("SIH GET %s attempt %s failed: %s", url, attempt, exc)
                time.sleep(delay + random.uniform(0, 0.4))
                delay *= 2
    raise last_error or RuntimeError(f"Failed to fetch {url}")


def fetch_html_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("#dataTablePS tbody tr, table tbody tr", timeout=30_000)
            return page.content()
        finally:
            browser.close()


def scrape_problem_statements(
    urls: list[str] | None = None,
    *,
    use_playwright: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    last_error: Exception | None = None
    fetchers = [fetch_html]
    if use_playwright:
        fetchers.append(fetch_html_playwright)
    for url in urls or candidate_urls():
        for fetcher in fetchers:
            try:
                html = fetcher(url)
                rows = parse_problem_statements(html)
                if rows:
                    log.info("SIH scraped %s PS from %s via %s", len(rows), url, fetcher.__name__)
                    return rows, url
            except Exception as exc:
                last_error = exc
                log.warning("SIH %s failed for %s: %s", fetcher.__name__, url, exc)
    if last_error:
        raise last_error
    raise RuntimeError("SIH scrape returned no problem statements")
