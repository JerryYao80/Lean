#!/usr/bin/env python
"""
A股公司经营合作公告增量下载器。

数据源：
  1. 东方财富公告 API（免费、无需认证、T+0）
  2. 巨潮资讯 cninfo API（免费、无需认证、T+0，法定披露平台）

防封禁策略：
  - 严格的请求频率限制（东方财富 15 RPM，巨潮 8 RPM）
  - 请求间隔 ≥ 3 秒
  - 每日请求上限 200 次/源
  - 仅在 16:00-23:00 CST 执行
  - User-Agent 轮换
  - 指数退避重试，403 立即停止
  - 单次最多 5 页（150 条公告）

用法：
  python announcement_downloader.py                    # 自动增量下载
  python announcement_downloader.py --dry-run          # 只打印计划
  python announcement_downloader.py --source eastmoney # 仅东方财富
  python announcement_downloader.py --force            # 忽略时间窗口
"""
import argparse
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config
from rate_limiter import RateLimiter


LOGGER = logging.getLogger("announcement_downloader")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# =============================================================================
# 常量配置
# =============================================================================

ANNOUNCEMENT_DATA_DIR = Path(config.DATA_DIR) / "announcements"
STATE_FILE = Path("download_state/announcement_state.json")

# 东方财富配置
EASTMONEY_BASE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EASTMONEY_RPM = 15
EASTMONEY_PAGE_SIZE = 30
EASTMONEY_MAX_PAGES = 5
EASTMONEY_INTER_REQUEST_DELAY = 3.0
EASTMONEY_DAILY_REQUEST_LIMIT = 200

# 巨潮资讯配置
CNINFO_BASE_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_RPM = 8
CNINFO_PAGE_SIZE = 30
CNINFO_MAX_PAGES_PER_KEYWORD = 2  # 每关键词每交易所最多 2 页
CNINFO_INTER_REQUEST_DELAY = 3.0
CNINFO_DAILY_REQUEST_LIMIT = 200

# 经营合作相关公告类别码（东方财富）
COOPERATION_CATEGORY_CODES = {
    "001002006004": "重大合同",
    "001002006005": "签订协议",
    "001002007003004": "收购出售资产/股权",
    "001002007003005": "股权转让",
    "001002007005": "关联交易",
}

# 巨潮资讯搜索关键词
CNINFO_SEARCH_KEYWORDS = ["合同", "协议", "收购"]

# 巨潮资讯交易所
CNINFO_EXCHANGES = ["sse", "szse"]

# 执行时间窗口（CST）
FETCH_HOUR_START = 16
FETCH_HOUR_END = 23

# 回溯天数（增量补漏）
LOOKBACK_DAYS = 3

# 重试配置
MAX_RETRIES = 3
BASE_RETRY_DELAY = 2.0
MAX_RETRY_DELAY = 30.0

# User-Agent 轮换池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]


def _random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _strip_html(text: str) -> str:
    """移除 HTML 标签（巨潮资讯标题中的 <em> 高亮）。"""
    return re.sub(r"<[^>]+>", "", text)


def _market_code_to_suffix(market_code: str) -> str:
    """东方财富 market_code → 交易所后缀。1=SH, 0=SZ"""
    return ".SH" if market_code == "1" else ".SZ"


def _code_prefix_to_suffix(code: str) -> str:
    """股票代码前缀推断交易所。6xx/9xx=SH, 0xx/3xx=SZ"""
    if code.startswith(("6", "9")):
        return ".SH"
    return ".SZ"


# =============================================================================
# AnnouncementState — 状态跟踪
# =============================================================================


class AnnouncementState:
    """跟踪每个数据源的下载状态和每日请求计数。"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {"sources": {}}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Failed to load state %s: %s", self.file_path, exc)
            return {"sources": {}}

    def get_source_state(self, source: str) -> Dict[str, Any]:
        return self.state.setdefault("sources", {}).setdefault(source, {})

    def update_source_state(self, source: str, **kwargs: Any) -> None:
        src = self.get_source_state(source)
        src.update(kwargs)
        src["updated_at"] = datetime.now(SHANGHAI_TZ).isoformat()

    def increment_daily_count(self, source: str) -> int:
        """递增每日请求计数，跨日自动重置。返回当前计数。"""
        src = self.get_source_state(source)
        today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
        if src.get("daily_request_date") != today:
            src["daily_request_date"] = today
            src["daily_request_count"] = 0
        src["daily_request_count"] = src.get("daily_request_count", 0) + 1
        return src["daily_request_count"]

    def check_daily_limit(self, source: str, limit: int) -> bool:
        """检查是否超过每日请求上限。返回 True 表示已达上限。"""
        src = self.get_source_state(source)
        today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
        if src.get("daily_request_date") != today:
            return False
        return src.get("daily_request_count", 0) >= limit

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# =============================================================================
# EastMoneyClient — 东方财富公告 API
# =============================================================================


class EastMoneyClient:
    """东方财富公告 API 客户端，含保守限流和重试。"""

    def __init__(self, state: AnnouncementState, dry_run: bool = False):
        self.state = state
        self.dry_run = dry_run
        self.rate_limiter = RateLimiter(rpm=EASTMONEY_RPM)
        self.session = requests.Session()
        self.ip_banned = False

    def _wait(self) -> None:
        self.rate_limiter.wait_for_token()
        time.sleep(EASTMONEY_INTER_REQUEST_DELAY)

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": _random_user_agent(),
            "Referer": "https://data.eastmoney.com/",
            "Accept": "application/json",
        }

    def _request_with_retry(self, url: str, params: Dict) -> Optional[requests.Response]:
        """带重试的 HTTP GET，指数退避，403 立即标记封禁。"""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(
                    url, params=params, headers=self._headers(), timeout=15,
                )
                if resp.status_code == 403:
                    LOGGER.error("EastMoney 403 — IP 可能被封禁，停止请求")
                    self.ip_banned = True
                    return None
                if resp.status_code == 429:
                    delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    LOGGER.warning("EastMoney 429 限流，等待 %.0fs", delay)
                    time.sleep(delay)
                    continue
                if resp.status_code >= 500:
                    delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    LOGGER.warning("EastMoney %d 服务错误，等待 %.0fs", resp.status_code, delay)
                    time.sleep(delay)
                    continue
                return resp
            except requests.exceptions.Timeout:
                delay = 1.0  # 超时快速重试
                LOGGER.warning("EastMoney 请求超时，等待 %.0fs", delay)
                time.sleep(delay)
            except requests.exceptions.ConnectionError as exc:
                LOGGER.error("EastMoney 连接错误: %s", exc)
                return None
        return None

    def health_check(self) -> bool:
        """轻量健康检查（1 条数据）。"""
        try:
            resp = self.session.get(
                EASTMONEY_BASE_URL,
                params={"page_size": 1, "page_index": 1, "ann_type": "A", "client_source": "web"},
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("list") is not None
        except Exception:
            pass
        return False

    def fetch_page(self, page_index: int) -> Optional[List[Dict]]:
        """获取一页公告，返回原始公告列表。"""
        if self.ip_banned:
            return None
        if self.state.check_daily_limit("eastmoney", EASTMONEY_DAILY_REQUEST_LIMIT):
            LOGGER.warning("EastMoney 每日请求上限已达 %d", EASTMONEY_DAILY_REQUEST_LIMIT)
            return None

        self._wait()

        if self.dry_run:
            LOGGER.info("[dry-run] Would fetch EastMoney page %d", page_index)
            return []

        params = {
            "page_size": EASTMONEY_PAGE_SIZE,
            "page_index": page_index,
            "ann_type": "A",
            "client_source": "web",
            "f_node": 0,
            "s_node": 0,
        }

        resp = self._request_with_retry(EASTMONEY_BASE_URL, params)
        if resp is None:
            return None

        try:
            data = resp.json()
        except Exception:
            LOGGER.warning("EastMoney 响应解析失败")
            return None

        items = data.get("data", {}).get("list", []) or []
        self.state.increment_daily_count("eastmoney")
        return items

    def fetch_incremental(self) -> List[Dict]:
        """增量获取最近公告，客户端按类别码过滤。"""
        last_ann_id = self.state.get_source_state("eastmoney").get("last_ann_id", "")
        all_items: List[Dict] = []

        for page in range(1, EASTMONEY_MAX_PAGES + 1):
            items = self.fetch_page(page)
            if items is None:
                break
            if not items:
                LOGGER.info("EastMoney page %d 返回空，停止", page)
                break

            # 早停：遇到已处理过的 ann_id
            if last_ann_id:
                found_existing = False
                for item in items:
                    if item.get("art_code") == last_ann_id:
                        found_existing = True
                        break
                if found_existing:
                    LOGGER.info("EastMoney page %d 遇到已处理公告，停止", page)
                    all_items.extend(items)
                    break

            all_items.extend(items)

        # 客户端过滤：仅保留经营合作类别
        filtered = []
        for item in all_items:
            columns = item.get("columns", []) or []
            for col in columns:
                code = col.get("column_code", "")
                if code in COOPERATION_CATEGORY_CODES:
                    filtered.append(item)
                    break

        LOGGER.info(
            "EastMoney: 获取 %d 条原始公告，过滤后 %d 条经营合作公告",
            len(all_items), len(filtered),
        )
        return filtered


# =============================================================================
# CninfoClient — 巨潮资讯公告 API
# =============================================================================


class CninfoClient:
    """巨潮资讯公告 API 客户端，含保守限流和重试。"""

    def __init__(self, state: AnnouncementState, dry_run: bool = False):
        self.state = state
        self.dry_run = dry_run
        self.rate_limiter = RateLimiter(rpm=CNINFO_RPM)
        self.session = requests.Session()
        self.ip_banned = False

    def _wait(self) -> None:
        self.rate_limiter.wait_for_token()
        time.sleep(CNINFO_INTER_REQUEST_DELAY)

    def _headers(self) -> Dict[str, str]:
        return {
            "User-Agent": _random_user_agent(),
            "Referer": "http://www.cninfo.com.cn/",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }

    def _request_with_retry(self, data: bytes) -> Optional[requests.Response]:
        """带重试的 HTTP POST。"""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.post(
                    CNINFO_BASE_URL, data=data, headers=self._headers(), timeout=15,
                )
                if resp.status_code == 403:
                    LOGGER.error("cninfo 403 — IP 可能被封禁，停止请求")
                    self.ip_banned = True
                    return None
                if resp.status_code == 429:
                    delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    LOGGER.warning("cninfo 429 限流，等待 %.0fs", delay)
                    time.sleep(delay)
                    continue
                if resp.status_code >= 500:
                    delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    LOGGER.warning("cninfo %d 服务错误，等待 %.0fs", resp.status_code, delay)
                    time.sleep(delay)
                    continue
                return resp
            except requests.exceptions.Timeout:
                LOGGER.warning("cninfo 请求超时，等待 1s")
                time.sleep(1.0)
            except requests.exceptions.ConnectionError as exc:
                LOGGER.error("cninfo 连接错误: %s", exc)
                return None
        return None

    def health_check(self) -> bool:
        """轻量健康检查。"""
        try:
            data = "pageNum=1&pageSize=1&tabName=fulltext&column=sse".encode("utf-8")
            resp = self.session.post(
                CNINFO_BASE_URL, data=data, headers=self._headers(), timeout=10,
            )
            if resp.status_code == 200:
                result = resp.json()
                return "announcements" in result or "totalAnnouncement" in result
        except Exception:
            pass
        return False

    def fetch_page(
        self, keyword: str, column: str, start_date: str, end_date: str, page_num: int = 1,
    ) -> Optional[List[Dict]]:
        """获取一页公告。start_date/end_date 格式 YYYY-MM-DD。"""
        if self.ip_banned:
            return None
        if self.state.check_daily_limit("cninfo", CNINFO_DAILY_REQUEST_LIMIT):
            LOGGER.warning("cninfo 每日请求上限已达 %d", CNINFO_DAILY_REQUEST_LIMIT)
            return None

        self._wait()

        if self.dry_run:
            LOGGER.info("[dry-run] Would fetch cninfo keyword=%s column=%s page=%d", keyword, column, page_num)
            return []

        form_data = (
            f"pageNum={page_num}"
            f"&pageSize={CNINFO_PAGE_SIZE}"
            f"&tabName=fulltext"
            f"&column={column}"
            f"&searchkey={keyword}"
            f"&seDate={start_date}~{end_date}"
            f"&isHLtitle=true"
        ).encode("utf-8")

        resp = self._request_with_retry(form_data)
        if resp is None:
            return None

        try:
            result = resp.json()
        except Exception:
            LOGGER.warning("cninfo 响应解析失败")
            return None

        anns = result.get("announcements") or []
        self.state.increment_daily_count("cninfo")

        # cninfo 限流表现：返回空列表但 totalAnnouncement > 0
        if not anns and result.get("totalAnnouncement", 0) > 0:
            LOGGER.warning("cninfo 返回空列表但 total=%d，疑似限流，退避 10s",
                           result.get("totalAnnouncement"))
            time.sleep(10.0)

        return anns

    def fetch_incremental(self, start_date: str, end_date: str) -> List[Dict]:
        """增量获取公告，遍历关键词×交易所。日期格式 YYYY-MM-DD。"""
        seen_ids: set = set()
        all_items: List[Dict] = []

        for keyword in CNINFO_SEARCH_KEYWORDS:
            for column in CNINFO_EXCHANGES:
                for page in range(1, CNINFO_MAX_PAGES_PER_KEYWORD + 1):
                    anns = self.fetch_page(keyword, column, start_date, end_date, page)
                    if anns is None:
                        break
                    if not anns:
                        break

                    new_count = 0
                    for ann in anns:
                        ann_id = ann.get("adjunctUrl", "")
                        if ann_id and ann_id not in seen_ids:
                            seen_ids.add(ann_id)
                            all_items.append(ann)
                            new_count += 1

                    LOGGER.info(
                        "cninfo keyword=%s column=%s page=%d: %d 条（新增 %d）",
                        keyword, column, page, len(anns), new_count,
                    )

                    # 如果本页全是重复，停止翻页
                    if new_count == 0:
                        break

        LOGGER.info("cninfo: 共获取 %d 条去重公告", len(all_items))
        return all_items


# =============================================================================
# AnnouncementDownloader — 编排器
# =============================================================================


class AnnouncementDownloader:
    """编排增量公告获取、标准化、去重、存储。"""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        dry_run: bool = False,
        source: Optional[str] = None,
        force: bool = False,
    ):
        self.data_dir = Path(data_dir) if data_dir else ANNOUNCEMENT_DATA_DIR
        self.dry_run = dry_run
        self.source_filter = source  # "eastmoney" or "cninfo" or None (both)
        self.force = force
        self.state = AnnouncementState(STATE_FILE)
        self.eastmoney = EastMoneyClient(self.state, dry_run=dry_run)
        self.cninfo = CninfoClient(self.state, dry_run=dry_run)

    def _should_fetch_now(self) -> bool:
        """检查是否在执行时间窗口内。"""
        if self.force:
            return True
        now = datetime.now(SHANGHAI_TZ)
        return FETCH_HOUR_START <= now.hour < FETCH_HOUR_END

    def _get_date_range(self) -> Tuple[str, str]:
        """返回 (start_date, end_date)，YYYYMMDD 格式。"""
        now = datetime.now(SHANGHAI_TZ)
        end_date = now.strftime("%Y%m%d")

        # 从状态文件获取上次获取日期
        em_last = self.state.get_source_state("eastmoney").get("last_fetch_date", "")
        ci_last = self.state.get_source_state("cninfo").get("last_fetch_date", "")
        last_fetch = max(em_last, ci_last) if em_last and ci_last else (em_last or ci_last)

        if last_fetch:
            start_dt = max(
                parse_yyyymmdd(last_fetch),
                now - timedelta(days=LOOKBACK_DAYS),
            )
        else:
            start_dt = now - timedelta(days=LOOKBACK_DAYS)

        start_date = start_dt.strftime("%Y%m%d")
        return start_date, end_date

    def _normalize_eastmoney(self, raw_items: List[Dict]) -> pd.DataFrame:
        """将东方财富原始数据转换为标准 DataFrame。"""
        if not raw_items:
            return pd.DataFrame()

        rows = []
        fetch_time = datetime.now(SHANGHAI_TZ).isoformat()

        for item in raw_items:
            art_code = item.get("art_code", "")
            title = item.get("title", "") or item.get("title_ch", "")
            notice_date = item.get("notice_date", "")
            ann_date = notice_date.split(" ")[0].replace("-", "") if notice_date else ""

            # 提取股票代码
            codes = item.get("codes", []) or []
            stock_code = ""
            stock_name = ""
            if codes:
                first_code = codes[0]
                raw_code = first_code.get("stock_code", "")
                market_code = first_code.get("market_code", "0")
                if raw_code:
                    stock_code = raw_code + _market_code_to_suffix(market_code)
                stock_name = first_code.get("short_name", "")

            # 提取类别码
            category_code = ""
            category_name = ""
            for col in item.get("columns", []) or []:
                code = col.get("column_code", "")
                if code in COOPERATION_CATEGORY_CODES:
                    category_code = code
                    category_name = COOPERATION_CATEGORY_CODES[code]
                    break

            rows.append({
                "ann_id": art_code,
                "source": "eastmoney",
                "ann_date": ann_date,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "title": title,
                "category_code": category_code,
                "category_name": category_name,
                "pdf_url": "",
                "fetch_time": fetch_time,
            })

        return pd.DataFrame(rows)

    def _normalize_cninfo(self, raw_items: List[Dict]) -> pd.DataFrame:
        """将巨潮资讯原始数据转换为标准 DataFrame。"""
        if not raw_items:
            return pd.DataFrame()

        rows = []
        fetch_time = datetime.now(SHANGHAI_TZ).isoformat()

        for item in raw_items:
            adjunct_url = item.get("adjunctUrl", "")
            title = _strip_html(item.get("announcementTitle", ""))
            ann_ts = item.get("announcementTime", 0)
            if isinstance(ann_ts, (int, float)) and ann_ts > 0:
                ann_date = datetime.fromtimestamp(ann_ts / 1000).strftime("%Y%m%d")
            else:
                ann_date = ""

            sec_code = str(item.get("secCode", ""))
            sec_name = item.get("secName", "")
            stock_code = sec_code + _code_prefix_to_suffix(sec_code) if sec_code else ""

            rows.append({
                "ann_id": adjunct_url,
                "source": "cninfo",
                "ann_date": ann_date,
                "stock_code": stock_code,
                "stock_name": sec_name,
                "title": title,
                "category_code": "",
                "category_name": "",
                "pdf_url": f"http://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else "",
                "fetch_time": fetch_time,
            })

        return pd.DataFrame(rows)

    def _dedup_and_merge(self, existing_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
        """按 ann_id + source 去重合并。"""
        frames = [df for df in (existing_df, new_df) if df is not None and not df.empty]
        if not frames:
            return pd.DataFrame()
        if len(frames) == 1:
            return frames[0].copy()

        combined = pd.concat(frames, ignore_index=True)
        dedup_cols = ["ann_id", "source"]
        available_cols = [c for c in dedup_cols if c in combined.columns]
        if available_cols:
            combined = combined.drop_duplicates(subset=available_cols, keep="last")
            combined = combined.sort_values(available_cols).reset_index(drop=True)
        else:
            combined = combined.drop_duplicates().reset_index(drop=True)
        return combined

    def _save_parquet(self, df: pd.DataFrame, source: str, ann_date: str) -> int:
        """保存到分区 parquet，返回新增行数。"""
        if df.empty:
            return 0

        partition_dir = self.data_dir / f"source={source}" / f"date={ann_date}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / "data.parquet"

        existing_df = pd.DataFrame()
        if file_path.exists():
            try:
                existing_df = pd.read_parquet(file_path, engine="pyarrow")
            except Exception as exc:
                LOGGER.warning("读取已有 parquet 失败 %s: %s", file_path, exc)

        merged = self._dedup_and_merge(existing_df, df)
        new_rows = len(merged) - len(existing_df)

        if self.dry_run:
            LOGGER.info("[dry-run] Would write %d rows to %s", len(merged), file_path)
            return new_rows

        merged.to_parquet(file_path, engine="pyarrow", index=False)
        return new_rows

    def _run_eastmoney(self) -> Dict[str, Any]:
        """执行东方财富公告下载。"""
        summary: Dict[str, Any] = {"status": "skipped", "rows": 0, "pages": 0, "requests": 0}

        if self.source_filter and self.source_filter != "eastmoney":
            return summary

        LOGGER.info("=== 东方财富公告下载 ===")

        if not self.eastmoney.health_check():
            LOGGER.warning("东方财富健康检查失败，跳过")
            summary["status"] = "unhealthy"
            return summary

        raw_items = self.eastmoney.fetch_incremental()
        if not raw_items:
            LOGGER.info("东方财富：无新增经营合作公告")
            summary["status"] = "success"
            return summary

        df = self._normalize_eastmoney(raw_items)
        if df.empty:
            summary["status"] = "success"
            return summary

        # 按 ann_date 分区保存
        total_new = 0
        for ann_date, group in df.groupby("ann_date"):
            new_rows = self._save_parquet(group, "eastmoney", str(ann_date))
            total_new += new_rows

        # 更新状态
        last_ann_id = raw_items[0].get("art_code", "") if raw_items else ""
        today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
        self.state.update_source_state(
            "eastmoney",
            last_fetch_date=today,
            last_ann_id=last_ann_id,
        )

        summary.update(status="success", rows=len(df), new_rows=total_new)
        LOGGER.info("东方财富：保存 %d 条公告（新增 %d）", len(df), total_new)
        return summary

    def _run_cninfo(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """执行巨潮资讯公告下载。日期格式 YYYY-MM-DD。"""
        summary: Dict[str, Any] = {"status": "skipped", "rows": 0, "requests": 0}

        if self.source_filter and self.source_filter != "cninfo":
            return summary

        LOGGER.info("=== 巨潮资讯公告下载 ===")

        if not self.cninfo.health_check():
            LOGGER.warning("巨潮资讯健康检查失败，跳过")
            summary["status"] = "unhealthy"
            return summary

        raw_items = self.cninfo.fetch_incremental(start_date, end_date)
        if not raw_items:
            LOGGER.info("巨潮资讯：无新增公告")
            summary["status"] = "success"
            return summary

        df = self._normalize_cninfo(raw_items)
        if df.empty:
            summary["status"] = "success"
            return summary

        # 按 ann_date 分区保存
        total_new = 0
        for ann_date, group in df.groupby("ann_date"):
            new_rows = self._save_parquet(group, "cninfo", str(ann_date))
            total_new += new_rows

        # 更新状态
        last_ann_id = raw_items[0].get("adjunctUrl", "") if raw_items else ""
        today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
        self.state.update_source_state(
            "cninfo",
            last_fetch_date=today,
            last_ann_id=last_ann_id,
        )

        summary.update(status="success", rows=len(df), new_rows=total_new)
        LOGGER.info("巨潮资讯：保存 %d 条公告（新增 %d）", len(df), total_new)
        return summary

    def run(self) -> Dict[str, Any]:
        """主入口：执行增量公告下载。"""
        if not self._should_fetch_now():
            now = datetime.now(SHANGHAI_TZ)
            LOGGER.info(
                "当前时间 %02d:%02d CST 不在执行窗口 %02d:00-%02d:00，跳过（用 --force 忽略）",
                now.hour, now.minute, FETCH_HOUR_START, FETCH_HOUR_END,
            )
            return {"status": "outside_window"}

        start_date, end_date = self._get_date_range()
        LOGGER.info("公告下载日期范围: %s ~ %s", start_date, end_date)

        # 巨潮资讯日期格式 YYYY-MM-DD
        ci_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        ci_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        # 东方财富（优先，不限流更宽松）
        em_summary = self._run_eastmoney()

        # 巨潮资讯
        ci_summary = self._run_cninfo(ci_start, ci_end)

        # 保存状态
        if not self.dry_run:
            self.state.save()

        total_rows = em_summary.get("rows", 0) + ci_summary.get("rows", 0)
        summary = {
            "eastmoney": em_summary,
            "cninfo": ci_summary,
            "total_rows": total_rows,
            "date_range": [start_date, end_date],
        }
        LOGGER.info("公告下载完成: 共 %d 条", total_rows)
        return summary


# =============================================================================
# CLI 入口
# =============================================================================


def parse_yyyymmdd(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d")


def setup_logging() -> None:
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=config.LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(config.LOG_DIR) / "announcement_downloader.log",
                encoding="utf-8",
            ),
        ],
    )


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="A股经营合作公告增量下载器")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    parser.add_argument("--source", choices=["eastmoney", "cninfo"], help="仅下载指定源")
    parser.add_argument("--force", action="store_true", help="忽略时间窗口限制")
    args = parser.parse_args()

    downloader = AnnouncementDownloader(
        dry_run=args.dry_run,
        source=args.source,
        force=args.force,
    )
    summary = downloader.run()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
