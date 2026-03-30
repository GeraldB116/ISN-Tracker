"""
Altera ISN Tracker - NHS Digital Scraper
Uses Playwright (real browser) on GitHub Actions to bypass 403.
Falls back to cloudscraper/requests for local use.
"""

import re
import os
import sys
import time
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from bs4 import BeautifulSoup

URL = "https://digital.nhs.uk/data-and-information/information-standards/governance/latest-activity"
HTML_FILE = "index.html"
BASE_URL = "https://digital.nhs.uk"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}

MONTH_MAP = {
    "january": "01", "february": "02", "march": "03",
    "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}

REF_RE = re.compile(
    r"^((?:DAPB|DCB|SCCI|ISB|ISN)\d+[\w\-]*)",
    re.IGNORECASE,
)


def clean(text):
    """Strip non-breaking spaces and collapse whitespace."""
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(raw):
    """Convert UK date string to ISO format."""
    raw = clean(raw)
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", raw)
    if m:
        day = int(m.group(1))
        month = MONTH_MAP.get(m.group(2).lower(), "01")
        year = m.group(3)
        return f"{year}-{month}-{day:02d}"
    m = re.match(r"^([A-Za-z]+)\s+(\d{4})", raw)
    if m:
        month = MONTH_MAP.get(m.group(1).lower(), "01")
        year = m.group(2)
        return f"{year}-{month}-01"
    return datetime.now().strftime("%Y-%m-%d")


def parse_type(raw):
    """Normalise the Type cell value."""
    t = clean(raw).lower()
    if "standard and collection" in t or "standard & collection" in t:
        return "Standard & Collection"
    if "standard" in t and "collection" in t:
        return "Standard & Collection"
    if "collection" in t:
        return "Collection"
    if "standard" in t:
        return "Standard"
    if "consultation" in t:
        return "Consultation"
    return clean(raw)


def extract_ref(name_text):
    """Extract reference code from start of name text."""
    m = REF_RE.match(name_text)
    if m:
        return m.group(1).upper().strip()
    return ""


def fetch_with_playwright(url):
    """Fetch using a real Chrome browser via Playwright."""
    print("  Method   : Playwright (real browser)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        content = page.content()
        browser.close()
    print(f"  Status   : 200 OK (Playwright)")
    return BeautifulSoup(content, "lxml")


def fetch_with_cloudscraper(url):
    """Fetch using cloudscraper."""
    print("  Method   : cloudscraper")
    for attempt in range(3):
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
            resp = scraper.get(url, timeout=30)
            if resp.status_code == 200:
                print(f"  Status   : 200 OK (cloudscraper, attempt {attempt + 1})")
                return BeautifulSoup(resp.text, "lxml")
            else:
                print(f"  Warning  : cloudscraper attempt {attempt + 1} got status {resp.status_code}")
                time.sleep(2)
        except Exception as e:
            print(f"  Warning  : cloudscraper attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    return None


def fetch_with_requests(url):
    """Fetch using plain requests."""
    print("  Method   : requests")
    for attempt in range(3):
        try:
            session = requests.Session()
            session.headers.update(HEADERS)
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                print(f"  Status   : 200 OK (requests, attempt {attempt + 1})")
                return BeautifulSoup(resp.text, "lxml")
            else:
                print(f"  Warning  : requests attempt {attempt + 1} got status {resp.status_code}")
                time.sleep(3)
        except Exception as e:
            print(f"  Warning  : requests attempt {attempt + 1} failed: {e}")
            time.sleep(3)
    return None


def fetch(url):
    """Try Playwright first, then cloudscraper, then requests."""
    print(f"  Fetching : {url}")

    # Try Playwright first (works on GitHub Actions)
    if HAS_PLAYWRIGHT:
        try:
            return fetch_with_playwright(url)
        except Exception as e:
            print(f"  Warning  : Playwright failed: {e}")

    # Try cloudscraper second
    if HAS_CLOUDSCRAPER:
        result = fetch_with_cloudscraper(url)
        if result:
            return result

    # Try plain requests last
    if HAS_REQUESTS:
        result = fetch_with_requests(url)
        if result:
            return result

    print("\n  ERROR: Could not fetch the page with any method.")
    sys.exit(1)


def scrape(soup):
    """Extract all items from DAB approval tables."""
    sections = soup.find_all(
        "div",
        id=re.compile(r"dab-approvals", re.IGNORECASE),
    )
    print(f"\n  Found {len(sections)} DAB approval section(s):\n")

    all_items = []
    seen = set()

    for section in sections:
        heading = section.find(["h2", "h3"])
        label = clean(heading.get_text()) if heading else section.get("id", "?")
        print(f"  -- {label}")

        table = section.find("table", class_="nhsd-!t-display-s-show-table")
        if not table:
            tables = section.find_all("table")
            if tables:
                table = tables[0]
        if not table:
            print(f"     No table found - skipping.\n")
            continue

        tbody = table.find("tbody")
        if not tbody:
            print(f"     No tbody found - skipping.\n")
            continue

        rows = tbody.find_all("tr")
        print(f"     Rows found : {len(rows)}")

        section_count = 0
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            name_raw = cells[0].get_text(separator=" ")
            name_clean = clean(name_raw)
            if not name_clean:
                continue

            ref = extract_ref(name_clean)
            title = name_clean

            dedup_key = ref if ref else title
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            date_raw = cells[1].get_text(separator=" ") if len(cells) > 1 else ""
            date_iso = parse_date(date_raw)

            type_raw = cells[2].get_text(separator=" ") if len(cells) > 2 else ""
            item_type = parse_type(type_raw)

            link_tag = cells[0].find("a", href=True)
            link = ""
            if link_tag:
                href = link_tag.get("href", "")
                link = href if href.startswith("http") else BASE_URL + href

            all_items.append({
                "ref": ref,
                "title": title,
                "type": item_type,
                "status": "Approved",
                "date": date_iso,
                "link": link,
            })
            section_count += 1

        print(f"     Items extracted : {section_count}\n")

    return all_items


def esc(s):
    """Escape string for JavaScript."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "")
    return s


def get_field(name, text):
    """Extract a field value from a JS object string."""
    m = re.search(rf'{name}:"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if not m:
        return ""
    val = m.group(1)
    val = val.replace('\\"', '"').replace("\\\\", "\\")
    return val


def build_js_array(items):
    """Build the JavaScript DATA array."""
    rows = []
    for i in items:
        rows.append(
            f'  {{ '
            f'ref:"{esc(i["ref"])}", '
            f'title:"{esc(i["title"])}", '
            f'type:"{esc(i["type"])}", '
            f'status:"{esc(i["status"])}", '
            f'date:"{esc(i["date"])}", '
            f'link:"{esc(i.get("link", ""))}", '
            f'conformance:"{esc(i.get("conformance", ""))}", '
            f'documents:"{esc(i.get("documents", ""))}", '
            f'summary:"{esc(i.get("summary", ""))}" '
            f'}}'
        )
    return "[\n" + ",\n".join(rows) + "\n]"


def inject(items, html_path):
    """Replace DATA array in HTML file, preserving existing summaries."""
    if not os.path.exists(html_path):
        print(f"\n  ERROR: '{html_path}' not found.")
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    existing = {}
    data_match = re.search(
        r"(?:const|var)\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",
        html,
        re.MULTILINE,
    )
    if data_match:
        data_str = data_match.group(1)
        for obj in re.finditer(r"\{[^{}]+\}", data_str, re.DOTALL):
            o = obj.group(0)
            ref = get_field("ref", o)
            title = get_field("title", o)
            key = ref if ref else title
            if key:
                existing[key] = {
                    "summary": get_field("summary", o),
                    "conformance": get_field("conformance", o),
                    "documents": get_field("documents", o),
                }

    print(f"  Found {len(existing)} existing items with metadata.")

    merged = 0
    for item in items:
        key = item["ref"] if item["ref"] else item["title"]
        if key in existing:
            ex = existing[key]
            if ex["summary"] and not item.get("summary"):
                item["summary"] = ex["summary"]
                merged += 1
            if ex["conformance"] and not item.get("conformance"):
                item["conformance"] = ex["conformance"]
            if ex["documents"] and not item.get("documents"):
                item["documents"] = ex["documents"]

    print(f"  Summaries preserved: {merged}")

    pattern = re.compile(
        r"((?:const|var)\s+DATA\s*=\s*)\[[\s\S]*?\]\s*;",
        re.MULTILINE,
    )
    new_array = build_js_array(items)
    new_html, n = pattern.subn(rf"\g<1>{new_array};", html)

    if n == 0:
        print("\n  ERROR: Could not find DATA array in HTML.")
        sys.exit(1)

    today = datetime.now().strftime("%d %b %Y")
    new_html = re.sub(
        r"Last updated:[\s\w]+\d{4}",
        f"Last updated: {today}",
        new_html,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    with_summary = sum(1 for i in items if i.get("summary"))
    print(f"\n  {html_path} updated - {len(items)} items written.")
    print(f"  Items with summaries: {with_summary}")


def main():
    """Main entry point."""
    preview = "--preview" in sys.argv

    print()
    print("=" * 60)
    print("  Altera ISN Tracker - NHS Digital Scraper")
    print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")
    print("=" * 60)

    if HAS_PLAYWRIGHT:
        print("\n  Playwright : available (will use real browser)")
    elif HAS_CLOUDSCRAPER:
        print("\n  Playwright : not available")
        print("  cloudscraper: available")
    else:
        print("\n  Using: requests only")

    print(f"\n  Fetching page...\n")
    soup = fetch(URL)

    print("\n  Scanning DAB approval sections...")
    items = scrape(soup)

    if not items:
        print("\n  WARNING: No items were extracted.")
        sys.exit(1)

    print(f"{'=' * 75}")
    print(f"  {'#':<4}  {'REF':<20}  {'DATE':<12}  {'TYPE':<24}  TITLE")
    print(f"  {'='*4}  {'='*20}  {'='*12}  {'='*24}  {'='*25}")
    for i, item in enumerate(items, 1):
        ref_display = item["ref"] if item["ref"] else "(no ref)"
        print(
            f"  {i:<4}  {ref_display:<20}  {item['date']:<12}  "
            f"{item['type']:<24}  {item['title'][:35]}"
        )
    print(f"{'=' * 75}")
    print(f"  Total : {len(items)} items")
    print(f"{'=' * 75}\n")

    if preview:
        print("  Preview mode - HTML file was NOT changed.\n")
        return

    print(f"  Writing to {HTML_FILE}...")
    inject(items, HTML_FILE)

    print()
    print("=" * 60)
    print(f"  Done! Open '{HTML_FILE}' in your browser.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
