#!/usr/bin/env python3

import os
import sys
import yaml
import requests
from datetime import datetime
import urllib.parse
import time

def log(msg: str):
    """Timestamped log output (flushes immediately)."""
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)


# -------------------------------------------------
# Define Crossref search tools
# -------------------------------------------------


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_MAILTO = "carlossmwolff@gmail.com"  # optional but recommended
CROSSREF_ROWS = 3                    # how many candidates to consider
CROSSREF_SLEEP = 0.2                 # be polite; avoids hammering
TITLE_MATCH_THRESHOLD = 0.85         # 0..1, higher = stricter

def crossref_lookup_doi(title: str, year: str | None = None, author_hint: str | None = None) -> str | None:
    """Return a DOI string if we find a good match in Crossref, else None."""
    if not title:
        return None

    # Crossref recommends identifying your tool via User-Agent/mailto
    headers = {
        "User-Agent": f"al-folio-doi-lookup/1.0 (mailto:{CROSSREF_MAILTO})"
    }

    # A simple bibliographic query works well in practice
    query = title
    if author_hint:
        query = f"{title} {author_hint}"

    params = {
        "query.bibliographic": query,
        "rows": CROSSREF_ROWS,
    }

    time.sleep(CROSSREF_SLEEP)
    r = requests.get(CROSSREF_WORKS_URL, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", []) or []
    if not items:
        return None

    # Lightweight title similarity (no extra deps)
    def norm(s: str) -> str:
        return "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in (s or "")).split()

    def sim(a: str, b: str) -> float:
        a_set, b_set = set(norm(a)), set(norm(b))
        if not a_set or not b_set:
            return 0.0
        return len(a_set & b_set) / len(a_set | b_set)

    best = None
    best_score = -1.0

    for it in items:
        cr_title = (it.get("title") or [""])[0]
        doi = it.get("DOI")
        if not doi:
            continue

        score = sim(title, cr_title)

        # Optional small year check: penalize if year disagrees
        if year:
            try:
                y = int(str(year))
                issued = it.get("issued", {}).get("date-parts", [[None]])[0][0]
                if issued and issued != y:
                    score -= 0.05
            except Exception:
                pass

        if score > best_score:
            best_score = score
            best = doi

    if best and best_score >= TITLE_MATCH_THRESHOLD:
        return best

    return None


# -------------------------------------------------
# Configuration
# -------------------------------------------------
AUTHOR_ID = "-VPPZ8YAAAAJ"   # your Google Scholar user id
OUTPUT_FILE = "_data/citations.yml"
API_KEY = os.getenv("SERPAPI_API_KEY")
METRICS_FILE = "_data/scholar_metrics.yml"


log("Script started")

if not API_KEY:
    log("ERROR: SERPAPI_API_KEY environment variable is not set")
    sys.exit(1)

log("SERPAPI_API_KEY detected")


# -------------------------------------------------
# Build request
# -------------------------------------------------
url = "https://serpapi.com/search.json"
params = {
    "engine": "google_scholar_author",
    "author_id": AUTHOR_ID,
    "api_key": API_KEY,
}

log(f"Prepared SerpAPI request for author_id={AUTHOR_ID}")
log(f"Request URL: {url}")


# -------------------------------------------------
# Call SerpAPI
# -------------------------------------------------
try:
    log("Sending request to SerpAPI...")
    response = requests.get(url, params=params, timeout=30)
    log(f"HTTP response received (status={response.status_code})")
    response.raise_for_status()
except Exception as e:
    log(f"ERROR during SerpAPI request: {e}")
    sys.exit(1)


# -------------------------------------------------
# Parse response
# -------------------------------------------------
try:
    log("Parsing JSON response...")
    data = response.json()
except Exception as e:
    log(f"ERROR parsing JSON: {e}")
    sys.exit(1)

log("JSON parsed successfully")


# -------------------------------------------------
# Extract articles
# -------------------------------------------------
articles = data.get("articles", [])
log(f"Number of articles found: {len(articles)}")

if not articles:
    log("WARNING: No articles returned by SerpAPI")


# -------------------------------------------------
# Build citation data
# -------------------------------------------------
today = datetime.utcnow().strftime("%Y-%m-%d")
citation_data = {
    "metadata": {
        "last_updated": today,
        "source": "serpapi",
    },
    "papers": {},
}

log("Building citation entries...")

for idx, art in enumerate(articles, start=1):
    try:
        paper_id = art.get("citation_id") or art.get("link") or f"paper_{idx}"
        title = art.get("title", "Unknown title")
        year = art.get("year", "Unknown")
        citations = art.get("cited_by", {}).get("value", 0)
        link = art.get("link")
        authors = art.get("authors")
        venue = art.get("publication") or art.get("journal") or art.get("source")
        snippet = art.get("snippet")
        resources = art.get("resources")
        cited_by_link = art.get("cited_by", {}).get("link")


        log(f"[{idx}/{len(articles)}] {title} ({year}) → {citations} citations")
        if idx == 1:
            log(f"Sample extra fields: authors={authors}, venue={venue}, link={link}")

        # DOI lookup via Crossref (adds API calls)
        author_hint = None
        if isinstance(authors, str) and authors.strip():
            author_hint = authors.split(",")[0].strip()  # first author token

        doi = None
        try:
            doi = crossref_lookup_doi(title=title, year=str(year), author_hint=author_hint)
        except Exception as e:
            log(f"  ↳ Crossref lookup failed: {e}")
        if doi:
            log(f"  ↳ DOI: {doi}")

        citation_data["papers"][paper_id] = {
            "title": title,
            "year": year,
            "citations": citations,
            "authors": authors,
            "venue": venue,
            "link": link,
            "cited_by_link": cited_by_link,
            "snippet": snippet,
            "resources": resources,
            "doi": doi
        }


    except Exception as e:
        log(f"ERROR processing article #{idx}: {e}")


# -------------------------------------------------
# Write output file
# -------------------------------------------------
try:
    log("Ensuring _data directory exists...")
    os.makedirs("_data", exist_ok=True)

    log(f"Writing citation data to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        yaml.dump(citation_data, f, sort_keys=False, width=1000)


    log("Citation data written successfully")
except Exception as e:
    log(f"ERROR writing citation file: {e}")
    sys.exit(1)


# -------------------------------------------------
# Fetch author-level metrics + citations-per-year (1 extra API call)
# -------------------------------------------------
# -------------------------------------------------
# Fetch author-level metrics + citations-per-year (1 extra API call)
# -------------------------------------------------
try:
    log("Fetching author metrics (citations, h-index, i10-index) + citations-per-year graph...")

    metrics_params = {
        "engine": "google_scholar_author",
        "author_id": AUTHOR_ID,
        "view_op": "cited_by",
        "api_key": API_KEY,
    }

    log("Sending request for cited_by table/graph...")
    metrics_resp = requests.get(url, params=metrics_params, timeout=30)
    log(f"HTTP response received (status={metrics_resp.status_code})")
    metrics_resp.raise_for_status()

    log("Parsing JSON response for metrics...")
    metrics_data = metrics_resp.json()

    cited_by = metrics_data.get("cited_by", {})



    # ---- Table: citations / h-index / i10-index (usually "table")
    table = cited_by.get("table", [])


    # Convert table rows into a nice dict like:
    # {"citations": {"all": 1234, "since_2019": 567}, "h_index": {...}, "i10_index": {...}}
    metrics = {}

    for row in table:
        # Each row looks like: {"citations": {...}} or {"h_index": {...}} etc.
        if not isinstance(row, dict) or len(row) != 1:
            continue

        metric_name, values = next(iter(row.items()))
        if not isinstance(values, dict):
            continue

        metrics[metric_name] = values


    # ---- Graph: citations per year (usually "graph")
    graph = cited_by.get("graph", [])
    citations_per_year = {}
    for p in graph:
        y = p.get("year")
        c = p.get("citations")
        if isinstance(y, int):
            try:
                citations_per_year[str(y)] = int(c)
            except Exception:
                pass
    citations_per_year = dict(sorted(citations_per_year.items(), key=lambda kv: int(kv[0])))

    metrics_payload = {
        "metadata": {"last_updated": today, "source": "serpapi"},
        "author_id": AUTHOR_ID,
        "metrics": metrics,
        "citations_per_year": citations_per_year,
    }

    log(f"Writing metrics to {METRICS_FILE}...")
    with open(METRICS_FILE, "w") as f:
        yaml.dump(metrics_payload, f, sort_keys=True, width=1000)

    # Debug summary
    if "citations" in metrics:
        log(f"Total citations (table): {metrics['citations']}")
    if "h_index" in metrics:
        log(f"h-index (table): {metrics['h_index']}")
    if "i10_index" in metrics:
        log(f"i10-index (table): {metrics['i10_index']}")
    if citations_per_year:
        last5 = list(citations_per_year.items())[-5:]
        log("Latest years (graph): " + ", ".join([f"{yy}:{cc}" for yy, cc in last5]))
    else:
        log("WARNING: No citations-per-year graph returned (graph empty).")

    log("Author metrics file written successfully")

except Exception as e:
    log(f"ERROR fetching/writing author metrics: {e}")
    # Keep your workflow running even if the metrics call fails
    # sys.exit(1)




log("Script completed successfully")






