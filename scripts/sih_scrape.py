#!/usr/bin/env python3
"""Scrape SIH 2026 problem statements and optionally POST them to PBBot.

GitHub Actions (recommended):
  python scripts/sih_scrape.py --post "$SIH_INGEST_URL" --secret "$SIH_INGEST_SECRET"

Local dry-run (print JSON, no bot):
  python scripts/sih_scrape.py --json

Local file (verify parser without hitting sih.gov.in):
  python scripts/sih_scrape.py --html sih.html --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Running this file directly makes ``scripts/`` the first import location.
# Add the repository root so sibling packages such as ``features`` resolve in
# local shells, cron, and GitHub Actions alike.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import httpx

from features.sih_scrape import (
    candidate_urls,
    parse_problem_statements,
    scrape_problem_statements,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sih-scrape")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape SIH 2026 PS submission counts")
    parser.add_argument("--url", default="", help="Override SIH PS page URL")
    parser.add_argument("--html", default="", help="Parse a saved HTML file instead of fetching")
    parser.add_argument("--json", action="store_true", help="Print the payload to stdout")
    parser.add_argument("--post", default="", help="Bot ingest URL, e.g. https://host:8083/sih-ingest")
    parser.add_argument("--secret", default="", help="Value for X-SIH-Alert-Secret")
    parser.add_argument("--no-playwright", action="store_true", help="Disable Chromium fallback")
    parser.add_argument("--timeout", type=float, default=60.0, help="POST timeout seconds")
    args = parser.parse_args()

    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
        rows = parse_problem_statements(html)
        source_url = args.html
    else:
        rows, source_url = scrape_problem_statements(
            candidate_urls(args.url),
            use_playwright=not args.no_playwright,
        )

    payload = {
        "source_url": source_url,
        "problem_statements": rows,
    }
    log.info("Scraped %s problem statements from %s", len(rows), source_url)

    if args.json or not args.post:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    if not args.post:
        return 0 if rows else 1

    if not args.secret:
        log.error("--secret is required when posting to the bot")
        return 2

    response = httpx.post(
        args.post,
        json=payload,
        headers={"X-SIH-Alert-Secret": args.secret, "Content-Type": "application/json"},
        timeout=args.timeout,
        follow_redirects=True,
    )
    log.info("Bot ingest HTTP %s: %s", response.status_code, response.text[:500])
    if response.status_code >= 400:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
