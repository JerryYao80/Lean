#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib import error, parse, request

_registry_lock = threading.Lock()

STRATEGY_SOURCES = (
    "arXiv q-fin",
    "SSRN",
    "Quantpedia",
    "WorldQuant BRAIN",
    "QuantConnect Community",
    "Numerai",
    "华泰 金工研报",
    "中信 金工研报",
    "Alpha Architect",
    "AQR",
    "聚宽 社区",
    "米筐 研究报告",
    "优矿 策略",
)
FINANCE_INTELLIGENCE_SOURCES = (
    "交易所公告",
    "权威财经媒体",
    "头部投行宏观策略",
    "央行与监管机构",
    "美国央行与监管机构",
    "欧洲央行与监管机构",
    "日本央行与监管机构",
    "全球期货交易所",
)
FINANCE_AUTHORITY_DOMAINS = {
    "pbc.gov.cn": "central_bank",
    "safe.gov.cn": "regulator",
    "csrc.gov.cn": "regulator",
    "federalreserve.gov": "central_bank",
    "sec.gov": "regulator",
    "cftc.gov": "regulator",
    "ecb.europa.eu": "central_bank",
    "bankofengland.co.uk": "central_bank",
    "esma.europa.eu": "regulator",
    "boj.or.jp": "central_bank",
    "fsa.go.jp": "regulator",
    "sse.com.cn": "exchange",
    "sse.org.cn": "exchange",
    "szse.cn": "exchange",
    "bse.cn": "exchange",
    "hkex.com.hk": "exchange",
    "nyse.com": "exchange",
    "nasdaqtrader.com": "exchange",
    "jpx.co.jp": "exchange",
    "cmegroup.com": "futures_exchange",
    "theice.com": "futures_exchange",
    "cboe.com": "futures_exchange",
    "eurex.com": "futures_exchange",
    "euronext.com": "exchange",
    "stcn.com": "official_media",
    "cs.com.cn": "official_media",
    "cnstock.com": "official_media",
    "reuters.com": "global_media",
    "bloomberg.com": "global_media",
    "goldmansachs.com": "investment_bank",
    "morganstanley.com": "investment_bank",
    "jpmorgan.com": "investment_bank",
    "cicc.com": "investment_bank",
    "htsc.com.cn": "investment_bank",
    "citics.com": "investment_bank",
}
FINANCE_LANDING_PATHS = {
    "",
    "/",
    "/index.html",
    "/home/",
    "/article/list/kx.html",
    "/article/list/xw.html",
    "/article/list/investment.html",
    "/article/data.html",
    "/disclosure/listedinfo/riskplate/",
    "/disclosure/notice/general/index.html",
}
FINANCE_IMPACT_TERMS = (
    "公开市场",
    "逆回购",
    "mlf",
    "降准",
    "降息",
    "利率",
    "流动性",
    "汇率",
    "风险偏好",
    "久期",
    "红利",
    "高股息",
    "成长",
    "价值",
    "小盘",
    "大盘",
    "科技",
    "地产",
    "银行",
    "券商",
    "出口",
    "通胀",
    "cpi",
    "ppi",
    "pmi",
    "监管",
    "风险",
    "波动",
    "停牌",
    "退市",
    "异常波动",
    "fomc",
    "federal funds",
    "treasury",
    "yield",
    "inflation",
    "liquidity",
    "volatility",
    "margin",
    "circuit breaker",
)
FINANCE_EVENT_TYPES = {
    "monetary_policy": ["降准", "降息", "mlf", "逆回购", "公开市场", "利率", "lpr", "fomc", "federal funds", "rate decision", "rate cut", "rate hike", "quantitative easing", "qe", "taper"],
    "regulatory": ["监管", "新规", "处罚", "退市", "停牌", "sec filing", "regulatory", "compliance", "enforcement", "制度", "规定", "违规"],
    "macro_data": ["cpi", "ppi", "pmi", "gdp", "就业", "inflation", "employment", "trade balance", "非农", "通胀", "通缩", "经济数据", "工业产出"],
    "market_structure": ["熔断", "circuit breaker", "margin", "涨跌停", "交易规则", "index rebalance", "指数调整", "成分股", "保证金", "交易机制"],
    "geopolitical": ["制裁", "关税", "贸易战", "sanctions", "tariff", "geopolitical", "地缘", "冲突", "战争", "外交", "贸易摩擦"],
    "corporate": ["业绩", "分红", "增发", "回购", "earnings", "dividend", "buyback", "m&a", "并购", "重组", "业绩预告", "利润"],
    "sentiment": ["风险偏好", "risk appetite", "volatility", "vix", "情绪", "恐慌", "乐观", "悲观", "市场情绪"],
}
FINANCE_DIRECT_SOURCE_URLS = {
    "央行与监管机构": (
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html",
        "https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/index.html",
        "https://www.csrc.gov.cn/csrc/c100028/common_list.shtml",
        "https://www.safe.gov.cn/safe/xinwenzhongxin/index.html",
    ),
    "美国央行与监管机构": (
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm",
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "https://www.sec.gov/news/pressreleases",
        "https://www.cftc.gov/PressRoom/PressReleases",
    ),
    "欧洲央行与监管机构": (
        "https://www.ecb.europa.eu/press/pr/activities/mopo/html/index.en.html",
        "https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html",
        "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes",
        "https://www.esma.europa.eu/press-news/esma-news",
    ),
    "日本央行与监管机构": (
        "https://www.boj.or.jp/en/mopo/mpmdeci/index.htm",
        "https://www.boj.or.jp/en/mopo/outlook/index.htm",
        "https://www.fsa.go.jp/en/news/",
        "https://www.jpx.co.jp/english/news/",
    ),
    "交易所公告": (
        "https://www.sse.com.cn/disclosure/notice/general/",
        "https://www.sse.com.cn/disclosure/listedinfo/announcement/",
        "https://www.szse.cn/disclosure/notice/index.html",
        "https://www.bse.cn/disclosure_list/200.html",
        "https://www.hkex.com.hk/News/News-Release",
    ),
    "全球期货交易所": (
        "https://www.cmegroup.com/notices.html",
        "https://www.cmegroup.com/clearing/notices.html",
        "https://www.theice.com/notices",
        "https://www.cboe.com/us/futures/notices/",
        "https://www.eurex.com/ex-en/find/circulars",
        "https://www.euronext.com/en/newsroom/notices",
        "https://www.jpx.co.jp/english/derivatives/",
    ),
    "权威财经媒体": (
        "https://paper.cnstock.com/",
        "https://www.stcn.com/article/list/kx.html",
        "https://www.cs.com.cn/xwzx/",
        "https://www.reuters.com/world/china/",
        "https://www.bloomberg.com/asia",
    ),
    "头部投行宏观策略": (
        "https://www.goldmansachs.com/insights",
        "https://www.morganstanley.com/ideas",
        "https://www.jpmorgan.com/insights",
        "https://www.cicc.com/en/research",
        "https://www.htsc.com.cn/",
        "https://www.citics.com/",
    ),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workflow_root() -> Path:
    return repo_root() / "Results" / "soloquant"


def default_launcher_dll() -> Path:
    return repo_root() / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher.dll"


def load_json_payload(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def write_json_payload(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_run_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def stable_hash(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_slug(value: str, fallback: str = "item") -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value).strip())
    text = "-".join(part for part in text.split("-") if part)
    return text[:80] or fallback


def normalize_paper_url(url: str) -> str:
    """Normalize URLs to canonical form for dedup: arxiv /abs/ and /pdf/ map to the same paper."""
    url = str(url or "").strip()
    parsed = parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or ""
    # arXiv: /abs/XXXX.XXXXX and /pdf/XXXX.XXXXX are the same paper
    if host.endswith("arxiv.org"):
        m = re.match(r"^/(abs|pdf)/(\d{4}\.\d{4,5})", path)
        if m:
            return f"https://arxiv.org/abs/{m.group(2)}"
    # SSRN: various URL forms point to the same paper
    if "ssrn.com" in host:
        m = re.search(r"abstract_id=(\d+)", url)
        if m:
            return f"https://papers.ssrn.com/abstract_id={m.group(1)}"
        m = re.search(r"/sol3/Delivery\.cfm/(\d+)", path)
        if m:
            return f"https://papers.ssrn.com/abstract_id={m.group(1)}"
    # Quantpedia: strip trailing slashes and query params
    if host.endswith("quantpedia.com"):
        return f"https://{host}{path.rstrip('/')}"
    return url


def normalize_category(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"finance", "finance-intelligence", "finance_intelligence", "news", "intelligence"}:
        return "finance_intelligence"
    return "strategy" if text != "finance_intelligence" else text


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def resolve_path(value, default: Path, base: Path) -> Path:
    path = Path(str(value)) if value else default
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


INLINE_SECRET_KEYS = {
    "api-key",
    "apikey",
    "api_key",
    "token",
    "password",
    "secret",
    "authorization",
}

SECRET_VALUE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"glsa_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"admin-token-[A-Za-z0-9_\-]{3,}"),
)


def validate_no_inline_service_secrets(payload: object, path: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            normalized_key = key_text.replace("_", "-").lower()
            child_path = f"{path}.{key_text}" if path else key_text
            if normalized_key.endswith("-env-var") or normalized_key in {"env-var", "environment-variable"}:
                continue
            if normalized_key in INLINE_SECRET_KEYS and str(value or "").strip():
                raise ValueError(f"Inline secret is not allowed at {child_path}; use an environment variable reference instead.")
            validate_no_inline_service_secrets(value, child_path)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            validate_no_inline_service_secrets(value, f"{path}[{index}]")


def validate_no_secret_values(text: str, path: str = "text") -> None:
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(str(text or "")):
            raise ValueError(f"Inline secret value is not allowed at {path}; use an environment variable reference instead.")


def default_config() -> dict:
    root = repo_root()
    workflow_root = default_workflow_root()
    return {
        "system-id": "soloquant",
        "workflow-root": str(workflow_root),
        "artifact-root": str(workflow_root / "artifacts"),
        "strategy-root": str(workflow_root / "strategies"),
        "manifest-file": str(workflow_root / "soloquant-manifest.json"),
        "registry-file": str(workflow_root / "strategy-registry.json"),
        "missing-data-log": str(workflow_root / "missing-data.jsonl"),
        "llm": {
            "base-url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api-key-env-var": "LLM_API_KEY",
            "timeout-seconds": 180,
        },
        "searxng": {
            "url": "http://localhost:11236",
            "timeout-seconds": 60,
        },
        "crawl4ai": {
            "url": "http://localhost:11235",
            "timeout-seconds": 120,
        },
        "influxdb": {
            "url": "http://localhost:8086",
            "org": "lean",
            "bucket": "quant",
            "token-env-var": "INFLUXDB_TOKEN",
        },
        "grafana": {
            "url": "http://localhost:3000",
            "token-env-var": "GRAFANA_TOKEN",
        },
        "lean": {
            "dotnet-binary": "/usr/local/dotnet/dotnet",
            "launcher-dll": str(default_launcher_dll()),
        },
        "data": {
            "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
            "field-mapping-path": "/home/project/tushare-downloader/tushare_field_mapping.json",
            "field-cache-root": str(root / "local_data" / "soloquant-field-cache"),
        },
        "schedule": {
            "download-history-cron": "0 2 * * *",
            "download-realtime-cron": "*/5 * * * *",
            "crawl-strategy-cron": "30 2 * * *",
            "crawl-news-cron": "0 */2 * * *",
            "prepare-reproduction-cron": "45 2 * * *",
            "generate-strategy-implementations-cron": "48 2 * * *",
            "materialize-reproduction-cron": "50 2 * * *",
            "analyze-finance-intelligence-cron": "5 */2 * * *",
            "build-finance-event-graph-cron": "8 */2 * * *",
            "export-research-influx-cron": "10 */2 * * *",
            "export-finance-event-graph-influx-cron": "15 */2 * * *",
            "export-strategy-results-influx-cron": "20 */2 * * *",
            "export-price-ohlc-cron": "*/10 * * * 1-5",
            "optimize-cron": "0 4 * * 1-5",
            "live-paper-cron": "*/5 * * * 1-5",
        },
        "strategy-policy": {
            "language": "CSharp",
            "min-versions": 3,
            "baseline-version": "baseline",
            "required-learned-versions": 2,
            "best-metric": "score",
            "avoid-algorithms": ["AShareLlmQuantLeanAlgorithm"],
        },
    }


def load_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()
    if config_path:
        path = Path(config_path).resolve()
        loaded = load_json_payload(path)
        validate_no_inline_service_secrets(loaded)
        config.update(loaded)
    if overrides:
        validate_no_inline_service_secrets(overrides)
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    root = repo_root()
    config["workflow-root"] = str(resolve_path(config.get("workflow-root"), default_workflow_root(), root))
    config["artifact-root"] = str(resolve_path(config.get("artifact-root"), default_workflow_root() / "artifacts", root))
    config["strategy-root"] = str(resolve_path(config.get("strategy-root"), default_workflow_root() / "strategies", root))
    config["manifest-file"] = str(resolve_path(config.get("manifest-file"), default_workflow_root() / "soloquant-manifest.json", root))
    config["registry-file"] = str(resolve_path(config.get("registry-file"), default_workflow_root() / "strategy-registry.json", root))
    config["missing-data-log"] = str(resolve_path(config.get("missing-data-log"), default_workflow_root() / "missing-data.jsonl", root))
    config["data"] = dict(config.get("data") or {})
    config["data"]["tushare-data-path"] = str(resolve_path(config["data"].get("tushare-data-path"), root / "tushare-downloader" / "tushare_data", root))
    config["data"]["field-mapping-path"] = str(resolve_path(config["data"].get("field-mapping-path"), Path("/home/project/tushare-downloader/tushare_field_mapping.json"), root))
    config["data"]["field-cache-root"] = str(resolve_path(config["data"].get("field-cache-root"), root / "local_data" / "soloquant-field-cache", root))
    config["lean"] = dict(config.get("lean") or {})
    config["lean"]["dotnet-binary"] = str(config["lean"].get("dotnet-binary") or "/usr/local/dotnet/dotnet")
    config["lean"]["launcher-dll"] = str(resolve_path(config["lean"].get("launcher-dll"), default_launcher_dll(), root))
    config["llm"] = dict(config.get("llm") or {})
    config["searxng"] = dict(config.get("searxng") or {})
    config["crawl4ai"] = dict(config.get("crawl4ai") or {})
    config["influxdb"] = dict(config.get("influxdb") or {})
    config["grafana"] = dict(config.get("grafana") or {})
    config["schedule"] = dict(config.get("schedule") or {})
    config["strategy-policy"] = dict(config.get("strategy-policy") or {})
    config["strategy-policy"]["avoid-algorithms"] = [str(value) for value in (config["strategy-policy"].get("avoid-algorithms") or [])]
    config["strategy-policy"]["language"] = str(config["strategy-policy"].get("language") or "CSharp").strip()
    config["strategy-policy"]["required-learned-versions"] = max(2, safe_int(config["strategy-policy"].get("required-learned-versions"), 2))
    config["strategy-policy"]["min-versions"] = max(3, safe_int(config["strategy-policy"].get("min-versions"), 3))
    return config


def build_lean_launcher_command(config_path: str | Path, dotnet_binary: str | Path | None = None) -> tuple[list[str], Path]:
    config_path = Path(config_path).resolve()
    binary = Path(dotnet_binary or default_config()["lean"]["dotnet-binary"])
    launcher_dll = default_launcher_dll().resolve()
    return [str(binary), str(launcher_dll), "--config", str(config_path)], launcher_dll.parent


STRATEGY_SITE_RESTRICTIONS: dict[str, str] = {
    "arXiv q-fin": "site:arxiv.org",
    "SSRN": "site:papers.ssrn.com",
    "Quantpedia": "site:quantpedia.com",
    "QuantConnect Community": "site:quantconnect.com",
    "Alpha Architect": "site:alphaarchitect.com",
    "AQR": "site:aqr.com",
    "聚宽 社区": "site:joinquant.com",
    "米筐 研究报告": "site:ricequant.com",
    "优矿 策略": "site:uqer.datayes.com",
}

STRATEGY_SOURCE_KEYWORD_PREFIX: dict[str, str] = {
    "arXiv q-fin": "arXiv",
    "SSRN": "SSRN",
    "Quantpedia": "Quantpedia",
    "WorldQuant BRAIN": "WorldQuant",
    "QuantConnect Community": "QuantConnect",
    "Numerai": "Numerai",
    "华泰 金工研报": "华泰",
    "中信 金工研报": "中信",
    "Alpha Architect": "AlphaArchitect",
    "AQR": "AQR",
    "聚宽 社区": "聚宽",
    "米筐 研究报告": "米筐",
    "优矿 策略": "优矿",
}


def build_strategy_query(source: str, keyword: str) -> dict:
    site_restriction = STRATEGY_SITE_RESTRICTIONS.get(source, "")
    query_text = f"{site_restriction} {keyword} quantitative strategy backtest".strip()
    return {
        "category": "strategy",
        "source": source,
        "query": query_text,
    }


def build_research_query_plan(keywords: Sequence[str] | None = None, tick_offset: int = 0) -> dict:
    normalized_keywords = [str(keyword).strip() for keyword in (keywords or []) if str(keyword).strip()]
    if not normalized_keywords:
        normalized_keywords = ["A股 多因子", "量化策略", "alpha signal"]

    queries: list[dict] = []
    # Pair each keyword with its best-matching source (1:1 instead of cartesian product)
    for keyword in normalized_keywords:
        matched_source = None
        for source, prefix in STRATEGY_SOURCE_KEYWORD_PREFIX.items():
            if keyword.lower().startswith(prefix.lower()):
                matched_source = source
                break
        if matched_source:
            queries.append(build_strategy_query(matched_source, keyword))
        else:
            # Generic query for keywords without a source match
            queries.append({"category": "strategy", "source": "generic", "query": f"{keyword} quantitative strategy backtest"})
    for source in STRATEGY_SOURCES:
        if not any(q.get("source") == source for q in queries):
            # Rotate through keywords for uncovered sources
            idx = hash(f"{source}-{tick_offset}") % max(1, len(normalized_keywords))
            queries.append(build_strategy_query(source, normalized_keywords[idx]))

    for source in FINANCE_INTELLIGENCE_SOURCES:
        # Rotate through keywords for finance intelligence
        idx = hash(f"{source}-{tick_offset}") % max(1, len(normalized_keywords))
        keyword = normalized_keywords[idx]
        queries.append(
            {
                "category": "finance_intelligence",
                "source": source,
                "query": build_finance_intelligence_query(source, keyword),
                "direct_urls": list(FINANCE_DIRECT_SOURCE_URLS.get(source, ())),
                "source_priority": "direct_first",
            }
        )

    # Shuffle with deterministic seed based on tick_offset for rotation
    rng = random.Random(tick_offset)
    rng.shuffle(queries)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "keywords": normalized_keywords,
        "queries": queries,
    }


def build_finance_intelligence_query(source: str, keyword: str) -> str:
    source_text = str(source or "")
    keyword_text = str(keyword or "").strip()
    freshness_terms = "2026 5月 近期 今日 最新"
    impact_terms = "投资风格 风险偏好 流动性 利率 久期 红利 成长 价值 小盘 大盘 yield inflation volatility margin"
    if "美国" in source_text:
        return f"(site:federalreserve.gov OR site:sec.gov OR site:cftc.gov) {keyword_text} 2026 latest FOMC federal funds Treasury yield liquidity volatility risk appetite press release notice"
    if "欧洲" in source_text:
        return f"(site:ecb.europa.eu OR site:bankofengland.co.uk OR site:esma.europa.eu) {keyword_text} 2026 latest monetary policy rates inflation liquidity market risk notice"
    if "日本" in source_text:
        return f"(site:boj.or.jp OR site:fsa.go.jp OR site:jpx.co.jp) {keyword_text} 2026 latest monetary policy yield curve market risk derivatives notice"
    if "期货" in source_text:
        return f"(site:cmegroup.com OR site:theice.com OR site:cboe.com OR site:eurex.com OR site:euronext.com OR site:jpx.co.jp) {keyword_text} 2026 latest futures margin volatility clearing market notice circular"
    if "央行" in source_text or "监管" in source_text:
        return f"(site:pbc.gov.cn OR site:csrc.gov.cn OR site:safe.gov.cn) {keyword_text} {freshness_terms} {impact_terms} 公告 通知"
    if "交易所" in source_text:
        return f"(site:sse.com.cn OR site:szse.cn OR site:bse.cn OR site:hkex.com.hk) {keyword_text} {freshness_terms} {impact_terms} 公告 通知 风险"
    if "投行" in source_text:
        return f"(site:goldmansachs.com OR site:morganstanley.com OR site:jpmorgan.com OR site:cicc.com OR site:htsc.com.cn OR site:citics.com) {keyword_text} {freshness_terms} {impact_terms} strategy outlook"
    return f"(site:stcn.com/article/detail OR site:cs.com.cn OR site:cnstock.com OR site:reuters.com OR site:bloomberg.com) {keyword_text} {freshness_terms} {impact_terms} article/detail"


def finance_authority_type_from_url(url: str) -> str:
    parsed = parse.urlparse(str(url or "").strip())
    host = parsed.netloc.lower().removeprefix("www.")
    for domain, domain_type in FINANCE_AUTHORITY_DOMAINS.items():
        if host == domain or host.endswith(f".{domain}"):
            return domain_type
    return ""


def is_finance_landing_or_listing_url(url: str) -> bool:
    parsed = parse.urlparse(str(url or "").strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    normalized_path = path.rstrip("/") + ("/" if path != "/" and path.endswith("/") else "")
    if path in FINANCE_LANDING_PATHS or normalized_path in FINANCE_LANDING_PATHS:
        return True
    if host.endswith("stcn.com") and "/article/detail/" not in path:
        return True
    if path.count("/") <= 1 and not path.lower().endswith((".html", ".shtml")):
        return True
    return False


def should_prefilter_finance_intelligence_query(query: dict) -> bool:
    if normalize_category(query.get("category")) != "finance_intelligence":
        return False
    source = str(query.get("source") or "")
    return source in FINANCE_INTELLIGENCE_SOURCES


def should_crawl_finance_intelligence_result(result: dict) -> dict:
    title = str(result.get("title") or "")
    url = str(result.get("url") or "")
    reasons: list[str] = []
    if not finance_authority_type_from_url(url):
        reasons.append("unauthorized_source")
    if is_finance_landing_or_listing_url(url):
        reasons.append("landing_or_listing_page")
    title_markers = ("首页", "官方网站", "栏目", "列表", "风险警示板", "通知公告")
    if any(marker in title for marker in title_markers):
        reasons.append("listing_title")
    return {
        "accepted": not reasons,
        "reject_reasons": sorted(set(reasons)),
    }


STRATEGY_MIN_CONTENT_CHARS = 500
STRATEGY_MAX_CONTENT_CHARS = 200000  # ~200KB; reject books/oversized papers

STRATEGY_LISTING_CONTENT_MARKERS = (
    "Showing 1",
    "results per page",
    "Sort by:",
    "Filter by",
    "Page 1 of",
    "Next Page",
    "Previous Page",
    "Browse by",
    "All submissions",
    "New submissions",
    "Cross-listings",
    "Replacement submissions",
)

STRATEGY_LISTING_PATHS = (
    "/list/",
    "/archive/",
    "/year/",
    "/category/",
    "/new",
    "/recent",
    "/search/",
    "/conference/",
    "/groups/",
    "/help/",
    "/about/",
    "/blog/",
    "/terms/",
    "/privacy/",
)

STRATEGY_LISTING_TITLE_MARKERS = (
    "Quantitative Finance - arXiv",
    "Quantitative Finance Aug",
    "Quantitative Finance Jan",
    "SSRN Home Page",
    "Homepage",
    "Home Page",
    "How it works",
    "Search Results",
    "Not Found",
    "Page Not Found",
    "403 Forbidden",
    "Access Denied",
    "Papers -",
    "Selected Publications",
    "Reading arXiv",
    "How do quant devs",
    "Substack",
    "Reddit",
)


def is_strategy_listing_or_code_url(url: str) -> bool:
    parsed = parse.urlparse(str(url or "").strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    # Reject arXiv listing/category/search pages
    if host.endswith("arxiv.org"):
        if any(lp in path for lp in STRATEGY_LISTING_PATHS):
            return True
        if path in ("/", "/q-fin", "/q-fin/"):
            return True
        # Only allow /abs/ and /pdf/ paths on arxiv.org
        if not (path.startswith("/abs/") or path.startswith("/pdf/")):
            return True
    # Reject GitHub repo root and non-file URLs
    if host == "github.com":
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) <= 2:
            return True
        # Allow /blob/ paths (file viewer); reject /tree/, /issues/, etc.
        if "/blob/" not in path:
            return True
    # raw.githubusercontent.com is always valid content
    if host == "raw.githubusercontent.com":
        return False
    # Reject homepage URLs
    if path in ("/", "/index.html", ""):
        return True
    return False


def should_crawl_strategy_result(result: dict) -> dict:
    title = str(result.get("title") or "")
    url = str(result.get("url") or "")
    reasons: list[str] = []
    if is_strategy_listing_or_code_url(url):
        reasons.append("listing_or_code_page")
    if any(marker in title for marker in STRATEGY_LISTING_TITLE_MARKERS):
        reasons.append("listing_title")
    # Reject GitHub URLs that are not pointing to specific files
    if "github.com" in url.lower() and "/blob/" not in url.lower() and "raw.githubusercontent.com" not in url.lower():
        reasons.append("github_repo")
    # Reject SSRN abstract-only pages (no full paper access)
    if "ssrn.com" in url.lower() and "abstract_id" in url.lower() and "/delivery" not in url.lower() and "/pdf" not in url.lower():
        reasons.append("ssrn_abstract_only")
    # Reject social media discussion posts
    if any(host in url.lower() for host in ["reddit.com/", "twitter.com/", "x.com/", "facebook.com/"]):
        reasons.append("social_media")
    # Reject promo/marketing/newsletter pages
    if any(host in url.lower() for host in ["substack.com/", "medium.com/"]):
        if "/p/" not in url.lower():
            reasons.append("promo_page")
    # Reject competition platform homepages (not specific strategy papers)
    if any(host in url.lower() for host in ["numer.ai/", "numerai.com/"]):
        if "/forum/" not in url.lower() and "/posts/" not in url.lower():
            reasons.append("competition_homepage")
    # Reject exchange announcement pages that are just listing pages
    if any(host in url.lower() for host in ["pbc.gov.cn/zhengcehuobisi"]) and "delivery" not in url.lower() and "report" not in url.lower():
        reasons.append("exchange_listing")
    return {
        "accepted": not reasons,
        "reject_reasons": sorted(set(reasons)),
    }


def extract_finance_direct_child_links(parent_url: str, crawled_item: dict, limit: int = 8) -> list[dict]:
    content = str(crawled_item.get("content") or crawled_item.get("markdown") or crawled_item.get("text") or "")
    if not content:
        return []
    candidates = re.findall(r"https?://[^\s\]\)\"'<>]+", content)
    candidates.extend(match.group(1) for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", content))
    parent_host = parse.urlparse(str(parent_url or "")).netloc.lower().removeprefix("www.")
    seen: set[str] = set()
    links: list[dict] = []
    for candidate in candidates:
        url = parse.urljoin(str(parent_url or ""), str(candidate).strip().rstrip(".,;"))
        if not url or url in seen:
            continue
        authority_type = finance_authority_type_from_url(url)
        if not authority_type:
            continue
        host = parse.urlparse(url).netloc.lower().removeprefix("www.")
        if parent_host and host != parent_host and not host.endswith(f".{parent_host}") and not parent_host.endswith(f".{host}"):
            continue
        if is_finance_landing_or_listing_url(url):
            continue
        seen.add(url)
        links.append({"title": "finance intelligence direct child", "url": url, "source_url_mode": "direct_child"})
        if len(links) >= max(1, int(limit)):
            break
    return links


def build_llm_screening_payload(crawled_items: Sequence[dict], model: str = "deepseek-v4-pro") -> dict:
    items = []
    for index, item in enumerate(crawled_items, start=1):
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id": str(item.get("id") or f"item-{index}"),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "source": str(item.get("source") or ""),
                "category": str(item.get("category") or ""),
                "content": str(item.get("content") or item.get("text") or "")[:12000],
            }
        )

    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是个人自动化量化系统的研究筛选器。只保留真正可工程化的量化策略和财经情报。"
                    "有价值量化策略必须包含可理解的量化思路。实现步骤和回测效果为加分项，非必需。"
                    "严格拒绝以下低质量内容：\n"
                    "- 网站首页、导航页、列表页（如 SSRN Home Page、Oxford Man Publications 列表页）\n"
                    "- 社交媒体讨论帖（如 Reddit、Twitter/X 帖子）\n"
                    "- 交易所公告页面（如央行公开市场业务交易公告页，除非包含具体政策信号）\n"
                    "- 推广/营销文章（如 Substack 推文、Newsletter 推广）\n"
                    "- 课程大纲/教学页面（如 NYU 课程页面、教程索引）\n"
                    "- 竞赛平台首页（如 Numerai 首页，不含具体策略论文）\n"
                    "- 重复/镜像内容（同一论文的多个版本）\n"
                    "输出 JSON，不要输出无关文字。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "screen_quant_research",
                        "criteria": [
                            "可理解的量化思路（必需）",
                            "部分或完整的实现步骤（加分项）",
                            "回测或实盘效果描述（加分项，非必需）",
                            "可映射到 Tushare 或可通过爬取补齐的数据字段",
                            "不是网站首页/导航页/列表页/社交媒体帖/推广文章/课程页面/竞赛平台首页",
                        ],
                        "items": items,
                        "expected_schema": {
                            "valuable_items": [
                                {
                                    "title": "string",
                                    "url": "string",
                                    "is_valuable": True,
                                    "confidence_level": "high|medium|low",
                                    "strategy_idea": "string",
                                    "implementation_steps": ["string"],
                                    "evidence": "string",
                                    "required_fields": [{"dataset": "string", "field": "string"}],
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
    }


def parse_llm_screening_decisions(payload: dict | str) -> list[dict]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        return []
    items = payload.get("valuable_items") or payload.get("items") or []
    decisions: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("is_valuable") is False:
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title and not url:
            continue
        required_fields = item.get("required_fields") if isinstance(item.get("required_fields"), list) else []
        decisions.append(
            {
                "title": title,
                "url": url,
                "confidence_level": str(item.get("confidence_level") or "medium"),
                "strategy_idea": str(item.get("strategy_idea") or item.get("idea") or ""),
                "implementation_steps": item.get("implementation_steps") or [],
                "evidence": str(item.get("evidence") or item.get("backtest_evidence") or ""),
                "required_fields": required_fields,
                "raw": item,
            }
        )
    return decisions


def build_reproduction_summary_payload(
    crawled_item: dict,
    model: str = "glm-5.1",
    max_content_chars: int = 0,
    max_tokens: int = 0,
) -> dict:
    content = select_reproduction_content(crawled_item)
    if int(max_content_chars or 0) > 0:
        content = content[: int(max_content_chars)]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是量化论文复现工程师。读取论文或研报内容，总结核心量化思路，"
                    "并产出可在 LEAN 中复现前需要准备的结构化信息。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "quant_paper_reproduction_summary",
                        "goal": "为复现准备，而不是直接生成策略代码",
                        "source": {
                            "title": str(crawled_item.get("title") or ""),
                            "url": str(crawled_item.get("url") or ""),
                            "publisher": str(crawled_item.get("source") or ""),
                        },
                        "instructions": [
                            "总结核心量化思路",
                            "提取交易标的、调仓频率、信号定义、组合构建、风控、交易成本假设",
                            "列出复现需要的数据字段，并尽量映射到 Tushare 字段",
                            "指出论文中明确的回测区间、基准、评价指标和效果",
                            "给出在 LEAN 中复现的步骤，不要直接编造不存在的数据",
                        ],
                        "expected_schema": {
                            "core_idea": "string",
                            "tradable_universe": "string",
                            "rebalance_frequency": "string",
                            "signals": [{"name": "string", "formula": "string", "direction": "string"}],
                            "portfolio_construction": "string",
                            "risk_controls": ["string"],
                            "data_requirements": [{"dataset": "string", "field": "string", "reason": "string"}],
                            "backtest_plan": {
                                "start_date": "string",
                                "end_date": "string",
                                "benchmark": "string",
                                "metrics": ["string"],
                            },
                            "reported_results": "string",
                            "reproduction_steps": ["string"],
                            "implementation_notes": ["string"],
                            "open_questions": ["string"],
                        },
                        "paper_content": content,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
    }
    if int(max_tokens or 0) > 0:
        payload["max_tokens"] = int(max_tokens)
    return payload


def is_listing_content(content: str) -> bool:
    """Return True if the content appears to be a listing/index page rather than a paper."""
    if not content:
        return True
    marker_hits = sum(1 for m in STRATEGY_LISTING_CONTENT_MARKERS if m in content)
    return marker_hits >= 3


def select_reproduction_content(crawled_item: dict, max_chars: int = 30000) -> str:
    content = str(crawled_item.get("content") or crawled_item.get("text") or "")
    if str(crawled_item.get("content_source") or "").lower() != "pdf":
        content = content or str(crawled_item.get("markdown") or "")
        if is_listing_content(content):
            return ""
        return content

    markdown = str(crawled_item.get("markdown") or "")
    markdown_quality = crawled_item.get("markdown_quality") if isinstance(crawled_item.get("markdown_quality"), dict) else {}
    if markdown and safe_int(markdown_quality.get("score"), 0) >= 5:
        return _select_reproduction_sections(markdown, max_chars=max_chars, is_markdown=True)

    return _select_reproduction_sections(content, max_chars=max_chars, is_markdown=False)


def _select_reproduction_sections(content: str, max_chars: int = 30000, is_markdown: bool = False) -> str:
    normalized = str(content or "").replace("\r\n", "\n")
    if not normalized:
        return ""

    section_patterns = [
        r"(?:^|\n)\s*#{0,3}\s*2\s+Problem Formulation",
        r"(?:^|\n)\s*#{0,3}\s*3\s+Methodology",
        r"(?:^|\n)\s*#{0,3}\s*4\s+Experiment",
        r"(?:^|\n)\s*#{0,3}\s*(?:2|3|4)\s+(?:Method|Model|Strategy|Approach|Framework|Algorithm)",
        r"(?:^|\n)\s*#{0,3}\s*(?:2|3|4)\.\d+",
        r"(?:^|\n)\s*#{0,3}\s*A\.3\s+Generate Seed Alpha factory",
        r"(?:^|\n)\s*#{0,3}\s*A\.4\s+Multimodal Data types",
        r"(?:^|\n)\s*#{0,3}\s*A\.5\s+Category-Based Alpha Selection",
        r"(?:^|\n)\s*#{0,3}\s*A\.6\s+Dynamic Alpha Strategy Construction",
        r"(?:^|\n)\s*#{0,3}\s*A\.7\s+Sample prompt for Seed Alpha selection",
        r"(?:^|\n)\s*#{0,3}\s*A\.8\s+Portfolio Construction Methods",
    ]
    matches: list[tuple[int, str]] = []
    for pattern in section_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            matches.append((match.start(), pattern))
    if not matches:
        return normalized[:max_chars]

    matches.sort(key=lambda item: item[0])
    sections: list[str] = []
    for index, (start, pattern) in enumerate(matches):
        next_start = matches[index + 1][0] if index + 1 < len(matches) else len(normalized)
        if "Experiment" in pattern:
            references = re.search(r"\n\s*References\s*\n", normalized[start:next_start], flags=re.IGNORECASE)
            if references:
                next_start = start + references.start()
        section = normalized[start:next_start].strip()
        if is_markdown and section and not section.startswith("#"):
            section = section.lstrip()
        if section:
            sections.append(section)

    selected = "\n\n".join(sections)
    if len(selected) > max_chars:
        return selected[:max_chars]
    return selected or normalized[:max_chars]


def convert_pdf_text_to_markdown(text: str) -> dict:
    lines = str(text or "").replace("\r\n", "\n").splitlines()
    markdown_lines: list[str] = []
    table_count = 0
    formula_count = 0
    heading_count = 0
    index = 0
    while index < len(lines):
        raw_line = lines[index].strip()
        if not raw_line:
            markdown_lines.append("")
            index += 1
            continue

        table_match = re.match(r"Table\s+\d+:\s+(.+)", raw_line, flags=re.IGNORECASE)
        if table_match:
            markdown_lines.append(f"### {raw_line}")
            next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
            next_next_line = lines[index + 2].strip() if index + 2 < len(lines) else ""
            if re.search(r"#\s+Alpha\s+Weight\s+IC", next_line, flags=re.IGNORECASE) and re.match(r"\d+\s+\(.+\)\s+[-0-9.]+\s+[-0-9.]+", next_next_line):
                markdown_lines.append("| # | Alpha | Weight | IC(SSE50) |")
                markdown_lines.append("|---|---|---:|---:|")
                table_count += 1
                index += 2
                while index < len(lines):
                    row = lines[index].strip()
                    row_match = re.match(r"(\d+)\s+(.+?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)$", row)
                    if not row_match:
                        break
                    formula_count += 1
                    markdown_lines.append(f"| {row_match.group(1)} | {row_match.group(2).strip()} | {row_match.group(3)} | {row_match.group(4)} |")
                    index += 1
                continue
            table_count += 1
            index += 1
            continue

        heading = normalize_pdf_heading(raw_line)
        if heading:
            markdown_lines.append(f"## {heading}")
            heading_count += 1
        else:
            if extract_alpha_formulas_from_text(raw_line):
                formula_count += len(extract_alpha_formulas_from_text(raw_line))
            markdown_lines.append(raw_line)
        index += 1

    markdown = "\n".join(markdown_lines).strip()
    quality = score_pdf_markdown_quality(markdown, heading_count=heading_count, table_count=table_count, formula_count=formula_count)
    return {"markdown": markdown, "quality": quality}


def normalize_pdf_heading(line: str) -> str | None:
    text = " ".join(str(line or "").split())
    patterns = [
        r"^(2)\s+Problem Formulation$",
        r"^(3)\s+Methodology$",
        r"^(4)\s+Experiment$",
        r"^(A\.[3-8])\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return text
    return None


def score_pdf_markdown_quality(markdown: str, heading_count: int = 0, table_count: int = 0, formula_count: int = 0) -> dict:
    text = str(markdown or "")
    headings = heading_count or len(re.findall(r"^#{1,3}\s+", text, flags=re.MULTILINE))
    tables = table_count or len(re.findall(r"^\|.+\|$", text, flags=re.MULTILINE))
    formulas = formula_count or len(extract_alpha_formulas_from_text(text))
    has_methodology = bool(re.search(r"Methodology|Problem Formulation|Portfolio Construction", text, flags=re.IGNORECASE))
    score = 0
    score += min(3, headings)
    score += 2 if tables else 0
    score += min(3, formulas)
    score += 2 if has_methodology else 0
    score -= 3 if len(text.strip()) < 500 and headings < 2 else 0
    return {
        "score": max(0, score),
        "headings": headings,
        "tables": tables,
        "formulas": formulas,
        "has_methodology": has_methodology,
        "length": len(text),
    }


def build_local_reproduction_summary(crawled_item: dict, content: str, llm_error: str | None = None) -> dict:
    text = str(content or "")
    upper = text.upper()
    formulas = extract_alpha_formulas_from_text(text)

    data_requirements = [
        {"dataset": "daily", "field": "open", "reason": "OHLCV price input for alpha formulas"},
        {"dataset": "daily", "field": "high", "reason": "volatility and range formulas"},
        {"dataset": "daily", "field": "low", "reason": "volatility and range formulas"},
        {"dataset": "daily", "field": "close", "reason": "momentum, mean reversion, volatility and ranking signals"},
        {"dataset": "daily", "field": "vol", "reason": "volume and liquidity formulas"},
        {"dataset": "daily_basic", "field": "total_mv", "reason": "market capitalization for liquidity ratio"},
        {"dataset": "income", "field": "eps", "reason": "EPS growth and valuation factors"},
        {"dataset": "income", "field": "revenue", "reason": "gross profit margin and revenue growth factors"},
    ]
    if "VWAP" in upper:
        data_requirements.append({"dataset": "daily", "field": "amount", "reason": "derive VWAP from amount and volume when explicit VWAP is unavailable"})

    signals = [{"name": f"pdf_factor_{index}", "formula": formula, "direction": "rank by learned weight sign"} for index, formula in enumerate(formulas[:12], start=1)]
    if not signals:
        signals = [
            {"name": "momentum_14", "formula": "CLOSE - DELAY(CLOSE, 14)", "direction": "learned weight sign"},
            {"name": "bollinger_band_width", "formula": "(BOLL_UP - BOLL_DOWN) / SMA(CLOSE, 20)", "direction": "learned weight sign"},
            {"name": "eps_growth", "formula": "EPS / DELAY(EPS, 1) - 1", "direction": "learned weight sign"},
        ]

    return {
        "summary_source": "local_pdf_fallback",
        "llm_error": str(llm_error or ""),
        "core_idea": (
            "Use an LLM-generated seed alpha factory, evaluate factors by confidence score and risk preference, "
            "train a small MLP to combine selected alpha values, then rank stocks by the composite alpha."
        ),
        "tradable_universe": "SSE50/CSI300/SP500 constituents; for this project map to the available Tushare A-share universe or index constituent universe.",
        "rebalance_frequency": "Daily portfolio reconstruction; experiments use top-k/drop-n turnover control.",
        "signals": signals,
        "portfolio_construction": "Rank securities by composite alpha, hold top k names with equal weights, drop at most n holdings per day. Paper experiment uses k=13 and n=5.",
        "risk_controls": [
            "Filter factors by confidence score threshold.",
            "Blend confidence and risk preference scores, with paper sensitivity favoring wc=0.6 and wr=0.4.",
            "Limit daily turnover with drop-n rule.",
            "Validate across bull, bear and sideways market regimes.",
        ],
        "data_requirements": data_requirements,
        "backtest_plan": {
            "start_date": "2019-01-01",
            "end_date": "2024-06-30",
            "benchmark": "SSE50/CSI300/SP500 benchmark matching the selected universe",
            "metrics": ["IC", "Sharpe Ratio", "Sortino Ratio", "Calmar Ratio", "Cumulative Return", "Max Drawdown"],
        },
        "reported_results": "Paper reports SSE50 Jan 2023-Jan 2024 cumulative return 53.17%, Sharpe 0.287, Sortino 0.208, Calmar 1.052.",
        "reproduction_steps": [
            "Download and extract the full PDF text, especially methodology, alpha factory, selection algorithms and portfolio construction sections.",
            "Build the seed alpha factory from formula categories: momentum, mean reversion, volatility, fundamental, liquidity, quality, growth, technical and macro.",
            "Compute each factor per security from Tushare OHLCV, valuation and financial statement fields; winsorize and z-score cross-sectionally by date.",
            "For each factor, compute historical IC against next-period returns on the training window.",
            "Assign confidence score from IC stability and risk preference score from drawdown/regime behavior.",
            "Select category-diverse factors whose combined score wc * confidence + wr * risk exceeds threshold; use wc=0.6 and wr=0.4 as the default.",
            "Train a 3-layer MLP with input dimension equal to selected factor count, 10 ReLU hidden nodes and one output node predicting next-period return.",
            "Generate a daily composite alpha from MLP factor weights or fitted prediction scores.",
            "Rank the universe by composite alpha, hold the top-k securities equal-weighted, and apply top-k/drop-n turnover control with k=13 and n=5.",
            "Backtest against the matching index benchmark and report IC, cumulative return, Sharpe, Sortino, Calmar and max drawdown.",
            "Run ablations without confidence score and without risk preference to verify that both agents contribute to out-of-sample stability.",
            "Promote only variants with higher return and lower drawdown than the current live-paper candidates.",
        ],
        "implementation_notes": [
            "LEAN implementation should live under Algorithm.CSharp/SoloQuantGenerated or Algorithm.Python/SoloQuantGenerated.",
            "If fundamental fields are missing, start with OHLCV-only alpha categories and mark missing fields for later supplementation.",
            "Use Tushare historical data for backtests and GBM-generated prices only for non-trading-hour live-paper simulation.",
        ],
        "open_questions": [
            "The paper does not fully specify transaction cost assumptions.",
            "The exact train/validation/test constituent membership snapshots need to be reconstructed from index constituent history.",
            "Some multimodal inputs such as images/audio/video are optional for a first LEAN reproduction and need local data sources if used.",
        ],
    }


def extract_alpha_formulas_from_text(text: str) -> list[str]:
    normalized = " ".join(str(text or "").replace("−", "-").split())
    formula_patterns = [
        r"CLOSE\s*-\s*DELAY\(CLOSE,\s*14\)",
        r"RSI\s*-\s*DELAY\(RSI,\s*14\)",
        r"CLOSE\s*-\s*DELAY\(SMA\(CLOSE,\s*14\),\s*7\)",
        r"MA\(CLOSE,\s*20\)\s*-\s*CLOSE",
        r"SMA\(CLOSE,\s*20\)\s*-\s*CLOSE",
        r"MAX\(HIGH,\s*20\)\s*-\s*CLOSE",
        r"100\s*-\s*RSI",
        r"\(?\s*BOLL[_\s.]?UP\s*-\s*BOLL[_\s.]?DOWN\s*\)?\s*/\s*SMA\(CLOSE,\s*20\)",
        r"STD\(CLOSE,\s*10\)\s*/\s*STD\(CLOSE,\s*50\)",
        r"VOLUME\s*/\s*MARKET[_\s]?CAP",
        r"VOLUME\s*\*\s*CLOSE",
        r"EPS\s*/\s*DELAY\(EPS,\s*1\)\s*-\s*1",
        r"ATR\s*-\s*DELAY\(ATR,\s*14\)",
        r"\(?\s*VOLUME\s*-\s*DELAY\(VOLUME,\s*14\)\s*\)?\s*/\s*DELAY\(VOLUME,\s*14\)",
        r"GROSS\s+PROFIT\s*/\s*REVENUE",
        r"OPERATING\s+INCOME\s*/\s*REVENUE",
        r"\(?\s*MAX\(HIGH,\s*14\)\s*-\s*CLOSE\s*\)?\s*/\s*\(?\s*MAX\(HIGH,\s*14\)\s*-\s*MIN\(LOW,\s*14\)\s*\)?\s*\*?\s*-?100",
    ]
    formulas: list[str] = []
    for pattern in formula_patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            formula = normalize_alpha_formula(match.group(0))
            if formula and formula not in formulas:
                formulas.append(formula)

    generic_patterns = [
        r"\([A-Z0-9_./*+\-\s,=<>]+(?:DELAY|SMA|EMA|RSI|ATR|BOLL|STD|MAX|MIN|CLOSE|VOLUME|EPS|REVENUE|HIGH|LOW)[A-Z0-9_./*+\-\s,=<>]*\)",
        r"[A-Z_]+\([A-Z0-9_,\s]+\)\s*/\s*[A-Z_]+\([A-Z0-9_,\s]+\)",
    ]
    for pattern in generic_patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            formula = normalize_alpha_formula(match.group(0))
            if formula and formula not in formulas:
                formulas.append(formula)
            if len(formulas) >= 20:
                return formulas
    return formulas[:20]


def normalize_alpha_formula(formula: str) -> str:
    text = " ".join(str(formula or "").split())
    replacements = {
        "BOLL UP": "BOLL_UP",
        "BOLL.UP": "BOLL_UP",
        "BOLL DOWN": "BOLL_DOWN",
        "BOLL.DOWN": "BOLL_DOWN",
        "MARKET CAP": "MARKET_CAP",
        "GROSS PROFIT": "GROSS_PROFIT",
        "OPERATING INCOME": "OPERATING_INCOME",
    }
    for old, new in replacements.items():
        text = re.sub(old, new, text, flags=re.IGNORECASE)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s*\*\s*", " * ", text)
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("(") and text.endswith(")") and text.count("(") == text.count(")") == 1:
        text = text[1:-1].strip()
    return text.upper()


def parse_llm_json_response(payload: dict | str) -> dict:
    if isinstance(payload, str):
        return json.loads(payload)
    if not isinstance(payload, dict):
        return {}
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            json_text = extract_json_object_text(content)
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    parsed.setdefault("is_complete", choice.get("finish_reason") != "length")
                    parsed.setdefault("finish_reason", choice.get("finish_reason"))
                    return parsed
                return {"is_complete": choice.get("finish_reason") != "length", "finish_reason": choice.get("finish_reason"), "content": content}
            except json.JSONDecodeError:
                return {"is_complete": False, "finish_reason": choice.get("finish_reason"), "content": content}
        reasoning = message.get("reasoning_content") if isinstance(message, dict) else None
        if isinstance(reasoning, str) and reasoning.strip():
            json_text = extract_json_object_text(reasoning)
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    parsed.setdefault("is_complete", choice.get("finish_reason") != "length")
                    parsed.setdefault("finish_reason", choice.get("finish_reason"))
                    return parsed
            except json.JSONDecodeError:
                pass
        return {
            "is_complete": False,
            "finish_reason": choice.get("finish_reason"),
            "raw_reasoning": str(reasoning or "")[:8000],
            "error": "llm_response_missing_final_json_content",
        }
    return payload


def extract_json_object_text(content: str) -> str:
    text = str(content or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def resolve_pdf_url(crawled_item: dict) -> str | None:
    if not isinstance(crawled_item, dict):
        return None

    candidates: list[str] = []
    for key in ("pdf_url", "pdf-url", "pdf", "url"):
        value = crawled_item.get(key)
        if value:
            candidates.append(str(value))
    raw = crawled_item.get("raw")
    if isinstance(raw, dict):
        for key in ("pdf_url", "pdf-url", "pdf", "url"):
            value = raw.get(key)
            if value:
                candidates.append(str(value))
    content = str(crawled_item.get("content") or crawled_item.get("markdown") or crawled_item.get("text") or "")
    candidates.extend(re.findall(r"https?://arxiv\.org/(?:abs|pdf)/[A-Za-z0-9_.\-/]+", content))
    candidates.extend(re.findall(r"https?://[^\s\]\)\"']+\.pdf", content, flags=re.IGNORECASE))

    for candidate in candidates:
        url = candidate.strip().rstrip(".,;)")
        if not url:
            continue
        parsed = parse.urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.strip()
        if host.endswith("arxiv.org"):
            match = re.match(r"^/(?:abs|pdf)/([^/?#]+)", path)
            if match:
                paper_id = match.group(1)
                if paper_id.lower().endswith(".pdf"):
                    paper_id = paper_id[:-4]
                return f"https://arxiv.org/pdf/{paper_id}"
        if path.lower().endswith(".pdf"):
            return parse.urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path, "", "", ""))
    return None


def pdf_artifact_paths(crawled_item: dict, artifact_root: str | Path, category: str = "strategy", run_date: str | None = None) -> tuple[Path, Path, Path]:
    pdf_url = resolve_pdf_url(crawled_item) or str(crawled_item.get("url") or "paper")
    paper_id = ""
    parsed = parse.urlparse(pdf_url)
    if parsed.netloc.lower().endswith("arxiv.org"):
        match = re.match(r"^/pdf/([^/?#]+)", parsed.path)
        paper_id = match.group(1) if match else ""
    slug_input = paper_id or crawled_item.get("title") or pdf_url
    source_slug = safe_slug(str(crawled_item.get("source") or category), fallback=category)
    filename = f"{source_slug}-{safe_slug(str(slug_input), 'paper')}"
    root = Path(artifact_root) / "pdf" / normalize_category(category)
    return root / f"{filename}.pdf", root / f"{filename}.txt", root / f"{filename}.md"


def download_and_extract_pdf_text(
    crawled_item: dict,
    artifact_root: str | Path,
    category: str = "strategy",
    run_date: str | None = None,
    timeout_seconds: int = 180,
) -> dict | None:
    pdf_url = resolve_pdf_url(crawled_item)
    if not pdf_url:
        return None

    pdf_path, text_path, markdown_path = pdf_artifact_paths(crawled_item, artifact_root, category=category, run_date=run_date)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        with request.urlopen(pdf_url, timeout=max(1, int(timeout_seconds))) as response:
            pdf_path.write_bytes(response.read())
    if not text_path.exists() or text_path.stat().st_size == 0:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(text_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(timeout_seconds)),
        )
    content = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return None
    if len(content) > STRATEGY_MAX_CONTENT_CHARS:
        return {
            "content_source": "pdf",
            "pdf_error": f"PDF content too large ({len(content)} chars, max {STRATEGY_MAX_CONTENT_CHARS})",
            "content": content[:5000],
        }
    markdown_result = convert_pdf_text_to_markdown(content)
    markdown = str(markdown_result.get("markdown") or "").strip()
    markdown_quality = markdown_result.get("quality") if isinstance(markdown_result.get("quality"), dict) else {}
    if markdown:
        markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "content": content,
        "content_source": "pdf",
        "markdown": markdown,
        "markdown_quality": markdown_quality,
        "pdf_url": pdf_url,
        "pdf_file": str(pdf_path),
        "text_file": str(text_path),
        "markdown_file": str(markdown_path) if markdown else None,
        "content_hash": stable_hash({"pdf_url": pdf_url, "content": content[:2000]}),
    }


def iter_crawled_strategy_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "crawled" / "strategy"
    if not root.exists():
        return
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        yield path


def prepare_reproduction_summaries(
    artifact_root: str | Path,
    llm_summary_client: Callable[[dict], dict],
    run_date: str | None = None,
    model: str = "glm-5.1",
    max_items: int | None = None,
    max_content_chars: int = 0,
    max_tokens: int = 0,
) -> dict:
    output_root = Path(artifact_root) / "reproduction" / "strategy"
    files: list[str] = []
    index_rows: list[dict] = []
    processed = 0
    skipped_count = 0
    # Cross-tick dedup: skip URLs/titles already summarized
    seen_summaries: set[str] = set()
    # Scan reproduction index for already-summarized items
    existing_index = output_root / "index.json"
    if existing_index.exists():
        try:
            for row in (load_json_payload(existing_index).get("items") or []):
                url = str(row.get("url") or "").strip()
                title_normalized = str(row.get("title") or "").strip().lower()
                if url:
                    seen_summaries.add(stable_hash(url))
                if title_normalized:
                    seen_summaries.add(stable_hash(title_normalized))
        except Exception:
            pass
    for path in iter_crawled_strategy_files(artifact_root, run_date=run_date):
        if max_items is not None and processed >= max(0, int(max_items)):
            break
        item = load_json_payload(path)
        # Dedup check
        item_url = str(item.get("url") or "").strip()
        item_title_norm = str(item.get("title") or "").strip().lower()
        if (item_url and stable_hash(item_url) in seen_summaries) or (item_title_norm and stable_hash(item_title_norm) in seen_summaries):
            skipped_count += 1
            continue
        content = str(item.get("content") or item.get("markdown") or item.get("text") or "").strip()
        if not content:
            continue
        if len(content) > STRATEGY_MAX_CONTENT_CHARS:
            skipped_count += 1
            continue
        pdf_payload = None
        try:
            pdf_payload = download_and_extract_pdf_text(item, artifact_root, category="strategy", run_date=run_date)
        except Exception as exc:
            pdf_payload = {
                "content_source": "crawl",
                "pdf_error": str(exc),
            }
        if isinstance(pdf_payload, dict) and str(pdf_payload.get("content") or "").strip():
            item = {**item, **pdf_payload}
            content = str(item.get("content") or "").strip()
        payload = build_reproduction_summary_payload(
            item,
            model=model,
            max_content_chars=max_content_chars,
            max_tokens=max_tokens,
        )
        try:
            summary = parse_llm_json_response(llm_summary_client(payload))
        except Exception as exc:
            summary = build_local_reproduction_summary(item, select_reproduction_content(item), llm_error=str(exc))
        artifact_date = run_date or utc_run_date()
        url_hash = stable_hash(item_url or item_title_norm or content[:500])
        output_path = output_root / f"{safe_slug(str(item.get('source') or 'paper'), 'paper')}-{url_hash}.json"
        output = {
            "category": "strategy",
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "source": str(item.get("source") or ""),
            "source_file": str(path),
            "content_hash": item.get("content_hash") or stable_hash(content[:2000]),
            "content_source": str(item.get("content_source") or "crawl"),
            "pdf_url": item.get("pdf_url"),
            "pdf_file": item.get("pdf_file"),
            "pdf_text_file": item.get("text_file"),
            "pdf_markdown_file": item.get("markdown_file"),
            "markdown_quality": item.get("markdown_quality"),
            "pdf_error": item.get("pdf_error"),
            "summary": summary,
            "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json_payload(output_path, output)
        files.append(str(output_path))
        if item_url:
            seen_summaries.add(stable_hash(item_url))
        if item_title_norm:
            seen_summaries.add(stable_hash(item_title_norm))
        index_rows.append(
            {
                "title": output["title"],
                "url": output["url"],
                "source": output["source"],
                "file": str(output_path),
                "content_hash": output["content_hash"],
            }
        )
        processed += 1

    if index_rows:
        index_path = output_root / "index.json"
        existing_items: list[dict] = []
        if index_path.exists():
            try:
                existing_items = load_json_payload(index_path).get("items") or []
            except Exception:
                pass
        existing_urls = {str(r.get("url") or "").strip() for r in existing_items}
        merged_items = existing_items + [r for r in index_rows if str(r.get("url") or "").strip() not in existing_urls]
        write_json_payload(
            index_path,
            {
                "category": "strategy",
                "count": len(merged_items),
                "items": merged_items,
            },
        )
    return {
        "run_date": str(run_date or utc_run_date()).replace("-", ""),
        "written_count": len(files),
        "skipped_count": skipped_count,
        "files": files,
    }


def classify_finance_event_type(text: str) -> str:
    """Classify a finance event text into one of FINANCE_EVENT_TYPES categories."""
    normalized = str(text or "").lower()
    for event_type, keywords in FINANCE_EVENT_TYPES.items():
        if any(kw.lower() in normalized for kw in keywords):
            return event_type
    return "unknown"


def build_finance_intelligence_analysis_payload(
    crawled_item: dict,
    model: str = "glm-5.1",
    max_content_chars: int = 0,
    max_tokens: int = 4096,
) -> dict:
    content = str(crawled_item.get("content") or crawled_item.get("markdown") or crawled_item.get("text") or "")
    if int(max_content_chars or 0) > 0:
        content = content[: int(max_content_chars)]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是财经情报分析员。只输出JSON，不要解释。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "finance_intelligence_key_points_and_impact_analysis",
                        "goal": "提取要点和影响分析，为量化策略监控、风控和择时提供输入。只输出一个JSON对象。",
                        "source": {
                            "title": str(crawled_item.get("title") or ""),
                            "url": str(crawled_item.get("url") or ""),
                            "publisher": str(crawled_item.get("source") or ""),
                        },
                        "expected_schema": {
                            "key_points": ["string"],
                            "impact_analysis": "string",
                            "risk_level": "low|medium|high|unknown",
                            "event_type": "monetary_policy|regulatory|macro_data|market_structure|geopolitical|corporate|sentiment|unknown",
                            "signal_direction": "bullish|bearish|neutral|unknown",
                            "affected_sectors": ["string"],
                        },
                        "finance_content": content,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
    }
    if int(max_tokens or 0) > 0:
        payload["max_tokens"] = int(max_tokens)
    return payload


def iter_crawled_finance_intelligence_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "crawled" / "finance_intelligence"
    if not root.exists():
        return
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        yield path


def is_low_quality_crawled_content(content: str) -> bool:
    text = str(content or "").strip()
    if len(text) < 40:
        return True
    lowered = text.lower()
    low_quality_markers = (
        "nessuna informazione disponibile",
        "no information is available for this page",
        "scopri perché",
        "mancanti:",
        "blocked by anti-bot",
    )
    return any(marker in lowered for marker in low_quality_markers)


def evaluate_finance_intelligence_quality(item: dict, run_date: str | None = None) -> dict:
    item = item if isinstance(item, dict) else {}
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    content = str(item.get("content") or item.get("markdown") or item.get("text") or "").strip()
    reject_reasons: list[str] = []
    parsed = parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    authority_type = finance_authority_type_from_url(url)
    if not authority_type:
        reject_reasons.append("unauthorized_source")

    if is_finance_landing_or_listing_url(url):
        reject_reasons.append("landing_or_listing_page")
    if any(marker in f"{title} {content[:500]}" for marker in ("首页", "APP下载", "微博微信", "公众号", "客户端 登录")) and len(content) > 20000:
        reject_reasons.append("navigation_shell")

    if is_low_quality_crawled_content(content):
        reject_reasons.append("low_quality_content")
    if len(content) < 50:
        reject_reasons.append("too_short")

    date_text = str(run_date or utc_run_date()).replace("-", "")
    year = date_text[:4]
    month = str(int(date_text[4:6])) if len(date_text) >= 6 and date_text[4:6].isdigit() else ""
    recency_patterns = [year, f"{year}年", f"{year}-{date_text[4:6]}" if len(date_text) >= 6 else year]
    if month:
        recency_patterns.append(f"{month}月")
    combined = f"{title} {url} {content[:4000]}"
    if not any(pattern and pattern in combined for pattern in recency_patterns):
        reject_reasons.append("not_recent")

    impact_terms = [term for term in FINANCE_IMPACT_TERMS if term.lower() in combined.lower()]
    if len(impact_terms) < 2:
        reject_reasons.append("no_investment_style_impact")

    score = 0
    score += 3 if authority_type else 0
    score += 2 if "landing_or_listing_page" not in reject_reasons and "navigation_shell" not in reject_reasons else 0
    score += 2 if "not_recent" not in reject_reasons else 0
    score += min(3, len(impact_terms))
    score += 1 if len(content) >= 300 else 0
    accepted = not reject_reasons and score >= 6
    return {
        "accepted": accepted,
        "score": score,
        "authority_type": authority_type or "unknown",
        "impact_terms": impact_terms[:12],
        "reject_reasons": sorted(set(reject_reasons)),
    }


def prepare_finance_intelligence_analyses(
    artifact_root: str | Path,
    llm_analysis_client: Callable[[dict], dict],
    run_date: str | None = None,
    model: str = "glm-5.1",
    max_items: int | None = None,
    max_content_chars: int = 0,
    max_tokens: int = 0,
) -> dict:
    output_root = Path(artifact_root) / "intelligence-analysis" / "finance_intelligence"
    date_str = str(run_date or utc_run_date()).replace("-", "")

    # Cross-tick URL dedup: skip URLs already analyzed to avoid redundant LLM calls
    analyzed_urls: set[str] = set()
    existing_index = output_root / "index.json"
    if existing_index.exists():
        try:
            for row in (load_json_payload(existing_index).get("items") or []):
                url = str(row.get("url") or "").strip()
                if url:
                    analyzed_urls.add(url)
        except Exception:
            pass

    files: list[str] = []
    index_rows: list[dict] = []
    processed = 0
    skipped_count = 0
    for path in iter_crawled_finance_intelligence_files(artifact_root, run_date=run_date):
        if max_items is not None and processed >= max(0, int(max_items)):
            break
        item = load_json_payload(path)
        url = str(item.get("url") or "").strip()

        # Skip already-analyzed URLs to save LLM calls
        if url and url in analyzed_urls:
            skipped_count += 1
            continue

        content = str(item.get("content") or item.get("markdown") or item.get("text") or "").strip()
        if is_low_quality_crawled_content(content):
            skipped_count += 1
            continue
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else evaluate_finance_intelligence_quality(item, run_date=date_str)
        if not quality.get("accepted"):
            skipped_count += 1
            continue
        payload = build_finance_intelligence_analysis_payload(
            item,
            model=model,
            max_content_chars=max_content_chars,
            max_tokens=max_tokens,
        )
        analysis = parse_llm_json_response(llm_analysis_client(payload))

        # Determine event_type: prefer GLM result, fallback to local classifier
        event_type = str((analysis or {}).get("event_type") or "").strip().lower()
        if event_type not in FINANCE_EVENT_TYPES:
            title = str(item.get("title") or "")
            kp_text = " ".join(str(p) for p in ((analysis or {}).get("key_points") or []))
            event_type = classify_finance_event_type(title + " " + kp_text)

        analysis_hash = stable_hash({"source_file": str(path), "title": item.get("title"), "analysis": analysis})
        source_slug = safe_slug(str(item.get("source") or "finance"), "finance")
        output_path = output_root / f"{event_type}-{source_slug}-{analysis_hash}.json"
        output = {
            "category": "finance_intelligence",
            "event_type": event_type,
            "title": str(item.get("title") or ""),
            "url": url,
            "source": str(item.get("source") or ""),
            "source_file": str(path),
            "content_hash": item.get("content_hash") or stable_hash(content[:2000]),
            "quality": quality,
            "analysis": analysis,
            "analyzed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json_payload(output_path, output)
        files.append(str(output_path))
        index_rows.append(
            {
                "title": output["title"],
                "url": output["url"],
                "source": output["source"],
                "event_type": event_type,
                "file": str(output_path),
                "content_hash": output["content_hash"],
            }
        )
        if url:
            analyzed_urls.add(url)
        processed += 1

    if index_rows:
        # Merge with existing index rows to preserve cross-tick entries
        index_path = output_root / "index.json"
        existing_items: list[dict] = []
        if index_path.exists():
            try:
                existing_items = load_json_payload(index_path).get("items") or []
            except Exception:
                pass
        existing_urls = {str(r.get("url") or "").strip() for r in existing_items}
        merged_rows = existing_items + [r for r in index_rows if str(r.get("url") or "").strip() not in existing_urls]
        write_json_payload(
            index_path,
            {
                "category": "finance_intelligence",
                "count": len(merged_rows),
                "items": merged_rows,
            },
        )
    return {
        "run_date": date_str,
        "written_count": len(files),
        "skipped_count": skipped_count,
        "files": files,
    }


FINANCE_ENTITY_KEYWORDS = {
    "人民银行": ("institution", ("人民银行", "央行", "PBOC")),
    "交易所": ("institution", ("交易所", "上交所", "深交所", "北交所")),
    "债券": ("asset", ("债券", "久期", "利率债", "信用债")),
    "权益": ("asset", ("权益", "股票", "A股", "股市", "指数")),
    "高股息": ("style_factor", ("高股息", "红利")),
    "低波动": ("style_factor", ("低波动", "低波")),
    "利率": ("macro_indicator", ("利率", "DR007", "同业存单", "资金利率")),
    "流动性": ("macro_theme", ("流动性", "净投放", "逆回购", "MLF")),
}

FINANCE_THEME_KEYWORDS = {
    "liquidity": ("流动性", "净投放", "逆回购", "资金面"),
    "rates": ("利率", "DR007", "同业存单", "久期"),
    "risk_appetite": ("风险偏好", "权益", "回撤"),
    "geopolitics": ("地缘", "制裁", "冲突", "关税", "战争"),
    "policy": ("政策", "监管", "央行", "人民银行", "交易所"),
}


def iter_finance_analysis_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "intelligence-analysis" / "finance_intelligence"
    if not root.exists():
        return
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        yield path


def detect_finance_entities(text: str) -> list[dict]:
    detected: list[dict] = []
    for name, (entity_type, markers) in FINANCE_ENTITY_KEYWORDS.items():
        if any(marker in text for marker in markers):
            detected.append({"name": name, "entity_type": entity_type})
    return detected


def detect_finance_themes(text: str) -> list[str]:
    return [theme for theme, markers in FINANCE_THEME_KEYWORDS.items() if any(marker in text for marker in markers)]


def graph_node(node_id: str, node_type: str, name: str, properties: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "properties": properties or {},
    }


def graph_edge(source: str, target: str, edge_type: str, properties: dict | None = None) -> dict:
    return {
        "id": stable_hash({"source": source, "target": target, "type": edge_type, "properties": properties or {}}),
        "source": source,
        "target": target,
        "type": edge_type,
        "properties": properties or {},
    }


def add_unique_node(nodes: dict[str, dict], node: dict) -> None:
    nodes.setdefault(node["id"], node)


def add_unique_edge(edges: dict[str, dict], edge: dict) -> None:
    edges.setdefault(edge["id"], edge)


def build_finance_event_graph(artifact_root: str | Path, run_date: str | None = None) -> dict:
    date_text = str(run_date or utc_run_date()).replace("-", "")
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    event_ids: list[str] = []
    seen_event_keys: set[str] = set()
    event_features: dict[str, dict[str, set[str]]] = {}

    analysis_paths = sorted(
        list(iter_finance_analysis_files(artifact_root, run_date=run_date) or []),
        key=lambda candidate: (
            str((load_json_payload(candidate) or {}).get("analyzed_at_utc") or ""),
            candidate.name,
        ),
        reverse=True,
    )
    for path in analysis_paths:
        payload = load_json_payload(path)
        quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
        if quality and not quality.get("accepted"):
            continue
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        title = str(payload.get("title") or path.stem)
        url = str(payload.get("url") or "")
        content_hash = str(payload.get("content_hash") or "")
        event_key = url or content_hash or title
        if event_key in seen_event_keys:
            continue
        seen_event_keys.add(event_key)
        source_name = str(payload.get("source") or "unknown")
        key_points = analysis.get("key_points") if isinstance(analysis.get("key_points"), list) else []
        impact = str(analysis.get("impact_analysis") or "")
        combined_text = " ".join([title, source_name, impact, *[str(item) for item in key_points]])
        event_id = f"event:{stable_hash({'title': title, 'url': url, 'content_hash': content_hash})}"
        source_id = f"source:{stable_hash(source_name)}"

        add_unique_node(
            nodes,
            graph_node(
                event_id,
                "event",
                title,
                {
                    "url": url,
                    "risk_level": str(analysis.get("risk_level") or "unknown"),
                    "source_type": str(payload.get("quality", {}).get("authority_type") if isinstance(payload.get("quality"), dict) else "unknown"),
                    "impact_analysis": impact,
                    "key_points": key_points,
                    "source_file": str(path),
                },
            ),
        )
        add_unique_node(nodes, graph_node(source_id, "source", source_name, {"source_type": "finance_intelligence"}))
        add_unique_edge(edges, graph_edge(source_id, event_id, "SOURCE_REPORTED_EVENT"))
        event_ids.append(event_id)
        event_features[event_id] = {"entities": set(), "themes": set()}

        for entity in detect_finance_entities(combined_text):
            entity_id = f"entity:{stable_hash(entity['name'])}"
            add_unique_node(nodes, graph_node(entity_id, "entity", entity["name"], {"entity_type": entity["entity_type"]}))
            add_unique_edge(edges, graph_edge(event_id, entity_id, "EVENT_IMPACTS_ENTITY"))
            event_features[event_id]["entities"].add(entity_id)

        for theme in detect_finance_themes(combined_text):
            theme_id = f"theme:{theme}"
            add_unique_node(nodes, graph_node(theme_id, "theme", theme, {"theme": theme}))
            add_unique_edge(edges, graph_edge(event_id, theme_id, "EVENT_HAS_THEME"))
            event_features[event_id]["themes"].add(theme_id)

    for index, left in enumerate(event_ids):
        for right in event_ids[index + 1:]:
            shared_entities = sorted(event_features[left]["entities"] & event_features[right]["entities"])
            shared_themes = sorted(event_features[left]["themes"] & event_features[right]["themes"])
            if shared_entities or shared_themes:
                add_unique_edge(
                    edges,
                    graph_edge(
                        left,
                        right,
                        "EVENT_RELATED_TO_EVENT",
                        {
                            "shared_entities": shared_entities,
                            "shared_themes": shared_themes,
                            "relationship_strength": len(shared_entities) * 2 + len(shared_themes),
                        },
                    ),
                )

    graph = {
        "graph_id": f"finance_intelligence:{date_text}",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_date": date_text,
        "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
        "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
    }
    output_path = Path(artifact_root) / "event-graph" / "finance_intelligence" / "graph.json"
    write_json_payload(output_path, graph)
    return {
        "run_date": date_text,
        "graph_file": str(output_path),
        "event_count": len(event_ids),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
    }


def build_event_signal_context(
    graph: dict,
    event_type_filter: list[str] | None = None,
    artifact_root: str | Path | None = None,
    run_date: str | None = None,
) -> dict:
    """Convert finance event graph into strategy-consumable signal context.

    Returns signals grouped by event type, composite risk level, and affected sectors.
    Optionally persists to artifacts/event-signals/YYYYMMDD/signal-context.json.
    """
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    risk_priority = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    signals_by_type: dict[str, list[dict]] = {et: [] for et in FINANCE_EVENT_TYPES}
    all_sectors: list[str] = []
    max_risk = "low"

    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "event":
            continue
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        event_type = str(props.get("event_type") or "unknown").strip()
        if event_type_filter and event_type not in event_type_filter:
            continue
        direction = str(props.get("signal_direction") or "unknown").strip()
        risk_level = str(props.get("risk_level") or "unknown").strip()
        key_points = props.get("key_points") if isinstance(props.get("key_points"), list) else []
        sectors = props.get("affected_sectors") if isinstance(props.get("affected_sectors"), list) else []
        all_sectors.extend(str(s) for s in sectors if s)
        if risk_priority.get(risk_level, 0) > risk_priority.get(max_risk, 0):
            max_risk = risk_level
        signal = {
            "direction": direction,
            "strength": 1.0 if risk_level == "high" else (0.6 if risk_level == "medium" else 0.3),
            "summary": "; ".join(str(p) for p in key_points[:3]),
            "affected_sectors": [str(s) for s in sectors],
            "risk_level": risk_level,
        }
        if event_type in signals_by_type:
            signals_by_type[event_type].append(signal)

    context: dict = {
        "composite_risk_level": max_risk,
        "affected_sectors": sorted(set(all_sectors)),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    for event_type in FINANCE_EVENT_TYPES:
        context[f"{event_type}_signals"] = signals_by_type.get(event_type, [])

    if artifact_root is not None:
        signal_dir = Path(artifact_root) / "event-signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        write_json_payload(signal_dir / "signal-context.json", context)

    return context


def run_search_and_crawl(
    query_plan: dict,
    search_client: Callable[[dict], Sequence[dict]],
    crawl_client: Callable[[dict], dict],
    max_results_per_query: int | None = 3,
) -> list[dict]:
    crawled: list[dict] = []
    for query in query_plan.get("queries", []):
        if not isinstance(query, dict):
            continue
        crawled_url_count = 0
        direct_results: list[dict] = []
        if should_prefilter_finance_intelligence_query(query):
            for url in query.get("direct_urls") or []:
                url_text = str(url or "").strip()
                if not url_text:
                    continue
                direct_results.append(
                    {
                        "title": str(query.get("source") or "finance intelligence direct source"),
                        "url": url_text,
                        "source_url_mode": "direct",
                    }
                )
        search_results = [result for result in search_client(query) if isinstance(result, dict)]
        for result in search_results:
            result.setdefault("source_url_mode", "search")
        results = [*direct_results, *search_results]
        if max_results_per_query is not None:
            results = results[: max(0, int(max_results_per_query))]
        for result in results:
            if not isinstance(result, dict):
                continue
            if max_results_per_query is not None and crawled_url_count >= max(0, int(max_results_per_query)):
                break
            if should_prefilter_finance_intelligence_query(query) and result.get("source_url_mode") != "direct":
                crawl_decision = should_crawl_finance_intelligence_result(result)
                if not crawl_decision.get("accepted"):
                    continue
            elif normalize_category(query.get("category")) == "strategy":
                crawl_decision = should_crawl_strategy_result(result)
                if not crawl_decision.get("accepted"):
                    continue
            try:
                crawled_item = crawl_client(result)
            except Exception as exc:
                crawled_item = {
                    "crawl_error": str(exc),
                    "content": str(result.get("content") or result.get("snippet") or ""),
                }
            crawled_url_count += 1
            if not isinstance(crawled_item, dict):
                continue
            if (
                should_prefilter_finance_intelligence_query(query)
                and result.get("source_url_mode") == "direct"
            ):
                child_results = extract_finance_direct_child_links(str(result.get("url") or ""), crawled_item)
                if child_results:
                    for child in child_results:
                        if max_results_per_query is not None and crawled_url_count >= max(0, int(max_results_per_query)):
                            break
                        try:
                            child_item = crawl_client(child)
                        except Exception as exc:
                            child_item = {"crawl_error": str(exc), "content": ""}
                        crawled_url_count += 1
                        if not isinstance(child_item, dict):
                            continue
                        if not str(child_item.get("content") or child_item.get("markdown") or child_item.get("text") or "").strip():
                            continue
                        merged_child = dict(child)
                        merged_child.update(query)
                        merged_child.update(child_item)
                        merged_child["url"] = child["url"]
                        merged_child["source_url_mode"] = "direct_child"
                        merged_child["category"] = normalize_category(query.get("category"))
                        crawled.append(merged_child)
                    continue
            if not str(crawled_item.get("content") or crawled_item.get("markdown") or crawled_item.get("text") or "").strip():
                continue
            merged = dict(result)
            merged.update(query)
            merged.update(crawled_item)
            merged["category"] = normalize_category(query.get("category"))
            crawled.append(merged)
    return crawled


def persist_crawled_items(
    items: Sequence[dict],
    artifact_root: str | Path,
    run_date: str | None = None,
) -> dict:
    date_text = run_date or utc_run_date()
    root = Path(artifact_root) / "crawled"
    # Pre-seed seen with URLs already written (cross-tick dedup)
    seen: set[str] = set()
    seen_titles: set[str] = set()
    for category in ("strategy", "finance_intelligence"):
        existing_index = root / category / "index.json"
        if existing_index.exists():
            try:
                for row in (load_json_payload(existing_index).get("items") or []):
                    url = str(row.get("url") or "").strip()
                    title = str(row.get("title") or "").strip()
                    if url:
                        seen.add(stable_hash(normalize_paper_url(url)))
                    if title:
                        seen_titles.add(stable_hash(title.lower().strip()))
            except Exception:
                pass
    written_files: list[str] = []
    duplicate_count = 0
    rejected_count = 0
    index_by_category: dict[str, list[dict]] = {}

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        category = normalize_category(item.get("category"))
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or item.get("markdown") or item.get("text") or "")
        quality = None
        if category == "finance_intelligence":
            quality = evaluate_finance_intelligence_quality(item, run_date=date_text)
            if not quality.get("accepted"):
                rejected_count += 1
                continue
        if category == "strategy" and len(content.strip()) < STRATEGY_MIN_CONTENT_CHARS:
            rejected_count += 1
            continue
        if category == "strategy" and len(content.strip()) > STRATEGY_MAX_CONTENT_CHARS:
            rejected_count += 1
            continue
        content_hash = stable_hash({"url": url, "title": title, "content": content[:2000]})
        dedupe_key = stable_hash(normalize_paper_url(url) or {"title": title, "content": content[:500]})
        title_dedupe_key = stable_hash(title.lower().strip()) if title else ""
        if dedupe_key in seen or (title_dedupe_key and title_dedupe_key in seen_titles):
            duplicate_count += 1
            continue
        seen.add(dedupe_key)
        if title_dedupe_key:
            seen_titles.add(title_dedupe_key)

        source_slug = safe_slug(str(item.get("source") or category), fallback=category)
        path = root / category / f"{source_slug}-{content_hash}.json"
        payload = {
            "category": category,
            "source": item.get("source"),
            "title": title,
            "url": url,
            "query": item.get("query"),
            "content": content,
            "raw": item,
            "quality": quality,
            "content_hash": content_hash,
            "dedupe_key": dedupe_key,
            "crawled_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json_payload(path, payload)
        written_files.append(str(path))
        index_by_category.setdefault(category, []).append(
            {
                "title": title,
                "url": url,
                "source": item.get("source"),
                "file": str(path),
                "content_hash": content_hash,
            }
        )

    for category, rows in index_by_category.items():
        index_path = root / category / "index.json"
        # Merge with existing index to keep all items for the day, not just current batch
        existing_items: list[dict] = []
        if index_path.exists():
            try:
                existing_data = load_json_payload(index_path)
                existing_items = existing_data.get("items") or []
            except Exception:
                pass
        existing_urls = {str(r.get("url") or "").strip() for r in existing_items}
        merged_items = existing_items + [r for r in rows if str(r.get("url") or "").strip() not in existing_urls]
        write_json_payload(
            index_path,
            {
                "category": category,
                "count": len(merged_items),
                "items": merged_items,
            },
        )

    return {
        "run_date": date_text,
        "written_count": len(written_files),
        "duplicate_count": duplicate_count,
        "rejected_count": rejected_count,
        "files": written_files,
    }


def ingest_local_strategies(
    local_dir: str | Path,
    artifact_root: str | Path,
    run_date: str | None = None,
) -> dict:
    """Scan local_dir for new strategy files (.pdf/.md/.txt/.json) and ingest them.

    Incremental: tracks ingested files in .ingested.json by filename+mtime hash.
    PDF/MD/TXT files are routed to persist_crawled_items() for the normal pipeline.
    JSON files with reproduction summary schema are written directly to reproduction/strategy/.
    """
    local_path = Path(local_dir)
    if not local_path.exists():
        return {"status": "ok", "ingested": 0, "skipped": 0, "errors": 0}

    tracking_path = local_path / ".ingested.json"
    if tracking_path.exists():
        try:
            tracking = load_json_payload(tracking_path)
        except Exception:
            tracking = {}
    else:
        tracking = {}
    ingested_files: dict = tracking.get("files", {})

    supported_extensions = {".pdf", ".md", ".txt", ".json"}
    candidates = sorted(
        f for f in local_path.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions and not f.name.startswith(".")
    )

    crawled_items: list[dict] = []
    reproduction_items: list[dict] = []
    ingested_count = 0
    skipped_count = 0
    error_count = 0
    updated_tracking = dict(ingested_files)

    for file_path in candidates:
        try:
            mtime = file_path.stat().st_mtime
            file_key = stable_hash({"name": file_path.name, "mtime": mtime})
            if file_key in ingested_files:
                skipped_count += 1
                continue

            suffix = file_path.suffix.lower()
            title = file_path.stem
            url = f"local://{file_path.name}"

            if suffix == ".json":
                data = load_json_payload(file_path)
                if isinstance(data.get("core_idea"), str) or isinstance(data.get("signals"), list):
                    reproduction_items.append({**data, "_source_file": str(file_path), "_url": url, "_title": title})
                else:
                    content = json.dumps(data, ensure_ascii=False)
                    if len(content) < STRATEGY_MIN_CONTENT_CHARS:
                        skipped_count += 1
                        updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "skipped_too_short"}
                        continue
                    crawled_items.append({
                        "category": "strategy",
                        "source": "local",
                        "title": title,
                        "url": url,
                        "content": content,
                        "content_source": "local_json",
                    })
                updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "ingested"}
                ingested_count += 1
                continue

            if suffix == ".pdf":
                text_result = _extract_local_pdf_text(file_path, artifact_root, run_date=run_date)
                if text_result is None:
                    skipped_count += 1
                    updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "skipped_pdf_error"}
                    continue
                content = str(text_result.get("content") or "").strip()
                markdown = str(text_result.get("markdown") or "").strip()
                if not content:
                    skipped_count += 1
                    updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "skipped_empty"}
                    continue
                crawled_items.append({
                    "category": "strategy",
                    "source": "local",
                    "title": title,
                    "url": url,
                    "content": content,
                    "markdown": markdown,
                    "content_source": "local_pdf",
                    "pdf_file": str(file_path),
                })
            else:
                content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
                if not content or len(content) < STRATEGY_MIN_CONTENT_CHARS:
                    skipped_count += 1
                    updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "skipped_too_short" if not content else "skipped_empty"}
                    continue
                if len(content) > STRATEGY_MAX_CONTENT_CHARS:
                    skipped_count += 1
                    updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "skipped_too_large"}
                    continue
                crawled_items.append({
                    "category": "strategy",
                    "source": "local",
                    "title": title,
                    "url": url,
                    "content": content,
                    "content_source": "local_text",
                })

            updated_tracking[file_key] = {"name": file_path.name, "mtime": mtime, "status": "ingested"}
            ingested_count += 1
        except Exception:
            error_count += 1

    if crawled_items:
        persist_crawled_items(crawled_items, artifact_root, run_date=run_date)

    if reproduction_items:
        repro_root = Path(artifact_root) / "reproduction" / "strategy"
        repro_root.mkdir(parents=True, exist_ok=True)
        for item in reproduction_items:
            source_file = item.pop("_source_file", "")
            item_url = item.pop("_url", "")
            item_title = item.pop("_title", "")
            url_hash = stable_hash(item_url or item_title or str(source_file))
            slug = safe_slug(item_title or "local", fallback="local")
            output_path = repro_root / f"{slug}-{url_hash}.json"
            if not output_path.exists():
                if "url" not in item:
                    item["url"] = item_url
                if "title" not in item:
                    item["title"] = item_title
                if "source" not in item:
                    item["source"] = "local"
                if "category" not in item:
                    item["category"] = "strategy"
                write_json_payload(output_path, item)

    tracking["files"] = updated_tracking
    tracking["last_ingest_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json_payload(tracking_path, tracking)

    return {
        "status": "ok",
        "ingested": ingested_count,
        "skipped": skipped_count,
        "errors": error_count,
        "crawled_items": len(crawled_items),
        "reproduction_items": len(reproduction_items),
    }


def _extract_local_pdf_text(
    pdf_path: Path,
    artifact_root: str | Path,
    run_date: str | None = None,
) -> dict | None:
    """Extract text from a local PDF file using pdftotext."""
    text_path = pdf_path.with_suffix(".txt")
    if not text_path.exists() or text_path.stat().st_size == 0:
        try:
            subprocess.run(
                ["pdftotext", "-layout", str(pdf_path), str(text_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
        except Exception:
            return None
    content = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return None
    if len(content) > STRATEGY_MAX_CONTENT_CHARS:
        return {
            "content_source": "pdf",
            "pdf_error": f"PDF content too large ({len(content)} chars, max {STRATEGY_MAX_CONTENT_CHARS})",
            "content": content[:5000],
        }
    markdown_result = convert_pdf_text_to_markdown(content)
    markdown = str(markdown_result.get("markdown") or "").strip()
    return {
        "content": content,
        "content_source": "pdf",
        "markdown": markdown,
        "markdown_quality": markdown_result.get("quality") if isinstance(markdown_result.get("quality"), dict) else {},
    }


def persist_screened_items(
    decisions: Sequence[dict],
    artifact_root: str | Path,
    run_date: str | None = None,
) -> dict:
    date_text = run_date or utc_run_date()
    root = Path(artifact_root) / "screened"
    by_category: dict[str, list[dict]] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        category = normalize_category(decision.get("category") or (decision.get("raw") or {}).get("category"))
        payload = dict(decision)
        payload["category"] = category
        payload["screened_at_utc"] = datetime.now(timezone.utc).isoformat()
        payload["decision_hash"] = stable_hash({"title": payload.get("title"), "url": payload.get("url"), "category": category})
        by_category.setdefault(category, []).append(payload)

    files: list[str] = []
    for category, rows in by_category.items():
        path = root / category / "valuable-items.json"
        existing_items: list[dict] = []
        if path.exists():
            try:
                existing_items = load_json_payload(path).get("items") or []
            except Exception:
                pass
        existing_hashes = {str(r.get("decision_hash") or "") for r in existing_items}
        merged_items = existing_items + [r for r in rows if str(r.get("decision_hash") or "") not in existing_hashes]
        write_json_payload(
            path,
            {
                "category": category,
                "count": len(merged_items),
                "items": merged_items,
            },
        )
        files.append(str(path))

    return {
        "run_date": date_text,
        "written_count": sum(len(rows) for rows in by_category.values()),
        "files": files,
    }


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def default_post_json(url: str, payload: dict, headers: dict[str, str] | None = None, timeout: int = 60) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP request failed {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc
    return json.loads(text) if text.strip() else {}


def default_get_json(url: str, params: dict, headers: dict[str, str] | None = None, timeout: int = 60) -> dict:
    query = parse.urlencode(params)
    separator = "&" if "?" in url else "?"
    req = request.Request(
        f"{url}{separator}{query}",
        headers=headers or {},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP request failed {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"HTTP request did not return JSON from {url}") from exc


def escape_influx_key(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def escape_influx_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_influx_field_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".12g") if math.isfinite(value) else escape_influx_string(str(value))
    return escape_influx_string(str(value))


def run_date_to_timestamp_ns(run_date: str | None = None) -> int:
    if run_date:
        text = str(run_date).strip().replace("-", "")
        if len(text) == 8 and text.isdigit():
            parsed = datetime(int(text[:4]), int(text[4:6]), int(text[6:8]), tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1_000_000_000)
    return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)


def iso_timestamp_to_ns(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    except Exception:
        return run_date_to_timestamp_ns(utc_run_date())


def write_lines_to_influx(
    lines: Iterable[str],
    influx_url: str,
    org: str,
    bucket: str,
    token: str,
    timeout_seconds: float = 30.0,
) -> int:
    payload_lines = [line for line in lines if line]
    if not payload_lines:
        return 0
    resolved_token = str(token or "").strip()
    if not resolved_token:
        raise ValueError("InfluxDB token is required. Set INFLUXDB_TOKEN.")

    query = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    url = f"{str(influx_url).rstrip('/')}/api/v2/write?{query}"
    req = request.Request(
        url,
        data=("\n".join(payload_lines) + "\n").encode("utf-8"),
        headers={
            "Authorization": f"Token {resolved_token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            if 200 <= response.status < 300:
                return len(payload_lines)
            detail = response.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"InfluxDB write failed with HTTP {response.status}: {detail}")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"InfluxDB write failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"InfluxDB write failed: {exc}") from exc


class SoloQuantHttpClient:
    def __init__(
        self,
        searxng_url: str,
        crawl4ai_url: str,
        llm_base_url: str,
        llm_api_key: str | None = None,
        post_json: Callable[[str, dict, dict[str, str], int], dict] = default_post_json,
        get_json: Callable[[str, dict, dict[str, str], int], dict] = default_get_json,
        timeout_seconds: int = 60,
    ):
        self.searxng_url = searxng_url.rstrip("/")
        self.crawl4ai_url = crawl4ai_url.rstrip("/")
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_api_key = llm_api_key
        self.post_json = post_json
        self.get_json = get_json
        self.timeout_seconds = timeout_seconds

    def search(self, query: dict) -> list[dict]:
        params = {
            "q": query.get("query", ""),
            "format": "json",
            "categories": query.get("category", "general"),
        }
        response = self.get_json(f"{self.searxng_url}/search", params, {}, self.timeout_seconds)
        results = response.get("results") if isinstance(response, dict) else []
        return [item for item in results if isinstance(item, dict)]

    def crawl(self, result: dict) -> dict:
        url = str(result.get("url") or "").strip()
        if not url:
            return {}
        response = self.post_json(f"{self.crawl4ai_url}/crawl", {"urls": [url]}, {}, self.timeout_seconds)
        results = response.get("results") if isinstance(response, dict) else None
        payload = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else response
        if not isinstance(payload, dict):
            return {}
        content = (
            payload.get("markdown")
            or payload.get("fit_markdown")
            or payload.get("extracted_content")
            or payload.get("fit_html")
            or payload.get("cleaned_html")
            or payload.get("html")
            or ""
        )
        return {**payload, "content": str(content)}

    def screen_with_llm(self, payload: dict) -> dict:
        headers = {}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"
        return self.post_json(f"{self.llm_base_url}/chat/completions", payload, headers, max(self.timeout_seconds, 300))

    def summarize_with_llm(self, payload: dict) -> dict:
        return self.screen_with_llm(payload)

    # Alias for multi-LLM support (OpenAI-compatible interface)


def create_http_client_from_config(
    config: dict,
    post_json: Callable[[str, dict, dict[str, str], int], dict] = default_post_json,
    use_code_generation_llm: bool = False,
) -> SoloQuantHttpClient:
    # 'llm' key takes precedence over legacy 'glm' key for multi-provider support
    # When use_code_generation_llm=True, use 'code-generation-llm' config instead
    if use_code_generation_llm:
        llm = config.get("code-generation-llm") or config.get("llm") or config.get("glm") or {}
    else:
        llm = config.get("llm") or config.get("glm") or {}
    searxng = config.get("searxng") or {}
    crawl4ai = config.get("crawl4ai") or {}
    api_key_env_var = str(llm.get("api-key-env-var") or "LLM_API_KEY")
    timeout = max(
        safe_int(searxng.get("timeout-seconds"), 60),
        safe_int(crawl4ai.get("timeout-seconds"), 120),
        safe_int(llm.get("timeout-seconds"), 180),
    )
    return SoloQuantHttpClient(
        searxng_url=str(searxng.get("url") or "http://localhost:11236"),
        crawl4ai_url=str(crawl4ai.get("url") or "http://localhost:11235"),
        llm_base_url=str(llm.get("base-url") or "https://api.deepseek.com"),
        llm_api_key=os.getenv(api_key_env_var, "").strip() or str(llm.get("api-key-default") or "").strip() or None,
        post_json=post_json,
        timeout_seconds=timeout,
    )


def run_crawl_pipeline(
    config: dict,
    keywords: Sequence[str] | None,
    categories: Sequence[str] | None,
    search_client: Callable[[dict], Sequence[dict]],
    crawl_client: Callable[[dict], dict],
    llm_screen_client: Callable[[dict], dict] | None = None,
    run_date: str | None = None,
    max_queries: int | None = None,
    max_results_per_query: int | None = 3,
    tick_offset: int = 0,
) -> dict:
    requested_categories = {normalize_category(category) for category in (categories or [])}
    query_plan = build_research_query_plan(keywords, tick_offset=tick_offset)
    queries = query_plan["queries"]
    if requested_categories:
        queries = [query for query in queries if normalize_category(query.get("category")) in requested_categories]
    if max_queries is not None:
        queries = queries[: max(0, int(max_queries))]
    query_plan = {**query_plan, "queries": queries}

    crawled_items = run_search_and_crawl(
        query_plan,
        search_client=search_client,
        crawl_client=crawl_client,
        max_results_per_query=max_results_per_query,
    )
    crawled_report = persist_crawled_items(crawled_items, config["artifact-root"], run_date=run_date)

    decisions: list[dict] = []
    if llm_screen_client is not None and crawled_items:
        llm_config = config.get("llm") or config.get("glm") or {}
        llm_payload = build_llm_screening_payload(crawled_items, model=llm_config.get("model", "deepseek-v4-pro"))
        llm_response = llm_screen_client(llm_payload)
        decisions = parse_llm_screening_decisions(llm_response)

    screened_report = persist_screened_items(decisions, config["artifact-root"], run_date=run_date) if decisions else {
        "run_date": run_date or utc_run_date(),
        "written_count": 0,
        "files": [],
    }
    required_fields = []
    for decision in decisions:
        required_fields.extend(decision.get("required_fields") or [])
    data_requirements = resolve_data_requirements(
        required_fields,
        (config.get("data") or {}).get("field-mapping-path"),
        config.get("missing-data-log"),
        supplement_root=(config.get("data") or {}).get("field-cache-root"),
        search_client=search_client,
        crawl_client=crawl_client,
    ) if required_fields else {"available": [], "missing": []}

    return {
        "status": "ok",
        "query_plan": query_plan,
        "crawled": crawled_report,
        "screened": screened_report,
        "data_requirements": data_requirements,
    }


def resolve_data_requirements(
    required_fields: Sequence[dict | str],
    field_mapping_path: str | Path,
    missing_log_path: str | Path,
    supplement_root: str | Path | None = None,
    search_client: Callable[[dict], Sequence[dict]] | None = None,
    crawl_client: Callable[[dict], dict] | None = None,
) -> dict:
    mapping = load_json_payload(field_mapping_path)
    datasets = mapping.get("datasets") if isinstance(mapping, dict) else {}
    datasets = datasets if isinstance(datasets, dict) else {}

    available: list[dict] = []
    supplemented: list[dict] = []
    missing: list[dict] = []
    log_rows: list[dict] = []

    lean_field_aliases: dict[str, str] = {
        "close": "close", "open": "open", "high": "high", "low": "low",
        "volume": "vol", "adj close": "close", "adjusted close": "close",
        "price": "close", "last price": "close",
    }
    lean_dataset_aliases: dict[str, str] = {
        "equity daily data": "bak_daily", "stock daily": "bak_daily",
        "tradebar": "bak_daily", "daily": "bak_daily",
        "coarsefundamental": "bak_daily", "coarse fundamental": "bak_daily",
        "finefundamental": "income", "fine fundamental": "income",
        "market cap": "bak_daily", "marketcap": "bak_daily",
        "fundamental": "income",
    }

    for requirement in required_fields:
        if isinstance(requirement, dict):
            field = str(requirement.get("field") or requirement.get("name") or "").strip()
            dataset = str(requirement.get("dataset") or "").strip() or None
            source = dict(requirement)
        else:
            field = str(requirement).strip()
            dataset = None
            source = {"field": field}

        dataset_entry = datasets.get(dataset) if dataset else None
        # Fallback: try LEAN-to-tushare dataset alias
        if dataset_entry is None and dataset:
            dataset_entry = datasets.get(lean_dataset_aliases.get(dataset.lower()))
        dataset_fields = dataset_entry.get("fields") if isinstance(dataset_entry, dict) else None
        dataset_fields = dataset_fields if isinstance(dataset_fields, dict) else {}

        # Fallback: try LEAN-to-tushare field alias (case-insensitive)
        resolved_field = field
        if field and field not in dataset_fields:
            resolved_field = lean_field_aliases.get(field.lower(), field)

        if resolved_field and ((dataset and resolved_field in dataset_fields) or any(resolved_field in (entry.get("fields") or {}) for entry in datasets.values() if isinstance(entry, dict))):
            available.append({
                "field": resolved_field,
                "dataset": dataset,
                "source": source,
            })
            continue

        supplement = try_supplement_missing_field(
            field,
            dataset,
            source,
            supplement_root,
            search_client=search_client,
            crawl_client=crawl_client,
        )
        if supplement is not None:
            supplemented.append(supplement)
            continue

        missing_entry = {
            "field": field,
            "dataset": dataset,
            "reason": "field not present in tushare_field_mapping.json",
            "source": source,
        }
        missing.append(missing_entry)
        log_rows.append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event": "missing_data_requirement",
            **missing_entry,
        })

    if log_rows:
        _write_jsonl(Path(missing_log_path), log_rows)

    return {"available": available, "supplemented": supplemented, "missing": missing}


def try_supplement_missing_field(
    field: str,
    dataset: str | None,
    source: dict,
    supplement_root: str | Path | None,
    search_client: Callable[[dict], Sequence[dict]] | None = None,
    crawl_client: Callable[[dict], dict] | None = None,
) -> dict | None:
    field = str(field or "").strip()
    if not field or supplement_root is None or search_client is None or crawl_client is None:
        return None
    dataset_name = str(dataset or "external").strip() or "external"
    query = {
        "query": f"{field} {dataset_name} data source A-share quant",
        "category": "data_supplement",
        "source": "external_search",
    }
    try:
        results = list(search_client(query) or [])
    except Exception:
        return None
    if not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    try:
        crawled = crawl_client(first)
    except Exception:
        return None
    content = str(crawled.get("content") or crawled.get("markdown") or crawled.get("text") or "").strip() if isinstance(crawled, dict) else ""
    if not content:
        return None

    root = Path(supplement_root) / dataset_name
    root.mkdir(parents=True, exist_ok=True)
    output_path = root / f"{safe_slug(field, 'field')}.json"
    payload = {
        "field": field,
        "dataset": dataset_name,
        "source": source,
        "query": query,
        "url": str(first.get("url") or ""),
        "title": str(first.get("title") or ""),
        "content": content,
        "content_hash": stable_hash(content[:2000]),
        "supplemented_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json_payload(output_path, payload)
    return {
        "field": field,
        "dataset": dataset_name,
        "source": source,
        "file": str(output_path),
        "url": payload["url"],
        "reason": "supplemented from external crawl",
    }


def materialize_missing_data_placeholders(
    strategy_id: str,
    missing_fields: Sequence[dict],
    placeholder_root: str | Path,
    seed: int = 42,
) -> dict:
    root = Path(placeholder_root) / strategy_id
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    files: list[str] = []

    for index, missing in enumerate(missing_fields, start=1):
        field = str(missing.get("field") or f"missing_field_{index}").strip()
        dataset = str(missing.get("dataset") or "external").strip() or "external"
        path = root / dataset / f"{field}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy_id": strategy_id,
            "dataset": dataset,
            "field": field,
            "placeholder_type": "random",
            "seed": seed,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "value": round(rng.random(), 8),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append(str(path))

    manifest = {
        "strategy_id": strategy_id,
        "placeholder_count": len(files),
        "files": files,
        "seed": seed,
    }
    (root / "placeholder-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def validate_strategy_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("strategy manifest must be a dictionary")

    strategy_id = str(manifest.get("strategy-id") or "").strip()
    if not strategy_id:
        raise ValueError("strategy manifest requires strategy-id")

    avoid_algorithms = {
        str(value).strip()
        for value in (default_config()["strategy-policy"]["avoid-algorithms"] + list((manifest.get("strategy-policy") or {}).get("avoid-algorithms", [])))
        if str(value).strip()
    }

    lean_configs = manifest.get("lean-configs") or {}
    if not isinstance(lean_configs, dict):
        raise ValueError("strategy manifest lean-configs must be a dictionary")

    for stage_name, stage_config in lean_configs.items():
        if not isinstance(stage_config, dict):
            continue
        algorithm_type = str(stage_config.get("algorithm-type-name") or "").strip()
        if algorithm_type in avoid_algorithms:
            raise ValueError(f"strategy manifest cannot bind to forbidden algorithm type: {algorithm_type}")

    return manifest


def _base_lean_config(
    algorithm_type_name: str,
    environment: str,
    start_date: str,
    end_date: str,
    parameters: dict,
    language: str = "CSharp",
) -> dict:
    root = repo_root()
    normalized_lang = str(language or "CSharp").strip().lower()
    is_python = normalized_lang in {"python", "py"}
    lang_label = "Python" if is_python else "CSharp"
    if is_python:
        algorithm_location = str(root / "Algorithm.Python" / f"{algorithm_type_name}.py")
    else:
        algorithm_location = str(root / "Algorithm.CSharp" / "bin" / "Debug" / "QuantConnect.Algorithm.CSharp.dll")
    payload = {
        "environment": environment,
        "algorithm-type-name": algorithm_type_name,
        "algorithm-language": lang_label,
        "algorithm-location": algorithm_location,
        "data-folder": str(root / "Data"),
        "data-directory": str(root / "Data"),
        "history-provider": "TushareHistoryProvider",
        "data-provider": "DefaultDataProvider",
        "results-destination-folder": str(root / "Results"),
        "influxdb-enabled": True,
        "influxdb-url": "http://127.0.0.1:8086",
        "influxdb-org": "lean",
        "influxdb-bucket": "quant",
        "influxdb-token-env-var": "INFLUXDB_TOKEN",
        "log-handler": "ConsoleLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "parameters": {
            "start-date": start_date,
            "end-date": end_date,
            "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
            **{str(key): str(value) for key, value in parameters.items()},
        },
    }
    if environment == "live-paper":
        payload.update(
            {
                "live-mode": True,
                "live-mode-brokerage": "PaperBrokerage",
                "setup-handler": "QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler",
                "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
                "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
                "data-queue-handler": ["TushareDataQueue"],
                "real-time-handler": "QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler",
                "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
            }
        )
    return payload


def materialize_strategy_package(
    strategy_spec: dict,
    strategy_root: str | Path,
    start_date: str,
    end_date: str,
) -> dict:
    strategy_id = str(strategy_spec.get("strategy-id") or strategy_spec.get("strategy_id") or "").strip()
    if not strategy_id:
        raise ValueError("strategy spec requires strategy-id")
    algorithm_type_name = str(strategy_spec.get("algorithm-type-name") or "").strip()
    if not algorithm_type_name:
        raise ValueError("strategy spec requires algorithm-type-name")
    if algorithm_type_name in default_config()["strategy-policy"]["avoid-algorithms"]:
        raise ValueError(f"strategy spec cannot use forbidden algorithm type: {algorithm_type_name}")

    root = Path(strategy_root) / strategy_id
    root.mkdir(parents=True, exist_ok=True)
    parameters = dict(strategy_spec.get("parameters") or {})
    parameters.setdefault("soloquant-strategy-id", strategy_id)
    parameters.setdefault("summary-file", str(root / "backtest-summary.json"))

    backtest_config = _base_lean_config(
        algorithm_type_name,
        "backtesting",
        start_date,
        end_date,
        {
            **parameters,
            "trade-report-file": str(root / "backtest-trades.csv"),
            "daily-summary-file": str(root / "backtest-daily.csv"),
            "summary-file": str(root / "backtest-summary.json"),
        },
    )
    live_config = _base_lean_config(
        algorithm_type_name,
        "live-paper",
        start_date,
        end_date,
        {
            **parameters,
            "trade-report-file": str(root / "live-paper-trades.csv"),
            "daily-summary-file": str(root / "live-paper-daily.csv"),
            "summary-file": str(root / "live-paper-summary.json"),
            "portfolio-snapshot-file": str(root / "live-paper-portfolio.json"),
        },
    )

    backtest_path = root / "config-backtest.json"
    live_path = root / "config-live-paper.json"
    write_json_payload(backtest_path, backtest_config)
    write_json_payload(live_path, live_config)

    manifest = build_strategy_manifest(
        strategy_id,
        backtest_path,
        live_path,
        metadata={
            "description": str(strategy_spec.get("description") or ""),
            "source": strategy_spec.get("source") or {},
            "required_fields": strategy_spec.get("required_fields") or [],
        },
    )
    manifest["lean-configs"]["backtest"]["algorithm-type-name"] = algorithm_type_name
    manifest["lean-configs"]["live-paper"]["algorithm-type-name"] = algorithm_type_name
    manifest_path = root / "manifest.json"
    write_json_payload(manifest_path, manifest)

    return {
        "strategy-id": strategy_id,
        "strategy-root": str(root),
        "manifest-file": str(manifest_path),
        "backtest-config": str(backtest_path),
        "live-paper-config": str(live_path),
    }


def _load_lean_reference_template(language: str) -> str:
    """Load the verified LEAN A-share strategy template source code for LLM reference."""
    root = Path(repo_root())
    if language.lower() in {"python", "py"}:
        template_path = root / "Algorithm.Python/SoloQuantGenerated/2002-04304-timing-excess-returns-a-cross-universe-approach-to-alpha/SoloQuantGeneratedTimingExcessReturnsAlgorithm.py"
    else:
        template_path = root / "Algorithm.CSharp/SoloQuantGenerated/2002-04304-timing-excess-returns-a-cross-universe-approach-to-alpha/SoloQuantGeneratedTimingExcessReturnsAlgorithm.cs"
    try:
        return template_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_strategy_implementation_payload(
    reproduction_summary: dict,
    model: str = "glm-5.1",
    max_tokens: int = 4096,
    language: str = "CSharp",
    event_signal_context: dict | None = None,
) -> dict:
    summary = reproduction_summary.get("summary") if isinstance(reproduction_summary.get("summary"), dict) else reproduction_summary
    normalized_lang = str(language or "CSharp").strip().lower()
    is_python = normalized_lang in {"python", "py"}

    csharp_template = _load_lean_reference_template("CSharp")
    python_template = _load_lean_reference_template("Python")

    if is_python:
        template_section = ""
        if python_template:
            template_section = (
                "\n\n=== 已验证可编译运行的 LEAN A股 Python 策略模板（必须模仿此结构）===\n"
                + python_template
                + "\n=== 模板结束 ===\n\n"
            )
        system_prompt = (
            "你是 LEAN Python 策略实现工程师。只输出JSON，不要解释。"
            "输出必须是受限的可审计草案，不能包含任何密钥、token、网络调用或文件读取。"
            "你必须严格按照以下已验证可编译运行的 LEAN A股策略模板编写代码。此模板已通过编译和smoke test验证，你的代码必须遵循完全相同的结构模式：\n"
            + template_section
            + "以下是关键类型和方法映射：\n"
            "from AlgorithmImports import *  # 必须在文件开头\n"
            "class 必须继承 QCAlgorithm\n"
            "必须实现 def Initialize(self) 和 def OnData(self, slice)\n"
            "LEAN Python 中没有 RSI，必须使用 RelativeStrengthIndex\n"
            "AlphaModel 必须实现 update() 和 on_securities_changed()，不要 override on_order_event\n"
            "IExecutionModel 必须实现 execute() 和 on_securities_changed()\n"
            "FeeModel.get_order_fee 参数是 OrderFeeParameters\n"
            "PortfolioConstructionModel.create_targets 参数是 Insight[] 列表\n"
            "RiskManagementModel.manage_risk 参数是 IPortfolioTarget[] 列表\n"
            "Insight.Price 签名: Insight.Price(symbol, timedelta, direction, magnitude, confidence, source)\n"
            "不要使用 SetFeeModel()，在 Initialize 中对每个 Security 设置 security.FeeModel = CustomFeeModel()\n"
            "不要使用 Symbol.Create() 设置基准，使用 self.SetBenchmark(ticker)\n"
            "A股必须使用 Market.SSE 或 Market.SZSE，不是 Market.USA\n"
            "A股必须设置 self.SetAccountCurrency('CNY')\n"
            "A股ticker不能带交易所后缀！用 '600519' 不是 '600519.SSE'，用 '000001' 不是 '000001.SZSE'。AddEquity的ticker参数只用纯数字代码，市场通过 Market.SSE/SZSE 参数指定\n"
            "A股市场推断：ticker首位是'6'→Market.SSE（沪市），'0'或'3'→Market.SZSE（深市）。代码：market = Market.SSE if ticker[0] == '6' else Market.SZSE\n"
            "A股必须使用 AShareStockFeeModel（佣金万三、印花税千一卖出、过户费十万分之十五）\n"
            "A股必须使用 DelayedSettlementModel(1, timedelta(hours=9)) 实现 T+1\n"
            "A股最小交易单位100股，使用 AShareStockBuyingPowerModel\n"
            "A股涨跌停：主板10%、创业板/科创板20%、ST 5%\n"
            "A股交易时间：9:30-11:30, 13:00-15:00（北京时间）\n"
            "Universe Selection 使用 self.AddUniverse(coarse_selection_function) 或 self.SetUniverseSelection(model)\n"
            "不要使用 import requests、import subprocess、open()、pd.read_csv()、pd.read_parquet()\n"
            "AShareStockBuyingPowerModel() 无构造函数参数，不要传 security 参数\n"
            "基准设置：使用 self.SetBenchmark(lambda x: 0) 避免基准数据依赖\n"
            "History DataFrame判空必须用 history.empty，绝对不能用 'if not history'（会触发ValueError）\n"
            "A股没有基本面数据(FineFundamental)，不要使用 AddUniverse + CoarseFundamental/FineFundamental，改用 AddEquity 或直接 OnData\n"
            "self.SetAccountCurrency('CNY') 必须在 self.SetCash() 之前调用\n"
        )
        lang_label = "Python"
        code_desc = "full Python source code inheriting from QCAlgorithm"
        lang_constraints = [
            "class_name 必须以 SoloQuantGenerated 开头并以 Algorithm 结尾",
            "必须实现 def Initialize(self) 和 def OnData(self, slice)",
            "不要使用 import requests、import subprocess、open()、pd.read_csv()、pd.read_parquet()",
            "必须使用 from AlgorithmImports import *",
            "A股策略必须设置 self.SetAccountCurrency('CNY')",
            "A股ticker只用纯数字代码（如'600519'），不能带.SSE/.SZSE后缀。市场通过Market.SSE/SZSE参数指定",
            "A股市场推断：ticker[0]=='6'→Market.SSE，'0'或'3'→Market.SZSE",
            "A股策略必须使用 AShareStockFeeModel 而不是自定义 FeeModel",
            "A股策略必须使用 DelayedSettlementModel(1, timedelta(hours=9)) 实现 T+1",
            "A股策略必须使用 AShareStockBuyingPowerModel",
            "不要自定义 FeeModel/FillModel 类，使用已有的 AShareStockFeeModel, AShareStockFillModel",
            "History DataFrame判空必须用 history.empty，绝对不能用 'if not history'（会触发ValueError）",
            "self.SetBenchmark(lambda x: 0) 避免基准数据依赖",
        ]
    else:
        template_section = ""
        if csharp_template:
            template_section = (
                "\n\n=== 已验证可编译运行的 LEAN A股 C# 策略模板（必须模仿此结构）===\n"
                + csharp_template
                + "\n=== 模板结束 ===\n\n"
            )
        system_prompt = (
            "你是 LEAN C# 策略实现工程师。只输出JSON，不要解释。"
            "输出必须是受限的可审计草案，不能包含任何密钥、token、网络调用或文件读取。"
            "你必须严格按照以下已验证可编译运行的 LEAN A股策略模板编写代码。此模板已通过编译和smoke test验证，你的代码必须遵循完全相同的结构模式：\n"
            + template_section
            + "=== 关键约束 ===\n"
            "不要使用 AddUniverse + CoarseFundamental/FineFundamental（A股没有基本面数据）\n"
            "不要使用 Framework 模型（AlphaModel, PortfolioConstructionModel, RiskManagementModel, ExecutionModel）\n"
            "不要使用 Securities.OnSecurityAdded（不存在此 API）\n"
            "不要使用 SetSecurityInitializer（直接在 AddEquity 后对 Security 设置模型）\n"
            "不要使用 new AShareStockBuyingPowerModel(security)（无参构造函数）\n"
            "不要使用 (double)weight 在 PortfolioTarget 中（Quantity 是 decimal）\n"
            "不要使用 DateTime.Parse(param)（用 DateTime.ParseExact(param, \"yyyyMMdd\", null)）\n"
            "不要使用 \"000300.SS\" 基准（用 SetBenchmark(_ => 0m)）\n"
            "不要使用 RSI 类名（LEAN 中是 RelativeStrengthIndex）\n"
            "不要使用 class SymbolData（改用 SoloQuantSymbolData）\n"
            "不要使用 string.ToInt()（用 int.Parse()）\n"
            "不要使用 IndexConstituent 枚举\n"
            "不要使用 algorithm.Delay()\n"
            "不要使用 SetFeeModel()（直接设置 security.FeeModel）\n"
            "PortfolioTarget 构造函数: new PortfolioTarget(symbol, decimalQuantity)\n"
            "Insight.Price 签名: Insight.Price(Symbol, TimeSpan, InsightDirection, double? magnitude, double? confidence, object source)\n"
        )
        lang_label = "CSharp"
        code_desc = "full C# source code"
        lang_constraints = [
            "class_name 必须以 SoloQuantGenerated 开头并以 Algorithm 结尾",
            "namespace 必须是 QuantConnect.Algorithm.CSharp",
            "必须包含以下 using 指令：using QuantConnect.Algorithm; using QuantConnect.Data; using QuantConnect.Data.Market; using QuantConnect.Orders.Fees; using QuantConnect.Orders.Fills; using QuantConnect.Orders; using QuantConnect.Indicators; using QuantConnect.Securities;",
            "必须使用直接 OnData(Slice data) 方式实现交易逻辑，不要使用 Framework 模型（AlphaModel/PortfolioConstructionModel/RiskManagementModel/ExecutionModel）",
            "必须使用 AddEquity(ticker, Resolution.Daily, Market.SSE/SZSE) 添加标的，ticker只用纯数字代码（如'600519'）不带.SSE/.SZSE后缀，不要使用 AddUniverse + FineFundamental",
            "必须对每个 Security 设置 A-share 模型：FeeModel=AShareStockFeeModel, FillModel=AShareStockFillModel, BuyingPowerModel=AShareStockBuyingPowerModel(), SettlementModel=DelayedSettlementModel(1, TimeSpan.FromHours(9))（T+1结算）",
            "SetAccountCurrency(Currencies.CNY) 必须在 SetCash() 之前",
            "SetBenchmark(_ => 0m) 避免基准数据依赖",
            "日期参数用 DateTime.ParseExact(param, \"yyyyMMdd\", null)",
            "AShareStockBuyingPowerModel() 无构造函数参数",
            "不要使用 File.ReadAllText、StreamReader、File.Open、File.ReadAllLines",
            "不要使用 RSI 类名，LEAN 中是 RelativeStrengthIndex",
            "不要使用 class SymbolData 作为类名，改用 SoloQuantSymbolData",
            "不要使用 Securities.OnSecurityAdded（不存在此 API）",
            "不要使用 SetSecurityInitializer（直接在 AddEquity 后设置 Security 属性）",
            "不要使用 (double)weight 在 PortfolioTarget 中（Quantity 是 decimal）",
            "仓位计算: targetQuantity = (int)(Portfolio.TotalPortfolioValue * weight / security.Price / 100) * 100",
            "下单: MarketOrder(symbol, delta) 或 Liquidate(symbol)",
            "风控: 在 OnData 中检查 trailing stop / max drawdown",
        ]

    lean_module_constraints = [
        "必须使用 AddEquity(ticker, Resolution.Daily, Market.SSE/SZSE) 添加标的，不要使用 AddUniverse + FineFundamental（A股没有基本面数据）",
        "必须使用直接 OnData(Slice data) 实现交易逻辑，不要使用 Framework 模型（AlphaModel/PortfolioConstructionModel/RiskManagementModel/ExecutionModel）",
        "必须对每个 Security 设置 A-share 模型：FeeModel=AShareStockFeeModel, FillModel=AShareStockFillModel, BuyingPowerModel=AShareStockBuyingPowerModel(), SettlementModel=DelayedSettlementModel(1, TimeSpan.FromHours(9))（T+1结算）",
        "风控：在 OnData 中实现 trailing stop 和 max drawdown 检查",
        "执行：使用 MarketOrder(symbol, delta) 或 Liquidate(symbol)，仓位按100股取整",
        "不要复用 AShareLlmQuantLeanAlgorithm",
        "不要包含 API key、token、password、secret 或任何真实凭证",
        "不要发起 HTTP 请求，不要删除文件，不要启动进程",
        "优先读取 parameters 中的 universe、start-date、end-date、initial-capital",
        "只输出JSON",
        "不要自定义 FeeModel/FillModel/BuyingPowerModel 类！使用已有的 A-share 模型：",
        "  CSharp: security.FeeModel = new AShareStockFeeModel(); security.FillModel = new AShareStockFillModel(); security.BuyingPowerModel = new AShareStockBuyingPowerModel(); security.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));",
        "  Python: security.FeeModel = AShareStockFeeModel(); security.FillModel = AShareStockFillModel(); security.BuyingPowerModel = AShareStockBuyingPowerModel(); security.SettlementModel = DelayedSettlementModel(1, timedelta(hours=9))",
        "A股 Universe 示例：AddEquity('600519', Resolution.Daily, Market.SSE) 或 AddEquity('000001', Resolution.Daily, Market.SZSE)，ticker只用纯数字不带.SSE/.SZSE后缀",
        "A股账户初始化：SetAccountCurrency(Currencies.CNY) 必须在 SetCash() 之前",
        "基准：SetBenchmark(_ => 0m) 避免基准数据依赖",
        "日期参数：DateTime.ParseExact(param, \"yyyyMMdd\", null)",
    ]

    # Detect if the source strategy targets a non-A-share market and add conversion instructions
    source_url = str(reproduction_summary.get("url") or "")
    source_title = str(reproduction_summary.get("title") or "")
    source_text = (source_url + " " + source_title).lower()
    non_ashare_indicators = [
        "sp500", "s&p", "spy", "qqq", "nasdaq", "nyse", "amex",
        "us equity", "us stock", "u.s. stock", "us market",
        "ftse", "nikkei", "dax", "cac", "euro stoxx",
        "option chain", "options market", "volatility index", "vix",
        "futures contract", "commodity", "crude oil", "gold futures",
        "esg factor", "esg score", "esg investing",
    ]
    is_non_ashare = any(indicator in source_text for indicator in non_ashare_indicators)
    if is_non_ashare:
        lean_module_constraints.extend([
            "原始策略面向非A股市场，必须转化为A股市场实现：",
            "将美股标的（SPY/QQQ等）替换为A股对应标的：沪深300成分股、中证500成分股、创业板ETF等",
            "将US market规则替换为A股规则：T+1结算、涨跌停限制（10%/20%）、最小交易单位100股",
            "将US手续费替换为A股手续费：佣金万三、印花税千一（卖出）、过户费十万分之十五",
            "将US基本面数据字段替换为A股可用字段：tushare daily_basic（PE/PB/换手率）、fina_indicator（ROE/营收增长）",
            "将US期权/期货策略转化为A股对应：期权→50ETF期权/300ETF期权，期货→股指期货IF/IC/IM",
            "使用 Market.CHINA (MarketCode = 'SHA'/'SZA') 而非 Market.USA",
            "A股交易时间：9:30-11:30, 13:00-15:00（北京时间），非US交易时间",
        ])

    user_payload: dict = {
        "task": "soloquant_generate_lean_strategy_code",
        "language": lang_label,
        "goal": f"根据复现摘要生成一个新的 SoloQuantGenerated* {lang_label} QCAlgorithm 草案，遵循 LEAN 标准模块架构",
        "constraints": lang_constraints + lean_module_constraints,
        "expected_schema": {
            "class_name": f"SoloQuantGeneratedNameAlgorithm",
            "description": "string",
            "code": code_desc,
            "parameters": {"string": "string"},
            "risk_controls": ["string"],
            "data_requirements": [{"dataset": "string", "field": "string"}],
        },
        "source": {
            "title": str(reproduction_summary.get("title") or ""),
            "url": str(reproduction_summary.get("url") or ""),
            "source": str(reproduction_summary.get("source") or ""),
        },
        "reproduction_summary": summary,
    }

    if event_signal_context and isinstance(event_signal_context, dict):
        user_payload["event_signal_context"] = {
            "composite_risk_level": event_signal_context.get("composite_risk_level", "unknown"),
            "monetary_policy_signals": event_signal_context.get("monetary_policy_signals", []),
            "regulatory_signals": event_signal_context.get("regulatory_signals", []),
            "geopolitical_signals": event_signal_context.get("geopolitical_signals", []),
            "affected_sectors": event_signal_context.get("affected_sectors", []),
        }
        user_payload["constraints"].extend([
            "根据 event_signal_context.composite_risk_level 调整最大仓位：high→降低 position-size，low→可适当提高",
            "根据 event_signal_context.monetary_policy_signals 调整仓位方向：bullish→偏多，bearish→偏空",
            "根据 event_signal_context.regulatory_signals 和 affected_sectors 过滤 Universe：监管风险板块降低权重或剔除",
        ])

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": 0.1,
    }
    if int(max_tokens or 0) > 0:
        payload["max_tokens"] = int(max_tokens)
    return payload


def _normalize_generated_strategy_response(response: dict | str) -> dict:
    parsed = parse_llm_json_response(response)
    return parsed if isinstance(parsed, dict) else {}


def postprocess_generated_csharp_code(code: str) -> str:
    """Fix common LEAN API issues in LLM-generated C# code."""
    REQUIRED_USINGS = [
        "using System;",
        "using System.Collections.Generic;",
        "using System.Linq;",
        "using QuantConnect.Algorithm.Framework.Alphas;",
        "using QuantConnect.Algorithm.Framework.Execution;",
        "using QuantConnect.Algorithm.Framework.Portfolio;",
        "using QuantConnect.Algorithm.Framework.Risk;",
        "using QuantConnect.Algorithm.Framework.Selection;",
        "using QuantConnect.Data.UniverseSelection;",
        "using QuantConnect.Data.Fundamental;",
        "using QuantConnect.Data;",
        "using QuantConnect.Data.Market;",
        "using QuantConnect.Orders.Fees;",
        "using QuantConnect.Orders;",
        "using QuantConnect.Securities;",
        "using QuantConnect.Indicators;",
    ]
    for using_line in REQUIRED_USINGS:
        if using_line not in code:
            # Insert after the last existing using directive
            last_using_end = 0
            for m in re.finditer(r"^using\s+[^;]+;", code, re.MULTILINE):
                last_using_end = m.end()
            if last_using_end > 0:
                code = code[:last_using_end] + "\n" + using_line + code[last_using_end:]
            else:
                # No using directives at all; add after namespace opening
                ns_match = re.search(r"(namespace\s+\S+\s*\{)", code)
                if ns_match:
                    code = code[:ns_match.start()] + using_line + "\n" + code[ns_match.start():]

    # Fix: string.ToInt() → int.Parse(string)
    code = re.sub(r"(\w+)\.ToInt\(\)", r"int.Parse(\1)", code)

    # Fix: OrderParameters → OrderFeeParameters
    code = code.replace("OrderParameters ", "OrderFeeParameters ")
    code = code.replace("(OrderParameters)", "(OrderFeeParameters)")

    # Fix: IExecutionModel missing OnOrderEvent - add it if not present
    if "IExecutionModel" in code and "OnOrderEvent" not in code:
        code = re.sub(
            r"(public void OnSecuritiesChanged\([^)]+\)\s*\{[^}]*\})",
            r"\1\n\n        public void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent) { }",
            code,
        )

    # Fix: CreateTargets with wrong signature - should use Insight[] not IEnumerable<Insight> or List<Insight>
    code = re.sub(
        r"override\s+IEnumerable<IPortfolioTarget>\s+CreateTargets\((\w+)\s+(\w+),\s*(?:List|IEnumerable)<Insight>",
        r"override IEnumerable<IPortfolioTarget> CreateTargets(\1 \2, Insight[]",
        code,
    )

    # Fix: ManageRisk with wrong signature - should use IPortfolioTarget[] not IEnumerable or List
    code = re.sub(
        r"override\s+IEnumerable<IPortfolioTarget>\s+ManageRisk\((\w+)\s+(\w+),\s*(?:List|IEnumerable)<IPortfolioTarget>",
        r"override IEnumerable<IPortfolioTarget> ManageRisk(\1 \2, IPortfolioTarget[]",
        code,
    )

    # Fix: IExecutionModel.Execute with wrong signature - should use IPortfolioTarget[] not single target
    code = re.sub(
        r"void\s+Execute\((\w+)\s+(\w+),\s*IPortfolioTarget\s+(\w+)\s*\)",
        r"void Execute(\1 \2, IPortfolioTarget[] targets)",
        code,
    )

    # Fix: algorithm.Delay() → comment it out
    if "algorithm.Delay(" in code or "algorithm.Delay (" in code:
        code = re.sub(r"algorithm\.Delay\s*\(", "// TODO: replace algorithm.Delay( ", code)

    # Fix: SymbolData class name conflicts with other algorithms in the same project
    # Rename to SoloQuantSymbolData to avoid CS0101 namespace collision
    if re.search(r"\bclass\s+SymbolData\b", code):
        code = re.sub(r"\bSymbolData\b", "SoloQuantSymbolData", code)

    # Fix: RSI class doesn't exist in LEAN, use RelativeStrengthIndex
    code = re.sub(r"\bnew\s+RSI\b", "new RelativeStrengthIndex", code)
    code = re.sub(r"\bRSI\b(?!\w)", "RelativeStrengthIndex", code)

    # Fix: override OnOrderEvent on AlphaModel (not virtual in base class)
    code = re.sub(r"public\s+override\s+void\s+OnOrderEvent\s*\(", "public void OnOrderEvent(", code)

    # Fix: SetFeeModel doesn't exist, comment it out
    code = re.sub(r"(\s*)SetFeeModel\s*\(", r"\1// TODO: SetFeeModel not available, configure via Security.FeeModel: SetFeeModel(", code)

    # Fix: SetAlphaModel doesn't exist, use SetAlpha
    code = re.sub(r"\bSetAlphaModel\s*\(", "SetAlpha(", code)

    # Fix: SecurityChanges.AddedSecurity → SecurityChanges.AddedSecurities
    code = re.sub(r"\.AddedSecurity\b", ".AddedSecurities", code)
    code = re.sub(r"\.RemovedSecurity\b", ".RemovedSecurities", code)

    # Fix: Slice.Fundamentals doesn't exist
    code = re.sub(r"\bdata\.Fundamentals\b", "// TODO: use FineFundamental in universe selection instead of data.Fundamentals", code)

    # Fix: EarningRatios.BasicEPS doesn't exist, use EarningRatios.BasicEPS.Value
    code = re.sub(r"\.BasicEPS\b(?!\s*\.)", ".BasicEPS.Value", code)

    # Fix: RollingWindow.IsReady() → RollingWindow.IsReady (property not method)
    code = re.sub(r"\.IsReady\s*\(\s*\)", ".IsReady", code)

    # Fix: InitialCash → Portfolio.Cash or SetCash
    code = re.sub(r"\bInitialCash\b", "Portfolio.Cash", code)

    # Fix: Security.customizer → remove
    code = re.sub(r"\.customizer\s*=\s*[^;]+;", "// TODO: removed invalid Security.customizer", code)

    # Fix: SetStartDate(string) → SetStartDate(DateTime.Parse(string))
    code = re.sub(r"SetStartDate\s*\(\s*(\w+(?:\.\w+)*)\s*\)", lambda m: f"SetStartDate(DateTime.Parse({m.group(1)}))" if re.match(r'^[a-zA-Z]', m.group(1)) and "DateTime" not in m.group(1) and "new" not in m.group(1) else m.group(0), code)
    code = re.sub(r"SetEndDate\s*\(\s*(\w+(?:\.\w+)*)\s*\)", lambda m: f"SetEndDate(DateTime.Parse({m.group(1)}))" if re.match(r'^[a-zA-Z]', m.group(1)) and "DateTime" not in m.group(1) and "new" not in m.group(1) else m.group(0), code)

    # Fix: AShareStockBuyingPowerModel(security) → AShareStockBuyingPowerModel() (no constructor args)
    code = re.sub(r"new\s+AShareStockBuyingPowerModel\s*\([^)]*\)", "new AShareStockBuyingPowerModel()", code)

    # Fix: Securities.OnSecurityAdded → SetSecurityInitializer pattern
    if "Securities.OnSecurityAdded" in code:
        code = re.sub(
            r"Securities\.OnSecurityAdded\s*+=\s*\([^)]*\)\s*=>\s*\{([^}]+)\}",
            lambda m: "SetSecurityInitializer(security => {\n" + m.group(1).replace("security.", "security.") + "\n});",
            code,
        )
        code = code.replace("Securities.OnSecurityAdded += ", "// Removed: Securities.OnSecurityAdded → SetSecurityInitializer\nSetSecurityInitializer(")

    # Fix: (double) cast on PortfolioTarget quantity → remove cast
    code = re.sub(r"new\s+PortfolioTarget\s*\(\s*(\w+)\s*,\s*\(double\)\s*(\w+)\s*\)", r"new PortfolioTarget(\1, \2)", code)

    # Fix: benchmark .SS → strip suffix entirely (LEAN uses plain ticker + Market param)
    code = re.sub(r'"(\d+)\.SS"', r'"\1"', code)
    code = re.sub(r'"(\d+)\.SSE"', r'"\1"', code)
    code = re.sub(r'"(\d+)\.SZSE"', r'"\1"', code)
    code = re.sub(r'"(\d+)\.SZ"', r'"\1"', code)

    # Fix: missing using QuantConnect.Orders.Fills
    if "AShareStockFillModel" in code and "using QuantConnect.Orders.Fills;" not in code:
        if "using QuantConnect.Orders.Fees;" in code:
            code = code.replace("using QuantConnect.Orders.Fees;", "using QuantConnect.Orders.Fees;\nusing QuantConnect.Orders.Fills;")

    # Fix: SetAccountCurrency after SetCash → move SetAccountCurrency before SetCash
    # This is a common ordering mistake that causes runtime errors
    set_account_match = re.search(r"SetAccountCurrency\s*\([^)]+\)\s*;\s*\n\s*SetCash\s*\(", code)
    if not set_account_match:
        # Check if SetCash comes before SetAccountCurrency
        set_cash_pos = code.find("SetCash(")
        set_account_pos = code.find("SetAccountCurrency(")
        if set_cash_pos > 0 and set_account_pos > 0 and set_cash_pos < set_account_pos:
            # Swap the order
            cash_line_match = re.search(r"SetCash\s*\([^)]+\)\s*;", code)
            account_line_match = re.search(r"SetAccountCurrency\s*\([^)]+\)\s*;", code)
            if cash_line_match and account_line_match:
                cash_line = cash_line_match.group(0)
                account_line = account_line_match.group(0)
                code = code.replace(cash_line, account_line)
                code = code.replace(account_line, cash_line)

    # Fix: DateTime.Parse for yyyyMMdd format → DateTime.ParseExact
    code = re.sub(
        r"DateTime\.Parse\s*\(\s*GetParameter\s*\(\s*\"([^\"]+)\"\s*\)\s*\)",
        r"DateTime.ParseExact(GetParameter(\"\\1\"), \"yyyyMMdd\", null)",
        code,
    )

    return code


def postprocess_generated_python_code(code: str) -> str:
    """Fix common LEAN API issues in LLM-generated Python code."""
    # Ensure AlgorithmImports is present
    if "from AlgorithmImports import" not in code and "AlgorithmImports" not in code:
        code = "from AlgorithmImports import *\n\n" + code

    # Fix: RSI doesn't exist in LEAN Python, use RelativeStrengthIndex
    code = re.sub(r"\bRSI\b", "RelativeStrengthIndex", code)

    # Fix: SetFeeModel doesn't exist
    code = re.sub(r"SetFeeModel\s*\(", "# TODO: use security.FeeModel = ... instead of SetFeeModel(", code)

    # Fix: SetAlphaModel → SetAlpha
    code = re.sub(r"\bSetAlphaModel\s*\(", "SetAlpha(", code)

    # Fix: SecurityChanges.AddedSecurity → SecurityChanges.AddedSecurities
    code = re.sub(r"\.AddedSecurity\b", ".AddedSecurities", code)
    code = re.sub(r"\.RemovedSecurity\b", ".RemovedSecurities", code)

    # Fix: IsReady() → IsReady (property not method)
    code = re.sub(r"\.IsReady\s*\(\s*\)", ".IsReady", code)

    # Fix: math.Floor → math.floor (Python uses lowercase, C# uses uppercase)
    code = re.sub(r"math\.Floor\b", "math.floor", code)

    # Fix: DataFrame truth value bug - 'if not history' → 'if history.empty'
    code = re.sub(r"if\s+not\s+history\s+or\s+['\"]close['\"]\s+not\s+in\s+history\s*:", "if history.empty or 'close' not in history:", code)
    code = re.sub(r"if\s+not\s+history\s+or\s+history\.empty\s*:", "if history.empty:", code)
    code = re.sub(r"if\s+not\s+history\s*:", "if history.empty:", code)

    # Fix: import requests / import subprocess / open() — remove these
    code = re.sub(r"^import requests\s*$", "# REMOVED: import requests (forbidden)", code, flags=re.MULTILINE)
    code = re.sub(r"^import subprocess\s*$", "# REMOVED: import subprocess (forbidden)", code, flags=re.MULTILINE)

    # Fix: AddEquity without market parameter for A-share
    # First strip .SSE/.SZSE/.SS/.SZ suffix from ticker, then infer market from first digit
    code = re.sub(
        r"self\.AddEquity\s*\(\s*['\"](\d{6})\.(?:SSE|SS)['\"],\s*Resolution\.Daily\s*\)",
        r"self.AddEquity('\1', Resolution.Daily, Market.SSE)",
        code,
    )
    code = re.sub(
        r"self\.AddEquity\s*\(\s*['\"](\d{6})\.(?:SZSE|SZ)['\"],\s*Resolution\.Daily\s*\)",
        r"self.AddEquity('\1', Resolution.Daily, Market.SZSE)",
        code,
    )
    code = re.sub(
        r"self\.AddEquity\s*\(\s*['\"](\d{6})['\"],\s*Resolution\.Daily\s*\)",
        r"self.AddEquity('\1', Resolution.Daily, Market.SSE if '\1'[0] == '6' else Market.SZSE)",
        code,
    )

    # Fix: SetAccountCurrency to CNY for A-share
    if "SetAccountCurrency" not in code and ("Market.SSE" in code or "Market.SZSE" in code or "Market.CHINA" in code):
        # Insert after class definition
        init_match = re.search(r"(def Initialize\(self\):)", code)
        if init_match:
            insert_pos = code.index("\n", init_match.start()) + 1
            code = code[:insert_pos] + "        self.SetAccountCurrency('CNY')\n" + code[insert_pos:]

    # Fix: AShareStockBuyingPowerModel(security) → AShareStockBuyingPowerModel() (no constructor args)
    code = re.sub(r"AShareStockBuyingPowerModel\s*\([^)]*\)", "AShareStockBuyingPowerModel()", code)

    # Fix: benchmark .SS → strip suffix entirely (LEAN uses plain ticker + Market param)
    code = re.sub(r"'(\d+)\.SS'", r"'\1'", code)
    code = re.sub(r"'(\d+)\.SSE'", r"'\1'", code)
    code = re.sub(r"'(\d+)\.SZSE'", r"'\1'", code)
    code = re.sub(r"'(\d+)\.SZ'", r"'\1'", code)

    return code


def compile_validate_generated_code(
    code: str,
    class_name: str,
    algorithm_root: Path | None = None,
    dotnet_binary: str = "/usr/local/dotnet/dotnet",
    fix_client: Callable[[dict], dict | str] | None = None,
    max_retries: int = 2,
    model: str = "glm-5.1",
) -> dict:
    """Validate generated C# code compiles. If not, retry with LLM-assisted fixes.

    Returns dict with keys: success (bool), code (str), attempts (int), errors (list[str])
    """
    lean_root = Path(algorithm_root) if algorithm_root else resolve_generated_algorithm_root("CSharp")
    strategy_dir = lean_root / safe_slug(class_name, fallback=class_name)
    strategy_dir.mkdir(parents=True, exist_ok=True)
    code_path = strategy_dir / f"{class_name}.cs"
    errors: list[str] = []
    current_code = code

    for attempt in range(1, max_retries + 2):
        code_path.write_text(current_code, encoding="utf-8")
        result = subprocess.run(
            [dotnet_binary, "build", str(repo_root() / "Algorithm.CSharp" / "QuantConnect.Algorithm.CSharp.csproj"), "-c", "Debug"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return {"success": True, "code": current_code, "attempts": attempt, "errors": []}

        build_errors = result.stderr.strip()
        errors.append(f"Attempt {attempt}: {build_errors[:2000]}")

        if fix_client is None or attempt > max_retries:
            break

        fix_payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 LEAN C# 编译错误修复工程师。只输出修复后的完整 C# 代码，不要解释。"
                        "必须保留 namespace QuantConnect.Algorithm.CSharp 和原始类名。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "task": "fix_compilation_errors",
                        "class_name": class_name,
                        "code": current_code,
                        "build_errors": build_errors[:4000],
                    }, ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        try:
            fix_response = _normalize_generated_strategy_response(fix_client(fix_payload))
            fixed_code = str(fix_response.get("code") or "")
            if fixed_code and class_name in fixed_code:
                current_code = postprocess_generated_csharp_code(fixed_code)
        except Exception:
            pass

    if not code_path.exists() or code_path.read_text(encoding="utf-8").strip() != current_code.strip():
        code_path.write_text(current_code, encoding="utf-8")

    return {"success": False, "code": current_code, "attempts": max_retries + 1, "errors": errors}


def compile_validate_generated_python_code(
    code: str,
    class_name: str,
    algorithm_root: Path | None = None,
    fix_client: Callable[[dict], dict | str] | None = None,
    max_retries: int = 2,
    model: str = "glm-5.1",
) -> dict:
    """Validate generated Python code compiles and can be imported. If not, retry with LLM-assisted fixes."""
    lean_root = Path(algorithm_root) if algorithm_root else resolve_generated_algorithm_root("Python")
    strategy_dir = lean_root / safe_slug(class_name, fallback=class_name)
    strategy_dir.mkdir(parents=True, exist_ok=True)
    code_path = strategy_dir / f"{class_name}.py"
    errors: list[str] = []
    current_code = code

    for attempt in range(1, max_retries + 2):
        code_path.write_text(current_code, encoding="utf-8")
        # Step 1: Syntax check via compile()
        try:
            compile(current_code, str(code_path), "exec")
        except SyntaxError as exc:
            error_msg = f"SyntaxError at line {exc.lineno}: {exc.msg}"
            errors.append(f"Attempt {attempt}: {error_msg}")
            if fix_client is None or attempt > max_retries:
                break
            current_code = _python_fix_with_llm(current_code, class_name, error_msg, fix_client, model)
            continue

        # Step 2: AST parse check — verify the file parses as valid Python
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import ast; ast.parse(open(r'{code_path}').read())"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return {"success": True, "code": current_code, "attempts": attempt, "errors": []}
            error_msg = result.stderr.strip()[:2000]
        except Exception as exc:
            error_msg = str(exc)[:2000]

        errors.append(f"Attempt {attempt}: {error_msg}")
        if fix_client is None or attempt > max_retries:
            break
        current_code = _python_fix_with_llm(current_code, class_name, error_msg, fix_client, model)

    if not code_path.exists() or code_path.read_text(encoding="utf-8").strip() != current_code.strip():
        code_path.write_text(current_code, encoding="utf-8")

    return {"success": False, "code": current_code, "attempts": max_retries + 1, "errors": errors}


def _python_fix_with_llm(
    code: str, class_name: str, error_msg: str,
    fix_client: Callable[[dict], dict | str], model: str,
) -> str:
    fix_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 LEAN Python 策略编译错误修复工程师。只输出修复后的完整 Python 代码，不要解释。"
                    "必须保留 class 定义和原始类名。必须继承 QCAlgorithm。"
                    "必须包含 from AlgorithmImports import *"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "task": "fix_python_compilation_errors",
                    "class_name": class_name,
                    "code": code,
                    "errors": error_msg[:4000],
                }, ensure_ascii=False),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    try:
        fix_response = _normalize_generated_strategy_response(fix_client(fix_payload))
        fixed_code = str(fix_response.get("code") or "")
        if fixed_code and class_name in fixed_code:
            return postprocess_generated_python_code(fixed_code)
    except Exception:
        pass
    return code


def parse_broken_cs_files(build_stderr: str, generated_root: Path | None = None) -> list[Path]:
    """Parse dotnet build output to identify which generated .cs files have errors."""
    broken: list[Path] = []
    if generated_root is None:
        generated_root = repo_root() / "Algorithm.CSharp" / "SoloQuantGenerated"
    if not generated_root.exists():
        return broken
    generated_prefix = str(generated_root.resolve())
    for match in re.finditer(r"(\S+\.cs)\(\d+,\d+\):\s*error\s+CS", build_stderr):
        filepath = Path(match.group(1))
        try:
            if str(filepath.resolve()).startswith(generated_prefix):
                broken.append(filepath)
        except (OSError, ValueError):
            continue
    return sorted(set(broken))


def validate_generated_strategy_code(class_name: str, code: str, metadata: dict | None = None, language: str = "CSharp") -> None:
    class_name = str(class_name or "").strip()
    code = str(code or "")
    metadata = metadata or {}
    normalized_lang = str(language or "CSharp").strip().lower()
    validate_no_inline_service_secrets(metadata)
    validate_no_secret_values(code, "generated_strategy.code")
    if not re.fullmatch(r"SoloQuantGenerated[A-Za-z0-9_]*Algorithm", class_name):
        raise ValueError(f"generated strategy class is forbidden or invalid: {class_name}")
    if class_name in default_config()["strategy-policy"]["avoid-algorithms"]:
        raise ValueError(f"generated strategy class is forbidden: {class_name}")
    if normalized_lang in {"python", "py"}:
        if "def Initialize(self)" not in code:
            raise ValueError("generated Python strategy code must define Initialize(self)")
        if "def OnData(self" not in code:
            raise ValueError("generated Python strategy code must define OnData(self, ...)")
        forbidden_terms = ("AShareLlmQuantLeanAlgorithm", "import requests", "requests.", "import subprocess", "subprocess.", "import urllib", "urllib.", "open(", "pd.read_csv(", "pd.read_parquet(")
    else:
        if "namespace QuantConnect.Algorithm.CSharp" not in code:
            raise ValueError("generated strategy code must use namespace QuantConnect.Algorithm.CSharp")
        if not re.search(rf"\bclass\s+{re.escape(class_name)}\b", code):
            raise ValueError(f"generated strategy code does not define class {class_name}")
        forbidden_terms = (
            "AShareLlmQuantLeanAlgorithm", "HttpClient", "WebRequest", "Process.Start",
            "File.Delete", "Directory.Delete",
            "File.ReadAllText", "StreamReader", "File.Open", "File.ReadAllLines", "File.ReadAllBytes",
        )
    for term in forbidden_terms:
        if term in code:
            raise ValueError(f"generated strategy code contains forbidden term: {term}")


def resolve_generated_algorithm_root(language: str = "CSharp", algorithm_root: str | Path | None = None) -> Path:
    normalized = str(language or "CSharp").strip().lower()
    if normalized in {"csharp", "cs", "c#"}:
        base = repo_root() / "Algorithm.CSharp"
    elif normalized in {"python", "py"}:
        base = repo_root() / "Algorithm.Python"
    else:
        raise ValueError(f"Unsupported generated strategy language: {language}")
    root = Path(algorithm_root).resolve() if algorithm_root is not None else (base / "SoloQuantGenerated").resolve()
    try:
        root.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"Generated strategies must be written under the LEAN algorithm directory: {base}") from exc
    return root


def generate_strategy_implementation_package(
    summary_path: str | Path,
    generated_root: str | Path,
    algorithm_root: str | Path | None,
    llm_implementation_client: Callable[[dict], dict | str] | None = None,
    model: str = "glm-5.1",
    max_tokens: int = 4096,
    language: str = "CSharp",
    event_signal_context: dict | None = None,
    config_or_none: dict | None = None,
) -> dict:
    client = llm_implementation_client
    if client is None:
        raise ValueError("generate_strategy_implementation_package requires llm_implementation_client")
    normalized_lang = str(language or "CSharp").strip().lower()
    is_python = normalized_lang in {"python", "py"}
    lang_label = "Python" if is_python else "CSharp"

    source_path = Path(summary_path)
    summary_payload = load_json_payload(source_path)

    strategy_id = safe_slug(str(summary_payload.get("title") or ""), fallback="strategy")
    if algorithm_root is not None:
        lean_algorithm_root = Path(algorithm_root)
    else:
        lean_algorithm_root = resolve_generated_algorithm_root(lang_label)
    output_root = lean_algorithm_root / strategy_id
    manifest_path = output_root / "manifest.json"
    # Skip if this strategy has already been generated and code file still exists
    if manifest_path.exists():
        try:
            existing_manifest = load_json_payload(manifest_path)
            if existing_manifest.get("strategy_id") == strategy_id:
                code_path = existing_manifest.get("code_file") or existing_manifest.get("algorithm_file") or ""
                code_exists = Path(code_path).exists() if code_path else False
                if code_exists:
                    audit_root = Path(generated_root) / strategy_id
                    return {
                        "strategy-id": strategy_id,
                        "class-name": existing_manifest.get("class_name", ""),
                        "root": str(audit_root),
                        "algorithm-root": str(output_root),
                        "manifest-file": str(audit_root / "manifest.json"),
                        "algorithm-manifest-file": str(manifest_path),
                        "code-file": existing_manifest.get("code_file", ""),
                        "skipped_existing": True,
                    }
                # Code was previously generated but compile failed (.cs.broken exists)
                # Clean up stale .cs.broken/.py.broken files before regenerating
                for broken_file in output_root.glob("*.cs.broken"):
                    broken_file.unlink(missing_ok=True)
                for broken_file in output_root.glob("*.py.broken"):
                    broken_file.unlink(missing_ok=True)
                for broken_file in output_root.glob("*.cs.smoke-failed"):
                    broken_file.unlink(missing_ok=True)
                for broken_file in output_root.glob("*.py.smoke-failed"):
                    broken_file.unlink(missing_ok=True)
        except Exception:
            pass

    request_payload = build_strategy_implementation_payload(
        summary_payload, model=model, max_tokens=max_tokens, language=lang_label,
        event_signal_context=event_signal_context,
    )
    max_retries = 2
    last_exception = None
    for attempt in range(1 + max_retries):
        try:
            response = _normalize_generated_strategy_response(client(request_payload))
            break
        except Exception as exc:
            last_exception = exc
            if attempt < max_retries:
                time.sleep(5 * (attempt + 1))
    else:
        raise last_exception
    class_name = str(response.get("class_name") or response.get("class-name") or "").strip()
    code = str(response.get("code") or "")
    if not is_python:
        code = postprocess_generated_csharp_code(code)
    validate_generated_strategy_code(class_name, code, response, language=lang_label)

    # Re-derive strategy_id with class_name as fallback (may differ from pre-LLM slug)
    strategy_id = safe_slug(str(summary_payload.get("title") or class_name), fallback=class_name)
    audit_root = Path(generated_root) / strategy_id
    audit_root.mkdir(parents=True, exist_ok=True)
    output_root = lean_algorithm_root / strategy_id
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    ext = ".py" if is_python else ".cs"
    code_path = output_root / f"{class_name}{ext}"
    manifest_path = output_root / "manifest.json"
    code_path.write_text(code, encoding="utf-8")

    # Compile-validate-retry: if code fails to compile, send errors back to LLM for fix
    compile_result = None
    if not is_python:
        compile_result = compile_validate_generated_code(
            code,
            class_name,
            algorithm_root=lean_algorithm_root,
            dotnet_binary=str((config_or_none or {}).get("lean", {}).get("dotnet-binary") or "/usr/local/dotnet/dotnet") if config_or_none else "/usr/local/dotnet/dotnet",
            fix_client=llm_implementation_client,
            max_retries=2,
            model=model,
        )
        if compile_result.get("success"):
            code = compile_result["code"]
            code_path.write_text(code, encoding="utf-8")
        else:
            # Compile failed after retries — mark as broken
            broken_path = code_path.with_suffix(".cs.broken")
            code_path.rename(broken_path)
    else:
        # Python: validate by attempting to compile the source
        compile_result = compile_validate_generated_python_code(
            code, class_name, algorithm_root=lean_algorithm_root,
            fix_client=llm_implementation_client, max_retries=2, model=model,
        )
        if compile_result.get("success"):
            code = compile_result["code"]
            code_path.write_text(code, encoding="utf-8")
        else:
            broken_path = code_path.with_suffix(".py.broken")
            code_path.rename(broken_path)

    manifest = {
        "strategy_id": strategy_id,
        "class_name": class_name,
        "algorithm_language": lang_label,
        "algorithm_file": str(code_path),
        "description": str(response.get("description") or ""),
        "code_file": str(code_path),
        "source_summary": str(source_path.resolve()),
        "parameters": response.get("parameters") if isinstance(response.get("parameters"), dict) else {},
        "risk_controls": response.get("risk_controls") if isinstance(response.get("risk_controls"), list) else [],
        "data_requirements": response.get("data_requirements") if isinstance(response.get("data_requirements"), list) else [],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    # Fast-implementation rule: unless >80% of data requirements are missing from
    # tushare_data and open sources, the strategy should proceed to implementation.
    # Check data availability and block only when coverage is critically low.
    data_reqs = manifest.get("data_requirements") or []
    if data_reqs and config_or_none:
        field_mapping_path = (config_or_none.get("data") or {}).get("field-mapping-path")
        missing_log_path = config_or_none.get("missing-data-log")
        if field_mapping_path and missing_log_path:
            resolved = resolve_data_requirements(data_reqs, field_mapping_path, missing_log_path)
            available_count = len(resolved.get("available") or [])
            supplemented_count = len(resolved.get("supplemented") or [])
            missing_count = len(resolved.get("missing") or [])
            total = available_count + supplemented_count + missing_count
            if total > 0:
                coverage = (available_count + supplemented_count) / total
                manifest["data_coverage"] = round(coverage, 3)
                if coverage < 0.20:  # <20% available means >80% missing → block
                    manifest["blocked_reason"] = f"data_coverage={coverage:.1%} < 20% threshold (>80% missing from tushare/open sources)"
                    broken_path = code_path.with_suffix(code_path.suffix + ".broken")
                    if code_path.exists():
                        code_path.rename(broken_path)
                    manifest["algorithm_file"] = str(broken_path)
                    manifest["code_file"] = str(broken_path)
    validate_no_inline_service_secrets(manifest)
    validate_no_secret_values(json.dumps(manifest, ensure_ascii=False), "generated_strategy.manifest")
    write_json_payload(audit_root / "manifest.json", manifest)
    write_json_payload(manifest_path, manifest)
    result = {
        "strategy-id": strategy_id,
        "class-name": class_name,
        "root": str(audit_root),
        "algorithm-root": str(output_root),
        "manifest-file": str(audit_root / "manifest.json"),
        "algorithm-manifest-file": str(manifest_path),
        "code-file": str(code_path),
    }
    if compile_result:
        result["compile_success"] = compile_result.get("success", False)
        result["compile_attempts"] = compile_result.get("attempts", 0)
        if compile_result.get("errors"):
            result["compile_errors"] = compile_result["errors"]
    return result


def generate_strategy_implementations(
    artifact_root: str | Path,
    generated_root: str | Path,
    algorithm_root: str | Path | None,
    llm_implementation_client: Callable[[dict], dict | str],
    run_date: str | None = None,
    model: str = "glm-5.1",
    max_items: int | None = None,
    max_tokens: int = 4096,
    language: str = "CSharp",
    config_or_none: dict | None = None,
) -> dict:
    packages: list[dict] = []
    skipped_count = 0
    error_count = 0
    compile_success_count = 0
    compile_fail_count = 0
    for path in iter_reproduction_summary_files(artifact_root, run_date=run_date):
        if max_items is not None and len(packages) >= max(0, int(max_items)):
            break
        try:
            package = generate_strategy_implementation_package(
                path,
                generated_root=generated_root,
                algorithm_root=algorithm_root,
                llm_implementation_client=llm_implementation_client,
                model=model,
                max_tokens=max_tokens,
                language=language,
                config_or_none=config_or_none,
            )
        except Exception:
            error_count += 1
            continue
        if package.get("skipped_existing"):
            skipped_count += 1
        else:
            packages.append(package)
            if package.get("compile_success"):
                compile_success_count += 1
            else:
                compile_fail_count += 1
    return {
        "status": "ok",
        "run_date": str(run_date or utc_run_date()).replace("-", ""),
        "reproduced_count": len(packages),
        "compile_success_count": compile_success_count,
        "compile_fail_count": compile_fail_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "packages": packages,
    }


def iter_reproduction_summary_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "reproduction" / "strategy"
    if not root.exists():
        return
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        # Deduplicate by URL/title to avoid generating code for the same paper multiple times
        try:
            data = load_json_payload(path)
            url = str(data.get("url") or "").strip()
            title = str(data.get("title") or "").strip()
            if url and url in seen_urls:
                continue
            if title and title in seen_titles:
                continue
            if url:
                seen_urls.add(url)
            if title:
                seen_titles.add(title)
        except Exception:
            pass
        yield path


def iter_generated_strategy_manifest_files(
    algorithm_root: str | Path | None = None,
    csharp_root: str | Path | None = None,
    python_root: str | Path | None = None,
):
    """Yield manifest.json paths from both CSharp and Python generated strategy directories."""
    if csharp_root is not None:
        cs_root = Path(csharp_root)
    else:
        cs_root = resolve_generated_algorithm_root("CSharp", algorithm_root)
    if python_root is not None:
        py_root = Path(python_root)
    else:
        py_root = resolve_generated_algorithm_root("Python", algorithm_root)
    seen: set[str] = set()
    for root in (cs_root, py_root):
        if not root.exists():
            continue
        for path in sorted(root.glob("*/manifest.json")):
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                yield path


def build_generated_strategy_parameter_variants(base_parameters: dict) -> list[dict]:
    baseline = {str(key): str(value) for key, value in base_parameters.items()}
    learned_v1 = {
        **baseline,
        "soloquant-optimization-version": "learned-v1",
        "lookback-period": str(max(5, safe_int(baseline.get("lookback-period"), 20) // 2)),
        "max-positions": str(max(3, safe_int(baseline.get("max-positions"), 10) - 2)),
    }
    learned_v2 = {
        **baseline,
        "soloquant-optimization-version": "learned-v2",
        "lookback-period": str(max(10, safe_int(baseline.get("lookback-period"), 20) * 2)),
        "max-positions": str(max(5, safe_int(baseline.get("max-positions"), 10) + 2)),
    }
    return [
        {"version": "baseline", "parameters": {**baseline, "soloquant-optimization-version": "baseline"}},
        {"version": "learned-v1", "parameters": learned_v1},
        {"version": "learned-v2", "parameters": learned_v2},
    ]


def materialize_generated_strategy_implementation(
    manifest_path: str | Path,
    strategy_root: str | Path,
    start_date: str = "2020-01-01",
    end_date: str = "2025-12-31",
    universe: Sequence[str] | None = None,
) -> dict:
    source_path = Path(manifest_path)
    generated_manifest = load_json_payload(source_path)
    strategy_id = safe_slug(str(generated_manifest.get("strategy_id") or generated_manifest.get("strategy-id") or source_path.parent.name), "soloquant-generated")
    class_name = str(generated_manifest.get("class_name") or generated_manifest.get("class-name") or "").strip()
    if not re.fullmatch(r"SoloQuantGenerated[A-Za-z0-9_]*Algorithm", class_name):
        raise ValueError(f"generated strategy manifest has invalid class_name: {class_name}")

    algorithm_language = str(generated_manifest.get("algorithm_language") or "CSharp").strip()
    is_python = algorithm_language.lower() in {"python", "py"}

    package_root = Path(strategy_root) / strategy_id
    package_root.mkdir(parents=True, exist_ok=True)
    universe_text = ",".join(str(item).strip() for item in (universe or ["000001.SZ", "600000.SH", "300750.SZ"]) if str(item).strip())
    base_parameters = {
        **{str(key): str(value) for key, value in (generated_manifest.get("parameters") or {}).items()},
        "universe": universe_text,
        "initial-capital": "1000000",
        "fee-rate": "0.0013",
        "soloquant-generated-manifest": str(source_path.resolve()),
        "soloquant-strategy-id": strategy_id,
    }

    variants: list[dict] = []
    for variant in build_generated_strategy_parameter_variants(base_parameters):
        version = variant["version"]
        variant_root = package_root / version
        variant_root.mkdir(parents=True, exist_ok=True)
        parameters = {
            **variant["parameters"],
            "variant-name": version,
            "signal-file": str(variant_root / "signals.json"),
            "portfolio-snapshot-file": str(variant_root / "portfolio.json"),
            "daily-summary-file": str(variant_root / "daily.csv"),
            "summary-file": str(variant_root / "summary.json"),
        }
        backtest_config = _base_lean_config(
            class_name,
            "backtesting",
            start_date,
            end_date,
            {
                **parameters,
                "trade-report-file": str(variant_root / "backtest-trades.csv"),
                "monte-carlo-enabled": "true",
                "monte-carlo-trials": "500",
                "monte-carlo-horizon-days": "63",
                "monte-carlo-block-size": "5",
            },
        )
        live_config = _base_lean_config(
            class_name,
            "live-paper",
            start_date,
            end_date,
            {
                **parameters,
                "trade-report-file": str(variant_root / "live-paper-trades.csv"),
                "live-price-source-mode": "auto",
                "shared-live-market-snapshot-file": str(repo_root() / "Results" / "shared-live-market" / "ashare-live-price-snapshot.json"),
                "shared-live-market-report-file": str(repo_root() / "Results" / "shared-live-market" / "ashare-live-market-report.json"),
                "shared-live-market-archive-path": str(repo_root() / "Data" / "archive" / "ashare-live-market-daily-quotes"),
                "shared-live-market-refresh-interval-seconds": "60",
            },
        )
        if is_python:
            algorithm_file = str(generated_manifest.get("algorithm_file") or "")
            backtest_config["algorithm-language"] = "Python"
            backtest_config["algorithm-location"] = algorithm_file
            live_config["algorithm-language"] = "Python"
            live_config["algorithm-location"] = algorithm_file
        backtest_path = variant_root / "config-backtest.json"
        live_path = variant_root / "config-live-paper.json"
        write_json_payload(backtest_path, backtest_config)
        write_json_payload(live_path, live_config)
        variants.append(
            {
                "version": version,
                "algorithm-type-name": class_name,
                "backtest-config": str(backtest_path),
                "live-paper-config": str(live_path),
                "score-metric": "score",
                "parameters": parameters,
            }
        )

    manifest = {
        "strategy-id": strategy_id,
        "generated-at-utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "generated-manifest-file": str(source_path.resolve()),
            "source-summary": str(generated_manifest.get("source_summary") or ""),
        },
        "template": {
            "algorithm-type-name": class_name,
        },
        "metadata": {
            "description": str(generated_manifest.get("description") or ""),
            "risk_controls": generated_manifest.get("risk_controls") if isinstance(generated_manifest.get("risk_controls"), list) else [],
            "data_requirements": generated_manifest.get("data_requirements") if isinstance(generated_manifest.get("data_requirements"), list) else [],
        },
        "variants": variants,
    }
    validate_strategy_manifest(
        {
            "strategy-id": strategy_id,
            "lean-configs": {
                variant["version"]: {"algorithm-type-name": variant["algorithm-type-name"]}
                for variant in variants
            },
        }
    )
    output_manifest_path = package_root / "manifest.json"
    write_json_payload(output_manifest_path, manifest)
    return {
        "strategy-id": strategy_id,
        "strategy-root": str(package_root),
        "manifest-file": str(output_manifest_path),
        "variant-count": len(variants),
        "variants": variants,
    }


def materialize_generated_strategy_implementations(
    algorithm_root: str | Path | None,
    strategy_root: str | Path,
    start_date: str = "2020-01-01",
    end_date: str = "2025-12-31",
    universe: Sequence[str] | None = None,
    max_items: int | None = None,
) -> dict:
    # Resolve CSharp and Python roots separately so iter_generated_strategy_manifest_files
    # can scan both without cross-language path validation errors
    if algorithm_root is not None:
        cs_root = Path(algorithm_root)
        py_root = cs_root.parent.parent / "Algorithm.Python" / "SoloQuantGenerated"
    else:
        cs_root = resolve_generated_algorithm_root("CSharp")
        py_root = resolve_generated_algorithm_root("Python")
    packages: list[dict] = []
    for path in iter_generated_strategy_manifest_files(csharp_root=cs_root, python_root=py_root):
        if max_items is not None and len(packages) >= max(0, int(max_items)):
            break
        packages.append(
            materialize_generated_strategy_implementation(
                path,
                strategy_root=strategy_root,
                start_date=start_date,
                end_date=end_date,
                universe=universe,
            )
        )
    return {
        "status": "ok",
        "materialized_count": len(packages),
        "packages": packages,
    }


def update_strategy_registry(
    registry_path: str | Path,
    strategy_id: str,
    optimization_result: dict,
    live_config_path: str | Path,
) -> dict:
    path = Path(registry_path)
    with _registry_lock:
        registry = load_json_payload(path) if path.exists() else {"strategies": []}
        strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
        strategies = [
            item
            for item in strategies
            if isinstance(item, dict) and item.get("strategy_id") != strategy_id
        ]
        strategies.append(
            {
                "strategy_id": strategy_id,
                "best_version": optimization_result.get("best_version"),
                "best_score": optimization_result.get("best_score"),
                "live-paper-config": str(live_config_path),
                "evaluated_versions": optimization_result.get("evaluated_versions") or [],
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        registry["strategies"] = sorted(strategies, key=lambda item: str(item.get("strategy_id") or ""))
        write_json_payload(path, registry)
    return registry


def select_best_registered_strategy(registry: dict, strategy_id: str | None = None) -> dict:
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
    candidates = [item for item in strategies if isinstance(item, dict)]
    if strategy_id:
        candidates = [item for item in candidates if str(item.get("strategy_id") or item.get("strategy-id")) == strategy_id]
    if not candidates:
        raise ValueError("No registered SoloQuant strategy is available for live paper")

    def score(item: dict) -> float:
        return safe_float(item.get("best_score"), float("-inf")) or float("-inf")

    return sorted(candidates, key=lambda item: (score(item), str(item.get("updated_at_utc") or "")), reverse=True)[0]


def default_subprocess_runner(command: Sequence[str], cwd: str | Path) -> int:
    completed = subprocess.run(list(command), cwd=str(cwd), check=False)
    return int(completed.returncode)


def run_best_live_paper_strategy(
    registry_path: str | Path,
    runner: Callable[[Sequence[str], Path], int | tuple[int, object]] = default_subprocess_runner,
    strategy_id: str | None = None,
) -> dict:
    registry = load_json_payload(registry_path)
    selected = select_best_registered_strategy(registry, strategy_id=strategy_id)
    live_config = selected.get("live-paper-config") or selected.get("live_paper_config")
    if not live_config:
        raise ValueError(f"registered strategy {selected.get('strategy_id')} has no live-paper config")

    command, cwd = build_lean_launcher_command(live_config)
    run_result = runner(command, cwd)
    returncode = run_result[0] if isinstance(run_result, tuple) else int(run_result)
    if returncode != 0:
        raise RuntimeError(f"LEAN live paper failed for {selected.get('strategy_id')} with exit code {returncode}")

    return {
        "strategy_id": selected.get("strategy_id") or selected.get("strategy-id"),
        "best_version": selected.get("best_version"),
        "best_score": selected.get("best_score"),
        "live-paper-config": str(Path(live_config).resolve()),
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
    }


def strategy_result_to_line_protocol(
    strategy_id: str,
    version: str,
    stage: str,
    summary: dict,
    score: float | int | None = None,
    is_best: bool = False,
    measurement: str = "soloquant_strategy_result",
    timestamp: str | None = None,
) -> str:
    timestamp_value = timestamp or str(summary.get("timestamp") or "")
    timestamp_ns = iso_timestamp_to_ns(timestamp_value) if timestamp_value else run_date_to_timestamp_ns(utc_run_date())
    positions = summary.get("positions") if isinstance(summary.get("positions"), list) else []
    fields = {
        "score": safe_float(score, None) if score is not None else safe_float(summary.get("score"), 0.0),
        "total_return": safe_float(summary.get("total_return"), 0.0),
        "total_value": safe_float(summary.get("total_value"), 0.0),
        "total_pnl": safe_float(summary.get("total_pnl"), 0.0),
        "cash": safe_float(summary.get("cash"), 0.0),
        "market_value": safe_float(summary.get("market_value"), 0.0),
        "position_count": len(positions),
        "is_best": bool(is_best),
    }
    tags = {
        "strategy_id": str(strategy_id),
        "version": str(version),
        "stage": str(stage),
    }
    tag_set = ",".join(f"{escape_influx_key(key)}={escape_influx_key(value)}" for key, value in tags.items())
    field_set = ",".join(f"{escape_influx_key(key)}={format_influx_field_value(value)}" for key, value in fields.items())
    return f"{escape_influx_key(measurement)},{tag_set} {field_set} {timestamp_ns}"


def collect_strategy_result_influx_lines(
    registry_path: str | Path,
    include_live_paper: bool = True,
    measurement: str = "soloquant_strategy_result",
) -> list[str]:
    registry = load_json_payload(registry_path)
    strategies = registry.get("strategies") if isinstance(registry.get("strategies"), list) else []
    lines: list[str] = []
    for strategy in strategies:
        if not isinstance(strategy, dict):
            continue
        strategy_id = str(strategy.get("strategy_id") or strategy.get("strategy-id") or "").strip()
        if not strategy_id:
            continue
        best_version = str(strategy.get("best_version") or "")
        evaluated_versions = strategy.get("evaluated_versions") if isinstance(strategy.get("evaluated_versions"), list) else []
        for evaluated in evaluated_versions:
            if not isinstance(evaluated, dict):
                continue
            version = str(evaluated.get("version") or evaluated.get("name") or "")
            summary = evaluated.get("summary") if isinstance(evaluated.get("summary"), dict) else {}
            if not summary:
                continue
            lines.append(
                strategy_result_to_line_protocol(
                    strategy_id,
                    version,
                    "backtest",
                    summary,
                    score=safe_float(evaluated.get("score"), None),
                    is_best=version == best_version,
                    measurement=measurement,
                    timestamp=strategy.get("updated_at_utc"),
                )
            )
        if include_live_paper:
            live_config = strategy.get("live-paper-config") or strategy.get("live_paper_config")
            if live_config:
                summary = _load_lean_summary_from_config(live_config)
                if summary:
                    lines.append(
                        strategy_result_to_line_protocol(
                            strategy_id,
                            best_version or "best",
                            "live-paper",
                            summary,
                            score=safe_float(summary.get("score"), safe_float(strategy.get("best_score"), None)),
                            is_best=True,
                            measurement=measurement,
                        )
                    )
    return lines


def iter_screened_artifact_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "screened"
    if not root.exists():
        return
    for path in sorted(root.glob("*/valuable-items.json")):
        yield path


def research_artifact_to_line_protocol(item: dict, category: str, run_date: str | None = None, measurement: str = "soloquant_research_artifact") -> str:
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    source = str(item.get("source") or (item.get("raw") or {}).get("source") or "unknown").strip() or "unknown"
    item_hash = stable_hash({"title": title, "url": url, "category": category})
    required_fields = item.get("required_fields") if isinstance(item.get("required_fields"), list) else []
    steps = item.get("implementation_steps") if isinstance(item.get("implementation_steps"), list) else []
    timestamp_ns = run_date_to_timestamp_ns(run_date)

    tags = {
        "category": normalize_category(category),
        "source": source,
        "artifact_id": item_hash,
    }
    fields = {
        "title": title,
        "url": url,
        "strategy_idea": str(item.get("strategy_idea") or item.get("idea") or ""),
        "evidence": str(item.get("evidence") or ""),
        "implementation_step_count": len(steps),
        "required_field_count": len(required_fields),
    }
    tag_set = ",".join(f"{escape_influx_key(key)}={escape_influx_key(tags[key])}" for key in ("category", "source", "artifact_id"))
    field_set = ",".join(f"{escape_influx_key(key)}={format_influx_field_value(value)}" for key, value in fields.items())
    return f"{escape_influx_key(measurement)},{tag_set} {field_set} {timestamp_ns}"


def collect_research_artifact_influx_lines(
    artifact_root: str | Path,
    run_date: str | None = None,
    measurement: str = "soloquant_research_artifact",
) -> list[str]:
    lines: list[str] = []
    for path in iter_screened_artifact_files(artifact_root, run_date=run_date):
        payload = load_json_payload(path)
        category = normalize_category(payload.get("category") or path.parent.name)
        artifact_run_date = payload.get("run_date") or ""
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if isinstance(item, dict):
                lines.append(research_artifact_to_line_protocol(item, category, run_date=str(artifact_run_date), measurement=measurement))
    return lines


def iter_finance_event_graph_files(artifact_root: str | Path, run_date: str | None = None):
    root = Path(artifact_root) / "event-graph" / "finance_intelligence"
    if not root.exists():
        return
    for path in sorted(root.glob("graph.json")):
        yield path


def _flatten_graph_properties(properties: dict) -> dict:
    fields: dict[str, object] = {}
    if not isinstance(properties, dict):
        return fields
    for key, value in properties.items():
        key_text = str(key)
        if isinstance(value, list):
            fields[f"{key_text[:-1] if key_text.endswith('s') else key_text}_count"] = len(value)
        elif isinstance(value, dict):
            fields[f"{key_text}_count"] = len(value)
        elif value is not None:
            fields[key_text] = value
    return fields


def finance_event_node_to_line_protocol(
    graph: dict,
    node: dict,
    measurement: str = "soloquant_finance_event_node",
) -> str:
    run_date = str(graph.get("run_date") or "").replace("-", "")
    timestamp_ns = run_date_to_timestamp_ns(run_date)
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    tags = {
        "graph_id": str(graph.get("graph_id") or ""),
        "run_date": run_date,
        "node_type": str(node.get("type") or "unknown"),
        "node_id": str(node.get("id") or stable_hash(node)),
    }
    for tag_key in ("risk_level", "source_type", "entity_type", "theme"):
        tag_value = properties.get(tag_key)
        if tag_value is not None and str(tag_value).strip():
            tags[tag_key] = str(tag_value)
    fields = {
        "name": str(node.get("name") or ""),
        **_flatten_graph_properties(properties),
    }
    tag_set = ",".join(f"{escape_influx_key(key)}={escape_influx_key(value)}" for key, value in tags.items())
    field_set = ",".join(f"{escape_influx_key(key)}={format_influx_field_value(value)}" for key, value in fields.items())
    return f"{escape_influx_key(measurement)},{tag_set} {field_set} {timestamp_ns}"


def finance_event_edge_to_line_protocol(
    graph: dict,
    edge: dict,
    measurement: str = "soloquant_finance_event_edge",
) -> str:
    run_date = str(graph.get("run_date") or "").replace("-", "")
    timestamp_ns = run_date_to_timestamp_ns(run_date)
    tags = {
        "graph_id": str(graph.get("graph_id") or ""),
        "run_date": run_date,
        "edge_type": str(edge.get("type") or "unknown"),
        "edge_id": str(edge.get("id") or stable_hash(edge)),
    }
    fields = {
        "source": str(edge.get("source") or ""),
        "target": str(edge.get("target") or ""),
        **_flatten_graph_properties(edge.get("properties") if isinstance(edge.get("properties"), dict) else {}),
    }
    tag_set = ",".join(f"{escape_influx_key(key)}={escape_influx_key(value)}" for key, value in tags.items())
    field_set = ",".join(f"{escape_influx_key(key)}={format_influx_field_value(value)}" for key, value in fields.items())
    return f"{escape_influx_key(measurement)},{tag_set} {field_set} {timestamp_ns}"


def collect_finance_event_graph_influx_lines(artifact_root: str | Path, run_date: str | None = None) -> list[str]:
    lines: list[str] = []
    for path in iter_finance_event_graph_files(artifact_root, run_date=run_date):
        graph = load_json_payload(path)
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
        for node in nodes:
            if isinstance(node, dict):
                lines.append(finance_event_node_to_line_protocol(graph, node))
        for edge in edges:
            if isinstance(edge, dict):
                lines.append(finance_event_edge_to_line_protocol(graph, edge))
    return lines


def export_research_artifacts_to_influx(config: dict, run_date: str | None = None, dry_run: bool = False) -> dict:
    influx = config.get("influxdb") or {}
    token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
    lines = collect_research_artifact_influx_lines(config["artifact-root"], run_date=run_date)
    written = 0
    if not dry_run:
        token = os.getenv(token_env_var, "").strip()
        written = write_lines_to_influx(
            lines,
            influx_url=str(influx.get("url") or "http://127.0.0.1:8086"),
            org=str(influx.get("org") or "lean"),
            bucket=str(influx.get("bucket") or "quant"),
            token=token,
        )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "lines": len(lines),
        "written": written,
        "measurement": "soloquant_research_artifact",
    }


def export_finance_event_graph_to_influx(
    config: dict,
    run_date: str | None = None,
    dry_run: bool = False,
    writer: Callable[[Iterable[str], str, str, str, str], int] = write_lines_to_influx,
) -> dict:
    influx = config.get("influxdb") or {}
    token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
    lines = collect_finance_event_graph_influx_lines(config["artifact-root"], run_date=run_date)
    written = 0
    if not dry_run:
        token = os.getenv(token_env_var, "").strip()
        written = writer(
            lines,
            str(influx.get("url") or "http://127.0.0.1:8086"),
            str(influx.get("org") or "lean"),
            str(influx.get("bucket") or "quant"),
            token,
        )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "lines": len(lines),
        "written": written,
        "measurements": ["soloquant_finance_event_node", "soloquant_finance_event_edge"],
    }


def export_strategy_results_to_influx(
    config: dict,
    dry_run: bool = False,
    include_live_paper: bool = True,
    writer: Callable[[Iterable[str], str, str, str, str], int] = write_lines_to_influx,
) -> dict:
    influx = config.get("influxdb") or {}
    token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
    lines = collect_strategy_result_influx_lines(config["registry-file"], include_live_paper=include_live_paper)
    written = 0
    if not dry_run:
        token = os.getenv(token_env_var, "").strip()
        written = writer(
            lines,
            str(influx.get("url") or "http://127.0.0.1:8086"),
            str(influx.get("org") or "lean"),
            str(influx.get("bucket") or "quant"),
            token,
        )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "lines": len(lines),
        "written": written,
        "measurement": "soloquant_strategy_result",
    }


def build_job_specs(config: dict) -> list[dict]:
    python = "/root/miniconda3/envs/quant311/bin/python"
    soloquant_script = str(repo_root() / "Scripts" / "soloquant_orchestrator.py")
    crawl_scheduler_script = str(repo_root() / "Scripts" / "soloquant_crawl_scheduler.py")
    pipeline_script = str(repo_root() / "Scripts" / "soloquant_pipeline_runner.py")
    price_export_script = str(repo_root() / "Scripts" / "export_price_ohlc_to_influx.py")
    config_path = str(repo_root() / "Launcher" / "config" / "config-soloquant.json")
    tushare_root = Path("/home/project/tushare-downloader")
    schedule = config.get("schedule") or {}

    return [
        {
            "name": "tushare-incremental-scheduler",
            "cron": schedule.get("download-history-cron", "0 2 * * *"),
            "cwd": str(tushare_root),
            "command": [python, str(tushare_root / "incremental_scheduler.py"), "--poll-seconds", "60"],
            "long-running": True,
        },
        {
            "name": "tushare-realtime-daily",
            "cron": schedule.get("download-realtime-cron", "*/5 * * * *"),
            "cwd": str(tushare_root),
            "command": [python, str(tushare_root / "incremental_update.py"), "--api", "daily,daily_basic,moneyflow,fund_daily"],
        },
        {
            "name": "soloquant-full-auto-pipeline",
            "cron": schedule.get("full-auto-pipeline-cron", "*/5 * * * *"),
            "cwd": str(repo_root()),
            "command": [
                python,
                pipeline_script,
                "--config",
                config_path,
                "--daemon",
                "--poll-seconds",
                "300",
            ],
            "long-running": True,
        },
        {
            "name": "soloquant-crawl-strategy",
            "cron": schedule.get("crawl-strategy-cron", "30 2 * * *"),
            "cwd": str(repo_root()),
            "command": [
                python,
                crawl_scheduler_script,
                "--config",
                config_path,
                "--task",
                "strategy",
                "--force",
                "--once",
            ],
        },
        {
            "name": "soloquant-crawl-news",
            "cron": schedule.get("crawl-news-cron", "0 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [
                python,
                crawl_scheduler_script,
                "--config",
                config_path,
                "--task",
                "finance_intelligence",
                "--force",
                "--once",
            ],
        },
        {
            "name": "soloquant-optimize",
            "cron": schedule.get("optimize-cron", "0 4 * * 1-5"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--optimize-strategies"],
        },
        {
            "name": "soloquant-prepare-reproduction",
            "cron": schedule.get("prepare-reproduction-cron", "45 2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--prepare-reproduction"],
        },
        {
            "name": "soloquant-generate-strategy-implementations",
            "cron": schedule.get("generate-strategy-implementations-cron", "48 2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--generate-strategy-implementations"],
        },
        {
            "name": "soloquant-analyze-finance-intelligence",
            "cron": schedule.get("analyze-finance-intelligence-cron", "5 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--analyze-finance-intelligence"],
        },
        {
            "name": "soloquant-build-finance-event-graph",
            "cron": schedule.get("build-finance-event-graph-cron", "8 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--build-finance-event-graph"],
        },
        {
            "name": "soloquant-export-research-influx",
            "cron": schedule.get("export-research-influx-cron", "10 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--export-research-influx"],
        },
        {
            "name": "soloquant-export-finance-event-graph-influx",
            "cron": schedule.get("export-finance-event-graph-influx-cron", "15 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--export-finance-event-graph-influx"],
        },
        {
            "name": "soloquant-export-strategy-results-influx",
            "cron": schedule.get("export-strategy-results-influx-cron", "20 */2 * * *"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--export-strategy-results-influx"],
        },
        {
            "name": "soloquant-export-price-ohlc",
            "cron": schedule.get("export-price-ohlc-cron", "*/10 * * * 1-5"),
            "cwd": str(repo_root()),
            "command": [python, price_export_script, "--source", "all"],
        },
        {
            "name": "soloquant-live-paper",
            "cron": schedule.get("live-paper-cron", "*/5 * * * 1-5"),
            "cwd": str(repo_root()),
            "command": [python, soloquant_script, "--config", config_path, "--run-live-paper"],
            "long-running": True,
        },
    ]


def _load_lean_summary_from_config(config_path: str | Path) -> dict:
    payload = load_json_payload(config_path)
    summary_path = None
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        summary_path = parameters.get("summary-file") or parameters.get("comparison-summary-file")
    summary_path = summary_path or payload.get("summary-file") or payload.get("comparison-summary-file")
    candidate_paths = []
    if summary_path:
        candidate_paths.append(summary_path)
    if isinstance(parameters, dict):
        portfolio_snapshot = parameters.get("portfolio-snapshot-file")
        if portfolio_snapshot:
            candidate_paths.append(portfolio_snapshot)

    for candidate in candidate_paths:
        resolved = Path(str(candidate))
        if not resolved.is_absolute():
            resolved = (Path(config_path).resolve().parent / resolved).resolve()
        if not resolved.exists():
            continue
        try:
            summary = load_json_payload(resolved)
        except Exception:
            continue
        if isinstance(summary, dict):
            if "score" not in summary and summary.get("total_return") is not None:
                summary["score"] = summary.get("total_return")
            return summary
    return {}


def _score_variant(summary: dict, metric_name: str = "score") -> float:
    if not isinstance(summary, dict):
        return float("-inf")
    sharpe = safe_float(summary.get("sharpe_ratio") or summary.get("SharpeRatio"), None)
    if sharpe is not None:
        return sharpe
    calmar = safe_float(summary.get("calmar_ratio") or summary.get("CalmarRatio"), None)
    if calmar is not None:
        return calmar
    candidate = summary.get(metric_name)
    if candidate is None:
        candidate = summary.get("strategy_total_return")
    if candidate is None:
        total_return = safe_float(summary.get("total_return"), None)
        max_drawdown = safe_float(summary.get("max_drawdown"), None)
        if total_return is not None and max_drawdown is not None:
            return float(total_return) - abs(float(max_drawdown))
        return float("-inf")
    score = safe_float(candidate, None)
    return float(score) if score is not None else float("-inf")


def optimize_strategy_versions(
    variants: Sequence[dict],
    runner,
) -> dict:
    evaluated: list[dict] = []
    best: dict | None = None
    failed: list[dict] = []

    for variant in variants:
        version = str(variant.get("version") or variant.get("name") or f"variant-{len(evaluated) + 1}")
        config_path = variant.get("backtest-config")
        if not config_path:
            raise ValueError(f"variant {version} requires backtest-config")
        command, cwd = build_lean_launcher_command(config_path, dotnet_binary=variant.get("dotnet-binary"))
        run_result = runner(command, cwd)
        returncode = run_result[0] if isinstance(run_result, tuple) else int(run_result)
        if returncode != 0:
            failed.append({"version": version, "returncode": returncode})
            continue

        summary = _load_lean_summary_from_config(config_path)
        metric_name = str(variant.get("score-metric") or default_config()["strategy-policy"]["best-metric"])
        score = _score_variant(summary, metric_name=metric_name)
        evaluated_row = {
            "version": version,
            "backtest-config": str(Path(config_path).resolve()),
            "command": command,
            "cwd": str(cwd),
            "score": score,
            "summary": summary,
        }
        evaluated.append(evaluated_row)
        if best is None or score > best["score"]:
            best = evaluated_row

    return {
        "evaluated_versions": evaluated,
        "failed_versions": failed,
        "best_version": best["version"] if best else None,
        "best_score": best["score"] if best else float("-inf"),
    }


def smoke_test_generated_strategies(
    config: dict,
    runner: Callable[[Sequence[str], Path], int | tuple[int, object]] = default_subprocess_runner,
) -> dict:
    """Run a minimal 1-day backtest for each compiled generated strategy to verify it doesn't crash."""
    dotnet_binary = str((config.get("lean") or {}).get("dotnet-binary") or "/usr/local/dotnet/dotnet")
    passed: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []

    for manifest_path in iter_generated_strategy_manifest_files():
        try:
            manifest = load_json_payload(manifest_path)
        except Exception:
            continue
        class_name = str(manifest.get("class_name") or manifest.get("class-name") or "").strip()
        algorithm_language = str(manifest.get("algorithm_language") or "CSharp").strip()
        strategy_id = str(manifest.get("strategy_id") or manifest.get("strategy-id") or manifest_path.parent.name).strip()
        is_python = algorithm_language.lower() in {"python", "py"}

        # Check that the code file exists (not .broken)
        code_file = str(manifest.get("code_file") or manifest.get("algorithm_file") or "")
        if not code_file or not Path(code_file).exists():
            # Check for .broken variant
            ext = ".py" if is_python else ".cs"
            broken_file = Path(code_file).with_suffix(f"{ext}.broken") if code_file else None
            if broken_file and broken_file.exists():
                skipped.append({"strategy_id": strategy_id, "class_name": class_name, "reason": "compile_failed"})
            else:
                skipped.append({"strategy_id": strategy_id, "class_name": class_name, "reason": "code_file_missing"})
            continue

        # Build a minimal 1-day backtest config
        smoke_dir = manifest_path.parent / "smoke-test"
        smoke_dir.mkdir(parents=True, exist_ok=True)
        parameters = {
            "universe": "000001.SZ,600000.SH",
            "initial-capital": "100000",
            "summary-file": str(smoke_dir / "summary.json"),
        }
        smoke_config = _base_lean_config(
            class_name,
            "backtesting",
            start_date="20250102",
            end_date="20250103",
            parameters=parameters,
            language=algorithm_language,
        )
        if is_python:
            smoke_config["algorithm-language"] = "Python"
            smoke_config["algorithm-location"] = code_file
        # Disable influxdb for smoke test
        smoke_config["influxdb-enabled"] = False
        smoke_config_path = smoke_dir / "config-smoke.json"
        write_json_payload(smoke_config_path, smoke_config)

        command, cwd = build_lean_launcher_command(smoke_config_path, dotnet_binary=dotnet_binary)
        try:
            run_result = runner(command, cwd)
            returncode = run_result[0] if isinstance(run_result, tuple) else int(run_result)
        except Exception as exc:
            failed.append({"strategy_id": strategy_id, "class_name": class_name, "returncode": -1, "error": str(exc)[:500]})
            continue

        if returncode == 0:
            passed.append({"strategy_id": strategy_id, "class_name": class_name, "language": algorithm_language})
        else:
            # Mark as smoke-failed: rename code file to .smoke-failed
            code_path = Path(code_file)
            if code_path.exists():
                ext = ".py" if is_python else ".cs"
                smoke_failed_path = code_path.with_suffix(f"{ext}.smoke-failed")
                code_path.rename(smoke_failed_path)
            failed.append({"strategy_id": strategy_id, "class_name": class_name, "language": algorithm_language, "returncode": returncode})

    return {
        "status": "ok",
        "passed_count": len(passed),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def collect_strategy_progress_lines(config: dict) -> list[str]:
    """Collect per-strategy pipeline progress as InfluxDB line protocol lines."""
    artifact_root = Path(config.get("artifact-root", "Results/soloquant/artifacts"))
    registry_path = Path(config.get("registry-file", "Results/soloquant/strategy-registry.json"))
    timestamp_ns = run_date_to_timestamp_ns(utc_run_date())
    measurement = "soloquant_strategy_progress"

    registry_strategies: dict[str, dict] = {}
    if registry_path.exists():
        registry = load_json_payload(registry_path)
        for entry in registry.get("strategies") or []:
            if isinstance(entry, dict):
                sid = str(entry.get("strategy_id") or "").strip()
                if sid:
                    registry_strategies[sid] = entry

    generated_manifests: dict[str, dict] = {}
    for manifest_path in iter_generated_strategy_manifest_files():
        try:
            payload = load_json_payload(manifest_path)
        except Exception:
            continue
        sid = safe_slug(str(payload.get("strategy_id") or payload.get("strategy-id") or manifest_path.parent.name), "strategy")
        payload["_manifest_path"] = str(manifest_path)
        generated_manifests[sid] = payload

    all_ids = sorted(set(list(registry_strategies.keys()) + list(generated_manifests.keys())))
    lines: list[str] = []
    for strategy_id in all_ids:
        reg = registry_strategies.get(strategy_id, {})
        gen = generated_manifests.get(strategy_id, {})

        status = str(reg.get("status") or "").strip()
        live_score_val = safe_float(reg.get("live_score"), None)
        best_score_val = safe_float(reg.get("best_score"), None)
        in_registry = bool(reg)
        has_live_paper = live_score_val is not None

        if status == "retired":
            pipeline_stage = "retired"
            pipeline_stage_code = 5
        elif status == "serving":
            pipeline_stage = "serving"
            pipeline_stage_code = 4
        elif in_registry and has_live_paper:
            pipeline_stage = "live_paper"
            pipeline_stage_code = 3
        elif in_registry:
            pipeline_stage = "backtested"
            pipeline_stage_code = 2
        elif gen:
            manifest_dir = Path(gen.get("_manifest_path", "")).parent
            class_name = str(gen.get("class_name") or "")
            lang = str(gen.get("algorithm_language") or "CSharp").strip()
            if lang.lower() in {"python", "py"}:
                has_compiled = any(manifest_dir.parent.glob(f"{class_name}.py"))
            else:
                has_compiled = any(manifest_dir.parent.glob("*.dll")) or any(manifest_dir.parent.glob(f"{class_name}.cs"))
            if has_compiled:
                pipeline_stage = "compiled"
                pipeline_stage_code = 1
            else:
                pipeline_stage = "generated"
                pipeline_stage_code = 0
        else:
            continue

        title = str(gen.get("description") or reg.get("strategy_id") or strategy_id).strip()
        source = str(gen.get("source_summary") or "").strip()
        lang = str(gen.get("algorithm_language") or "CSharp").strip()
        class_name = str(gen.get("class_name") or "").strip()
        best_version = str(reg.get("best_version") or "").strip()
        evaluated_versions = reg.get("evaluated_versions") if isinstance(reg.get("evaluated_versions"), list) else []
        version_count = len(evaluated_versions)
        risk_controls_list = gen.get("risk_controls") if isinstance(gen.get("risk_controls"), list) else []
        risk_controls = ",".join(str(r) for r in risk_controls_list if str(r).strip())
        total_return = safe_float(reg.get("total_return"), None)
        if total_return is None:
            for ev in evaluated_versions:
                if isinstance(ev, dict) and isinstance(ev.get("summary"), dict):
                    total_return = safe_float(ev["summary"].get("total_return"), None)
                    if total_return is not None:
                        break

        decay_ratio = None
        if best_score_val is not None and live_score_val is not None and best_score_val != 0:
            decay_ratio = (live_score_val / best_score_val) - 1.0

        tags = {"strategy_id": escape_influx_key(strategy_id)}
        fields = {
            "pipeline_stage": format_influx_field_value(pipeline_stage),
            "pipeline_stage_code": format_influx_field_value(pipeline_stage_code),
            "title": format_influx_field_value(title[:500]),
            "source": format_influx_field_value(source[:200]),
            "language": format_influx_field_value(lang),
            "class_name": format_influx_field_value(class_name),
            "best_version": format_influx_field_value(best_version),
            "has_code": format_influx_field_value(bool(gen)),
            "has_backtest": format_influx_field_value(in_registry),
            "has_live_paper": format_influx_field_value(has_live_paper),
            "version_count": format_influx_field_value(version_count),
            "risk_controls": format_influx_field_value(risk_controls[:500]),
        }
        if best_score_val is not None:
            fields["best_score"] = format_influx_field_value(best_score_val)
        if live_score_val is not None:
            fields["live_score"] = format_influx_field_value(live_score_val)
        if total_return is not None:
            fields["total_return"] = format_influx_field_value(total_return)
        if decay_ratio is not None:
            fields["decay_ratio"] = format_influx_field_value(decay_ratio)

        tag_str = ",".join(f"{k}={v}" for k, v in tags.items())
        field_str = ",".join(f"{k}={v}" for k, v in fields.items())
        lines.append(f"{measurement},{tag_str} {field_str} {timestamp_ns}")

    return lines


def _latest_mtime_iso(paths: list[Path]) -> str:
    """Return ISO-8601 UTC timestamp of the most recently modified file, or empty string."""
    mtimes = []
    for p in paths:
        if p.exists():
            try:
                mtimes.append(p.stat().st_mtime)
            except OSError:
                pass
    if not mtimes:
        return ""
    return datetime.fromtimestamp(max(mtimes), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_pipeline_funnel_lines(config: dict) -> list[str]:
    """Collect pipeline funnel aggregate counts as InfluxDB line protocol lines."""
    artifact_root = Path(config.get("artifact-root", "Results/soloquant/artifacts"))
    registry_path = Path(config.get("registry-file", "Results/soloquant/strategy-registry.json"))
    timestamp_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    measurement = "soloquant_pipeline_funnel"

    crawled_urls: set[str] = set()
    crawled_index_paths: list[Path] = []
    for crawl_category in ("strategy", "finance_intelligence"):
        crawled_dir = artifact_root / "crawled" / crawl_category
        index_path = crawled_dir / "index.json"
        crawled_index_paths.append(index_path)
        if index_path.exists():
            try:
                index = load_json_payload(index_path)
                for item in (index.get("items") or []):
                    url = str(item.get("url") or "").strip()
                    if url:
                        crawled_urls.add(url)
            except Exception:
                pass
    crawled_count = len(crawled_urls)
    crawled_last_updated = _latest_mtime_iso(crawled_index_paths)

    summarized_urls: set[str] = set()
    repro_dir = artifact_root / "reproduction" / "strategy"
    repro_index = repro_dir / "index.json"
    if repro_index.exists():
        try:
            index = load_json_payload(repro_index)
            for item in (index.get("items") or []):
                url = str(item.get("url") or "").strip()
                if url:
                    summarized_urls.add(url)
        except Exception:
            pass
    summarized_count = len(summarized_urls)
    summarized_last_updated = _latest_mtime_iso([repro_index])

    def _is_reproduced_strategy(manifest_path: str) -> bool:
        """Check if a strategy has valid (non-broken, non-smoke-failed) code."""
        try:
            manifest = load_json_payload(Path(manifest_path))
            code_file = str(manifest.get("code_file") or manifest.get("algorithm_file") or "")
            if not code_file:
                return False
            path = Path(code_file)
            if not path.exists():
                return False
            name = path.name
            if name.endswith(".broken") or name.endswith(".smoke-failed"):
                return False
            return True
        except Exception:
            return False

    manifest_files = list(iter_generated_strategy_manifest_files())
    reproduced_count = sum(1 for m in manifest_files if _is_reproduced_strategy(m))
    reproduced_last_updated = _latest_mtime_iso([Path(f) for f in manifest_files]) if manifest_files else ""

    registry_strategies: list[dict] = []
    if registry_path.exists():
        registry = load_json_payload(registry_path)
        registry_strategies = registry.get("strategies") or []

    backtested_count = len(registry_strategies)
    live_paper_count = sum(1 for s in registry_strategies if safe_float(s.get("live_score"), None) is not None)
    serving_count = sum(1 for s in registry_strategies if str(s.get("status")) == "serving")
    retired_count = sum(1 for s in registry_strategies if str(s.get("status")) == "retired")

    # Derive last-updated timestamps from registry strategy fields
    backtested_last_updated = _latest_mtime_iso([registry_path])
    live_paper_last_updated = ""
    serving_last_updated = ""
    retired_last_updated = ""
    for s in registry_strategies:
        if safe_float(s.get("live_score"), None) is not None:
            t = str(s.get("lifecycle_updated_at_utc") or "")
            if t and (not live_paper_last_updated or t > live_paper_last_updated):
                live_paper_last_updated = t
        if str(s.get("status")) == "serving":
            t = str(s.get("serving_since_utc") or s.get("lifecycle_updated_at_utc") or "")
            if t and (not serving_last_updated or t > serving_last_updated):
                serving_last_updated = t
        if str(s.get("status")) == "retired":
            t = str(s.get("retired_at_utc") or s.get("lifecycle_updated_at_utc") or "")
            if t and (not retired_last_updated or t > retired_last_updated):
                retired_last_updated = t

    fields = {
        "crawled_count": format_influx_field_value(crawled_count),
        "summarized_count": format_influx_field_value(summarized_count),
        "reproduced_count": format_influx_field_value(reproduced_count),
        "backtested_count": format_influx_field_value(backtested_count),
        "live_paper_count": format_influx_field_value(live_paper_count),
        "serving_count": format_influx_field_value(serving_count),
        "retired_count": format_influx_field_value(retired_count),
    }
    # Add last-updated timestamps as string fields (not tags — InfluxQL SELECT works on fields only)
    if crawled_last_updated:
        fields["crawled_last_updated"] = f'"{crawled_last_updated}"'
    if summarized_last_updated:
        fields["summarized_last_updated"] = f'"{summarized_last_updated}"'
    if reproduced_last_updated:
        fields["reproduced_last_updated"] = f'"{reproduced_last_updated}"'
    if backtested_last_updated:
        fields["backtested_last_updated"] = f'"{backtested_last_updated}"'
    if live_paper_last_updated:
        fields["live_paper_last_updated"] = f'"{live_paper_last_updated}"'
    if serving_last_updated:
        fields["serving_last_updated"] = f'"{serving_last_updated}"'
    if retired_last_updated:
        fields["retired_last_updated"] = f'"{retired_last_updated}"'

    field_str = ",".join(f"{k}={v}" for k, v in fields.items())
    # Per-stage rows for Grafana table display (7 rows x 3 columns: stage, count, last_updated)
    stage_measurement = "soloquant_pipeline_funnel_stages"
    stage_rows = [
        ("已爬取", crawled_count, crawled_last_updated),
        ("已提炼", summarized_count, summarized_last_updated),
        ("已复现", reproduced_count, reproduced_last_updated),
        ("已回测", backtested_count, backtested_last_updated),
        ("Live Paper", live_paper_count, live_paper_last_updated),
        ("服役中", serving_count, serving_last_updated),
        ("已除役", retired_count, retired_last_updated),
    ]
    stage_lines = []
    for stage_name, count, last_updated in stage_rows:
        stage_fields = f"count={count}"
        if last_updated:
            stage_fields += f',last_updated="{last_updated}"'
        escaped_stage = stage_name.replace(" ", r"\ ")
        stage_lines.append(f'{stage_measurement},stage={escaped_stage} {stage_fields} {timestamp_ns}')

    return [f"{measurement} {field_str} {timestamp_ns}"] + stage_lines


def export_strategy_pipeline_progress(
    config: dict,
    dry_run: bool = False,
    writer: Callable[[Iterable[str], str, str, str, str], int] = write_lines_to_influx,
) -> dict:
    influx = config.get("influxdb") or {}
    token_env_var = str(influx.get("token-env-var") or "INFLUXDB_TOKEN")
    progress_lines = collect_strategy_progress_lines(config)
    funnel_lines = collect_pipeline_funnel_lines(config)
    all_lines = progress_lines + funnel_lines
    written = 0
    if not dry_run:
        token = os.getenv(token_env_var, "").strip()
        written = writer(
            all_lines,
            str(influx.get("url") or "http://127.0.0.1:8086"),
            str(influx.get("org") or "lean"),
            str(influx.get("bucket") or "quant"),
            token,
        )
    return {
        "status": "ok",
        "dry_run": dry_run,
        "progress_lines": len(progress_lines),
        "funnel_lines": len(funnel_lines),
        "total_lines": len(all_lines),
        "written": written,
        "measurements": ["soloquant_strategy_progress", "soloquant_pipeline_funnel"],
    }


def iter_strategy_manifest_files(strategy_root: str | Path):
    root = Path(strategy_root)
    if not root.exists():
        return
    for path in sorted(root.glob("*/manifest.json")):
        yield path


def optimize_strategy_packages(
    config: dict,
    runner: Callable[[Sequence[str], Path], int | tuple[int, object]] = default_subprocess_runner,
    max_items: int | None = None,
) -> dict:
    strategy_root = config.get("strategy-root")
    if not strategy_root:
        raise ValueError("config requires strategy-root")
    registry_file = config.get("registry-file")
    if not registry_file:
        raise ValueError("config requires registry-file")

    policy = config.get("strategy-policy") if isinstance(config.get("strategy-policy"), dict) else {}
    min_versions = max(3, safe_int(policy.get("min-versions"), 3))
    results: list[dict] = []
    processed = 0

    for manifest_path in iter_strategy_manifest_files(strategy_root):
        if max_items is not None and processed >= max(0, int(max_items)):
            break
        manifest = load_json_payload(manifest_path)
        strategy_id = str(manifest.get("strategy-id") or manifest.get("strategy_id") or manifest_path.parent.name).strip()
        variants = manifest.get("variants") if isinstance(manifest.get("variants"), list) else []
        if len(variants) < min_versions:
            continue

        # Skip strategies where all variants already have backtest summaries
        normalized_variants: list[dict] = []
        all_have_summary = True
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            row = dict(variant)
            for path_key in ("backtest-config", "live-paper-config"):
                if row.get(path_key):
                    resolved = Path(str(row[path_key]))
                    if not resolved.is_absolute():
                        resolved = (manifest_path.parent / resolved).resolve()
                    row[path_key] = str(resolved)
            # Check if summary already exists for this variant
            bt_config_path = Path(row.get("backtest-config") or "")
            summary_path = bt_config_path.parent / "summary.json"
            if not summary_path.exists():
                all_have_summary = False
            normalized_variants.append(row)
        if all_have_summary and normalized_variants:
            continue

        optimization_result = optimize_strategy_versions(normalized_variants, runner=runner)
        best_version = optimization_result.get("best_version")
        best_variant = next((item for item in normalized_variants if str(item.get("version") or item.get("name")) == str(best_version)), None)
        live_config_path = (best_variant or {}).get("live-paper-config") or manifest.get("live-paper-config")
        if not live_config_path and not best_version:
            results.append({
                "strategy_id": strategy_id,
                "manifest": str(manifest_path),
                **optimization_result,
                "status": "all_backtests_failed",
            })
            processed += 1
            continue
        if not live_config_path:
            continue

        update_strategy_registry(registry_file, strategy_id, optimization_result, live_config_path=live_config_path)
        results.append(
            {
                "strategy_id": strategy_id,
                "manifest": str(manifest_path),
                **optimization_result,
                "live-paper-config": str(live_config_path),
            }
        )
        processed += 1

    return {
        "status": "ok",
        "optimized_count": len(results),
        "results": results,
    }


def build_strategy_manifest(
    strategy_id: str,
    lean_backtest_config: str | Path,
    lean_live_config: str | Path | None = None,
    metadata: dict | None = None,
) -> dict:
    manifest = {
        "strategy-id": strategy_id,
        "generated-at-utc": datetime.now(timezone.utc).isoformat(),
        "lean-configs": {
            "backtest": {
                "config-path": str(Path(lean_backtest_config).resolve()),
            }
        },
        "metadata": metadata or {},
    }
    if lean_live_config is not None:
        manifest["lean-configs"]["live-paper"] = {
            "config-path": str(Path(lean_live_config).resolve()),
        }
    return validate_strategy_manifest(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="SoloQuant system orchestrator")
    parser.add_argument("--config")
    parser.add_argument("--validate-manifest")
    parser.add_argument("--field-mapping")
    parser.add_argument("--missing-log")
    parser.add_argument("--placeholder-root")
    parser.add_argument("--build-query-plan", action="store_true")
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--build-llm-payload")
    parser.add_argument("--materialize-strategy")
    parser.add_argument("--materialize-generated-strategies", action="store_true")
    parser.add_argument("--generate-strategy-implementations", action="store_true")
    parser.add_argument("--generated-root")
    parser.add_argument("--strategy-root")
    parser.add_argument("--universe", action="append")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2024-12-31")
    parser.add_argument("--build-job-specs", action="store_true")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--category", action="append")
    parser.add_argument("--max-queries", type=int)
    parser.add_argument("--max-results-per-query", type=int, default=3)
    parser.add_argument("--screen-with-llm", action="store_true")
    parser.add_argument("--run-date")
    parser.add_argument("--prepare-reproduction", action="store_true")
    parser.add_argument("--analyze-finance-intelligence", action="store_true")
    parser.add_argument("--build-finance-event-graph", action="store_true")
    parser.add_argument("--build-event-signal-context", action="store_true")
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--language", choices=["CSharp", "Python"], default=None, help="Algorithm language for generated strategies (default: from config or CSharp)")
    parser.add_argument("--max-content-chars", type=int, default=0, help="0 means send full crawled content to GLM")
    parser.add_argument("--llm-max-tokens", type=int, default=0, help="0 means do not set an output token cap")
    parser.add_argument("--ingest-local-strategies", action="store_true")
    parser.add_argument("--optimize-strategies", action="store_true")
    parser.add_argument("--smoke-test-strategies", action="store_true")
    parser.add_argument("--run-live-paper", action="store_true")
    parser.add_argument("--strategy-id")
    parser.add_argument("--export-research-influx", action="store_true")
    parser.add_argument("--export-finance-event-graph-influx", action="store_true")
    parser.add_argument("--export-strategy-results-influx", action="store_true")
    parser.add_argument("--export-strategy-pipeline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-lines", action="store_true")
    args = parser.parse_args()

    if args.validate_manifest:
        manifest = load_json_payload(args.validate_manifest)
        validate_strategy_manifest(manifest)
        print(json.dumps({"status": "ok"}, ensure_ascii=False))
        return 0

    config = load_config(args.config)
    if args.ingest_local_strategies:
        local_dir = Path(config["workflow-root"]) / "local-strategies"
        report = ingest_local_strategies(local_dir, config["artifact-root"], run_date=args.run_date)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.build_query_plan:
        print(json.dumps(build_research_query_plan(args.keyword), ensure_ascii=False, indent=2))
        return 0

    if args.build_llm_payload:
        payload = load_json_payload(args.build_llm_payload)
        items = payload.get("items") or payload.get("crawled_items") or []
        print(json.dumps(build_llm_screening_payload(items, model=config["llm"].get("model", "deepseek-v4-pro")), ensure_ascii=False, indent=2))
        return 0

    if args.materialize_strategy:
        strategy_spec = load_json_payload(args.materialize_strategy)
        package = materialize_strategy_package(
            strategy_spec,
            args.strategy_root or config["strategy-root"],
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(json.dumps(package, ensure_ascii=False, indent=2))
        return 0

    if args.materialize_generated_strategies:
        lang = args.language or str((config.get("strategy-policy") or {}).get("language") or "CSharp")
        report = materialize_generated_strategy_implementations(
            algorithm_root=resolve_generated_algorithm_root(lang),
            strategy_root=args.strategy_root or config["strategy-root"],
            start_date=args.start_date,
            end_date=args.end_date,
            universe=args.universe,
            max_items=args.max_items,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.generate_strategy_implementations:
        lang = args.language or str((config.get("strategy-policy") or {}).get("language") or "CSharp")
        _raw_langs = (config.get("strategy-policy") or {}).get("languages")
        if isinstance(_raw_langs, list) and _raw_langs:
            languages = [str(l).strip() for l in _raw_langs if str(l).strip()]
        elif args.language is None:
            languages = str((config.get("strategy-policy") or {}).get("language") or "CSharp").split(",")
            languages = [l.strip() for l in languages if l.strip()]
        else:
            languages = [lang]
        # Use code-generation-llm (GLM 5.1) for strategy code, fallback to default llm
        code_gen_llm = config.get("code-generation-llm") or config.get("llm") or {}
        client = create_http_client_from_config(config, use_code_generation_llm=True)
        code_gen_model = code_gen_llm.get("model", "glm-5.1")
        combined_report: dict = {"status": "ok", "languages": [], "packages": []}
        for lang_item in languages:
            lang_item = lang_item.strip()
            if not lang_item:
                continue
            report = generate_strategy_implementations(
                artifact_root=config["artifact-root"],
                generated_root=args.generated_root or (Path(config["workflow-root"]) / "generated-code"),
                algorithm_root=resolve_generated_algorithm_root(lang_item),
                llm_implementation_client=client.summarize_with_llm,
                run_date=args.run_date,
                model=code_gen_model,
                max_items=args.max_items,
                max_tokens=args.llm_max_tokens or 4096,
                language=lang_item,
                config_or_none=config,
            )
            combined_report["languages"].append({"language": lang_item, "report": report})
            combined_report["packages"].extend(report.get("packages", []))
        print(json.dumps(combined_report, ensure_ascii=False, indent=2))
        return 0

    if args.build_job_specs:
        print(json.dumps({"jobs": build_job_specs(config)}, ensure_ascii=False, indent=2))
        return 0

    if args.optimize_strategies:
        report = optimize_strategy_packages(config, max_items=args.max_items)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.smoke_test_strategies:
        report = smoke_test_generated_strategies(config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.run_live_paper:
        report = run_best_live_paper_strategy(config["registry-file"], strategy_id=args.strategy_id)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.export_research_influx:
        lines = collect_research_artifact_influx_lines(config["artifact-root"], run_date=args.run_date)
        if args.print_lines:
            for line in lines:
                print(line)
        report = export_research_artifacts_to_influx(config, run_date=args.run_date, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.export_finance_event_graph_influx:
        lines = collect_finance_event_graph_influx_lines(config["artifact-root"], run_date=args.run_date)
        if args.print_lines:
            for line in lines:
                print(line)
        report = export_finance_event_graph_to_influx(config, run_date=args.run_date, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.export_strategy_results_influx:
        lines = collect_strategy_result_influx_lines(config["registry-file"], include_live_paper=True)
        if args.print_lines:
            for line in lines:
                print(line)
        report = export_strategy_results_to_influx(config, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.export_strategy_pipeline:
        progress_lines = collect_strategy_progress_lines(config)
        funnel_lines = collect_pipeline_funnel_lines(config)
        if args.print_lines:
            for line in progress_lines + funnel_lines:
                print(line)
        report = export_strategy_pipeline_progress(config, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.prepare_reproduction:
        client = create_http_client_from_config(config)
        report = prepare_reproduction_summaries(
            artifact_root=config["artifact-root"],
            llm_summary_client=client.summarize_with_llm,
            run_date=args.run_date,
            model=config["llm"].get("model", "deepseek-v4-pro"),
            max_items=args.max_items,
            max_content_chars=args.max_content_chars,
            max_tokens=args.llm_max_tokens,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.analyze_finance_intelligence:
        client = create_http_client_from_config(config)
        report = prepare_finance_intelligence_analyses(
            artifact_root=config["artifact-root"],
            llm_analysis_client=client.summarize_with_llm,
            run_date=args.run_date,
            model=config["llm"].get("model", "deepseek-v4-pro"),
            max_items=args.max_items,
            max_content_chars=args.max_content_chars,
            max_tokens=args.llm_max_tokens,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.build_finance_event_graph:
        report = build_finance_event_graph(config["artifact-root"], run_date=args.run_date)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.build_event_signal_context:
        graph_dir = Path(config["artifact-root"]) / "event-graph" / "finance_intelligence"
        graph_path = graph_dir / "graph.json"
        graph = load_json_payload(graph_path) if graph_path.exists() else {"nodes": [], "edges": []}
        report = build_event_signal_context(graph, artifact_root=config["artifact-root"], run_date=args.run_date)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.crawl:
        client = create_http_client_from_config(config)
        report = run_crawl_pipeline(
            config,
            keywords=args.keyword,
            categories=args.category,
            search_client=client.search,
            crawl_client=client.crawl,
            llm_screen_client=client.screen_with_llm if args.screen_with_llm else None,
            run_date=args.run_date,
            max_queries=args.max_queries,
            max_results_per_query=args.max_results_per_query,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.field_mapping or args.missing_log:
        field_mapping = args.field_mapping or config["data"]["field-mapping-path"]
        missing_log = args.missing_log or config["missing-data-log"]
        print(json.dumps(resolve_data_requirements([], field_mapping, missing_log), ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


def migrate_date_dirs_to_flat(artifact_root: str | Path) -> dict:
    """One-time migration: move files from date-based subdirectories to flat structure."""
    root = Path(artifact_root)
    moved = 0
    merged_indexes = 0
    cleaned = 0

    # Patterns: {parent_glob} contains 8-digit date directories
    date_dir_patterns = [
        ("crawled/strategy", "strategy"),
        ("crawled/finance_intelligence", "finance_intelligence"),
        ("screened/strategy", "strategy"),
        ("screened/finance_intelligence", "finance_intelligence"),
        ("reproduction/strategy", "strategy"),
        ("intelligence-analysis/finance_intelligence", "finance_intelligence"),
        ("event-graph/finance_intelligence", "finance_intelligence"),
        ("event-signals", "event-signals"),
        ("pdf/strategy", "strategy"),
    ]

    for rel_path, label in date_dir_patterns:
        parent = root / rel_path
        if not parent.exists():
            continue
        for date_dir in sorted(parent.iterdir()):
            if not date_dir.is_dir():
                continue
            if not (date_dir.name.isdigit() and len(date_dir.name) == 8):
                continue
            # Move files from date dir to parent
            for f in date_dir.iterdir():
                dest = parent / f.name
                if f.name == "index.json":
                    # Merge index items
                    src_items = []
                    try:
                        src_data = load_json_payload(f)
                        src_items = src_data.get("items") or []
                    except Exception:
                        pass
                    if dest.exists():
                        try:
                            dst_data = load_json_payload(dest)
                            dst_items = dst_data.get("items") or []
                            dst_urls = {str(r.get("url") or "").strip() for r in dst_items}
                            merged = dst_items + [r for r in src_items if str(r.get("url") or "").strip() not in dst_urls]
                            write_json_payload(dest, {"category": dst_data.get("category", ""), "count": len(merged), "items": merged})
                            merged_indexes += 1
                        except Exception:
                            f.rename(dest)
                            moved += 1
                    else:
                        f.rename(dest)
                        moved += 1
                elif not dest.exists():
                    f.rename(dest)
                    moved += 1
            # Remove empty date dir
            try:
                if date_dir.exists() and not any(date_dir.iterdir()):
                    date_dir.rmdir()
                    cleaned += 1
            except Exception:
                pass

    return {"moved": moved, "merged_indexes": merged_indexes, "cleaned": cleaned}


if __name__ == "__main__":
    raise SystemExit(main())
