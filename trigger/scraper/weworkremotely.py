"""
Scraper for weworkremotely.com job listings.

Pivoted from scraping the HTML /search page to WeWorkRemotely's Programming category RSS
feed. Reason: WWR returns 403 Forbidden to the HTML search endpoint when the request comes
from Trigger.dev's deployed cloud workers (AWS us-east-1 datacenter IP range) - confirmed via
Trigger.dev run logs on 2026-08-11, even though the same request works fine from a home IP.
The RSS feed is not blocked, so this fetches it and filters items by whether the search term
appears in the job title or description.

Usage (CLI - this is how the Trigger.dev task calls it):
    python3 -m scraper.weworkremotely "Python Developer"
    -> prints a JSON array of {job_url, job_description, company_name, job_title, date_posted,
       job_type} to stdout. date_posted comes from the RSS <pubDate>; job_type has no
       structured source (see _infer_job_type) so it's a best-effort keyword guess.

Usage (as a library):
    from scraper.weworkremotely import scrape_jobs
    jobs = scrape_jobs("Python Developer")
"""

from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import dataclass, asdict
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

RSS_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

REQUEST_TIMEOUT = 15
MAX_JOBS = 25


@dataclass
class Job:
    job_url: str
    job_description: str
    company_name: str
    job_title: str
    date_posted: str | None
    job_type: str


def _strip_html(text: str) -> str:
    """RSS descriptions are HTML; strip tags for a clean plain-text description."""
    return BeautifulSoup(text, "html.parser").get_text("\n", strip=True)


# Unicode categories for decorative emoji/symbol/mark characters WWR sometimes prepends to
# titles (e.g. a location-pin emoji before "Location Services Engineer..."). str.strip() only
# removes whitespace, so these survive untouched and silently break alphabetical sorting -
# they compare as "earlier" than any real letter even though they render as an near-invisible
# leading glyph/gap.
_DECORATIVE_CATEGORIES = {"So", "Sk", "Mn", "Me", "Cf"}


def _clean_title(text: str) -> str:
    text = text.strip()
    while text and unicodedata.category(text[0]) in _DECORATIVE_CATEGORIES:
        text = text[1:].strip()
    return text


def _parse_pub_date(raw_pub_date: str) -> str | None:
    """RSS pubDate is RFC 822 (e.g. 'Wed, 22 Jul 2026 07:02:04 +0000'); store as ISO 8601."""
    if not raw_pub_date:
        return None
    try:
        return parsedate_to_datetime(raw_pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def _infer_job_type(title: str, description: str) -> str:
    """WWR's RSS feed has no structured employment-type field (confirmed: not in the RSS
    description, and the detail pages that do show it are blocked for cloud IPs - see the
    WeWorkRemotely blocking gotcha in CLAUDE.md). Best-effort keyword guess instead."""
    text = f"{title} {description}".lower()
    if "intern" in text:
        return "Internship"
    if "part-time" in text or "part time" in text:
        return "Part-time"
    if "contract" in text or "contractor" in text:
        return "Contract"
    return "Full-time"


def scrape_jobs(query: str, max_jobs: int = MAX_JOBS) -> list[dict]:
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[scraper] failed to fetch {RSS_URL}: {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"[scraper] failed to parse RSS: {exc}", file=sys.stderr)
        return []

    query_lower = query.strip().lower()
    jobs: list[Job] = []

    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        raw_description = item.findtext("description") or ""
        raw_pub_date = item.findtext("pubDate") or ""

        if not raw_title or not link:
            continue

        # WWR RSS titles are formatted "Company Name: Job Title"
        if ":" in raw_title:
            company_name, job_title = raw_title.split(":", 1)
            company_name = _clean_title(company_name)
            job_title = _clean_title(job_title)
        else:
            company_name, job_title = "", _clean_title(raw_title)

        if query_lower not in job_title.lower() and query_lower not in raw_description.lower():
            continue

        description = _strip_html(raw_description)

        jobs.append(
            Job(
                job_url=link,
                job_description=description,
                company_name=company_name,
                job_title=job_title,
                date_posted=_parse_pub_date(raw_pub_date),
                job_type=_infer_job_type(job_title, description),
            )
        )
        if len(jobs) >= max_jobs:
            break

    return [asdict(j) for j in jobs]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 -m scraper.weworkremotely "<job title>"', file=sys.stderr)
        sys.exit(1)
    result = scrape_jobs(sys.argv[1])
    print(json.dumps(result))
