"""
Scraper for weworkremotely.com job listings.

Usage (CLI - this is how the Trigger.dev task calls it):
    python3 -m scraper.weworkremotely "Python Developer"
    -> prints a JSON array of {job_url, job_description, company_name, job_title} to stdout

Usage (as a library):
    from scraper.weworkremotely import scrape_jobs
    jobs = scrape_jobs("Python Developer")

IMPORTANT - read before debugging:
This has now been run against the real site twice: once via a fetch tool from my end (rendered
to text, so no visibility into raw CSS classes), and once by you, running the actual scraper
with real `requests` output. The second run caught two real bugs that the first check couldn't:
  1. _find_listing_links was matching marketing/nav links like /remote-jobs/find-your-plan
     alongside real job postings, because it only checked the URL prefix.
  2. _extract_description / _extract_company were guessing CSS selectors
     (.listing-container, [class*='company']) that turned out to match the wrong, oversized
     elements on the real page - description came back full of "Related Jobs" sidebar text,
     and company_name came back with almost the entire page body.

Both are fixed below using signals confirmed reliable from your actual run: real job URLs end
in "-<digits>" (e.g. ...-python-3), so links are now filtered on that pattern instead of just
the /remote-jobs/ prefix. Description/company now try a JSON-LD <script type="application/
ld+json"> JobPosting block first (a common, standards-based pattern many job boards embed for
SEO - untested here since my fetch tool strips <script> tags, so this needs your confirmation),
then fall back to the <meta name="description"> tag and the page <title>'s "X at Company"
split - both proven correct against your real output. <h1> for job_title was already confirmed
working and is unchanged.

If anything still looks wrong, run `python3 -m scraper.weworkremotely "<query>"` and share the
output - real output beats guessing every time, as this round showed.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://weworkremotely.com"
SEARCH_URL = BASE_URL + "/remote-jobs/search?term={query}"

# Real job listing URLs observed end in a numeric id, e.g. /remote-jobs/proxify-ab-senior-
# fullstack-developer-python-3. Marketing/nav links under /remote-jobs/ (like /find-your-plan)
# don't match this, so this is a much more reliable filter than the URL prefix alone.
JOB_URL_PATH_RE = re.compile(r"^/remote-jobs/[a-z0-9-]+-\d+$")

HEADERS = {
    # A realistic browser UA reduces the chance of being blocked outright.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 15
MAX_JOBS = 25  # keep it simple - cap how many detail pages we fetch per search
POLITE_DELAY_SECONDS = 0.5


@dataclass
class Job:
    job_url: str
    job_description: str
    company_name: str
    job_title: str


def _get(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[scraper] failed to fetch {url}: {exc}", file=sys.stderr)
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _find_listing_links(soup: BeautifulSoup) -> list[str]:
    """
    Real job listing URLs end in a numeric id (JOB_URL_PATH_RE) - this excludes
    marketing/nav links like /remote-jobs/find-your-plan that share the /remote-jobs/
    prefix but aren't actual postings (confirmed by a real run turning up 3 of those
    mixed in with 2 real jobs before this filter was added).
    """
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href^='/remote-jobs/']"):
        href = a.get("href", "")
        if not href or href in seen:
            continue
        path = href.split("?", 1)[0]
        if not JOB_URL_PATH_RE.match(path):
            continue
        seen.add(href)
        links.append(BASE_URL + path)
    return links


def _find_job_posting_json_ld(soup: BeautifulSoup) -> Optional[dict]:
    """
    Many job boards embed a schema.org JobPosting block in <script type="application/
    ld+json"> for SEO - if present, it's clean, structured, and doesn't require guessing
    CSS classes at all. Not yet confirmed present on WWR (my fetch tool strips <script>
    tags so I couldn't check), so this is tried first but never assumed - every caller
    falls back to other proven signals if this returns None.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _extract_description(soup: BeautifulSoup, json_ld: Optional[dict] = None) -> str:
    if json_ld and json_ld.get("description"):
        # JSON-LD descriptions are sometimes raw HTML - strip tags for a clean string.
        return BeautifulSoup(json_ld["description"], "html.parser").get_text("\n", strip=True)

    # Proven reliable against a real fetch: every WWR job page has a
    # <meta name="description"> summarizing the role specifically (not sidebar/nav junk).
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()

    og_meta = soup.find("meta", attrs={"property": "og:description"})
    if og_meta and og_meta.get("content"):
        return og_meta["content"].strip()

    return ""


def _extract_company(soup: BeautifulSoup, json_ld: Optional[dict] = None, fallback: str = "") -> str:
    if json_ld:
        org = json_ld.get("hiringOrganization")
        if isinstance(org, dict) and org.get("name"):
            return org["name"].strip()

    if soup.title and soup.title.string:
        # Proven reliable against a real fetch: WWR page titles are formatted
        # "<Job Title> at <Company Name>".
        parts = soup.title.string.split(" at ")
        if len(parts) == 2:
            return parts[1].split("|")[0].strip()

    return fallback


def _extract_title(soup: BeautifulSoup, json_ld: Optional[dict] = None, fallback: str = "") -> str:
    if json_ld and json_ld.get("title"):
        return json_ld["title"].strip()

    # Proven reliable against a real fetch: the page's <h1> is the job title.
    node = soup.select_one("h1")
    if node and node.get_text(strip=True):
        return node.get_text(strip=True)
    return fallback


def scrape_jobs(query: str, max_jobs: int = MAX_JOBS) -> list[dict]:
    search_soup = _get(SEARCH_URL.format(query=quote_plus(query)))
    if search_soup is None:
        return []

    listing_urls = _find_listing_links(search_soup)[:max_jobs]

    jobs: list[Job] = []
    for url in listing_urls:
        detail_soup = _get(url)
        if detail_soup is None:
            continue
        json_ld = _find_job_posting_json_ld(detail_soup)
        jobs.append(
            Job(
                job_url=url,
                job_description=_extract_description(detail_soup, json_ld),
                company_name=_extract_company(detail_soup, json_ld),
                job_title=_extract_title(detail_soup, json_ld, fallback=query),
            )
        )
        time.sleep(POLITE_DELAY_SECONDS)  # be a polite scraper, avoid hammering the site

    return [asdict(j) for j in jobs]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 -m scraper.weworkremotely "<job title>"', file=sys.stderr)
        sys.exit(1)
    result = scrape_jobs(sys.argv[1])
    print(json.dumps(result))
