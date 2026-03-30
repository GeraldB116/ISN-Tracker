import re
import os
import sys
import time
import json
import requests
from bs4 import BeautifulSoup

API_KEY = ""
HTML_FILE = "index.html"
MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are a senior health informatics analyst at NHS England.
Write clear structured summaries for a non-technical audience.
Use exactly these four section headers on their own lines with no extra text:
OVERVIEW
WHO IT APPLIES TO
WHY IT MATTERS
COMPLIANCE
Write 2-3 sentences under each header. Do not use bullet points."""


def get_api_key():
    """Get API key from file variable or environment."""
    key = API_KEY.strip()
    if not key:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("ERROR: No OpenAI API key found.")
        print("Paste your sk-... key into the API_KEY variable at the top,")
        print("or set the OPENAI_API_KEY environment variable.")
        sys.exit(1)
    return key


def create_session():
    """Create HTTP session for scraping detail pages."""
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
    except ImportError:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        return s


def read_html():
    """Read the HTML file."""
    if not os.path.exists(HTML_FILE):
        print(f"ERROR: {HTML_FILE} not found in this folder.")
        sys.exit(1)
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        return f.read()


def get_field(name, text):
    """Extract a field value from a JS object string."""
    m = re.search(rf'{name}:"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if not m:
        return ""
    val = m.group(1)
    val = val.replace('\\"', '"').replace("\\\\", "\\")
    return val


def extract_items(html):
    """Extract all items from the DATA array in the HTML."""
    data_match = re.search(
        r"(?:const|var)\s+DATA\s*=\s*\[([\s\S]*?)\]\s*;",
        html,
        re.MULTILINE,
    )
    if not data_match:
        print("ERROR: Could not find DATA array in index.html")
        sys.exit(1)
    data_str = data_match.group(1)
    items = []
    for obj in re.finditer(r"\{[^{}]+\}", data_str, re.DOTALL):
        o = obj.group(0)
        title = get_field("title", o)
        if not title:
            continue
        items.append({
            "ref": get_field("ref", o),
            "title": title,
            "type": get_field("type", o),
            "date": get_field("date", o),
            "link": get_field("link", o),
            "summary": get_field("summary", o),
            "conformance": get_field("conformance", o),
            "documents": get_field("documents", o),
        })
    print(f"  Found {len(items)} items.")
    return items


def scrape_page(url, session):
    """Visit an NHS Digital detail page and extract overview, conformance, documents."""
    result = {"overview_text": "", "conformance": "", "documents": []}
    if not url or url == "#" or "future.nhs.uk" in url:
        return result
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"    Page returned {resp.status_code}")
            return result
        soup = BeautifulSoup(resp.text, "lxml")
        parts = []
        main = soup.find("main") or soup.body
        for p in main.find_all("p", limit=8):
            text = p.get_text(separator=" ", strip=True).replace("\xa0", " ")
            if len(text) > 60:
                parts.append(text)
            if len(parts) >= 4:
                break
        result["overview_text"] = " ".join(parts)
        full_text = soup.get_text(separator=" ").replace("\xa0", " ")
        patterns = [
            r"[Cc]onformance\s+date[:\s]+([A-Za-z]+\s+\d{4})",
            r"[Ii]mplementation\s+date[:\s]+([A-Za-z]+\s+\d{4})",
            r"[Mm]andatory\s+from[:\s]+([A-Za-z]+\s+\d{4})",
            r"[Cc]ompliance\s+date[:\s]+([A-Za-z]+\s+\d{4})",
            r"[Ee]ffective\s+from[:\s]+([A-Za-z]+\s+\d{4})",
        ]
        for pat in patterns:
            m = re.search(pat, full_text)
            if m:
                result["conformance"] = m.group(1).strip()
                break
        if not result["conformance"]:
            for dt in soup.find_all("dt"):
                dt_text = dt.get_text(strip=True).lower()
                keywords = ["conformance", "implementation", "mandatory", "effective"]
                if any(k in dt_text for k in keywords):
                    dd = dt.find_next_sibling("dd")
                    if dd:
                        val = dd.get_text(strip=True).replace("\xa0", " ")
                        if re.search(r"\d{4}", val):
                            result["conformance"] = val
                            break
        documents = []
        seen_urls = set()
        for heading in soup.find_all(["h2", "h3", "h4"]):
            heading_text = heading.get_text(strip=True).lower()
            doc_keywords = ["key document", "publication", "guidance", "specification", "download"]
            if any(k in heading_text for k in doc_keywords):
                sibling = heading.find_next_sibling()
                attempts = 0
                while sibling and attempts < 10:
                    for a in sibling.find_all("a", href=True):
                        href = a.get("href", "").strip()
                        atitle = a.get_text(strip=True).replace("\xa0", " ")
                        if not href or not atitle or len(atitle) < 5:
                            continue
                        if href.startswith("/"):
                            href = "https://digital.nhs.uk" + href
                        if href in seen_urls:
                            continue
                        seen_urls.add(href)
                        if any(ext in href.lower() for ext in [".pdf", ".doc", ".docx", ".xlsx"]):
                            dtype = "pdf" if ".pdf" in href.lower() else "word"
                            documents.append({"title": atitle, "url": href, "type": dtype})
                        elif "digital.nhs.uk" in href and href != url:
                            documents.append({"title": atitle, "url": href, "type": "page"})
                    sibling = sibling.find_next_sibling()
                    attempts += 1
                if documents:
                    break
        if not documents:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "").strip()
                atitle = a.get_text(strip=True).replace("\xa0", " ")
                if not href or not atitle or len(atitle) < 5:
                    continue
                if href.startswith("/"):
                    href = "https://digital.nhs.uk" + href
                if href in seen_urls:
                    continue
                if any(ext in href.lower() for ext in [".pdf", ".doc", ".docx"]):
                    seen_urls.add(href)
                    dtype = "pdf" if ".pdf" in href.lower() else "word"
                    documents.append({"title": atitle, "url": href, "type": dtype})
        result["documents"] = documents[:10]
        print(f"    Overview   : {len(result['overview_text'])} chars")
        print(f"    Conformance: {result['conformance'] or 'not found'}")
        print(f"    Documents  : {len(result['documents'])} found")
    except Exception as e:
        print(f"    ERROR: {e}")
    return result


def generate_summary(client, item, page_data):
    """Call OpenAI to generate a structured summary."""
    context = [
        f"Reference: {item['ref']}",
        f"Full name: {item['title']}",
        f"Type: {item['type']}",
        f"Approval date: {item['date']}",
    ]
    if page_data["conformance"]:
        context.append(f"Conformance date: {page_data['conformance']}")
    if page_data["overview_text"]:
        context.append(f"\nOfficial description:\n{page_data['overview_text'][:1500]}")
    if page_data["documents"]:
        doc_list = "\n".join(f"- {d['title']}" for d in page_data["documents"][:6])
        context.append(f"\nKey documents:\n{doc_list}")
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(context)},
            ],
            max_tokens=400,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    OpenAI error: {e}")
        return ""


def process_items(items, client, session):
    """Process each item: scrape detail page and generate summary."""
    total = len(items)
    new_count = 0
    skip_count = 0
    error_count = 0
    for idx, item in enumerate(items, 1):
        ref = item["ref"] or "(no ref)"
        title = item["title"][:50]
        print(f"\n  [{idx:>2}/{total}] {ref}")
        print(f"           {title}")
        already_rich = (
            item["summary"]
            and len(item["summary"]) > 150
            and "OVERVIEW" in item["summary"]
        )
        if already_rich:
            print(f"           SKIP - already has rich summary")
            skip_count += 1
            continue
        print(f"           Visiting NHS Digital page...")
        page_data = scrape_page(item["link"], session)
        if page_data["conformance"] and not item["conformance"]:
            item["conformance"] = page_data["conformance"]
        if page_data["documents"]:
            item["documents"] = json.dumps(page_data["documents"])
        print(f"           Calling OpenAI...")
        summary = generate_summary(client, item, page_data)
        if summary:
            item["summary"] = summary
            new_count += 1
            preview = summary.replace("\n", " ")[:80]
            print(f"           OK: {preview}...")
        else:
            print(f"           WARNING: No summary generated")
            error_count += 1
        time.sleep(2.5)
    print(f"\n  Generated  : {new_count}")
    print(f"  Skipped    : {skip_count}")
    print(f"  Errors     : {error_count}")
    return items


def esc(s):
    """Escape string for JavaScript — includes newline escaping."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "")
    return s


def build_js_array(items):
    """Build the JavaScript DATA array string."""
    rows = []
    for i in items:
        docs = i.get("documents", "")
        if isinstance(docs, list):
            docs = json.dumps(docs)
        rows.append(
            f'  {{ '
            f'ref:"{esc(i["ref"])}", '
            f'title:"{esc(i["title"])}", '
            f'type:"{esc(i["type"])}", '
            f'status:"Approved", '
            f'date:"{esc(i["date"])}", '
            f'link:"{esc(i.get("link", ""))}", '
            f'conformance:"{esc(i.get("conformance", ""))}", '
            f'documents:"{esc(docs)}", '
            f'summary:"{esc(i["summary"])}" '
            f'}}'
        )
    return "[\n" + ",\n".join(rows) + "\n]"


def write_html(html, items):
    """Write updated DATA array back into the HTML file."""
    pattern = re.compile(
        r"(?:const|var)\s+DATA\s*=\s*\[[\s\S]*?\]\s*;",
        re.MULTILINE,
    )
    new_html = pattern.sub(
        lambda m: f"var DATA = {build_js_array(items)};",
        html,
    )
    if new_html == html:
        print("ERROR: Could not update DATA array in index.html")
        sys.exit(1)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(new_html)
    print(f"\n  SAVED: {os.path.abspath(HTML_FILE)}")


def main():
    """Main entry point."""
    print()
    print("=" * 55)
    print("  Altera ISN Tracker - AI Summary Generator")
    print("=" * 55)

    api_key = get_api_key()

    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    session = create_session()

    print(f"\n  Reading {HTML_FILE}...")
    html = read_html()
    items = extract_items(html)

    needs = [
        i for i in items
        if not i["summary"]
        or len(i["summary"]) < 150
        or "OVERVIEW" not in i["summary"]
    ]

    print(f"\n  Already complete : {len(items) - len(needs)}")
    print(f"  Need processing  : {len(needs)}")

    if not needs:
        print("\n  All items already have rich summaries.")
        print("  Re-writing HTML to fix any escaping issues...")
        write_html(html, items)
        return

    est_time = len(needs) * 4
    est_cost = len(needs) * 0.0004
    print(f"\n  Estimated time : ~{est_time} seconds (~{est_time // 60} minutes)")
    print(f"  Estimated cost : ~${est_cost:.4f} USD")
    print()

    if "--auto" in sys.argv:
        confirm = "y"
    else:
        confirm = input("  Type y to proceed: ").strip().lower()

    if confirm != "y":
        print("  Cancelled.")
        return

    print(f"\n  Starting...\n")
    items = process_items(items, client, session)

    print(f"\n  Writing to {HTML_FILE}...")
    write_html(html, items)

    print()
    print("=" * 55)
    print("  DONE!")
    print(f"  Upload {HTML_FILE} to GitHub to go live.")
    print("=" * 55)
    print()


if __name__ == "__main__":
    main()
