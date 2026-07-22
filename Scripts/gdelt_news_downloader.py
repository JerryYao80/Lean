#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CSV_COLUMNS = [
    "trade_date",
    "query",
    "article_count",
    "mean_tone",
    "positive_count",
    "negative_count",
    "neutral_count",
    "source_count",
    "top_domain",
]

FINANCIAL_INTELLIGENCE_SCOPES = [
    ("market_structure", "market volatility liquidity trading volume"),
    ("monetary_policy", "central bank interest rate monetary policy"),
    ("macro_growth", "GDP inflation unemployment economic growth"),
    ("credit_risk", "debt default credit rating bond yield"),
    ("sector_rotation", "sector industry earnings profit guidance"),
    ("commodities_energy", "oil gas copper iron ore commodity prices"),
    ("geopolitical_risk", "sanctions trade war tariff supply chain"),
    ("regulatory_policy", "regulation investigation antitrust policy"),
]

EVENT_STOP_WORDS = {
    "about",
    "after",
    "again",
    "amid",
    "and",
    "announces",
    "are",
    "as",
    "for",
    "from",
    "has",
    "into",
    "its",
    "market",
    "markets",
    "news",
    "on",
    "over",
    "said",
    "says",
    "stock",
    "stocks",
    "the",
    "their",
    "to",
    "with",
}


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def ts_code_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = str(ts_code).strip().upper().split(".", 1)
    return ticker, "sse" if suffix == "SH" else "szse"


def build_doc_api_url(query: str, start_date: date, end_date: date, max_records: int = 250) -> str:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "sort": "datedesc",
        "maxrecords": max(1, min(int(max_records), 250)),
        "startdatetime": f"{start_date:%Y%m%d}000000",
        "enddatetime": f"{end_date:%Y%m%d}235959",
    }
    return f"https://api.gdeltproject.org/api/v2/doc/doc?{urlencode(params)}"


def fetch_articles(query: str, start_date: date, end_date: date, max_records: int = 250, timeout: int = 30) -> list[dict]:
    url = build_doc_api_url(query, start_date, end_date, max_records)
    request = Request(url, headers={"User-Agent": "Lean-GDELT-News-Downloader/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    articles = payload.get("articles") or []
    return articles if isinstance(articles, list) else []


def parse_seen_date(value: str) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y%m%d")
    except ValueError:
        return None


def parse_tone(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def clean_text(value) -> str:
    return " ".join(str(value or "").split())


def dedupe_articles(articles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for article in articles:
        key = clean_text(article.get("url")) or f"{clean_text(article.get('title'))}|{clean_text(article.get('domain'))}|{clean_text(article.get('seendate'))}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped


def build_financial_intelligence_queries(base_query: str) -> list[dict]:
    base = clean_text(base_query)
    queries = [{"scope": "core", "query": base}]
    for scope, suffix in FINANCIAL_INTELLIGENCE_SCOPES:
        queries.append({"scope": scope, "query": f"{base} {suffix}"})

    deduped: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        if query["query"] in seen:
            continue
        seen.add(query["query"])
        deduped.append(query)
    return deduped


def extract_article_samples(articles: list[dict], limit: int = 5) -> list[dict]:
    samples: list[dict] = []
    for article in articles[: max(0, int(limit))]:
        samples.append(
            {
                "seen_date": parse_seen_date(article.get("seendate")) or "",
                "title": clean_text(article.get("title")),
                "domain": clean_text(article.get("domain")),
                "url": clean_text(article.get("url")),
                "tone": parse_tone(article.get("tone")),
                "language": clean_text(article.get("language")),
                "source_country": clean_text(article.get("sourcecountry")),
            }
        )
    return samples


def event_key_from_title(title: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", clean_text(title).lower())
    keywords = [word for word in words if len(word) > 2 and word not in EVENT_STOP_WORDS]
    return " ".join(keywords[:4]) if keywords else clean_text(title)[:80].lower()


def days_between(first_seen: str, last_seen: str) -> int:
    first = parse_date(first_seen)
    last = parse_date(last_seen)
    return (last - first).days + 1


def extract_event_lifecycles(
    articles: list[dict],
    start_date: date,
    end_date: date,
    limit: int = 10,
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        seen_date = parse_seen_date(article.get("seendate"))
        title = clean_text(article.get("title"))
        if not seen_date or not title:
            continue
        grouped[event_key_from_title(title)].append(article)

    lifecycles: list[dict] = []
    end_key = f"{end_date:%Y%m%d}"
    for event_key, event_articles in grouped.items():
        dates = [parse_seen_date(article.get("seendate")) for article in event_articles]
        dates = [seen_date for seen_date in dates if seen_date]
        domains = [clean_text(article.get("domain")) for article in event_articles]
        domains = [domain for domain in domains if domain]
        tones = [parse_tone(article.get("tone")) for article in event_articles]
        daily_counts = Counter(dates)
        first_seen = min(dates)
        last_seen = max(dates)
        lifecycles.append(
            {
                "event_key": event_key,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "duration_days": days_between(first_seen, last_seen),
                "status": "active" if last_seen >= end_key else "stale",
                "article_count": len(event_articles),
                "source_count": len(set(domains)),
                "peak_date": daily_counts.most_common(1)[0][0],
                "mean_tone": round(sum(tones) / len(tones), 6) if tones else 0.0,
                "top_domains": [domain for domain, _ in Counter(domains).most_common(3)],
                "sample_titles": [clean_text(article.get("title")) for article in event_articles[:3]],
            }
        )

    return sorted(
        lifecycles,
        key=lambda lifecycle: (lifecycle["article_count"], lifecycle["source_count"], lifecycle["last_seen"]),
        reverse=True,
    )[: max(0, int(limit))]


def iter_dates(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def aggregate_articles(articles: list[dict], query: str, start_date: date, end_date: date) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        trade_date = parse_seen_date(article.get("seendate"))
        if trade_date:
            grouped[trade_date].append(article)

    rows: list[dict] = []
    for current in iter_dates(start_date, end_date):
        trade_date = f"{current:%Y%m%d}"
        daily_articles = grouped.get(trade_date, [])
        tones = [parse_tone(article.get("tone")) for article in daily_articles]
        domains = [str(article.get("domain") or "").strip() for article in daily_articles]
        domains = [domain for domain in domains if domain]
        domain_counts = Counter(domains)
        mean_tone = sum(tones) / len(tones) if tones else 0.0
        rows.append(
            {
                "trade_date": trade_date,
                "query": query,
                "article_count": len(daily_articles),
                "mean_tone": round(mean_tone, 6),
                "positive_count": sum(1 for tone in tones if tone > 1.0),
                "negative_count": sum(1 for tone in tones if tone < -1.0),
                "neutral_count": sum(1 for tone in tones if -1.0 <= tone <= 1.0),
                "source_count": len(domain_counts),
                "top_domain": domain_counts.most_common(1)[0][0] if domain_counts else "",
            }
        )
    return rows


def write_symbol_csv(output_root: Path, ts_code: str, rows: list[dict]) -> Path:
    ticker, market = ts_code_parts(ts_code)
    path = Path(output_root) / market / "daily" / f"{ticker}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            output = dict(row)
            output["mean_tone"] = f"{float(output.get('mean_tone') or 0.0):.6f}"
            writer.writerow(output)
    return path


def load_symbol_queries(path: Path) -> dict[str, str]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Symbol query file must be a JSON object like {\"600000.SH\": \"company name\"}")
    return {str(symbol).upper(): str(query) for symbol, query in payload.items() if str(query).strip()}


def download_symbol(
    ts_code: str,
    query: str,
    output_root: Path,
    start_date: date,
    end_date: date,
    max_records: int,
    sleep_seconds: float,
    sample_limit: int = 5,
    expand_financial_intelligence: bool = False,
    lifecycle_limit: int = 10,
) -> dict:
    intelligence_queries = build_financial_intelligence_queries(query) if expand_financial_intelligence else [{"scope": "core", "query": query}]
    scoped_articles: list[dict] = []
    query_errors: list[dict] = []
    for index, intelligence_query in enumerate(intelligence_queries):
        try:
            fetched_articles = fetch_articles(intelligence_query["query"], start_date, end_date, max_records=max_records)
        except Exception as error:
            query_errors.append(
                {
                    "scope": intelligence_query["scope"],
                    "query": intelligence_query["query"],
                    "error_type": type(error).__name__,
                    "error": clean_text(error),
                }
            )
            fetched_articles = []
        for article in fetched_articles:
            enriched_article = dict(article)
            enriched_article["intelligence_scope"] = intelligence_query["scope"]
            enriched_article["intelligence_query"] = intelligence_query["query"]
            scoped_articles.append(enriched_article)
        if sleep_seconds > 0 and index < len(intelligence_queries) - 1:
            time.sleep(sleep_seconds)

    articles = dedupe_articles(scoped_articles)
    rows = aggregate_articles(articles, query=query, start_date=start_date, end_date=end_date)
    output_path = write_symbol_csv(output_root, ts_code, rows)
    return {
        "symbol": ts_code,
        "query": query,
        "article_count": sum(row["article_count"] for row in rows),
        "output": str(output_path),
        "intelligence_queries": intelligence_queries,
        "query_errors": query_errors,
        "sample_articles": extract_article_samples(articles, limit=sample_limit),
        "event_lifecycles": extract_event_lifecycles(articles, start_date, end_date, limit=lifecycle_limit),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download GDELT DOC 2.0 news tone snapshots into LEAN custom-data CSV files.")
    parser.add_argument("--symbol-query-file", required=True, help="JSON object mapping ts_code to GDELT query text.")
    parser.add_argument("--output-root", default="Data/alternative/gdelt-news-sentiment")
    parser.add_argument("--start-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--max-records", type=int, default=250, help="GDELT DOC API articlelist max records per symbol, capped at 250.")
    parser.add_argument("--sleep-seconds", type=float, default=1.0, help="Delay between public API requests.")
    parser.add_argument("--sample-articles", type=int, default=5, help="Number of downloaded GDELT article samples to include in the JSON report.")
    parser.add_argument("--expand-financial-intelligence", action="store_true", help="Fetch additional macro, policy, credit, sector, commodity, and geopolitical intelligence queries for each symbol.")
    parser.add_argument("--lifecycle-events", type=int, default=10, help="Number of important event lifecycle summaries to include in the JSON report.")
    parser.add_argument("--report-file", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = parse_date(args.start_date)
    end = parse_date(args.end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")

    symbol_queries = load_symbol_queries(Path(args.symbol_query_file))
    output_root = Path(args.output_root)
    results = [
        download_symbol(
            symbol,
            query,
            output_root,
            start,
            end,
            args.max_records,
            args.sleep_seconds,
            args.sample_articles,
            args.expand_financial_intelligence,
            args.lifecycle_events,
        )
        for symbol, query in sorted(symbol_queries.items())
    ]
    report = {
        "start_date": f"{start:%Y%m%d}",
        "end_date": f"{end:%Y%m%d}",
        "symbol_count": len(results),
        "symbols": results,
    }
    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
