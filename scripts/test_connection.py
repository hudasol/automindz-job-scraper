"""
Run this FIRST, locally, before touching anything else:

    python3 scripts/test_connection.py

I could not reach weworkremotely.com at all from my sandbox (every request timed out) -
I don't know if that's bot protection on their end or just a restriction in my own
environment. This script tells us, from your machine, which one it is, and shows the
first chunk of real HTML so we can confirm (or fix) the selectors in
scraper/weworkremotely.py before building the rest on top of guesses.
"""

import sys

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

URL = "https://weworkremotely.com/remote-jobs/search?term=python+developer"


def main() -> None:
    print(f"Requesting: {URL}\n")
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=15)
    except requests.RequestException as exc:
        print(f"FAILED to connect: {exc}")
        print(
            "\nIf this fails on your machine too, the site is likely blocking "
            "plain `requests` traffic. Try `pip install cloudscraper` and swap "
            "`requests.get` for a cloudscraper session in scraper/weworkremotely.py, "
            "or fall back to Dice / add Playwright for a real headless browser."
        )
        sys.exit(1)

    print("Status code:", resp.status_code)
    print("\n--- First 3000 characters of the response ---\n")
    print(resp.text[:3000])
    print(
        "\n--- Look for the job listing links above (they should look like "
        '"/remote-jobs/<company>-<title>-<id>") and confirm they match the '
        "selectors in scraper/weworkremotely.py's _find_listing_links(). ---"
    )


if __name__ == "__main__":
    main()
