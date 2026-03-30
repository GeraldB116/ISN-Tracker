"""
Altera ISN Tracker - NHS Digital Scraper
Scrapes DAB approval tables from the NHS Digital latest-activity page.
"""

import re
import os
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime

URL = "https://digital.nhs.uk/data-and-information/information-standards/governance/latest-activity"
HTML_FILE = "index.html"
BASE_URL = "https://digital.nhs.uk"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

MONTH_MAP = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
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
    """Convert UK date string to ISO YYYY-MM-DD."""
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
    """Extract reference code from start of Name cell text."""
    m = REF_RE.match(name_text)
    if m:
        return m.group(1).upper().strip()
    return ""


def fetch(url):
    """Download a page and return BeautifulSoup object."""
    print(f"  Fetching : {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"  Status   : {resp.status_code} OK")
    return BeautifulSoup(resp.text, "lxml")


def scrape(soup):
    """Find all DAB approval sections and extract items from desktop tables."""
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
            print("     No desktop table found - skipping.")
            continue
        tbody = table.find("tbody")
        if not tbody:
            print("     Table has no tbody - skipping.")
            continue
        rows = tbody.find_all("tr")
        print(f"     Rows found : {len(rows)}")
        section_count = 0
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            name_cell = cells[0]
            name_text = clean(name_cell.get_text(separator=" "))
            if not name_text:
                continue
            ref = extract_ref(name_text)
            title = name_text
            dedup_key = ref if ref else title
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            date_text = cells[1].get_text(separator=" ") if len(cells) > 1 else ""
            date_iso = parse_date(date_text)
            type_text = cells[2].get_text(separator=" ") if len(cells) > 2 else ""
            item_type = parse_type(type_text)
            link_tag = name_cell.find("a", href=True)
            link = ""
            if link_tag:
                href = link_tag.get("href", "")
                if href.startswith("http"):
                    link = href
                elif href.startswith("/"):
                    link = BASE_URL + href
                else:
                    link = href
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
    """Escape a string for use inside a JavaScript double-quoted string."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "")
    return s


def build_js_array(items):
    """Build the JavaScript array literal for the DATA variable."""
    rows = []
    for item in items:
        row = (
            f'  {{ '
            f'ref:"{esc(item["ref"])}", '
            f'title:"{esc(item["title"])}", '
            f'type:"{esc(item["type"])}", '
            f'status:"{esc(item["status"])}", '
            f'date:"{esc(item["date"])}", '
            f'link:"{esc(item.get("link", ""))}", '
            f'conformance:"", '
            f'documents:"", '
            f'summary:"" '
            f'}}'
        )
        rows.append(row)
    return "[\n" + ",\n".join(rows) + "\n]"


def merge_with_existing(new_items, html_path):
    """Merge new scraped items with existing items that have summaries."""
    if not os.path.exists(html_path):
        return new_items
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    data_match = re.search(
        r"(?:const|var)\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",
        html,
        re.MULTILINE,
    )
    if not data_match:
        return new_items
    data_str = data_match.group(1)
    existing = {}
    for obj in re.finditer(r"\{[^{}]+\}", data_str, re.DOTALL):
        o = obj.group(0)
        m_ref = re.search(r'ref:"((?:[^"\\]|\\.)*)"', o, re.DOTALL)
        m_title = re.search(r'title:"((?:[^"\\]|\\.)*)"', o, re.DOTALL)
        m_summary = re.search(r'summary:"((?:[^"\\]|\\.)*)"', o, re.DOTALL)
        m_conform = re.search(r'conformance:"((?:[^"\\]|\\.)*)"', o, re.DOTALL)
        m_docs = re.search(r'documents:"((?:[^"\\]|\\.)*)"', o, re.DOTALL)
        ref_val = m_ref.group(1) if m_ref else ""
        title_val = m_title.group(1) if m_title else ""
        summary_val = m_summary.group(1) if m_summary else ""
        conform_val = m_conform.group(1) if m_conform else ""
        docs_val = m_docs.group(1) if m_docs else ""
        key = ref_val if ref_val else title_val
        if key and (summary_val or conform_val or docs_val):
            existing[key] = {
                "summary": summary_val,
                "conformance": conform_val,
                "documents": docs_val,
            }
    merged_count = 0
    for item in new_items:
        key = item["ref"] if item["ref"] else item["title"]
        if key in existing:
            item["summary"] = existing[key]["summary"]
            item["conformance"] = existing[key]["conformance"]
            item["documents"] = existing[key]["documents"]
            merged_count += 1
        else:
            item["summary"] = ""
            item["conformance"] = ""
            item["documents"] = ""
    print(f"  Merged {merged_count} existing summaries into new data.")
    return new_items


def build_js_array_full(items):
    """Build JS array including summary, conformance, documents fields."""
    rows = []
    for item in items:
        row = (
            f'  {{ '
            f'ref:"{esc(item["ref"])}", '
            f'title:"{esc(item["title"])}", '
            f'type:"{esc(item["type"])}", '
            f'status:"{esc(item["status"])}", '
            f'date:"{esc(item["date"])}", '
            f'link:"{esc(item.get("link", ""))}", '
            f'conformance:"{esc(item.get("conformance", ""))}", '
            f'documents:"{esc(item.get("documents", ""))}", '
            f'summary:"{esc(item.get("summary", ""))}" '
            f'}}'
        )
        rows.append(row)
    return "[\n" + ",\n".join(rows) + "\n]"


def inject(items, html_path):
    """Replace the DATA array in the HTML file with new items."""
    if not os.path.exists(html_path):
        print(f"\n  ERROR: '{html_path}' not found.")
        print(f"  Place scraper.py in the same folder as {html_path}")
        sys.exit(1)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    if "DATA" not in html:
        print(f"\n  ERROR: 'DATA' not found in {html_path}.")
        print("  Make sure you are using the correct HTML file.")
        sys.exit(1)
    pattern = re.compile(
        r"((?:const|var)\s+DATA\s*=\s*)\[[\s\S]*?\]\s*;",
        re.MULTILINE,
    )
    new_array = build_js_array_full(items)
    new_html, n = pattern.subn(rf"\g<1>{new_array};", html)
    if n == 0:
        print("\n  ERROR: Could not find and replace the DATA array.")
        sys.exit(1)
    today = datetime.now().strftime("%d %b %Y")
    new_html = re.sub(
        r"Last updated:[\s\w]+\d{4}",
        f"Last updated: {today}",
        new_html,
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"  Saved {html_path} with {len(items)} items.")


def main():
    """Main entry point."""
    preview = "--preview" in sys.argv
    print()
    print("=" * 60)
    print("  Altera ISN Tracker - NHS Digital Scraper")
    print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")
    print("=" * 60)
    print("\n  Fetching page...\n")
    soup = fetch(URL)
    print("\n  Scanning DAB approval sections...")
    items = scrape(soup)
    if not items:
        print("\n  No items were extracted.")
        print("  The page structure may have changed.")
        sys.exit(1)
    print(f"  Total unique items : {len(items)}")
    print()
    print(f"  {'#':<4}  {'REF':<20}  {'DATE':<12}  {'TYPE':<24}  TITLE")
    print(f"  {'='*4}  {'='*20}  {'='*12}  {'='*24}  {'='*25}")
    for i, item in enumerate(items, 1):
        ref_display = item["ref"] if item["ref"] else "(no ref)"
        print(
            f"  {i:<4}  {ref_display:<20}  {item['date']:<12}  "
            f"{item['type']:<24}  {item['title'][:35]}"
        )
    print()
    if preview:
        print("  Preview mode - HTML file was NOT changed.\n")
        return
    print("  Merging with existing summaries...")
    items = merge_with_existing(items, HTML_FILE)
    print(f"\n  Writing to {HTML_FILE}...")
    inject(items, HTML_FILE)
    has_summary = sum(1 for i in items if i.get("summary"))
    needs_summary = sum(1 for i in items if not i.get("summary"))
    print()
    print("=" * 60)
    print(f"  Done! {len(items)} items in {HTML_FILE}")
    print(f"  With summaries    : {has_summary}")
    print(f"  Needs summaries   : {needs_summary}")
    if needs_summary > 0:
        print(f"\n  Run: python generate_summaries.py")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
