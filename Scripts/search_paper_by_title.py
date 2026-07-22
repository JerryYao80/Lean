#!/usr/bin/env python3
"""
search_paper_by_title.py — Real "paper title -> exact paper" entry point.

Given a research paper title, search SearxNG (scientific publications + web),
rank results by title similarity + source authority (arxiv/SSRN preferred),
fetch the exact paper (PDF via pdftotext, or HTML via crawl4ai), and drop the
extracted text into Results/soloquant/local-strategies/ so the existing
SoloQuant pipeline ingest_local_strategies stage picks it up for reproduction.

This is a NEW, independent entry point. It reuses the existing orchestrator
HTTP client and PDF helpers without modifying any existing feature.

Usage:
    python3 Scripts/search_paper_by_title.py \
        --config Launcher/config/config-soloquant.json \
        --title "Returns to Buying Winners and Selling Losers" \
        --top-k 5

After this, run the normal pipeline:
    python3 Scripts/soloquant_orchestrator.py --config Launcher/config/config-soloquant.json \
        --ingest-local-strategies
then prepare_reproduction -> generate_strategy_implementations as usual.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing the orchestrator module when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import soloquant_orchestrator as orchestrator  # noqa: E402

# Host authority boost — academic paper sources ranked first.
PAPER_HOST_AUTHORITY: dict[str, float] = {
    "arxiv.org": 3.0,
    "papers.ssrn.com": 3.0,
    "ssrn.com": 2.5,
    "semanticscholar.org": 2.0,
    "sciencedirect.com": 1.5,
    "tandfonline.com": 1.5,
    "jstor.org": 1.5,
    "wiley.com": 1.2,
    "springer.com": 1.2,
    "nber.org": 2.0,
    "aqr.com": 1.5,
    "quantpedia.com": 1.5,
}

STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "in", "on", "for", "to", "with",
    "from", "by", "is", "are", "at", "as", "this", "that", "its",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS and len(t) > 1}


def _title_similarity(query_title: str, result_title: str) -> float:
    """Jaccard overlap of meaningful tokens between query and result title."""
    q = _tokens(query_title)
    r = _tokens(result_title)
    if not q or not r:
        return 0.0
    return len(q & r) / len(q | r)


def _host_authority(url: str) -> float:
    host = (url or "").lower()
    for domain, boost in PAPER_HOST_AUTHORITY.items():
        if domain in host:
            return boost
    return 0.0


def rank_results(query_title: str, results: list[dict]) -> list[dict]:
    """Rank search results by title similarity + source authority."""
    scored: list[tuple[float, dict]] = []
    for item in results:
        url = str(item.get("url", "") or "").strip()
        title = str(item.get("title", "") or "")
        if not url:
            continue
        sim = _title_similarity(query_title, title)
        auth = _host_authority(url)
        # Weight similarity highest; authority breaks ties / boosts canonical sources.
        score = sim * 4.0 + auth
        scored.append((score, {**item, "_similarity": round(sim, 3), "_authority": auth, "_score": round(score, 3)}))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


def _slug(title: str) -> str:
    return orchestrator.safe_slug(title, fallback="paper")


def fetch_paper_text(client: orchestrator.SoloQuantHttpClient, item: dict, artifact_root: Path) -> dict:
    """Fetch paper text. Prefer direct PDF download (pdftotext); fall back to crawl4ai markdown."""
    # Try the native PDF path first (works for arxiv/SSRN canonical URLs).
    pdf_error = ""
    try:
        pdf_result = orchestrator.download_and_extract_pdf_text(item, str(artifact_root), category="strategy")
        if pdf_result and (pdf_result.get("markdown") or pdf_result.get("content")):
            return {
                "source": "pdf",
                "url": pdf_result.get("pdf_url") or item.get("url"),
                "text": pdf_result.get("markdown") or pdf_result.get("content"),
                "pdf": True,
            }
    except Exception as exc:  # noqa: BLE001
        pdf_error = str(exc)

    # Fall back to crawl4ai (HTML -> markdown).
    try:
        crawled = client.crawl(item)
        content = str(crawled.get("content") or "").strip()
        if content:
            return {"source": "crawl4ai", "url": item.get("url"), "text": content, "pdf": False}
    except Exception as exc:  # noqa: BLE001
        pdf_error = f"{pdf_error}; crawl error: {exc}".strip("; ")

    return {"source": "none", "url": item.get("url"), "text": "", "pdf": False, "error": pdf_error or "no content"}


def write_local_strategy(out_dir: Path, title: str, url: str, text: str, similarity: float) -> Path:
    """Write extracted paper text as a markdown file for ingest_local_strategies."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(title)}.md"
    ts = datetime.now(timezone.utc).isoformat()
    header = (
        f"# {title}\n\n"
        f"- source_url: {url}\n"
        f"- fetched_at: {ts}\n"
        f"- title_similarity: {similarity}\n"
        f"- fetched_by: search_paper_by_title.py\n\n"
        f"---\n\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return path


def search_and_fetch(config: dict, title: str, top_k: int, out_dir: Path, dry_run: bool) -> dict:
    client = orchestrator.create_http_client_from_config(config)
    artifact_root = Path(config["artifact-root"])

    results = client.search({"category": "strategy", "query": title})
    ranked = rank_results(title, results)

    report: dict = {
        "query_title": title,
        "total_results": len(ranked),
        "candidates": [],
    }

    for item in ranked[: max(1, top_k)]:
        candidate = {
            "title": item.get("title"),
            "url": item.get("url"),
            "similarity": item.get("_similarity"),
            "authority": item.get("_authority"),
            "score": item.get("_score"),
        }
        report["candidates"].append(candidate)

    if not ranked:
        report["status"] = "no_results"
        report["message"] = "SearxNG returned no results for this title. Check SearxNG health (localhost:11236)."
        return report

    best = ranked[0]
    report["selected"] = {
        "title": best.get("title"),
        "url": best.get("url"),
        "similarity": best.get("_similarity"),
    }

    if dry_run:
        report["status"] = "dry_run"
        return report

    fetched = fetch_paper_text(client, best, artifact_root)
    report["fetch"] = {k: fetched.get(k) for k in ("source", "url", "pdf", "error")}
    report["fetch"]["text_chars"] = len(fetched.get("text") or "")

    if not fetched.get("text"):
        report["status"] = "fetch_failed"
        report["message"] = f"Could not extract paper text. {fetched.get('error', '')}"
        return report

    path = write_local_strategy(out_dir, title, fetched["url"], fetched["text"], best.get("_similarity", 0.0))
    report["status"] = "ok"
    report["local_strategy_path"] = str(path)
    report["next_step"] = (
        "Run: python3 Scripts/soloquant_orchestrator.py --config <config> --ingest-local-strategies"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search and fetch a research paper by exact title for SoloQuant.")
    parser.add_argument("--config", default=str(orchestrator.repo_root() / "Launcher" / "config" / "config-soloquant.json"))
    parser.add_argument("--title", required=True, help="Exact (or close) paper title to search for")
    parser.add_argument("--top-k", type=int, default=5, help="How many ranked candidates to report")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Where to write the fetched paper markdown (default: <workflow-root>/local-strategies)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Search and rank only; do not fetch or write")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = orchestrator.load_config(args.config)
    workflow_root = Path(config["workflow-root"])
    out_dir = Path(args.out_dir) if args.out_dir else workflow_root / "local-strategies"

    report = search_and_fetch(config, args.title, args.top_k, out_dir, args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in ("ok", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
