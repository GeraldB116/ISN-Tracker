"""
Altera ISN Tracker — NHS Digital Scraper
=========================================
Targets the actual confirmed HTML file (attached).

KEY FIX: Uses a lambda replacement in re.sub so backslashes
         inside the DATA array are never misinterpreted.

Run:
    python scraper.py            <- scrape + update HTML
    python scraper.py --preview  <- print results, no file change
"""

import re
import os
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

URL       = "https://digital.nhs.uk/data-and-information/information-standards/governance/latest-activity"
HTML_FILE = "altera_isn_tracker.html"
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

REF_RE = re.compile(
    r'^((?:DAPB|DCB|SCCI|ISB|ISN)\d+[\w\-]*)',
    re.IGNORECASE
)

# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def clean(text):
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def format_date(raw):
    """
    Return "Month YYYY" e.g. "January 2026".
    Strips &nbsp; and drops day number if present.
    """
    raw = clean(raw)
    if not raw:
        return ""
    # "15 March 2025" -> "March 2025"
    m = re.match(r'^\d{1,2}\s+([A-Za-z]+\s+\d{4})', raw)
    if m:
        return m.group(1)
    # "March 2025" -> "March 2025"
    m = re.match(r'^([A-Za-z]+\s+\d{4})', raw)
    if m:
        return m.group(1)
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

# ─────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────

def fetch(url):
    print(f"  Fetching : {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"  Status   : {resp.status_code} OK")
    return BeautifulSoup(resp.text, "lxml")

# ─────────────────────────────────────────────────────────────────
# FIND TABLE
# ─────────────────────────────────────────────────────────────────

def find_table(section):
    """
    Three strategies to find the data table.
    Strategy 1: table with data-responsive attribute (confirmed live page)
    Strategy 2: first table inside nhsd-m-table wrapper div
    Strategy 3: any table in the section
    """
    for t in section.find_all("table"):
        if t.has_attr("data-responsive"):
            return t
    wrapper = section.find("div", class_="nhsd-m-table")
    if wrapper:
        t = wrapper.find("table")
        if t:
            return t
    return section.find("table")

# ─────────────────────────────────────────────────────────────────
# SCRAPE
# ─────────────────────────────────────────────────────────────────

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
            print("  WARNING : No table found in this section")
            continue

        tbody = table.find("tbody")
        if not tbody:
            print("  WARNING : Table has no tbody")
            continue

        rows = tbody.find_all("tr")
        print(f"  ROWS    : {len(rows)}")

        count = 0
        for tr in rows:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            name_text = clean(cells[0].get_text(separator=" "))
            if not name_text:
                continue

            ref   = extract_ref(name_text)
            title = name_text

            dedup_key = ref if ref else title
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            date_raw  = cells[1].get_text(separator=" ") if len(cells) > 1 else ""
            date_str  = format_date(date_raw)

            type_raw  = cells[2].get_text(separator=" ") if len(cells) > 2 else ""
            item_type = parse_type(type_raw)

            all_items.append({
                "ref"   : ref,
                "title" : title,
                "type"  : item_type,
                "status": "Approved",
                "date"  : date_str,
            })
            count += 1

        print(f"  ITEMS   : {count}")

    return all_items

# ─────────────────────────────────────────────────────────────────
# BUILD JAVASCRIPT DATA ARRAY
# ─────────────────────────────────────────────────────────────────

def build_js_array(items):
    """
    Builds the const DATA = [...] literal.
    Matches the exact object shape in the HTML:
    { ref:"...", title:"...", type:"...", status:"...", date:"..." }
    """
    def esc(s):
        # Escape backslashes first, then double quotes
        return s.replace("\\", "\\\\").replace('"', '\\"')

    rows = []
    for item in items:
        row = (
            '  { '
            f'ref:"{esc(item["ref"])}", '
            f'title:"{esc(item["title"])}", '
            f'type:"{esc(item["type"])}", '
            f'status:"{esc(item["status"])}", '
            f'date:"{esc(item["date"])}"'
            ' }'
        )
        rows.append(row)

    return "[\n" + ",\n".join(rows) + "\n]"

# ─────────────────────────────────────────────────────────────────
# PATCH HTML
#
# Four targeted changes to altera_isn_tracker.html:
#
# 1. Replace const DATA = [...]; with live scraped data
#    FIX: uses a lambda so backslashes are never misinterpreted
#
# 2. Patch fmtDate() — your HTML uses new Date(iso) which expects
#    ISO "2026-01-01". Dates are now "January 2026" strings.
#    Replace the whole function with: function fmtDate(d){return d;}
#
# 3. Patch year filter — your HTML uses .startsWith(yearF) which
#    works on "2026-01-01" but NOT on "January 2026".
#    Change to .includes(yearF) so "January 2026".includes("2026")
#    returns true correctly.
#
# 4. Update "Last updated" timestamp in the top bar
# ─────────────────────────────────────────────────────────────────

def inject(items, html_path):

    if not os.path.exists(html_path):
        print(f"\n  ERROR: '{html_path}' not found.")
        print(f"  Place scraper.py in the same folder as {html_path}")
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    if "const DATA" not in html:
        print(f"\n  ERROR: 'const DATA' not found in {html_path}.")
        print("  Make sure you are using the correct HTML file.")
        sys.exit(1)

    # ── 1. Replace const DATA = [...]; ──────────────────────────
    # CRITICAL: use a lambda replacement, NOT an rf"..." string.
    # rf"..." replacement strings interpret \g, \1, \n etc which
    # corrupts any backslashes inside the new data array.
    pattern_data = re.compile(
        r"const\s+DATA\s*=\s*\[[\s\S]*?\]\s*;",
        re.MULTILINE
    )

    new_array    = build_js_array(items)
    replacement  = f"const DATA = {new_array};"

    # Lambda prevents re.sub from interpreting backslashes
    new_html = pattern_data.sub(lambda m: replacement, html)

    if new_html == html:
        print("\n  ERROR: DATA array was not replaced.")
        print("  Check the HTML file still contains: const DATA = [")
        sys.exit(1)

    print(f"  OK  DATA array replaced ({len(items)} items).")

    # ── 2. Patch fmtDate() ──────────────────────────────────────
    # Original in your HTML:
    #   function fmtDate(iso) {
    #     const d = new Date(iso);
    #     return d.toLocaleDateString("en-GB", { ... });
    #   }
    # Replacement:
    #   function fmtDate(d) { return d; }
    pattern_fmt = re.compile(
        r'function\s+fmtDate\s*\(\s*\w+\s*\)\s*\{[^}]*\}',
        re.DOTALL
    )

    new_html_fmt = pattern_fmt.sub(
        lambda m: "function fmtDate(d) { return d; }",
        new_html
    )

    if new_html_fmt != new_html:
        new_html = new_html_fmt
        print("  OK  fmtDate() patched to return 'Month YYYY' strings.")
    else:
        # fmtDate may span multiple lines — try broader match
        pattern_fmt2 = re.compile(
            r'function\s+fmtDate\s*\([^)]*\)\s*\{.*?\}',
            re.DOTALL
        )
        new_html_fmt2 = pattern_fmt2.sub(
            lambda m: "function fmtDate(d) { return d; }",
            new_html
        )
        if new_html_fmt2 != new_html:
            new_html = new_html_fmt2
            print("  OK  fmtDate() patched (broad match).")
        else:
            print("  NOTE: fmtDate() not found — inserting manual patch.")
            # Insert a redefinition right after the DATA array
            new_html = new_html.replace(
                "const DATA =",
                "function fmtDate(d) { return d; }\nconst DATA =",
                1
            )

    # ── 3. Patch year filter ─────────────────────────────────────
    # Your HTML has: !item.date.startsWith(yearF)
    # Change to:     !item.date.includes(yearF)
    if "item.date.startsWith(yearF)" in new_html:
        new_html = new_html.replace(
            "!item.date.startsWith(yearF)",
            "!item.date.includes(yearF)"
        )
        print("  OK  Year filter: .startsWith() replaced with .includes().")
    else:
        print("  NOTE: Year filter already uses .includes() or not found.")

    # ── 4. Update timestamp ──────────────────────────────────────
    today    = datetime.now().strftime("%d %b %Y")
    new_html = re.sub(
        r"Last updated:[\s\w]+\d{4}",
        f"Last updated: {today}",
        new_html
    )
    print(f"  OK  Timestamp updated to {today}.")

    # ── Write back ───────────────────────────────────────────────
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"\n  FILE SAVED: {os.path.abspath(html_path)}")

# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    preview = "--preview" in sys.argv

    print()
    print("=" * 65)
    print("  Altera ISN Tracker — NHS Digital Scraper")
    print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")
    print("=" * 65)

    # 1. Fetch
    print("\n  Fetching NHS Digital page...")
    soup = fetch(URL)

    # 2. Scrape
    print("\n  Parsing DAB approval sections...")
    items = scrape(soup)

    if not items:
        print("\n  ERROR: No items extracted.")
        print("  Check the section IDs still exist on the NHS page:")
        for tid in TARGET_IDS:
            print(f"    {tid}")
        sys.exit(1)

    # 3. Print results table
    print()
    print(f"  {'─' * 72}")
    print(f"  {'#':<4}  {'REF':<20}  {'DATE':<16}  {'TYPE':<24}  TITLE")
    print(f"  {'─'*4}  {'─'*20}  {'─'*16}  {'─'*24}  {'─'*22}")

    for i, item in enumerate(items, 1):
        ref_display = item["ref"] if item["ref"] else "(no ref)"
        print(
            f"  {i:<4}  {ref_display:<20}  {item['date']:<16}  "
            f"{item['type']:<24}  {item['title'][:28]}"
        )

    print(f"  {'─' * 72}")
    print(f"  TOTAL: {len(items)} items")
    print(f"  {'─' * 72}")
    print()

    # 4. Preview — stop without touching HTML
    if preview:
        print("  Preview mode — HTML was NOT changed.")
        print()
        return

    # 5. Inject
    print(f"  Patching {HTML_FILE}...")
    print()
    inject(items, HTML_FILE)

    print()
    print("=" * 65)
    print(f"  DONE — open '{HTML_FILE}' in your browser")
    print("=" * 65)
    print()


if __name__ == "__main__":
    main()