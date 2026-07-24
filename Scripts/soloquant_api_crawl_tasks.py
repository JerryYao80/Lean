#!/usr/bin/env python3
"""
SoloQuant API Crawl Tasks
Direct API data source crawlers for arXiv, Fed RSS, FRED, etc.
Each task fetches data, scores it, and writes metrics to InfluxDB.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import soloquant_orchestrator as orchestrator


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _escape_influx_key(value: str) -> str:
    return str(value).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _write_crawl_metrics_and_items(source_type, source_name, category, report, decisions, config):
    """Write both soloquant_crawl_source and soloquant_crawl_item metrics."""
    now = datetime.now(timezone.utc)
    ts_ns = int(now.timestamp() * 1e9)
    influx_config = config.get("influxdb") or {}
    url = str(influx_config.get("url") or "http://localhost:8086")
    org = str(influx_config.get("org") or "lean")
    bucket = str(influx_config.get("bucket") or "quant")
    token_env = str(influx_config.get("token-env-var") or "INFLUXDB_TOKEN")
    token = os.getenv(token_env, "").strip()
    if not token:
        token = str(influx_config.get("token-default") or "").strip()
    if not token:
        return

    escaped_name = _escape_influx_key(source_name)
    escaped_cat = _escape_influx_key(category)
    crawled = report.get("crawled", {}) if isinstance(report.get("crawled"), dict) else {}
    screened = report.get("screened", {}) if isinstance(report.get("screened"), dict) else {}

    weight_values = [float(d.get("weight_score", 0)) for d in decisions if isinstance(d, dict)]
    lag_values = [float(d.get("lag_minutes", 0)) for d in decisions if isinstance(d, dict)]
    avg_weight = round(sum(weight_values) / len(weight_values), 1) if weight_values else 0.0
    max_weight = round(max(weight_values), 1) if weight_values else 0.0
    avg_lag = round(sum(lag_values) / len(lag_values), 1) if lag_values else 0.0
    max_lag = int(max(lag_values)) if lag_values else 0

    status = str(report.get("status", "unknown"))
    status_code = {"ok": 1, "skipped": 0, "degraded": 2, "error": 3}.get(status, -1)

    fields = [
        f"crawled_count={crawled.get('written_count', len(decisions))}i",
        f"duplicate_count={crawled.get('duplicate_count', 0)}i",
        f"rejected_count={crawled.get('rejected_count', 0)}i",
        f"screened_count={screened.get('written_count', len(decisions))}i",
        f"avg_lag_minutes={avg_lag}",
        f"max_lag_minutes={max_lag}i",
        f"avg_weight_score={avg_weight}",
        f"max_weight_score={max_weight}",
        f"status_code={status_code}i",
        f'status_text="{status}"',
    ]
    source_line = f"soloquant_crawl_source,source_type={source_type},source_name={escaped_name},category={escaped_cat} {','.join(fields)} {ts_ns}"

    item_lines = []
    for d in (decisions or []):
        if not isinstance(d, dict):
            continue
        ws = float(d.get("weight_score", 0))
        lag = int(float(d.get("lag_minutes", 0)))
        tier = "high" if ws >= 7 else ("medium" if ws >= 4 else "low")
        thash = hashlib.md5(str(d.get("title", "")).encode()).hexdigest()[:6]
        item_fields = [f"lag_minutes={lag}i", f"weight_score={round(ws, 1)}", "screened=1i", "is_duplicate=0i"]
        item_lines.append(f"soloquant_crawl_item,source_type={source_type},source_name={escaped_name},category={escaped_cat},weight_tier={tier},title_hash={thash} {','.join(item_fields)} {ts_ns}")

    all_lines = [source_line] + item_lines
    try:
        orchestrator.write_lines_to_influx(all_lines, url, org, bucket, token)
    except Exception:
        pass


def _llm_score(content, config):
    """Score content using LLM, with rule-based fallback."""
    if not content.strip():
        return 0.0
    llm_config = config.get("llm") or config.get("glm") or {}
    api_key_env = str(llm_config.get("api-key-env-var") or "LLM_API_KEY")
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        api_key = str(llm_config.get("api-key-default") or "").strip()
    if not api_key:
        return _rule_based_score(content)
    base_url = str(llm_config.get("base-url") or "https://api.deepseek.com")
    model = str(llm_config.get("model") or "deepseek-v4-pro")
    payload = {"model": model, "messages": [
        {"role": "system", "content": "Quant info scorer. Score 0-10. 9-10=central bank/shock; 7-8=key data; 5-6=routine; 3-4=generic; 0-2=spam. Output only a float."},
        {"role": "user", "content": content[:8000]}], "temperature": 0.1}
    try:
        import urllib.request, re
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        match = re.search(r"(\d+\.?\d*)", text)
        if match:
            return round(max(0.0, min(10.0, float(match.group(1)))), 1)
    except Exception:
        pass
    return _rule_based_score(content)


def _rule_based_score(content):
    score = 3.0
    lower = content.lower()
    for kw in ["rate cut","fomc","lpr","mlf","black swan","decision","factor","alpha"]:
        if kw in lower: score += 1.5; break
    for kw in ["strategy","momentum","backtest","research"]:
        if kw in lower: score += 0.5; break
    if len(content) > 5000: score += 0.5
    elif len(content) < 200: score -= 1.0
    return round(max(0.0, min(10.0, score)), 1)


# ─── arXiv API ────────────────────────────────────────────────────────────────

def crawl_arxiv_api(config: dict) -> dict:
    """Crawl arXiv q-fin + cs.LG papers via direct API."""
    import urllib.request, urllib.parse
    now = datetime.now(timezone.utc)
    decisions = []
    try:
        for category in ["q-fin", "cs.LG"]:
            query = f"cat:{category}"
            params = urllib.parse.urlencode({"search_query": query, "sortBy": "submittedDate", "max_results": "20"})
            url = f"http://export.arxiv.org/api/query?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "SoloQuant/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns):
                title = entry.find("atom:title", ns).text.strip().replace("\n", " ") if entry.find("atom:title", ns) is not None else ""
                link = entry.find("atom:id", ns).text.strip() if entry.find("atom:id", ns) is not None else ""
                published = entry.find("atom:published", ns).text.strip() if entry.find("atom:published", ns) is not None else ""
                summary = entry.find("atom:summary", ns).text.strip()[:4000] if entry.find("atom:summary", ns) is not None else ""
                # Compute lag
                lag = 0
                if published:
                    try:
                        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        lag = int((now - pub_dt).total_seconds() / 60)
                    except Exception:
                        pass
                score = _llm_score(f"{title}\n{summary}", config)
                decisions.append({"title": title, "url": link, "weight_score": score, "lag_minutes": lag})
    except Exception as exc:
        return {"status": "error", "error": str(exc), "decisions": decisions}

    report = {"status": "ok", "crawled": {"written_count": len(decisions)}, "screened": {"written_count": len(decisions)}}
    _write_crawl_metrics_and_items("api", "arXiv_API", "strategy", report, decisions, config)
    return {"status": "ok", "source": "arXiv_API", "items": len(decisions), "decisions": decisions}


# ─── Fed RSS ──────────────────────────────────────────────────────────────────

def crawl_fed_rss(config: dict) -> dict:
    """Subscribe to Federal Reserve RSS feeds."""
    import urllib.request
    now = datetime.now(timezone.utc)
    feeds = [
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "https://www.federalreserve.gov/feeds/speeches.xml",
    ]
    decisions = []
    for feed_url in feeds:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "SoloQuant/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            for item in root.iter("item"):
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date = item.findtext("pubDate", "").strip()
                description = item.findtext("description", "").strip()[:4000]
                lag = 0
                if pub_date:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub_date)
                        lag = int((now - pub_dt).total_seconds() / 60)
                    except Exception:
                        pass
                score = _llm_score(f"{title}\n{description}", config)
                if score >= 5.0:
                    decisions.append({"title": title, "url": link, "weight_score": score, "lag_minutes": lag})
        except Exception:
            continue

    report = {"status": "ok", "crawled": {"written_count": len(decisions)}, "screened": {"written_count": len(decisions)}}
    _write_crawl_metrics_and_items("rss", "Fed_RSS", "finance_intelligence", report, decisions, config)
    return {"status": "ok", "source": "Fed_RSS", "items": len(decisions), "decisions": decisions}


# ─── FRED API ─────────────────────────────────────────────────────────────────

def crawl_fred_api(config: dict) -> dict:
    """Fetch key macro indicators from FRED API."""
    import urllib.request, urllib.parse
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "FRED_API_KEY not set", "decisions": []}

    series = ["FEDFUNDS", "DGS10", "CPIAUCSL", "UNRATE", "GDP", "T10Y2Y"]
    now = datetime.now(timezone.utc)
    decisions = []
    for sid in series:
        try:
            params = urllib.parse.urlencode({"api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": "2"})
            url = f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}&{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "SoloQuant/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            observations = data.get("observations", [])
            if len(observations) >= 2:
                curr = float(observations[0].get("value", "0"))
                prev = float(observations[1].get("value", "0"))
                change = curr - prev
                if abs(change) > 0.01:
                    title = f"FRED {sid}: {prev} -> {curr} (change: {change:+.4f})"
                    score = min(10.0, 5.0 + abs(change) * 10)  # Larger changes = higher score
                    decisions.append({"title": title, "url": f"https://fred.stlouisfed.org/series/{sid}", "weight_score": round(score, 1), "lag_minutes": 0})
        except Exception:
            continue

    report = {"status": "ok", "crawled": {"written_count": len(decisions)}, "screened": {"written_count": len(decisions)}}
    _write_crawl_metrics_and_items("api", "FRED_API", "finance_intelligence", report, decisions, config)
    return {"status": "ok", "source": "FRED_API", "items": len(decisions), "decisions": decisions}


# ─── Orchestrate All API Sources ──────────────────────────────────────────────

def crawl_all_api_sources(config: dict) -> dict:
    """Run all API crawl tasks and aggregate results."""
    results = {}
    all_decisions = []
    for name, fn in [("arXiv_API", crawl_arxiv_api), ("Fed_RSS", crawl_fed_rss), ("FRED_API", crawl_fred_api)]:
        try:
            result = fn(config)
            results[name] = {"status": result.get("status", "ok"), "items": result.get("items", 0)}
            all_decisions.extend(result.get("decisions", []))
        except Exception as exc:
            results[name] = {"status": "error", "error": str(exc)}
    return {"status": "ok", "api_sources": results, "total_decisions": len(all_decisions)}


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SoloQuant API Crawl Tasks")
    parser.add_argument("--config", default=None)
    parser.add_argument("--source", choices=["arxiv", "fed", "fred", "all"], default="all")
    args = parser.parse_args()
    config = orchestrator.load_config(args.config) if args.config else {}
    if args.source == "arxiv":
        report = crawl_arxiv_api(config)
    elif args.source == "fed":
        report = crawl_fed_rss(config)
    elif args.source == "fred":
        report = crawl_fred_api(config)
    else:
        report = crawl_all_api_sources(config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
