#!/usr/bin/env python3
"""
Crawler Sherpa — main.py
Layer 4 Operational Unit for Raj Sadan

Discovers new APIs, tools, and services from curated web sources
and imports them into the Knowledge Service.

Usage:
  python sherpa/crawler/main.py --discover
  python sherpa/crawler/main.py --discover --dry-run
  python sherpa/crawler/main.py --discover --report --verbose
  python sherpa/crawler/main.py --source https://example.com/tools
"""

import argparse
import base64
import json
import os
import re
import sys
import time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[crawler-sherpa] FATAL: Missing dependencies. Run: pip install -r sherpa/crawler/requirements.txt", file=sys.stderr)
    sys.exit(1)

# ─── CONFIG ────────────────────────────────────────────────────────────────

KNOWLEDGE_URL = os.environ.get("KNOWLEDGE_URL", "http://127.0.0.1:3484")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = "qwen2.5-coder:7b"
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")

SOURCES = {
    "public-apis": "https://api.github.com/repos/public-apis/public-apis/contents/README.md",
    "free-for-dev": "https://api.github.com/repos/ripienaar/free-for-dev/contents/README.md",
}

REQUEST_DELAY = 0.5  # seconds between requests to respect rate limits
LLM_DELAY = 0.3  # seconds between LLM calls
REQUEST_TIMEOUT = 30  # seconds

VERBOSE = False


# ─── LOGGING ───────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    print(f"[crawler-sherpa] [{level}] {msg}", file=sys.stderr)


def log_verbose(msg):
    if VERBOSE:
        log(msg, "DEBUG")


# ─── OLLAMA ────────────────────────────────────────────────────────────────

def ollama_available():
    """Check if Ollama is reachable."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def ollama_classify(name, url, description=""):
    """Use local LLM to classify a discovered tool."""
    system_prompt = """You are a tool classifier for a developer's capability registry.
Given a tool name, URL, and optional description, classify it.

Return ONLY valid JSON (no markdown, no explanation):
{
  "tier": "T1-API or T2-FETCH or T5-BROWSER or T6-REFERENCE",
  "category": "<one of: APIs and Web Services, AI Tools, Design and Assets, UI Components, Content Creation, Dev — Mobile, Dev — Flutter, Dev — Python and AI, Learning Platforms, Web Utilities, Automation and Workflow, MCP and Agent Infra, Diagramming and Visualization>",
  "description": "<one-line description, max 100 chars>"
}

Tier rules:
- T1-API: Has a REST/GraphQL API with endpoints you can call programmatically
- T2-FETCH: Data can be fetched/scraped from the URL (JSON feeds, RSS, static data)
- T5-BROWSER: Requires a browser to interact with (web apps, dashboards, visual tools)
- T6-REFERENCE: Documentation, guides, lists, or reference material only"""

    user_prompt = f"Name: {name}\nURL: {url}\nDescription: {description}"

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": user_prompt,
                "system": system_prompt,
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=60,
        )
        raw = resp.json().get("response", "").strip()

        # Extract JSON from response
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(raw[json_start:json_end])
            # Validate tier
            valid_tiers = ["T1-API", "T2-FETCH", "T3-MCP", "T4-CLI", "T5-BROWSER", "T6-REFERENCE"]
            if parsed.get("tier") not in valid_tiers:
                parsed["tier"] = "T6-REFERENCE"
            return parsed

        log_verbose(f"LLM returned non-JSON for {name}: {raw[:100]}")
        return None

    except Exception as e:
        log_verbose(f"LLM classification failed for {name}: {e}")
        return None


# ─── KNOWLEDGE SERVICE ─────────────────────────────────────────────────────

def knowledge_available():
    """Check if Knowledge Service is reachable."""
    try:
        resp = requests.get(f"{KNOWLEDGE_URL}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def get_existing_capabilities():
    """Fetch all existing capabilities from Knowledge Service."""
    try:
        resp = requests.get(f"{KNOWLEDGE_URL}/capabilities", timeout=REQUEST_TIMEOUT)
        data = resp.json()
        caps = data.get("capabilities", [])
        # Build a set of known URLs for deduplication
        urls = set()
        names = set()
        for cap in caps:
            if cap.get("url"):
                urls.add(cap["url"].rstrip("/").lower())
            if cap.get("name"):
                names.add(cap["name"].lower())
        log(f"Loaded {len(caps)} existing capabilities ({len(urls)} unique URLs)")
        return urls, names
    except Exception as e:
        log(f"Failed to fetch existing capabilities: {e}", "WARN")
        return set(), set()


def import_to_knowledge(links, dry_run=False):
    """Import links via POST /nodes/bulk in batches of 50."""
    if not links:
        return {"imported": 0, "failed": 0, "results": []}

    if dry_run:
        log(f"DRY RUN: Would import {len(links)} capabilities")
        return {"imported": len(links), "failed": 0, "results": [{"url": l["url"], "status": "dry_run"} for l in links]}

    BATCH_SIZE = 50
    total_imported = 0
    total_failed = 0
    all_results = []

    for i in range(0, len(links), BATCH_SIZE):
        batch = links[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(links) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"Importing batch {batch_num}/{total_batches} ({len(batch)} links)...")
        try:
            resp = requests.post(
                f"{KNOWLEDGE_URL}/nodes/bulk",
                json={"links": batch},
                timeout=300,
            )
            if resp.status_code == 200:
                result = resp.json()
                imported = result.get("imported", 0)
                failed = result.get("failed", 0)
                total_imported += imported
                total_failed += failed
                all_results.extend(result.get("results", []))
                log(f"  Batch {batch_num}: {imported} imported, {failed} failed")
            else:
                log(f"  Batch {batch_num}: HTTP {resp.status_code}", "ERROR")
                total_failed += len(batch)
        except Exception as e:
            log(f"  Batch {batch_num} failed: {e}", "ERROR")
            total_failed += len(batch)
        time.sleep(0.5)  # Brief pause between batches

    log(f"Bulk import complete: {total_imported} imported, {total_failed} failed")
    return {"imported": total_imported, "failed": total_failed, "results": all_results}


# ─── SOURCE PARSERS ───────────────────────────────────────────────────────

def fetch_github_readme(api_url, source_name):
    """Fetch a README from the GitHub Contents API and decode it."""
    log(f"Fetching {source_name} from GitHub...")
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        # Use a user agent to avoid rate limiting
        headers["User-Agent"] = "RajSadan-CrawlerSherpa/1.0"
        resp = requests.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)

        if resp.status_code == 403:
            log(f"GitHub rate limited for {source_name}", "WARN")
            return None
        if resp.status_code != 200:
            log(f"GitHub returned {resp.status_code} for {source_name}", "WARN")
            return None

        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content

    except Exception as e:
        log(f"Failed to fetch {source_name}: {e}", "ERROR")
        return None


def parse_public_apis(markdown):
    """Parse the public-apis README markdown table.
    Format: | API | Description | Auth | HTTPS | CORS | Link |
    or: | [Name](url) | Description | ... |
    """
    entries = []

    # Match markdown table rows with links
    # Pattern: | [Name](URL) | Description | Auth | HTTPS | CORS |
    pattern = re.compile(
        r'\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([^|]+)\|',
        re.MULTILINE
    )

    for match in pattern.finditer(markdown):
        name = match.group(1).strip()
        url = match.group(2).strip()
        description = match.group(3).strip()

        if not url.startswith("http"):
            continue

        entries.append({
            "name": name,
            "url": url,
            "description": description,
            "source": "public-apis",
        })

    log(f"Parsed {len(entries)} entries from public-apis")
    return entries


def parse_free_for_dev(markdown):
    """Parse the free-for-dev README.
    Entries are typically markdown links: [Name](url) - description
    or list items with links.
    """
    entries = []

    # Match list items with links: - [Name](URL) — description or similar
    pattern = re.compile(
        r'[-*]\s+\[([^\]]+)\]\(([^)]+)\)\s*[-–—:]*\s*(.*)',
        re.MULTILINE
    )

    for match in pattern.finditer(markdown):
        name = match.group(1).strip()
        url = match.group(2).strip()
        description = match.group(3).strip()

        if not url.startswith("http"):
            continue

        # Skip anchors and internal links
        if "#" in url and "github.com" not in url and "." not in url.split("#")[0]:
            continue

        entries.append({
            "name": name,
            "url": url,
            "description": description[:200],
            "source": "free-for-dev",
        })

    log(f"Parsed {len(entries)} entries from free-for-dev")
    return entries


def crawl_exa(query="new free APIs for developers 2025 2026"):
    """Use Exa API to search for new tools."""
    if not EXA_API_KEY:
        log_verbose("EXA_API_KEY not set, skipping Exa source")
        return []

    log("Searching via Exa API...")
    try:
        resp = requests.post(
            "https://api.exa.ai/search",
            json={
                "query": query,
                "num_results": 30,
                "use_autoprompt": True,
                "type": "neural",
            },
            headers={
                "x-api-key": EXA_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code != 200:
            log(f"Exa returned {resp.status_code}", "WARN")
            return []

        data = resp.json()
        results = data.get("results", [])
        entries = []

        for r in results:
            entries.append({
                "name": r.get("title", "Unknown"),
                "url": r.get("url", ""),
                "description": r.get("text", r.get("title", ""))[:200],
                "source": "exa",
            })

        log(f"Exa returned {len(entries)} results")
        return entries

    except Exception as e:
        log(f"Exa search failed: {e}", "WARN")
        return []


def crawl_source_url(url):
    """Crawl a specific URL and extract links that look like tools/APIs."""
    log(f"Crawling URL: {url}")
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "RajSadan-CrawlerSherpa/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            log(f"URL returned {resp.status_code}", "ERROR")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        links = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)

            # Only external links
            if not href.startswith("http"):
                continue
            # Skip common non-tool links
            skip_domains = ["twitter.com", "x.com", "facebook.com", "linkedin.com",
                            "youtube.com", "instagram.com", "reddit.com", "t.me",
                            "mailto:", "javascript:"]
            if any(d in href.lower() for d in skip_domains):
                continue

            if text and len(text) > 2:
                links.append({
                    "name": text[:100],
                    "url": href,
                    "description": "",
                    "source": url,
                })

        log(f"Extracted {len(links)} links from {url}")

        # Use LLM to filter which are actually tools/APIs
        if links and ollama_available():
            links = llm_filter_tools(links)

        return links

    except Exception as e:
        log(f"Failed to crawl {url}: {e}", "ERROR")
        return []


def llm_filter_tools(links):
    """Use LLM to identify which links are actual tools/APIs/services."""
    if not links:
        return []

    # Batch links for a single LLM call (max 50 at a time)
    batch_size = 50
    filtered = []

    for i in range(0, len(links), batch_size):
        batch = links[i:i + batch_size]
        link_list = "\n".join(
            f"{j+1}. {l['name']} — {l['url']}"
            for j, l in enumerate(batch)
        )

        system_prompt = """You are filtering a list of links to find developer tools, APIs, and services.
Return ONLY a JSON array of numbers representing the links that ARE tools, APIs, or services.
Exclude: blog posts, social media, news articles, company about pages, generic websites.
Include: APIs, SDKs, libraries, developer tools, SaaS services, data services, CLI tools.
Return ONLY the JSON array, e.g. [1, 3, 7]"""

        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": f"Which of these links are developer tools/APIs/services?\n\n{link_list}",
                    "system": system_prompt,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=60,
            )
            raw = resp.json().get("response", "").strip()

            # Extract JSON array
            arr_start = raw.find("[")
            arr_end = raw.rfind("]") + 1
            if arr_start >= 0 and arr_end > arr_start:
                indices = json.loads(raw[arr_start:arr_end])
                for idx in indices:
                    if isinstance(idx, int) and 1 <= idx <= len(batch):
                        filtered.append(batch[idx - 1])

            time.sleep(LLM_DELAY)

        except Exception as e:
            log_verbose(f"LLM filtering failed: {e}")
            # On failure, include all links from this batch
            filtered.extend(batch)

    log(f"LLM filter: {len(filtered)} tools from {len(links)} links")
    return filtered


# ─── DEDUPLICATION ─────────────────────────────────────────────────────────

def deduplicate(entries, existing_urls, existing_names):
    """Remove entries that already exist in the Knowledge Service."""
    new = []
    seen_urls = set()

    for entry in entries:
        url_normalized = entry["url"].rstrip("/").lower()

        # Skip if URL already in knowledge service
        if url_normalized in existing_urls:
            log_verbose(f"Skip (exists): {entry['name']} — {entry['url']}")
            continue

        # Skip if we've already seen this URL in this crawl
        if url_normalized in seen_urls:
            continue

        seen_urls.add(url_normalized)
        new.append(entry)

    log(f"Deduplication: {len(entries)} total, {len(entries) - len(new)} duplicates, {len(new)} new")
    return new


# ─── CLASSIFICATION ───────────────────────────────────────────────────────

def classify_entries(entries):
    """Classify new entries using local LLM."""
    if not ollama_available():
        log("Ollama not available — using fallback classification", "WARN")
        for entry in entries:
            entry["tier"] = "T1-API" if "api" in entry.get("description", "").lower() else "T6-REFERENCE"
            entry["category"] = "APIs and Web Services"
        return entries

    classified = []
    total = len(entries)

    for i, entry in enumerate(entries):
        if (i + 1) % 25 == 0 or i == 0:
            log(f"Classifying {i + 1}/{total}...")

        cls = ollama_classify(entry["name"], entry["url"], entry.get("description", ""))

        if cls:
            entry["tier"] = cls.get("tier", "T6-REFERENCE")
            entry["category"] = cls.get("category", "APIs and Web Services")
            if cls.get("description"):
                entry["description"] = cls["description"]
        else:
            # Fallback: guess from URL/description
            desc_lower = (entry.get("description", "") + " " + entry.get("url", "")).lower()
            if "api" in desc_lower or "rest" in desc_lower or "graphql" in desc_lower:
                entry["tier"] = "T1-API"
            elif "data" in desc_lower or "feed" in desc_lower or "json" in desc_lower:
                entry["tier"] = "T2-FETCH"
            else:
                entry["tier"] = "T6-REFERENCE"
            entry["category"] = "APIs and Web Services"

        classified.append(entry)
        time.sleep(LLM_DELAY)

    return classified


# ─── BUILD IMPORT PAYLOAD ──────────────────────────────────────────────────

def build_links_payload(entries):
    """Convert classified entries to the format expected by POST /nodes/bulk."""
    links = []
    for entry in entries:
        links.append({
            "url": entry["url"],
            "title": entry["name"],
            "tier": entry.get("tier"),
            "category": entry.get("category"),
            "description": entry.get("description", ""),
        })
    return links


# ─── REPORT ────────────────────────────────────────────────────────────────

def print_report(discovered, new_entries, import_result, sources_used, dry_run=False):
    """Print structured summary report."""
    total_discovered = len(discovered)
    total_new = len(new_entries)
    imported = import_result.get("imported", 0) if import_result else 0
    failed = import_result.get("failed", 0) if import_result else 0

    print("\n" + "=" * 60)
    print("  CRAWLER SHERPA — Discovery Report")
    print("=" * 60)
    print(f"  Sources crawled : {', '.join(sources_used)}")
    print(f"  Total discovered: {total_discovered}")
    print(f"  New (unique)    : {total_new}")
    if dry_run:
        print(f"  Would import    : {total_new}")
        print(f"  Mode            : DRY RUN (nothing imported)")
    else:
        print(f"  Imported        : {imported}")
        print(f"  Failed          : {failed}")
    print("=" * 60)

    # Tier breakdown of new entries
    if new_entries:
        tiers = {}
        for e in new_entries:
            t = e.get("tier", "unknown")
            tiers[t] = tiers.get(t, 0) + 1
        print("  Tier breakdown:")
        for t in sorted(tiers.keys()):
            print(f"    {t}: {tiers[t]}")
        print("-" * 60)

    # Source breakdown
    if discovered:
        sources = {}
        for e in discovered:
            s = e.get("source", "unknown")
            sources[s] = sources.get(s, 0) + 1
        print("  Source breakdown:")
        for s, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"    {s}: {count}")
        print("-" * 60)

    # Sample of new entries
    if new_entries:
        sample_size = min(10, len(new_entries))
        print(f"  Sample of new entries ({sample_size}/{total_new}):")
        for entry in new_entries[:sample_size]:
            tier = entry.get("tier", "?")
            print(f"    [{tier}] {entry['name']}")
            print(f"           {entry['url']}")
        print("=" * 60)

    print()
    return {
        "discovered": total_discovered,
        "new": total_new,
        "imported": imported,
        "failed": failed,
        "dry_run": dry_run,
    }


# ─── MODES ─────────────────────────────────────────────────────────────────

def mode_discover(dry_run=False, report_only=False):
    """Crawl all curated sources and import new capabilities."""
    sources_used = []
    all_entries = []

    # 1. public-apis
    readme = fetch_github_readme(SOURCES["public-apis"], "public-apis")
    if readme:
        entries = parse_public_apis(readme)
        all_entries.extend(entries)
        sources_used.append("public-apis")
    time.sleep(REQUEST_DELAY)

    # 2. free-for-dev
    readme = fetch_github_readme(SOURCES["free-for-dev"], "free-for-dev")
    if readme:
        entries = parse_free_for_dev(readme)
        all_entries.extend(entries)
        sources_used.append("free-for-dev")
    time.sleep(REQUEST_DELAY)

    # 3. Exa (if key available)
    if EXA_API_KEY:
        entries = crawl_exa()
        all_entries.extend(entries)
        if entries:
            sources_used.append("exa")
        time.sleep(REQUEST_DELAY)

    if not all_entries:
        log("No entries discovered from any source", "WARN")
        print_report([], [], None, sources_used, dry_run=dry_run or report_only)
        return

    log(f"Total raw entries: {len(all_entries)}")

    # 4. Deduplicate against knowledge service
    existing_urls, existing_names = get_existing_capabilities()
    new_entries = deduplicate(all_entries, existing_urls, existing_names)

    if not new_entries:
        log("All discovered entries already exist in Knowledge Service")
        print_report(all_entries, [], None, sources_used, dry_run=dry_run or report_only)
        return

    # 5. Classify via LLM
    log(f"Classifying {len(new_entries)} new entries via local LLM...")
    classified = classify_entries(new_entries)

    # 6. Import (unless dry-run or report-only)
    import_result = None
    if report_only or dry_run:
        import_result = {"imported": 0, "failed": 0}
    else:
        if not knowledge_available():
            log("Knowledge Service not available — cannot import", "ERROR")
            import_result = {"imported": 0, "failed": len(classified), "error": "Knowledge Service unavailable"}
        else:
            links = build_links_payload(classified)
            import_result = import_to_knowledge(links, dry_run=False)

    # 7. Report
    summary = print_report(all_entries, classified, import_result, sources_used, dry_run=dry_run or report_only)
    return summary


def mode_source(url, dry_run=False, report_only=False):
    """Crawl a specific URL and import discovered tools."""
    entries = crawl_source_url(url)

    if not entries:
        log(f"No tools found on {url}")
        print_report([], [], None, [url], dry_run=dry_run or report_only)
        return

    # Deduplicate
    existing_urls, existing_names = get_existing_capabilities()
    new_entries = deduplicate(entries, existing_urls, existing_names)

    if not new_entries:
        log("All discovered entries already exist")
        print_report(entries, [], None, [url], dry_run=dry_run or report_only)
        return

    # Classify
    log(f"Classifying {len(new_entries)} new entries...")
    classified = classify_entries(new_entries)

    # Import
    import_result = None
    if report_only or dry_run:
        import_result = {"imported": 0, "failed": 0}
    else:
        if not knowledge_available():
            log("Knowledge Service not available", "ERROR")
            import_result = {"imported": 0, "failed": len(classified)}
        else:
            links = build_links_payload(classified)
            import_result = import_to_knowledge(links, dry_run=False)

    summary = print_report(entries, classified, import_result, [url], dry_run=dry_run or report_only)
    return summary


# ─── MAIN ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Crawler Sherpa — Discover and import new tools/APIs into Knowledge Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sherpa/crawler/main.py --discover
  python sherpa/crawler/main.py --discover --dry-run
  python sherpa/crawler/main.py --discover --report --verbose
  python sherpa/crawler/main.py --source https://github.com/awesome-list/tools
        """,
    )

    parser.add_argument("--discover", action="store_true", help="Crawl all curated sources")
    parser.add_argument("--source", type=str, help="Crawl a specific URL")
    parser.add_argument("--report", action="store_true", help="Generate report without importing (dry run)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without importing")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    if not args.discover and not args.source:
        parser.print_help()
        sys.exit(1)

    # Preflight checks
    log("Crawler Sherpa starting...")

    if not knowledge_available():
        log("Knowledge Service at {0} is not reachable".format(KNOWLEDGE_URL), "WARN")
        if not args.dry_run and not args.report:
            log("Use --dry-run or --report to proceed without Knowledge Service", "ERROR")
            sys.exit(1)

    if not ollama_available():
        log(f"Ollama at {OLLAMA_URL} is not reachable — classification will use fallback heuristics", "WARN")

    # Execute mode
    if args.discover:
        summary = mode_discover(dry_run=args.dry_run, report_only=args.report)
    elif args.source:
        summary = mode_source(args.source, dry_run=args.dry_run, report_only=args.report)

    # Exit code
    if summary and summary.get("imported", 0) > 0:
        sys.exit(0)
    elif summary and summary.get("new", 0) == 0:
        sys.exit(0)  # Nothing new is not an error
    elif args.dry_run or args.report:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
