#!/usr/bin/env python3

import os
import sys
import yaml
import requests
from datetime import datetime
import urllib.parse
import time
import re
import xml.etree.ElementTree as ET
from pathlib import Path

SKIP_EXISTING_PAPERS = True  # set True to only update citations, False to refresh all data of existing papers
TARGET_JOURNALS_METRICS = [
    "Nature Photonics",
    "Nature Communications",
    "Science Advances",
    "Physical Review Letters",
    "Nature Materials",
    "PRX",
    "PRX Quantum",
]


def log(msg: str):
    """Timestamped log output (flushes immediately)."""
    print(f"[{datetime.utcnow().isoformat()}] {msg}", flush=True)

def read_author_alias():
    '''Read author alias from _config.yml and return it as a string.
    '''
    # Path to _config.yml in repo root
    config_path = Path(__file__).resolve().parents[1] / "_config.yml"

    if not config_path.exists():
        raise FileNotFoundError(f"Could not find {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    first = config.get("first_name", "")
    middle = config.get("middle_name", "")
    last = config.get("last_name", "")

    # Collect initials:
    # - first letter of first name
    # - first letter of middle name (if present)
    # - first letter of each last-name component
    initials = []

    if first:
        initials.append(first.strip()[0])

    if middle:
        initials.append(middle.strip()[0])

    for part in last.split():
        if part:
            initials.append(part[0])

    alias = "".join(initials).upper()
    log(f"Author alias: {alias}")
    return alias

AUTHOR_ALIAS = read_author_alias()
# -------------------------------------------------
# Define Crossref search tools
# -------------------------------------------------


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_MAILTO = "carlossmwolff@gmail.com"  # optional but recommended
CROSSREF_ROWS = 3                    # how many candidates to consider
CROSSREF_SLEEP = 0.2                 # be polite; avoids hammering
TITLE_MATCH_THRESHOLD = 0.85         # 0..1, higher = stricter

# Write clarifying comment here to test commits.

def crossref_fetch_work(doi: str) -> dict:
    """Fetch full Crossref metadata for a DOI. Returns the Crossref 'message' dict."""
    time.sleep(CROSSREF_SLEEP)
    r = requests.get(f"{CROSSREF_WORKS_URL}/{doi}", timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("message", {})

def clean_crossref_abstract(a: str | None) -> str:
    if not a:
        return ""
    # remove very common JATS tags without regex
    for tag in ("<jats:p>", "</jats:p>", "<p>", "</p>"):
        a = a.replace(tag, "")
    return " ".join(a.split())


def crossref_compact(msg: dict) -> dict:
    """Keep a compact subset of Crossref fields (enough to build BibTeX later)."""
    if not msg:
        return {}

    def first(x):
        return x[0] if isinstance(x, list) and x else x

    issued = msg.get("issued", {}).get("date-parts", [])
    issued_ymd = issued[0] if issued and issued[0] else []

    out = {
        "DOI": msg.get("DOI"),
        "type": msg.get("type"),
        "title": first(msg.get("title")),
        "container_title": first(msg.get("container-title")),
        "short_container_title": first(msg.get("short-container-title")),
        "publisher": msg.get("publisher"),
        "publisher_location": msg.get("publisher-location"),
        "volume": msg.get("volume"),
        "issue": msg.get("issue"),
        "page": msg.get("page"),
        "article_number": msg.get("article-number"),
        "ISSN": msg.get("ISSN"),
        "ISBN": msg.get("ISBN"),
        "URL": msg.get("URL"),
        "issued": issued_ymd,
        "subject": msg.get("subject"),
        "author": msg.get("author"),
        "link": msg.get("link"),
        "license": msg.get("license"),
    }

    # Abstract is often absent; include if present
    if msg.get("abstract"):
        out["abstract"] = msg.get("abstract")

    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


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

ARXIV_API_URL = "https://export.arxiv.org/api/query"

def is_arxiv_doi(doi: str | None) -> bool:
    """True if DOI looks like the arXiv DOI minted by arXiv (10.48550/arXiv.*)."""
    if not doi:
        return False
    d = doi.strip().lower()
    return d.startswith("10.48550/arxiv.")

def split_name_to_given_family(name: str) -> tuple[str, str]:
    """
    Best-effort split 'First Middle Last' -> (given, family).
    arXiv author names are usually in that format (sometimes with particles).
    """
    name = " ".join((name or "").strip().split())
    if not name:
        return ("", "")
    parts = name.split()
    if len(parts) == 1:
        return ("", parts[0])
    return (" ".join(parts[:-1]), parts[-1])

def arxiv_fetch_metadata(arxiv_id: str) -> dict:
    """
    Fetch metadata for a single arXiv identifier using the arXiv Atom API.
    Returns: {"arxiv_id", "title", "published", "updated", "doi", "journal_ref", "authors"}
    where "authors" is a Crossref-like list of dicts with given/family/sequence/affiliation.
    """
    if not arxiv_id:
        return {}

    params = {"id_list": arxiv_id}
    r = requests.get(ARXIV_API_URL, params=params, timeout=20)
    r.raise_for_status()

    # Parse Atom XML
    root = ET.fromstring(r.text)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    entry = root.find("atom:entry", ns)
    if entry is None:
        return {"arxiv_id": arxiv_id}

    title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
    title = " ".join(title.split())

    published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
    updated = (entry.findtext("atom:updated", default="", namespaces=ns) or "").strip()

    doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip() or None
    journal_ref = (entry.findtext("arxiv:journal_ref", default="", namespaces=ns) or "").strip() or None

    # Authors
    author_nodes = entry.findall("atom:author", ns)
    authors_struct = []
    for i, an in enumerate(author_nodes):
        name = (an.findtext("atom:name", default="", namespaces=ns) or "").strip()
        given, family = split_name_to_given_family(name)
        if not (given or family):
            continue
        authors_struct.append({
            "given": given,
            "family": family,
            "sequence": "first" if i == 0 else "additional",
            "affiliation": [],
        })

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "published": published,
        "updated": updated,
        "doi": doi,
        "journal_ref": journal_ref,
        "authors": authors_struct,
    }

def extract_arxiv_id_from_venue(venue: str | None) -> str | None:
    """
    Robustly extract arXiv id from common Scholar venue strings like:
      'arXiv:2301.01234' or 'arXiv:hep-th/9901001'
    """
    if not venue:
        return None
    m = re.search(r"\barxiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+\/\d{7}(?:v\d+)?)\b", venue, re.IGNORECASE)
    return m.group(1) if m else None




# -------------------------------------------------
# Configuration
# -------------------------------------------------
AUTHOR_ID = "-VPPZ8YAAAAJ"   # your Google Scholar user id
OUTPUT_FILE = "_data/citations.yml"
API_KEY = os.getenv("SERPAPI_API_KEY")
METRICS_FILE = "_data/author_metrics.yml"


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

all_articles = []
start = 0
PAGE_SIZE = 20

while True:
    params["start"] = start
    params["num"] = PAGE_SIZE

    log(f"Fetching Scholar articles: start={start}")
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    page_articles = data.get("articles", []) or []
    log(f"Received {len(page_articles)} articles")

    if not page_articles:
        break

    all_articles.extend(page_articles)
    start += PAGE_SIZE


articles = all_articles

log(f"Number of articles found: {len(articles)}")

if not articles:
    log("WARNING: No articles returned by SerpAPI")


# -------------------------------------------------
# Build citation data
# -------------------------------------------------

# Load existing citations file (if any)
existing_papers = {}
if os.path.exists(OUTPUT_FILE):
    try:
        with open(OUTPUT_FILE, "r") as f:
            existing_yaml = yaml.safe_load(f) or {}
        existing_papers = existing_yaml.get("papers", {}) or {}
        log(f"Loaded existing citations.yml with {len(existing_papers)} papers")
    except Exception as e:
        log(f"WARNING: Could not read existing {OUTPUT_FILE}: {e}")
        existing_papers = {}
else:
    log("No existing citations.yml found (first run)")


today = datetime.utcnow().strftime("%Y-%m-%d")
citation_data = {
    "metadata": {
        "last_updated": today,
        "source": "serpapi",
    },
    "papers": dict(existing_papers),  # start from what we already have
}


log("Building citation entries...")

skipped_existing = 0

def alias_from_given_family(given: str | None, family: str | None) -> str:
    """
    Build alias by joining given+family and removing all lowercase letters.
    Keeps only uppercase letters (including accented uppercase).
    Example: given="C. Sánchez", family="Muñoz" -> "CSM"
    """
    s = f"{given or ''}{family or ''}"
    return "".join(ch for ch in s if ch.isupper())


def author_position_from_crossref_or_scholar(crossref: dict, authors_str: str | None, author_alias: str) -> str | None:
    """
    Returns "first" / "middle" / "last" if author_alias is found, else None.

    Preference:
      1) Crossref 'author' list (structured given/family)
      2) Scholar 'authors' string (comma-separated)
    """
    target = (author_alias or "").strip().upper()
    if not target:
        return None

    # ---- 1) Crossref structured authors
    cr_authors = (crossref or {}).get("author")
    if isinstance(cr_authors, list) and cr_authors:
        aliases = []
        for p in cr_authors:
            if not isinstance(p, dict):
                continue
            a = alias_from_given_family(p.get("given"), p.get("family"))
            if a:
                aliases.append(a)

        if aliases:
            try:
                i = aliases.index(target)
                if len(aliases) == 1:
                    return "first"  # single-author paper
                if i == 0:
                    return "first"
                if i == len(aliases) - 1:
                    return "last"
                return "middle"
            except ValueError:
                pass  # not found -> fallback

    # ---- 2) Scholar authors string fallback
    s = (authors_str or "").strip()
    if s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        # drop ellipsis tokens like "..." or "…"
        parts = [p for p in parts if p not in {"...", "…"}]

        aliases = []
        for name in parts:
            # Join name token and keep uppercase letters only
            a = "".join(ch for ch in name if ch.isupper())
            if a:
                aliases.append(a)

        if aliases:
            try:
                i = aliases.index(target)
                if len(aliases) == 1:
                    return "first"
                if i == 0:
                    return "first"
                if i == len(aliases) - 1:
                    return "last"
                return "middle"
            except ValueError:
                return None

    return None



for idx, art in enumerate(articles, start=1):
    try:
        paper_id = art.get("citation_id") or art.get("link") or f"paper_{idx}"
        citations = art.get("cited_by", {}).get("value", 0)
        venue = art.get("publication") or art.get("journal") or art.get("source")


        existing_entry = existing_papers.get(paper_id)

        if existing_entry:
            entry = dict(existing_entry)
        else:
            entry = {}

        # Update citations
        entry["citations"] = citations

        if SKIP_EXISTING_PAPERS and existing_entry:
            # Already have this paper stored; skip any API work for it and just update citations
            skipped_existing += 1
            citation_data["papers"][paper_id] = entry
            continue

        # If we do not skip, proceed to fetch more data
        
        title = art.get("title", "Unknown title")
        year = art.get("year", "Unknown")
        link = art.get("link")
        authors = art.get("authors")
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

        crossref = {}
        if doi:
            log(f"  ↳ DOI: {doi}")
            try:
                full_msg = crossref_fetch_work(doi)

                # put authors at top-level (not nested under crossref)
                cr_auth = (full_msg or {}).get("author")
                if isinstance(cr_auth, list) and cr_auth:
                    entry["author"] = cr_auth

                crossref = crossref_compact(full_msg)

            except Exception as e:
                log(f"  ↳ Crossref full-metadata fetch failed for {doi}: {e}")
                crossref = {}

        author_position = author_position_from_crossref_or_scholar(
        crossref=crossref,
        authors_str=authors,
        author_alias=AUTHOR_ALIAS)

        
        entry.update({
            "title": title,
            "year": year,
            "authors": authors,
            "venue": venue,
            "link": link,
            "cited_by_link": cited_by_link,
            "snippet": snippet,
            "resources": resources,
            "author_position": author_position,   
        })

        arxiv_id = None

        if doi:
            entry["doi"] = doi
            entry["is_arxiv"] = False
            entry["arxiv_id"] = None
        else:
            # No DOI: fall back to Scholar signals
            venue_l = (venue or "").lower()
            arxiv_id = extract_arxiv_id_from_venue(venue)

            if arxiv_id:
                entry["is_arxiv"] = True
                entry["arxiv_id"] = arxiv_id
                entry["doi"] = None

                # --- Fetch arXiv metadata ---
                arx = {}
                try:
                    arx = arxiv_fetch_metadata(arxiv_id)
                except Exception as e:
                    log(f"  ↳ arXiv metadata fetch failed for {arxiv_id}: {e}")
                    arx = {}

                # If arXiv reports a DOI and it's NOT the arXiv DOI, treat it as the real DOI.
                arxiv_reported_doi = (arx.get("doi") or "").strip() or None

                if arxiv_reported_doi and (not is_arxiv_doi(arxiv_reported_doi)):
                    # We found a real DOI via arXiv -> fetch Crossref and treat as published.
                    doi = arxiv_reported_doi
                    entry["doi"] = doi
                    entry["is_arxiv"] = None   # per your request: it was arXiv in Scholar, but we upgraded it
                    # keep arxiv_id for traceability
                    # entry["arxiv_id"] stays set

                    try:
                        full_msg = crossref_fetch_work(doi)
                        crossref = crossref_compact(full_msg)
                    except Exception as e:
                        log(f"  ↳ Crossref full-metadata fetch failed for {doi}: {e}")
                        crossref = {}

                    if crossref:
                        entry["crossref"] = crossref

                    # Populate top-level structured authors from Crossref if available
                    # (Crossref compact no longer contains 'author', so use full_msg if you want,
                    #  or fetch authors before compacting. Easiest: re-use full_msg['author'] here.)
                    try:
                        cr_auth = (full_msg or {}).get("author")
                        if isinstance(cr_auth, list) and cr_auth:
                            entry["author"] = cr_auth
                    except Exception:
                        pass

                else:
                    # No usable DOI (or only arXiv DOI): keep as arXiv preprint and use arXiv authors
                    if arx.get("authors"):
                        entry["author"] = arx["authors"]

            else:
                # If we cannot extract an id, keep heuristic flag
                entry["is_arxiv"] = ("arxiv" in venue_l)
                entry["arxiv_id"] = None
                entry["doi"] = None

        # Create a unique id which is either doi (if not null or arxiv id (if not null) or paper_id
        entry["unique_id"] = doi or arxiv_id or paper_id

        if crossref:
            entry["crossref"] = crossref


        citation_data["papers"][paper_id] = entry


    except Exception as e:
        log(f"ERROR processing article #{idx}: {e}")

log(f"Skipped {skipped_existing} existing papers")


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

def compute_metrics_summaries(citation_data: dict, target_journals: list[str]) -> tuple[list[dict], dict]:
    """
    Builds:
      - journal_summary: list of dicts per target journal
      - stats: peer_reviewed + preprints aggregates

    Uses per-entry fields:
      - is_arxiv (bool)
      - arxiv_id
      - doi
      - citations (int)
      - author_position ("first"/"middle"/"last"/None)
      - crossref.container_title (preferred) OR crossref.short_container_title (fallback)
    """
    papers = (citation_data or {}).get("papers", {}) or {}

    def norm_journal(s: str | None) -> str:
        return " ".join((s or "").strip().split())

    def get_container_title(e: dict) -> str:
        cr = e.get("crossref") or {}
        # Prefer full container title; fallback to short
        return norm_journal(cr.get("container_title") or cr.get("short_container_title") or "")

    # --- Journal summary (only target journals)
    # initialize counts for every target journal (so they always show up)
    summary_map = {
        j: {"journal": j, "count": 0, "first_author": 0, "last_author": 0}
        for j in target_journals
    }

    # --- Global stats buckets
    peer = {"count": 0, "first_author": 0, "last_author": 0, "total_citations_from_entries": 0}
    pre  = {"count": 0, "first_author": 0, "last_author": 0}

    for _, e in papers.items():
        is_preprint = bool(e.get("is_arxiv", False)) or bool(e.get("arxiv_id"))
        pos = e.get("author_position")  # "first"/"middle"/"last"/None
        c = e.get("citations", 0)
        try:
            c = int(c) if c is not None else 0
        except Exception:
            c = 0

        # global totals
        if is_preprint:
            pre["count"] += 1
            if pos == "first":
                pre["first_author"] += 1
            elif pos == "last":
                pre["last_author"] += 1
        else:
            peer["count"] += 1
            peer["total_citations_from_entries"] += c
            if pos == "first":
                peer["first_author"] += 1
            elif pos == "last":
                peer["last_author"] += 1

        # per-journal target summary (peer-reviewed only makes most sense, but
        # if you want preprints included too, remove the is_preprint check)
        if not is_preprint:
            jname = get_container_title(e)
            if jname in summary_map:
                summary_map[jname]["count"] += 1
                if pos == "first":
                    summary_map[jname]["first_author"] += 1
                elif pos == "last":
                    summary_map[jname]["last_author"] += 1

    journal_summary = [summary_map[j] for j in target_journals]

    stats = {
        "peer_reviewed": peer,
        "preprints": pre,
    }
    return journal_summary, stats


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


    journal_summary, stats = compute_metrics_summaries(citation_data, TARGET_JOURNALS_METRICS)

    metrics_payload = {
        "metadata": {"last_updated": today, "source": "serpapi"},
        "author_id": AUTHOR_ID,
        "total_papers": len(articles),
        "metrics": metrics,
        "citations_per_year": citations_per_year,
        "journal_summary": journal_summary,  
        "stats": stats,                      
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




# -------------------------------------------------
# Write/append BibTeX: _bibliography/papers.bib and _bibliography/preprints.bib (simple, no regex)
# -------------------------------------------------
from pathlib import Path

PAPERS_BIB = Path("_bibliography/papers.bib")
PREPRINTS_BIB = Path("_bibliography/preprints.bib")

log("Writing BibTeX files...")

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""

def write_text(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def bib_rhs_value(line: str) -> str | None:
    '''
    Extract RHS value from a BibTeX field line.
    Examples:
      '  title = {A Great Paper},'  --> 'A Great Paper'
      '  doi = "10.1234/abcd",'     --> '10.1234/abcd'
    Returns None if not found.
    '''
    # expects: field = {VALUE},  or field = "VALUE",
    if "=" not in line:
        return None
    rhs = line.split("=", 1)[1].strip().rstrip(",").strip()
    if rhs.startswith("{") and rhs.endswith("}"):
        return rhs[1:-1].strip()
    if rhs.startswith('"') and rhs.endswith('"'):
        return rhs[1:-1].strip()
    return None

def existing_field_values(bib_text: str, field: str) -> set[str]:
    '''
    Extract all values of a given field from BibTeX text.
    Returns a set of values (lowercased for DOI).
    '''
    vals = set()
    for ln in bib_text.splitlines():
        s = ln.strip()
        if s.lower().startswith(field.lower()):
            v = bib_rhs_value(s)
            if v:
                vals.add(v.strip().lower() if field.lower() == "doi" else v.strip())
    return vals


def _norm_space(s: str) -> str:
    return " ".join((s or "").strip().split())

def bibtex_author_from_crossref_person(p: dict) -> str:
    """Crossref person -> 'Family, Given' (BibTeX-friendly)."""
    family = _norm_space(p.get("family", ""))
    given = _norm_space(p.get("given", ""))
    if not family and not given:
        return ""
    if family and given:
        return f"{family}, {given}"
    return family or given

def authors_from_crossref(cr: dict, max_authors: int | None = None) -> str:
    """Crossref author list -> BibTeX 'A and B and C' (optionally 'and others')."""
    alist = cr.get("author")
    if not isinstance(alist, list) or not alist:
        return ""
    names = []
    for p in alist:
        if isinstance(p, dict):
            nm = bibtex_author_from_crossref_person(p)
            if nm:
                names.append(nm)
    if not names:
        return ""
    if max_authors is not None and len(names) > max_authors:
        names = names[:max_authors] + ["others"]
    return " and ".join(names)

def authors_from_scholar_string(authors: str) -> str:
    """
    Scholar gives: 'A, B, C, ...'  ->  BibTeX: 'A and B and C and others'
    """
    s = _norm_space(authors)
    if not s:
        return ""

    parts = [p.strip() for p in s.split(",") if p.strip()]

    has_ellipsis = False
    cleaned = []
    for p in parts:
        if p in {"...", "…"}:
            has_ellipsis = True
            continue
        if p.endswith("...") or p.endswith("…"):
            has_ellipsis = True
            p = p.rstrip(".…").strip()
            if p:
                cleaned.append(p)
            continue
        cleaned.append(p)

    if not cleaned:
        return ""

    if has_ellipsis:
        cleaned.append("others")

    return " and ".join(cleaned)

def authors_from_struct_list(alist: list, max_authors: int | None = None) -> str:
    """List of dicts with given/family -> BibTeX 'A and B and C'."""
    if not isinstance(alist, list) or not alist:
        return ""
    names = []
    for p in alist:
        if isinstance(p, dict):
            nm = bibtex_author_from_crossref_person(p)
            if nm:
                names.append(nm)
    if not names:
        return ""
    if max_authors is not None and len(names) > max_authors:
        names = names[:max_authors] + ["others"]
    return " and ".join(names)


def best_bibtex_authors(e: dict) -> str:
    """Prefer structured authors at paper level; fallback to Crossref; then repaired Scholar string."""
    top = e.get("author")
    top_auth = authors_from_struct_list(top, max_authors=None)
    if top_auth:
        return top_auth

    cr = e.get("crossref") or {}
    cr_auth = authors_from_crossref(cr, max_authors=None)
    if cr_auth:
        return cr_auth

    return authors_from_scholar_string(e.get("authors") or "")



def make_citekey(e: dict, suffix: str) -> str:
    top = e.get("author")
    if isinstance(top, list) and top and isinstance(top[0], dict):
        first_author = (top[0].get("family") or "unknown").strip().split()[-1].lower()
    else:
        cr = e.get("crossref") or {}
        alist = cr.get("author")
        if isinstance(alist, list) and alist and isinstance(alist[0], dict):
            first_author = (alist[0].get("family") or "unknown").strip().split()[-1].lower()
        else:
            authors = (e.get("authors") or "")
            first_chunk = (authors.split(",")[0].strip() if authors else "unknown")
            first_author = (first_chunk.split()[-1] if first_chunk else "unknown").lower()

    first_word = ((e.get("title") or "paper").split()[0]).lower()
    year = str(e.get("year") or "yyyy")
    return f"{first_author}{first_word}{year}{suffix}"



def esc(s: str | None) -> str:
    # minimal escaping; keeps your BibTeX readable
    s = (s or "").strip().replace("\n", " ")
    return " ".join(s.split())

def bib_article_entry(e: dict, doi: str) -> str:
    """Create a BibTeX @article entry from citation entry e and doi."""
    cr = e.get("crossref") or {}
    citekey = make_citekey(e, "")
    gs_id = (e.get("citation_id") or "").split(":")[-1]  # if present like -VPP...:bFI...

    # Prefer Crossref fields if present, fall back to Scholar fields
    # Prefer Crossref title when available (often better capitalization / punctuation)
    title = esc((cr.get("title") or e.get("title")))    
    author = esc(best_bibtex_authors(e))
    year = esc(str(e.get("year") or ""))
    journal = esc(cr.get("container_title") or e.get("venue") or "")
    abbr = cr.get("short_container_title") or cr.get("container_title")
    volume = esc(str(cr.get("volume") or ""))
    number = esc(str(cr.get("issue") or ""))
    pages = esc(cr.get("page") or "")
    publisher = esc(cr.get("publisher") or "")
    issn = cr.get("ISSN")
    issn_val = ""
    if isinstance(issn, list) and issn:
        issn_val = esc(issn[0])
    elif isinstance(issn, str):
        issn_val = esc(issn)
    abstract = esc(clean_crossref_abstract(cr.get("abstract")))

    urldate = datetime.utcnow().strftime("%Y-%m-%d")

    lines = []
    lines.append(f"@article{{{citekey},")
    if abbr:  
        lines.append(f"  abbr = {{{abbr}}},")
    lines.append(f"  title = {{{title}}},")
    if author:    lines.append(f"  author = {{{author}}},")
    if year:      lines.append(f"  year = {year},")
    if journal:   lines.append(f"  journal = {{{journal}}},")
    if volume:    lines.append(f"  volume = {{{volume}}},")
    if number:    lines.append(f"  number = {{{number}}},")
    if pages:     lines.append(f"  pages = {{{pages}}},")
    if publisher: lines.append(f"  publisher = {{{publisher}}},")
    lines.append(f"  dimensions = {{true}},")
    if issn_val:  lines.append(f"  issn = {{{issn_val}}},")
    lines.append(f"  doi = {{{doi}}},")
    lines.append(f"  urldate = {{{urldate}}},")
    if abstract:  lines.append(f"  abstract = {{{abstract}}},")
    if gs_id:     lines.append(f"  google_scholar_id = {{{gs_id}}},")
    # keep your style
    lines.append(f"  selected={{false}}")
    lines.append(f"}}\n")
    return "\n".join(lines)



def bib_preprint_entry(e: dict, arxiv_id: str) -> str:
    """Create a BibTeX @misc entry for arXiv preprint from citation entry e and arxiv_id."""
    citekey = make_citekey(e, "")
    gs_id = (e.get("citation_id") or "").split(":")[-1]
    title = esc(e.get("title"))
    author = esc(best_bibtex_authors(e))
    year = esc(str(e.get("year") or ""))
    lines = []
    lines.append(f"@misc{{{citekey},")
    lines.append(f"  title = {{{title}}},")
    lines.append("abbr = {arXiv},")
    if author: lines.append(f"  author = {{{author}}},")
    if year:   lines.append(f"  year = {year},")
    lines.append(f"  archivePrefix = {{arXiv}},")
    lines.append(f"  eprint = {{{arxiv_id}}},")
    if gs_id:  lines.append(f"  google_scholar_id = {{{gs_id}}},")
    lines.append(f"  selected={{false}}")
    lines.append(f"}}\n")
    return "\n".join(lines)


def comment_out_removed_preprints(bib_text: str, keep_eprints: set[str]) -> tuple[str, int]:
    '''Comment out BibTeX entries with eprint IDs not in keep_eprints set.
    Returns modified BibTeX text and number of entries commented out.
    '''
    # assumes entries start with '@' at beginning of line and end on a line containing only '}'
    out, buf = [], []
    in_entry = False
    eprint = None
    commented = 0

    def flush():
        nonlocal buf, eprint, commented
        if not buf:
            return
        if eprint and (eprint not in keep_eprints):
            commented += 1
            out.extend([("% " + ln) if not ln.lstrip().startswith("%") else ln for ln in buf])
        else:
            out.extend(buf)
        buf, eprint = [], None

    for ln in bib_text.splitlines(True):
        if ln.startswith("@"):
            flush()
            in_entry = True
            buf.append(ln)
            continue

        if in_entry:
            buf.append(ln)
            s = ln.strip()
            if s.lower().startswith("eprint"):
                v = bib_rhs_value(s)
                if v:
                    eprint = v.strip()
            if s == "}":
                in_entry = False
                flush()
        else:
            out.append(ln)

    flush()
    return "".join(out), commented


# ---- Collect current DOIs and arXiv ids from citations.yml data ----
papers = (citation_data or {}).get("papers", {}) or {}

current_dois = set()
current_arxivs = set()
for k, e in papers.items():
    # ensure citation_id exists for google_scholar_id field
    e["citation_id"] = e.get("citation_id") or k

    doi = (e.get("doi") or "").strip().lower()
    arx = (e.get("arxiv_id") or "").strip()
    if doi and not e.get("is_arxiv", False):
        current_dois.add(doi)
    if (not doi) and arx:
        current_arxivs.add(arx)

# ---- Published: append only new DOIs ----
papers_text = read_text(PAPERS_BIB)
existing_dois = existing_field_values(papers_text, "doi")

new_pub = []
for e in papers.values():
    doi = (e.get("doi") or "").strip().lower()
    if not doi or e.get("is_arxiv", False):
        continue
    if doi in existing_dois:
        continue
    new_pub.append(bib_article_entry(e, doi))
    existing_dois.add(doi)

if new_pub:
    papers_text = papers_text.rstrip() + ("\n\n" if papers_text.strip() else "")
    papers_text += "\n".join(new_pub) + "\n"
    write_text(PAPERS_BIB, papers_text)
log(f"papers.bib: added {len(new_pub)} new entries")

# ---- Preprints: comment out removed + append only new eprints ----
pre_text = read_text(PREPRINTS_BIB)
pre_text2, commented = comment_out_removed_preprints(pre_text, current_arxivs)
existing_eprints = existing_field_values(pre_text2, "eprint")

new_pre = []
for e in papers.values():
    doi = (e.get("doi") or "").strip().lower()
    arx = (e.get("arxiv_id") or "").strip()
    if doi or not arx:
        continue
    if arx in existing_eprints:
        continue
    new_pre.append(bib_preprint_entry(e, arx))
    existing_eprints.add(arx)

pre_text2 = pre_text2.rstrip() + ("\n\n" if pre_text2.strip() else "")
pre_text2 += "\n".join(new_pre) + "\n"
write_text(PREPRINTS_BIB, pre_text2)
log(f"preprints.bib: added {len(new_pre)} new entries; commented out {commented} old entries")


log("Script completed successfully")






