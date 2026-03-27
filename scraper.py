"""

Altera ISN Tracker — NHS Digital Scraper with AI Summaries

============================================================

Scrapes NHS Digital for new items, preserves existing AI summaries,

and generates new summaries via OpenAI for any new items found.

Run locally:

set OPENAI_API_KEY=sk-your-key-here

python scraper.py

Run automatically via GitHub Actions every day at 07:00 UTC.

"""

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

SYSTEM_PROMPT = """You are a health informatics analyst at NHS England.

Write clear, concise, jargon-free summaries for a non-technical audience.

Keep responses to exactly 2-3 sentences and under 60 words.

Do not use bullet points or headers.

Cover: what the standard does, who it applies to, and why it matters."""

# ─────────────────────────────────────────────────────────────────

# HELPERS

# ─────────────────────────────────────────────────────────────────

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

def esc(s):

return s.replace("\\", "\\\\").replace('"', '\\"')

# ─────────────────────────────────────────────────────────────────

# STEP 1 — LOAD EXISTING SUMMARIES FROM index.html

# This is the key function that preserves all existing summaries

# so they are never lost or overwritten by the scraper

# ─────────────────────────────────────────────────────────────────

def load_existing_summaries():

"""

Reads the current index.html and extracts all ref -> summary

pairs that are already stored there.

These are used to:

1. Preserve summaries for existing items

2. Avoid calling OpenAI for items we already have summaries for

"""

existing = {}

if not os.path.exists(HTML_FILE):

print("  No existing index.html found — starting fresh")

return existing

with open(HTML_FILE, "r", encoding="utf-8") as f:

html = f.read()

data_match = re.search(

r"const\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",

html,

re.MULTILINE

)

if not data_match:

return existing

data_str = data_match.group(1)

ref_pat     = re.compile(r'ref:"([^"]*)"')

summary_pat = re.compile(r'summary:"((?:[^"\\]|\\.)*)"')

refs      = ref_pat.findall(data_str)

summaries = summary_pat.findall(data_str)

for ref, summary in zip(refs, summaries):

cleaned = summary.replace('\\"', '"').replace("\\\\", "\\").strip()

if ref and cleaned and len(cleaned) > 30:

existing[ref] = cleaned

print(f"  Loaded {len(existing)} existing summaries from {HTML_FILE}")

return existing

# ─────────────────────────────────────────────────────────────────

# STEP 2 — FETCH NHS DIGITAL PAGE

# Uses cloudscraper to bypass Cloudflare 403 blocking

# ─────────────────────────────────────────────────────────────────

def fetch(url):

try:

import cloudscraper

scraper = cloudscraper.create_scraper(

browser={

"browser":  "chrome",

"platform": "windows",

"desktop":  True,

}

)

print(f"  Fetching via cloudscraper: {url}")

resp = scraper.get(url, timeout=60)

print(f"  Status   : {resp.status_code}")

print(f"  HTML size: {len(resp.text):,} chars")

if resp.status_code == 200 and len(resp.text) > 50000:

return BeautifulSoup(resp.text, "lxml")

print(f"  cloudscraper returned small/bad response")

except Exception as e:

print(f"  cloudscraper failed: {e}")

# Fallback to plain requests

print("  Trying plain requests as fallback...")

headers = {

"User-Agent": (

"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

"AppleWebKit/537.36 (KHTML, like Gecko) "

"Chrome/123.0.0.0 Safari/537.36"

),

"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",

"Accept-Language": "en-GB,en;q=0.9",

}

resp = requests.get(url, headers=headers, timeout=60)

print(f"  Fallback status: {resp.status_code}")

print(f"  Fallback size  : {len(resp.text):,} chars")

if resp.status_code == 200:

return BeautifulSoup(resp.text, "lxml")

print(f"ERROR: Could not fetch NHS Digital page (status {resp.status_code})")

sys.exit(1)

# ─────────────────────────────────────────────────────────────────

# STEP 3 — SCRAPE ALL DAB SECTIONS

# Finds ALL divs with 'dab-approvals' in the id automatically

# so new sections (e.g. April 2026) are picked up without any

# changes to this script

# ─────────────────────────────────────────────────────────────────

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

sections = soup.find_all(

"div",

id=re.compile(r"dab-approvals", re.IGNORECASE)

)

print(f"\n  Found {len(sections)} DAB approval section(s)")

items = []

seen  = set()

for section in sections:

h     = section.find(["h2", "h3"])

label = clean(h.get_text()) if h else section.get("id", "?")

print(f"\n  SECTION: {label}")

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

key  = ref if ref else title

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

# ─────────────────────────────────────────────────────────────────

# STEP 4 — GENERATE AI SUMMARY FOR ONE ITEM

# ─────────────────────────────────────────────────────────────────

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

"content": SYSTEM_PROMPT

},

{

"role": "user",

"content": (

f"Summarise this NHS information standard.\n\n"

f"Reference: {ref}\n"

f"Name: {title}\n"

f"Type: {item_type}\n\n"

f"Write 2-3 plain English sentences covering "

f"what it does, who it applies to, and why it matters."

)

}

],

"max_tokens":  120,

"temperature": 0.3,

},

timeout=30

)

if resp.status_code == 200:

return resp.json()["choices"][0]["message"]["content"].strip()

print(f"    OpenAI error {resp.status_code}: {resp.text[:100]}")

return ""

except Exception as e:

print(f"    Summary generation error: {e}")

return ""

# ─────────────────────────────────────────────────────────────────

# STEP 5 — MERGE SCRAPED ITEMS WITH EXISTING SUMMARIES

# AND GENERATE SUMMARIES FOR NEW ITEMS ONLY

# This is the core logic that makes everything work together:

#

# For each scraped item:

#   - If it already has a summary → keep it (no API call)

#   - If it is a new item        → call OpenAI to generate one

# ─────────────────────────────────────────────────────────────────

def add_summaries(items, existing_summaries):

openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

if not openai_key:

print("\n  INFO: OPENAI_API_KEY not set.")

print("  Existing summaries will be preserved.")

print("  New items will have empty summaries until the key is added.")

for item in items:

item["summary"] = existing_summaries.get(item["ref"], "")

return items

total     = len(items)

new_count = 0

cached    = 0

for idx, item in enumerate(items, 1):

ref = item["ref"]

# ── Use existing summary if we have one ──────────────────

if ref in existing_summaries and existing_summaries[ref]:

item["summary"] = existing_summaries[ref]

cached += 1

print(f"  [{idx:>2}/{total}] {ref:<22} CACHED")

continue

# ── New item — generate summary via OpenAI ───────────────

print(f"  [{idx:>2}/{total}] {ref:<22} NEW — generating summary...")

summary = generate_summary(

ref, item["title"], item["type"], openai_key

)

item["summary"] = summary

new_count += 1

if summary:

preview = summary[:75] + "..." if len(summary) > 75 else summary

print(f"             {preview}")

else:

print(f"             WARNING: No summary generated")

# Small delay to respect OpenAI rate limits

time.sleep(0.5)

print(f"\n  Summaries: {new_count} new | {cached} from cache")

return items

# ─────────────────────────────────────────────────────────────────

# STEP 6 — BUILD JAVASCRIPT DATA ARRAY

# ─────────────────────────────────────────────────────────────────

def build_js_array(items):

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

# ─────────────────────────────────────────────────────────────────

# STEP 7 — INJECT UPDATED DATA INTO index.html

# ─────────────────────────────────────────────────────────────────

def inject(items, path):

if not os.path.exists(path):

print(f"ERROR: {path} not found")

sys.exit(1)

with open(path, "r", encoding="utf-8") as f:

html = f.read()

if "const DATA" not in html:

print(f"ERROR: const DATA not found in {path}")

sys.exit(1)

pat = re.compile(r"const\s+DATA\s*=\s*\[[\s\S]*?\]\s*;", re.MULTILINE)

rep = f"const DATA = {build_js_array(items)};"

new = pat.sub(lambda m: rep, html)

if new == html:

print("ERROR: DATA array was not replaced")

sys.exit(1)

today = datetime.now().strftime("%d %b %Y")

new   = re.sub(r"Last updated:[\s\w]+\d{4}", f"Last updated: {today}", new)

with open(path, "w", encoding="utf-8") as f:

f.write(new)

print(f"  SAVED: {os.path.abspath(path)}")

# ─────────────────────────────────────────────────────────────────

# MAIN

# ─────────────────────────────────────────────────────────────────

def main():

preview = "--preview" in sys.argv

print("\n" + "=" * 60)

print("  Altera ISN Tracker — Scraper + AI Summary Generator")

print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")

print("=" * 60 + "\n")

# Step 1 — Load existing summaries (cache)

print("  Loading existing summaries from index.html...")

existing_summaries = load_existing_summaries()

# Step 2 — Fetch NHS Digital page

print("\n  Fetching NHS Digital page...")

soup = fetch(TARGET_URL)

# Step 3 — Scrape items

print("\n  Parsing DAB approval sections...")

items = scrape(soup)

if not items:

print("\nERROR: No items scraped from NHS Digital")

sys.exit(1)

# Count new vs existing

new_items = [

i for i in items

if i["ref"] not in existing_summaries

]

print(f"\n  Total items scraped : {len(items)}")

print(f"  New items found     : {len(new_items)}")

print(f"  Existing summaries  : {len(existing_summaries)}")

# Step 4 — Add summaries

print("\n  Processing summaries...")

items = add_summaries(items, existing_summaries)

# Step 5 — Print results

print(f"\n  {'─' * 65}")

print(f"  {'#':<4}  {'REF':<22}  {'DATE':<12}  {'AI':<4}  TITLE")

print(f"  {'─'*4}  {'─'*22}  {'─'*12}  {'─'*4}  {'─'*25}")

for idx, item in enumerate(items, 1):

has = "Yes" if item["summary"] else "No "

print(

f"  {idx:<4}  {item['ref']:<22}  "

f"{item['date']:<12}  {has:<4}  {item['title'][:30]}"

)

print(f"  {'─' * 65}")

if preview:

print("\n  Preview only — HTML not changed")

return

# Step 6 — Inject into HTML

print(f"\n  Writing to {HTML_FILE}...")

inject(items, HTML_FILE)

print("\n" + "=" * 60)

print(f"  DONE — {HTML_FILE} updated with {len(items)} items")

items_with_summary = sum(1 for i in items if i["summary"])

print(f"  Items with AI summaries: {items_with_summary}/{len(items)}")

print("=" * 60 + "\n")

if __name__ == "__main__":

main()
