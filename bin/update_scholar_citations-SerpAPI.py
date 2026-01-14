#!/usr/bin/env python3

import os
import sys
import yaml
import requests
from datetime import datetime

def log(msg: str):
    """Timestamped log output (flushes immediately)."""
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)


# -------------------------------------------------
# Configuration
# -------------------------------------------------
AUTHOR_ID = "-VPPZ8YAAAAJ"   # your Google Scholar user id
OUTPUT_FILE = "_data/citations.yml"
API_KEY = os.getenv("SERPAPI_API_KEY")
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

        log(f"[{idx}/{len(articles)}] {title} ({year}) → {citations} citations")

        citation_data["papers"][paper_id] = {
            "title": title,
            "year": year,
            "citations": citations,
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
        yaml.dump(citation_data, f, sort_keys=True, width=1000)

    log("Citation data written successfully")
except Exception as e:
    log(f"ERROR writing citation file: {e}")
    sys.exit(1)


log("Script completed successfully")
