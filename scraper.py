"""
Altera ISN Tracker — NHS Digital Scraper
Targets: index.html
Extracts: ref, title, type, date (ISO), link (href)
"""

import re
import os
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime

URL       = "https://digital.nhs.uk/data-and-information/information-standards/governance/latest-activity"
HTML_FILE = "index.html"
BASE_URL  = "https://digital.nhs.uk"

TARGET_IDS = [
    "data-assurance-board-dab-approvals-from-april-2025",
    "data-assurance-board-dab-approvals-from-march-2024-march-2025",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

MONTH_MAP = {
    "january":"01",  "february":"02", "march":"03",
    "april":"04",    "may":"05",      "june":"06",
    "july":"07",     "august":"08",   "september":"09",
    "october":"10",  "november":"11", "december":"12",
}

REF_RE = re.compile(
    r'^((?:DAPB|DCB|SCCI|ISB|ISN)\d+[\w\-]*)',
    re.IGNORECASE
)

def clean(text):
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def format_date(raw):
    raw = clean(raw)
    if not raw:
        return ""
    m = re.match(r'^\d{1,2}\s+([A-Za-z]+)\s+(\d{4})', raw)
    if m:
        month = MONTH_MAP.get(m.group(1).lower(), "01")
        return f"{m.group(2)}-{month}-01"
    m = re.match(r'^([A-Za-z]+)\s+(\d{4})', raw)
    if m:
        month = MONTH_MAP.get(m.group(1).lower(), "01")
        return f"{m.group(2)}-{month}-01"
    return raw

def parse_type(raw):
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
    m = REF_RE.match(name_text)
    return m.group(1).upper().strip() if m else ""

def fetch(url):
    print(f"  Fetching : {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"  Status   : {resp.status_code} OK")
    return BeautifulSoup(resp.text, "lxml")

def find_table(section):
    for t in section.find_all("table"):
        if t.has_attr("data-responsive"):
            return t
    wrapper = section.find("div", class_="nhsd-m-table")
    if wrapper:
        t = wrapper.find("table")
        if t:
            return t
    return section.find("table")

def scrape(soup):
    all_items = []
    seen = set()

    for div_id in TARGET_IDS:
        section = soup.find("div", id=div_id)
        if not section:
            print(f"\n  WARNING: Section not found: {div_id}")
            continue

        heading = section.find(["h2", "h3"])
        label   = clean(heading.get_text()) if heading else div_id
        print(f"\n  SECTION : {label}")

        table = find_table(section)
        if not table:
            print("  WARNING : No table found")
            continue

        tbody = table.find("tbody")
        if not tbody:
            print("  WARNING : No tbody found")
            continue

        rows = tbody.find_all("tr")
        print(f"  ROWS    : {len(rows)}")

        count = 0
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            name_cell = cells[0]
            name_text = clean(name_cell.get_text(separator=" "))
            if not name_text:
                continue

            ref   = extract_ref(name_text)
            title = name_text

            a_tag = name_cell.find("a", href=True)
            if a_tag:
                href = a_tag.get("href", "")
                link = href if href.startswith("http") else BASE_URL + href
            else:
                link = ""

            dedup_key = ref if ref else title
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            date_raw  = cells[1].get_text(separator=" ") if len(cells) > 1 else ""
            date_iso  = format_date(date_raw)

            type_raw  = cells[2].get_text(separator=" ") if len(cells) > 2 else ""
            item_type = parse_type(type_raw)

            all_items.append({
                "ref"   : ref,
                "title" : title,
                "type"  : item_type,
                "status": "Approved",
                "date"  : date_iso,
                "link"  : link,
            })
            count += 1

        print(f"  ITEMS   : {count}")

    return all_items

def build_js_array(items):
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    rows = []
    for item in items:
        row = (
            '  { '
            f'ref:"{esc(item["ref"])}", '
            f'title:"{esc(item["title"])}", '
            f'type:"{esc(item["type"])}", '
            f'status:"{esc(item["status"])}", '
            f'date:"{esc(item["date"])}", '
            f'link:"{esc(item["link"])}"'
            ' }'
        )
        rows.append(row)
    return "[\n" + ",\n".join(rows) + "\n]"

def inject(items, html_path):
    if not os.path.exists(html_path):
        print(f"\n  ERROR: '{html_path}' not found.")
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    if "const DATA" not in html:
        print(f"\n  ERROR: 'const DATA' not found in {html_path}.")
        sys.exit(1)

    pattern     = re.compile(r"const\s+DATA\s*=\s*\[[\s\S]*?\]\s*;", re.MULTILINE)
    new_array   = build_js_array(items)
    replacement = f"const DATA = {new_array};"
    new_html    = pattern.sub(lambda m: replacement, html)

    if new_html == html:
        print("\n  ERROR: DATA array was not replaced.")
        sys.exit(1)

    print(f"  OK  DATA array replaced ({len(items)} items).")

    today    = datetime.now().strftime("%d %b %Y")
    new_html = re.sub(
        r"Last updated:[\s\w]+\d{4}",
        f"Last updated: {today}",
        new_html
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"  FILE SAVED: {os.path.abspath(html_path)}")

def main():
    preview = "--preview" in sys.argv

    print()
    print("=" * 65)
    print("  Altera ISN Tracker — NHS Digital Scraper")
    print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")
    print("=" * 65)

    print("\n  Fetching NHS Digital page...\n")
    soup = fetch(URL)

    print("\n  Parsing DAB approval sections...")
    items = scrape(soup)

    if not items:
        print("\n  ERROR: No items extracted.")
        sys.exit(1)

    print(f"\n  {'─' * 70}")
    print(f"  {'#':<4}  {'REF':<20}  {'DATE':<12}  {'TYPE':<24}  TITLE")
    print(f"  {'─'*4}  {'─'*20}  {'─'*12}  {'─'*24}  {'─'*22}")
    for i, item in enumerate(items, 1):
        ref_display = item["ref"] if item["ref"] else "(no ref)"
        print(
            f"  {i:<4}  {ref_display:<20}  {item['date']:<12}  "
            f"{item['type']:<24}  {item['title'][:28]}"
        )
    print(f"  {'─' * 70}")
    print(f"  TOTAL: {len(items)} items\n")

    if preview:
        print("  Preview mode — HTML was NOT changed.\n")
        return

    print(f"  Patching {HTML_FILE}...")
    inject(items, HTML_FILE)

    print()
    print("=" * 65)
    print(f"  DONE — open '{HTML_FILE}' in your browser")
    print("=" * 65)
    print()

if __name__ == "__main__":
    main()
