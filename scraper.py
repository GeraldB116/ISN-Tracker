import re

import os

import sys

from bs4 import BeautifulSoup

from datetime import datetime

HTML_FILE = "index.html"

BASE_URL  = "https://digital.nhs.uk"

URL       = (

"https://digital.nhs.uk/data-and-information/information-standards"

"/governance/latest-activity"

)

TARGET_IDS = [

"data-assurance-board-dab-approvals-from-april-2025",

"data-assurance-board-dab-approvals-from-march-2024-march-2025",

]

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

return re.sub(r"\s+", " ", text).strip()

def format_date(raw):

raw = clean(raw)

m = re.match(r'^\d{1,2}\s+([A-Za-z]+)\s+(\d{4})', raw)

if m:

return f"{m.group(2)}-{MONTH_MAP.get(m.group(1).lower(), '01')}-01"

m = re.match(r'^([A-Za-z]+)\s+(\d{4})', raw)

if m:

return f"{m.group(2)}-{MONTH_MAP.get(m.group(1).lower(), '01')}-01"

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

def extract_ref(text):

m = REF_RE.match(text)

return m.group(1).upper().strip() if m else ""

def fetch(url):

"""

Use Playwright to launch a real Chromium browser.

This bypasses Cloudflare/WAF blocking that affects requests-based scrapers.

"""

from playwright.sync_api import sync_playwright

print(f"  Launching browser...")

with sync_playwright() as p:

browser = p.chromium.launch(headless=True)

context = browser.new_context(

user_agent=(

"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

"AppleWebKit/537.36 (KHTML, like Gecko) "

"Chrome/123.0.0.0 Safari/537.36"

),

locale="en-GB",

)

page = context.new_page()

print(f"  Navigating to: {url}")

page.goto(url, wait_until="networkidle", timeout=60000)

print(f"  Page loaded — extracting HTML...")

content = page.content()

browser.close()

return BeautifulSoup(content, "lxml")

def find_table(section):

for t in section.find_all("table"):

if t.has_attr("data-responsive"):

return t

w = section.find("div", class_="nhsd-m-table")

if w:

t = w.find("table")

if t:

return t

return section.find("table")

def scrape(soup):

items = []

seen  = set()

for div_id in TARGET_IDS:

section = soup.find("div", id=div_id)

if not section:

print(f"  WARNING: Section not found: {div_id}")

continue

h = section.find(["h2", "h3"])

print(f"\n  SECTION: {clean(h.get_text()) if h else div_id}")

table = find_table(section)

if not table:

print("  WARNING: No table found")

continue

tbody = table.find("tbody")

if not tbody:

print("  WARNING: No tbody found")

continue

rows = tbody.find_all("tr")

print(f"  ROWS   : {len(rows)}")

count = 0

for tr in rows:

cells = tr.find_all(["td", "th"])

if len(cells) < 2:

continue

nc   = cells[0]

name = clean(nc.get_text(separator=" "))

if not name:

continue

ref   = extract_ref(name)

title = name

a    = nc.find("a", href=True)

href = a.get("href", "") if a else ""

link = href if href.startswith("http") else BASE_URL + href

key = ref if ref else title

if key in seen:

continue

seen.add(key)

date_raw = cells[1].get_text(separator=" ") if len(cells) > 1 else ""

type_raw = cells[2].get_text(separator=" ") if len(cells) > 2 else ""

items.append({

"ref"   : ref,

"title" : title,

"type"  : parse_type(type_raw),

"status": "Approved",

"date"  : format_date(date_raw),

"link"  : link,

})

count += 1

print(f"  ITEMS  : {count}")

return items

def build_js_array(items):

def esc(s):

return s.replace("\\", "\\\\").replace('"', '\\"')

rows = []

for i in items:

rows.append(

f'  {{ ref:"{esc(i["ref"])}", title:"{esc(i["title"])}", '

f'type:"{esc(i["type"])}", status:"{esc(i["status"])}", '

f'date:"{esc(i["date"])}", link:"{esc(i["link"])}" }}'

)

return "[\n" + ",\n".join(rows) + "\n]"

def inject(items, path):

if not os.path.exists(path):

print(f"ERROR: {path} not found")

sys.exit(1)

with open(path, "r", encoding="utf-8") as f:

html = f.read()

if "const DATA" not in html:

print("ERROR: const DATA not found in HTML")

sys.exit(1)

pat = re.compile(r"const\s+DATA\s*=\s*\[[\s\S]*?\]\s*;", re.MULTILINE)

rep = f"const DATA = {build_js_array(items)};"

new = pat.sub(lambda m: rep, html)

if new == html:

print("ERROR: DATA array was not replaced")

sys.exit(1)

print(f"  OK: {len(items)} items injected")

today = datetime.now().strftime("%d %b %Y")

new   = re.sub(r"Last updated:[\s\w]+\d{4}", f"Last updated: {today}", new)

with open(path, "w", encoding="utf-8") as f:

f.write(new)

print(f"  SAVED: {os.path.abspath(path)}")

def main():

preview = "--preview" in sys.argv

print("\n" + "=" * 55)

print("  Altera ISN Tracker - NHS Digital Scraper")

print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")

print("=" * 55 + "\n")

soup  = fetch(URL)

items = scrape(soup)

if not items:

print("\nERROR: No items extracted")

sys.exit(1)

print(f"\n  TOTAL: {len(items)} items\n")

for idx, item in enumerate(items, 1):

print(

f"  {idx:<3}  {item['ref']:<20}  "

f"{item['date']:<12}  {item['title'][:40]}"

)

if preview:

print("\n  Preview only - HTML not changed")

return

inject(items, HTML_FILE)

print("\n" + "=" * 55)

print(f"  DONE - {HTML_FILE} updated")

print("=" * 55 + "\n")

if __name__ == "__main__":

main()
