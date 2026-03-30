"""
Altera ISN Tracker - AI Summary Generator
Stores summaries in summaries.json (safe, never wiped)
Reads items from index.html, writes summaries to summaries.json,
then rebuilds index.html with summaries merged in.
"""

import re
import os
import sys
import json
import time
import requests
from datetime import datetime

API_KEY = ""

SUMMARIES_FILE = "summaries.json"
HTML_FILE = "index.html"
BASE_URL = "https://digital.nhs.uk"


def get_api_key():
    """Get API key from config or environment."""
    key = API_KEY.strip()
    if not key:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
    return key


def load_summaries():
    """Load existing summaries from summaries.json."""
    if os.path.exists(SUMMARIES_FILE):
        try:
            with open(SUMMARIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_summaries(data):
    """Save summaries to summaries.json."""
    with open(SUMMARIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_items_from_html():
    """Extract items from the DATA array in index.html."""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    data_match = re.search(
        r"(?:const|var)\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",
        html, re.MULTILINE
    )

    if not data_match:
        print("  ERROR: Could not find DATA array in index.html")
        sys.exit(1)

    items = []
    data_str = data_match.group(1)

    for obj in re.finditer(r"\{[^{}]+\}", data_str, re.DOTALL):
        o = obj.group(0)
        ref = get_field("ref", o)
        title = get_field("title", o)
        link = get_field("link", o)
        item_type = get_field("type", o)
        date = get_field("date", o)

        items.append({
            "ref": ref,
            "title": title,
            "link": link,
            "type": item_type,
            "date": date,
        })

    return items


def get_field(name, text):
    """Extract a field value from a JS object string."""
    m = re.search(rf'{name}:"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if not m:
        return ""
    val = m.group(1)
    val = val.replace('\\"', '"').replace("\\\\", "\\")
    return val


def fetch_detail_page(url):
    """Fetch a detail page and extract useful info."""
    if not url or not url.startswith("http"):
        return "", "", ""

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return "", "", ""

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")

        overview = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            overview = meta["content"].strip()

        if not overview:
            main_el = soup.find("main") or soup.find("article") or soup.body
            if main_el:
                for p in main_el.find_all("p"):
                    text = p.get_text(strip=True)
                    if len(text) > 60:
                        overview = text[:500]
                        break

        conformance = ""
        for el in soup.find_all(["p", "li", "td", "span"]):
            text = el.get_text(strip=True).lower()
            if "conformance" in text and ("date" in text or "20" in text):
                conformance = el.get_text(strip=True)[:200]
                break

        documents = ""
        doc_links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            atext = a.get_text(strip=True)
            if any(ext in href.lower() for ext in [".pdf", ".xlsx", ".docx", ".csv"]):
                full_url = href if href.startswith("http") else BASE_URL + href
                doc_links.append(f"{atext} | {full_url}")
        if doc_links:
            documents = " || ".join(doc_links[:5])

        return overview, conformance, documents

    except Exception:
        return "", "", ""


def generate_summary(api_key, ref, title, item_type, overview):
    """Call OpenAI to generate a structured summary."""
    prompt = f"""You are a health informatics analyst. Summarise this NHS information standard or data collection.

Reference: {ref}
Title: {title}
Type: {item_type}
Overview from NHS Digital: {overview if overview else 'Not available'}

Write a summary with these 4 sections (use these exact headings):
OVERVIEW: 2-3 sentences on what this standard/collection does.
WHO IT APPLIES TO: Which organisations or services must comply.
WHY IT MATTERS: The benefit to patients or the health system.
COMPLIANCE: Any known conformance dates or requirements.

Keep it under 150 words total. No bullet points. Plain English."""

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.3,
        }
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            print(f"    API error: {resp.status_code}")
            return ""
    except Exception as e:
        print(f"    API error: {e}")
        return ""


def esc(s):
    """Escape string for JavaScript."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "")
    return s


def rebuild_html(summaries_dict):
    """Rebuild the DATA array in index.html with summaries from summaries.json."""
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    data_match = re.search(
        r"(?:const|var)\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",
        html, re.MULTILINE,
    )
    if not data_match:
        print("  ERROR: Could not find DATA array in index.html")
        return

    items = []
    data_str = data_match.group(1)

    for obj in re.finditer(r"\{[^{}]+\}", data_str, re.DOTALL):
        o = obj.group(0)
        item = {}
        for field in ["ref", "title", "type", "status", "date", "link"]:
            item[field] = get_field(field, o)
        items.append(item)

    rows = []
    for item in items:
        key = item["ref"] if item["ref"] else item["title"]
        saved = summaries_dict.get(key, {})

        summary = saved.get("summary", "")
        conformance = saved.get("conformance", "")
        documents = saved.get("documents", "")

        rows.append(
            f'  {{ '
            f'ref:"{esc(item["ref"])}", '
            f'title:"{esc(item["title"])}", '
            f'type:"{esc(item["type"])}", '
            f'status:"{esc(item["status"])}", '
            f'date:"{esc(item["date"])}", '
            f'link:"{esc(item.get("link", ""))}", '
            f'conformance:"{esc(conformance)}", '
            f'documents:"{esc(documents)}", '
            f'summary:"{esc(summary)}" '
            f'}}'
        )

    new_array = "[\n" + ",\n".join(rows) + "\n]"

    pattern = re.compile(
        r"((?:const|var)\s+DATA\s*=\s*)\[[\s\S]*?\]\s*;",
        re.MULTILINE,
    )
    new_html, n = pattern.subn(rf"\g<1>{new_array};", html)

    if n == 0:
        print("  ERROR: Could not replace DATA array")
        return

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)

    with_summary = sum(1 for item in items if summaries_dict.get(item["ref"] if item["ref"] else item["title"], {}).get("summary"))
    print(f"  Rebuilt {HTML_FILE} with {len(items)} items, {with_summary} summaries.")


def main():
    """Main entry point."""
    auto = "--auto" in sys.argv

    print()
    print("=" * 60)
    print("  Altera ISN Tracker - AI Summary Generator")
    print(f"  {datetime.now().strftime('%d %B %Y  %H:%M')}")
    print("=" * 60)

    api_key = get_api_key()
    if not api_key:
        print("\n  No API key found.")
        print("  Set API_KEY in this file or set OPENAI_API_KEY environment variable.")
        sys.exit(1)

    print(f"\n  Reading {HTML_FILE}...")
    items = extract_items_from_html()
    print(f"  Found {len(items)} items.")

    print(f"\n  Loading {SUMMARIES_FILE}...")
    summaries_dict = load_summaries()
    print(f"  Existing summaries: {len(summaries_dict)}")

    need_processing = []
    for item in items:
        key = item["ref"] if item["ref"] else item["title"]
        saved = summaries_dict.get(key, {})
        if not saved.get("summary"):
            need_processing.append(item)

    already = len(items) - len(need_processing)
    print(f"\n  Already complete : {already}")
    print(f"  Need processing  : {len(need_processing)}")

    if not need_processing:
        print("\n  All items have summaries. Nothing to do.")
        rebuild_html(summaries_dict)
        return

    est_time = len(need_processing) * 4
    est_cost = len(need_processing) * 0.0004
    print(f"  Estimated time   : ~{est_time} seconds")
    print(f"  Estimated cost   : ~${est_cost:.4f} USD")

    if auto:
        confirm = "y"
    else:
        confirm = input("\n  Type y to proceed: ").strip().lower()

    if confirm != "y":
        print("  Cancelled.")
        return

    print()
    generated = 0
    errors = 0

    for i, item in enumerate(need_processing, 1):
        key = item["ref"] if item["ref"] else item["title"]
        label = f"{item['ref']} {item['title'][:40]}" if item['ref'] else item['title'][:50]
        print(f"  [{i}/{len(need_processing)}] {label}")

        overview, conformance, documents = fetch_detail_page(item.get("link", ""))
        time.sleep(1)

        summary = generate_summary(
            api_key,
            item["ref"],
            item["title"],
            item.get("type", ""),
            overview,
        )
        time.sleep(0.5)

        if summary:
            summaries_dict[key] = {
                "summary": summary,
                "conformance": conformance,
                "documents": documents,
            }
            save_summaries(summaries_dict)
            generated += 1
            print(f"    OK: {summary[:60]}...")
        else:
            errors += 1
            print(f"    FAILED")

    print(f"\n  Generated : {generated}")
    print(f"  Errors    : {errors}")
    print(f"  Total in {SUMMARIES_FILE} : {len(summaries_dict)}")

    print(f"\n  Rebuilding {HTML_FILE}...")
    rebuild_html(summaries_dict)

    print()
    print("=" * 60)
    print(f"  Done!")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
