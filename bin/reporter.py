#!/usr/bin/env python3
"""
Generate _data/report.yml from:
  - _data/citations.yml
  - _data/scholar_metrics.yml
  - _config.yml  (for author alias matching)

This script ONLY writes report.yml. Your Markdown/Liquid can render from it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterable

import yaml


DEFAULT_JOURNALS_TO_COUNT = [
    "Nature Photonics",
    "Nature Communications",
    "Nature Materials",
    "Science Advances",
    "Physical Review Letters",
    "PRX",
    "PRX Quantum",
]

JOURNAL_ALIASES = {
    "prx": ["physical review x", "phys. rev. x", "phys rev x"],
    "prx quantum": ["prx quantum", "physical review x quantum", "phys. rev. x quantum", "phys rev x quantum"],
    "physical review letters": ["physical review letters", "phys. rev. lett.", "phys rev lett"],
}


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _dump_yaml(data: Any, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            default_flow_style=False,
        )


def _normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()
    s = re.sub(r"[\u2010-\u2015]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalize_name(s: str) -> str:
    s = _normalize_text(s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _authors_list(authors_field: Optional[str]) -> List[str]:
    if not authors_field:
        return []
    parts = [a.strip() for a in str(authors_field).split(",")]
    return [p for p in parts if p]


def _pick_journal(entry: Dict[str, Any]) -> str:
    cr = entry.get("crossref") or {}
    container = cr.get("container_title") or cr.get("short_container_title")
    if isinstance(container, list) and container:
        container = container[0]
    if isinstance(container, str) and container.strip():
        return container.strip()

    venue = entry.get("venue")
    if isinstance(venue, str) and venue.strip():
        return venue.split(",")[0].strip()

    return ""


def _match_journal(journal_name: str, targets: List[str]) -> Optional[str]:
    jn = _normalize_text(journal_name)
    if not jn:
        return None

    for t in targets:
        tn = _normalize_text(t)
        if tn and (tn in jn or jn in tn):
            return t
        for a in JOURNAL_ALIASES.get(tn, []):
            an = _normalize_text(a)
            if an in jn or jn in an:
                return t
    return None


def _h_index(citation_counts: List[int]) -> int:
    sorted_cites = sorted((c for c in citation_counts if c is not None), reverse=True)
    h = 0
    for i, c in enumerate(sorted_cites, start=1):
        if c >= i:
            h = i
        else:
            break
    return h


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _looks_like_paper_dict(d: Dict[str, Any]) -> bool:
    """Heuristic: a real paper entry has at least title+authors, and usually year/citations."""
    if not isinstance(d, dict):
        return False
    if "title" in d and "authors" in d:
        return True
    # sometimes title might be missing but unique_id/doi exist
    if "authors" in d and ("doi" in d or "unique_id" in d or "link" in d):
        return True
    return False


def _iter_paper_entries(node: Any, key_path: str = "") -> Iterable[Tuple[str, Dict[str, Any]]]:
    """
    Yield (paper_key, paper_dict) from citations.yml, robust to:
      - flat dict: key -> paper_dict
      - nested dict: author_id -> paper_id -> paper_dict
      - list of dict items
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _iter_paper_entries(item, f"{key_path}[{i}]")
        return

    if not isinstance(node, dict):
        return

    # If this dict itself is a paper, yield it.
    if _looks_like_paper_dict(node):
        yield (key_path or "paper", node)
        return

    # Otherwise, recurse into children
    for k, v in node.items():
        child_path = f"{key_path}:{k}" if key_path else str(k)
        if isinstance(v, dict) and _looks_like_paper_dict(v):
            yield (child_path, v)
        else:
            yield from _iter_paper_entries(v, child_path)


def _build_author_tokens_from_config(config: Dict[str, Any]) -> Tuple[str, List[str]]:
    base_first = (config.get("first_name") or "").strip()
    base_last = (config.get("last_name") or "").strip()
    base_name = " ".join([p for p in [base_first, base_last] if p]).strip() or "Unknown"

    scholar = config.get("scholar") or {}
    first_aliases = scholar.get("first_name") or []
    last_aliases = scholar.get("last_name") or []

    if not isinstance(first_aliases, list):
        first_aliases = [str(first_aliases)]
    if not isinstance(last_aliases, list):
        last_aliases = [str(last_aliases)]

    # Always include base names too
    if base_first:
        first_aliases.append(base_first)
        first_aliases.append(base_first[0] + ".")
    if base_last:
        last_aliases.append(base_last)

    # Cartesian product -> "first last" variants
    variants: List[str] = []
    for f in first_aliases:
        for l in last_aliases:
            f = str(f).strip()
            l = str(l).strip()
            if f and l:
                variants.append(f"{f} {l}")
    variants.append(base_name)

    # Normalize/deduplicate
    tokens: List[str] = []
    seen = set()
    for v in variants:
        t = _normalize_name(v)
        if t and t not in seen:
            seen.add(t)
            tokens.append(t)

    return base_name, tokens


def build_report(
    citations_root: Any,
    scholar_metrics: Dict[str, Any],
    config: Dict[str, Any],
    journals_to_count: List[str],
    top_n: int = 10,
) -> Dict[str, Any]:
    author_display_name, author_tokens = _build_author_tokens_from_config(config)

    # Flatten citations.yml to real paper dicts
    all_papers: List[Dict[str, Any]] = []
    for paper_key, paper in _iter_paper_entries(citations_root):
        # Keep the original key path for debugging / linking if useful
        paper = dict(paper)
        paper["_paper_key"] = paper_key
        all_papers.append(paper)

    peer_reviewed = [p for p in all_papers if not bool(p.get("is_arxiv", False))]
    preprints = [p for p in all_papers if bool(p.get("is_arxiv", False))]

    def author_position_flags(entry: Dict[str, Any]) -> Tuple[bool, bool]:
        authors = _authors_list(entry.get("authors"))
        if not authors:
            return (False, False)
        first = _normalize_name(authors[0])
        last = _normalize_name(authors[-1])
        return (first in author_tokens, last in author_tokens)

    # Peer-reviewed stats
    pr_citations = [_safe_int(e.get("citations")) for e in peer_reviewed]
    pr_total_citations = sum(pr_citations)
    pr_h = _h_index(pr_citations)

    pr_first = 0
    pr_last = 0

    journal_counts: Dict[str, Dict[str, int]] = {
        j: {"count": 0, "first_author": 0, "last_author": 0} for j in journals_to_count
    }

    for e in peer_reviewed:
        is_first, is_last = author_position_flags(e)
        if is_first:
            pr_first += 1
        if is_last:
            pr_last += 1

        matched = _match_journal(_pick_journal(e), journals_to_count)
        if matched:
            journal_counts[matched]["count"] += 1
            if is_first:
                journal_counts[matched]["first_author"] += 1
            if is_last:
                journal_counts[matched]["last_author"] += 1

    # Preprints stats
    pp_first = 0
    pp_last = 0
    for e in preprints:
        is_first, is_last = author_position_flags(e)
        if is_first:
            pp_first += 1
        if is_last:
            pp_last += 1

    # Journal summary list (Liquid-friendly)
    journal_summary = []
    for j in journals_to_count:
        c = journal_counts[j]
        if c["count"] > 0:
            journal_summary.append(
                {
                    "journal": j,
                    "count": c["count"],
                    "first_author": c["first_author"],
                    "last_author": c["last_author"],
                }
            )

    # Top-cited peer-reviewed
    def paper_brief(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": e.get("title"),
            "year": e.get("year"),
            "citations": _safe_int(e.get("citations")),
            "venue": _pick_journal(e),
            "link": e.get("link"),
            "unique_id": e.get("unique_id"),
            "paper_key": e.get("_paper_key"),
        }

    top_cited = sorted(peer_reviewed, key=lambda e: _safe_int(e.get("citations")), reverse=True)[:top_n]
    top_cited_brief = [paper_brief(e) for e in top_cited]

    today = dt.date.today().isoformat()

    return {
        "metadata": {
            "generated_on": today,
            "sources": {
                "citations_yml": "_data/citations.yml",
                "scholar_metrics_yml": "_data/scholar_metrics.yml",
                "config_yml": "_config.yml",
            },
            "paper_entries_detected": len(all_papers),
        },
        "author": {
            "name": author_display_name,
            "match_tokens": author_tokens,
            "author_id": scholar_metrics.get("author_id"),
        },
        "stats": {
            "peer_reviewed": {
                "count": len(peer_reviewed),
                "first_author": pr_first,
                "last_author": pr_last,
                "total_citations_from_entries": pr_total_citations,
                "h_index_from_entries": pr_h,
            },
            "preprints": {
                "count": len(preprints),
                "first_author": pp_first,
                "last_author": pp_last,
            },
            "journal_summary": journal_summary,
        },
        "scholar": {
            "metadata": scholar_metrics.get("metadata", {}),
            "metrics": scholar_metrics.get("metrics", {}),
            "citations_per_year": scholar_metrics.get("citations_per_year", {}),
            "total_papers": scholar_metrics.get("total_papers", None),
        },
        "highlights": {
            "top_cited_peer_reviewed": top_cited_brief,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--citations", default="_data/citations.yml")
    p.add_argument("--metrics", default="_data/scholar_metrics.yml")
    p.add_argument("--config", default="_config.yml")
    p.add_argument("--output", default="_data/report.yml")
    p.add_argument("--journals-to-count", nargs="*", default=DEFAULT_JOURNALS_TO_COUNT)
    p.add_argument("--top-n", type=int, default=10)
    args = p.parse_args()

    citations_root = _load_yaml(Path(args.citations))
    scholar_metrics = _load_yaml(Path(args.metrics)) or {}
    config = _load_yaml(Path(args.config)) or {}

    report = build_report(
        citations_root=citations_root,
        scholar_metrics=scholar_metrics,
        config=config,
        journals_to_count=args.journals_to_count,
        top_n=args.top_n,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml(report, out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
