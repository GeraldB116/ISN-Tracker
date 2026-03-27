import re

import os

import sys

import time

import requests

from bs4 import BeautifulSoup

from datetime import datetime

HTML_FILE  = "index.html"

BASE_URL   = "https://digital.nhs.uk"

TARGET_URL = (

"https://digital.nhs.uk/data-and-information/"

"information-standards/governance/latest-activity"

)

MONTH_MAP = {

"january": "01", "february": "02", "march":     "03",

"april":   "04", "may":      "05", "june":      "06",

"july":    "07", "august":   "08", "september": "09",

"october": "10", "november": "11", "december":  "12",

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

def load_existing_summaries():

existing = {}

if not os.path.exists(HTML_FILE):

return existing

with open(HTML_FILE, "r", encoding="utf-8") as f:

html = f.read()

pat = re.compile(r"const\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;", re.MULTILINE)

m = pat.search(html)

if not m:

return existing

data_str = m.group(1)

ref_pat     = re.compile(r'ref:"([^"]*)"')

summary_pat = re.compile(r'summary:"((?:[^"\\]|\\.)*)"')

refs      = ref_pat.findall(data_str)

summaries = summary_pat.findall(data_str)

for ref, summary in zip(refs, summaries):

if ref and summary.strip():

existing[ref] = summary.replace('\\"', '"').replace("\\\\", "\\")

print(f"  Loaded {len(existing)} existing summaries")

return existing

def fetch(url):

import cloudscraper

print(f"  Fetching: {url}")

scraper = cloudscraper.create_scraper(

browser={

"browser":  "chrome",

"platform": "windows",

"desktop":  True,

}

)

resp = scraper.get(url, timeout=60)

print(f"  Status   : {resp.status_code}")

print(f"  HTML size: {len(resp.text):,} chars")

if resp.status_code != 200:

print(f"  ERROR: {resp.status_code}")

sys.exit(1)

if len(resp.text) < 50000:

print(f"  WARNING: Small response — may be a block page")

print(f"  First 300 chars: {resp.text[:300]}")

return BeautifulSoup(resp.text, "lxml")

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

"""

KEY CHANGE: Instead of hardcoding section IDs, we find ALL divs

whose id contains 'dab-approvals' — this automatically picks up

new sections added for future time periods (e.g. April 2026).

"""

# Find ALL DAB approval sections dynamically

# This regex matches any div id containing 'dab-approvals'

# e.g. data-assurance-board-dab-approvals-from-april-2025

#      data-assurance-board-dab-approvals-from-april-2026  <- auto detected

#      data-assurance-board-dab-approvals-from-march-2024-march-2025

all_sections = soup.find_all(

"div",

id=re.compile(r"dab-approvals", re.IGNORECASE)

)

print(f"\n  Found {len(all_sections)} DAB approval section(s) on the page:")

items = []

seen  = set()

for section in all_sections:

h = section.find(["h2", "h3"])

label = clean(h.get_text()) if h else section.get("id", "unknown")

print(f"\n  SECTION: {label}")

table = find_table(section)

if not table:

print("  WARNING: No table found — skipping")

continue

tbody = table.find("tbody")

if not tbody:

print("  WARNING: No tbody — skipping")

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

"ref"    : ref,

"title"  : title,

"type"   : parse_type(type_raw),

"status" : "Approved",

"date"   : format_date(date_raw),

"link"   : link,

"summary": "",

})

count += 1

print(f"  ITEMS  : {count}")

return items

def generate_summary(ref, title, item_type, api_key):

try:

resp = requests.post(

"https://api.openai.com/v1/chat/completions",

headers={

"Authorization": f"Bearer {api_key}",

"Content-Type":  "application/json",

},

json={

"model": "gpt-4o-mini",

"messages": [

{

"role": "system",

"content": (

"You are a health informatics analyst at the NHS. "

"Write clear, jargon-free 2-3 sentence summaries "

"for a non-technical audience. Keep under 60 words. "

"Do not use bullet points."

)

},

{

"role": "user",

"content": (

f"Summarise this NHS information standard in plain English.\n\n"

f"Reference: {ref}\n"

f"Name: {title}\n"

f"Type: {item_type}\n\n"

f"Cover: what it does, who it applies to, and why it matters."

)

}

],

"max_tokens": 150,

"temperature": 0.3,

},

timeout=30

)

if resp.status_code == 200:

return resp.json()["choices"][0]["message"]["content"].strip()

print(f"    OpenAI error {resp.status_code}")

return ""

except Exception as e:

print(f"    Summary error: {e}")

return ""

def add_summaries(items, existing_summaries):

openai_key = os.environ.get("OPENAI_API_KEY", "")

if not openai_key:

print("\n  INFO: OPENAI_API_KEY not set — using cached summaries only")

for item in items:

item["summary"] = existing_summaries.get(item["ref"], "")

return items

total     = len(items)

new_count = 0

for idx, item in enumerate(items, 1):

ref = item["ref"]

if ref in existing_summaries and existing_summaries[ref]:

item["summary"] = existing_summaries[ref]

print(f"  [{idx}/{total}] {ref:<20} — cached")

else:

print(f"  [{idx}/{total}] {ref:<20} — generating new summary...")

summary = generate_summary(ref, item["title"], item["type"], openai_key)

item["summary"] = summary

new_count += 1

time.sleep(0.5)

print(f"\n  Summaries: {new_count} new | {total - new_count} from cache")

return items

def build_js_array(items):

def esc(s):

return s.replace("\\", "\\\\").replace('"', '\\"')

rows = []

for i in items:

rows.append(

f'  {{ '

f'ref:"{esc(i["ref"])}", '

f'title:"{esc(i["title"])}", '

f'type:"{esc(i["type"])}", '

f'status:"{esc(i["status"])}", '

f'date:"{esc(i["date"])}", '

f'link:"{esc(i["link"])}", '

f'summary:"{esc(i["summary"])}" '

f'}}'

)

return "[\n" + ",\n".join(rows) + "\n]"

def inject(items, path):

if not os.path.exists(path):

print(f"ERROR: {path} not found")

sys.exit(1)

with open(path, "r", encoding="utf-8") as f:

html = f.read()

if "const DATA" not in html:

print("ERROR: const DATA not found")

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

print("  Loading existing summaries...")

existing_summaries = load_existing_summaries()

print("\n  Fetching NHS Digital page...")

soup = fetch(TARGET_URL)

print("\n  Scanning for DAB approval sections...")

items = scrape(soup)

if not items:

print("\nERROR: No items extracted")

sys.exit(1)

print(f"\n  TOTAL: {len(items)} items found")

print("\n  Processing AI summaries...")

items = add_summaries(items, existing_summaries)

print(f"\n  {'─' * 70}")

print(f"  {'#':<4}  {'REF':<20}  {'DATE':<12}  {'AI':<5}  TITLE")

print(f"  {'─'*4}  {'─'*20}  {'─'*12}  {'─'*5}  {'─'*25}")

for idx, item in enumerate(items, 1):

has = "Yes" if item["summary"] else "No"

print(

f"  {idx:<4}  {item['ref']:<20}  "

f"{item['date']:<12}  {has:<5}  {item['title'][:30]}"

)

print(f"  {'─' * 70}")

if preview:

print("\n  Preview only — HTML not changed")

return

inject(items, HTML_FILE)

print("\n" + "=" * 55)

print(f"  DONE — {HTML_FILE} updated")

print("=" * 55 + "\n")

if __name__ == "__main__":

main()
