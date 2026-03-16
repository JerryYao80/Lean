"""
Tushare 数据下载器配置文件
"""
import os
from datetime import datetime

# =====================================================
# Tushare API 配置
# =====================================================
TUSHARE_TOKEN = os.getenv(
    "TUSHARE_TOKEN",
    "c735900235cd005d4a32c7fad8ef9bec4dbec1e4df8030d49f3c050a53bf"
)
TUSHARE_API_URL = "http://106.54.191.157:5000"
TUSHARE_TIMEOUT_SECONDS = int(os.getenv("TUSHARE_TIMEOUT_SECONDS", "15"))

# =====================================================
# 数据存储配置
# =====================================================
DATA_DIR = "/home/project/tushare-downloader/tushare_data"

# =====================================================
# 限流配置
# =====================================================
# 每分钟最大请求数 (RPM)
MAX_REQUESTS_PER_MINUTE = 200
# rt_k / rt_etf_k 实时日线接口单独按较严权限控制
RT_DAILY_MAX_REQUESTS_PER_MINUTE = int(os.getenv("RT_DAILY_MAX_REQUESTS_PER_MINUTE", "40"))
# 令牌桶容量
TOKEN_BUCKET_CAPACITY = 200
# 令牌补充速率 (每秒)
TOKEN_REFILL_RATE = MAX_REQUESTS_PER_MINUTE / 60.0  # ~3.33 tokens/sec

# =====================================================
# 重试配置
# =====================================================
MAX_RETRIES = 5
BASE_RETRY_DELAY = 2.0  # 秒
MAX_RETRY_DELAY = 60.0  # 最大延迟秒数
TIMEOUT_MAX_RETRIES = int(os.getenv("TIMEOUT_MAX_RETRIES", "2"))  # 超时错误快速放弃
TIMEOUT_RETRY_DELAY = float(os.getenv("TIMEOUT_RETRY_DELAY", "1.0"))  # 超时后快速重试秒数

# =====================================================
# 并发配置
# =====================================================
MAX_WORKERS = 1  # rt_k/rt_etf_k live-paper 默认串行，优先保证不撞 Tushare 逐分钟限额

# =====================================================
# 日期范围配置
# =====================================================
START_YEAR = 1990
END_YEAR = int(os.getenv("TUSHARE_END_YEAR", datetime.now().year))

# =====================================================
# 日志配置
# =====================================================
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
LOG_LEVEL = "INFO"

# =====================================================
# 数据验证配置
# =====================================================
MIN_ROWS_THRESHOLD = 0  # 最小行数阈值（0表示允许空数据）
VALIDATE_SCHEMA = True  # 是否验证Schema
