"""
Tushare API 接口注册表

定义所有可用的 Tushare Pro API 接口及其参数配置
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class ChunkStrategy(Enum):
    """分块策略"""
    NONE = "none"           # 不分块，一次性拉取
    YEAR = "year"           # 按年份分块
    DATE = "date"           # 按单日分块
    STOCK = "stock"         # 按股票代码分块
    QUARTER = "quarter"     # 按季度分块


@dataclass
class APIConfig:
    """API 接口配置"""
    api_name: str                               # 接口名称
    description: str                            # 中文描述
    chunk_strategy: ChunkStrategy = ChunkStrategy.NONE  # 分块策略
    date_field: Optional[str] = None            # 日期字段名
    code_field: Optional[str] = None            # 代码字段名
    start_date_param: str = "start_date"        # 开始日期参数名
    end_date_param: str = "end_date"            # 结束日期参数名
    required_params: Dict[str, Any] = field(default_factory=dict)  # 必要参数
    category: str = "other"                     # 数据分类
    priority: int = 1                           # 下载优先级 (1=高, 3=低)
    enabled: bool = True                        # 是否启用
    
    
# =====================================================
# 沪深股票 - 基础数据
# =====================================================
STOCK_BASIC_APIS = [
    APIConfig(
        api_name="stock_basic",
        description="股票列表",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_basic",
        priority=1
    ),
    APIConfig(
        api_name="trade_cal",
        description="交易日历",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"exchange": "SSE"},
        category="stock_basic",
        priority=1
    ),
    APIConfig(
        api_name="namechange",
        description="股票曾用名",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_basic"
    ),
    APIConfig(
        api_name="hs_const",
        description="沪深股通成分股",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"hs_type": "SH"},
        category="stock_basic"
    ),
    APIConfig(
        api_name="stk_managers",
        description="上市公司管理层",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_basic"
    ),
    APIConfig(
        api_name="stk_rewards",
        description="管理层薪酬和持股",
        chunk_strategy=ChunkStrategy.STOCK,
        code_field="ts_code",
        category="stock_basic"
    ),
    APIConfig(
        api_name="new_share",
        description="IPO新股上市",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_basic"
    ),
    APIConfig(
        api_name="bak_basic",
        description="备用基础信息",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_basic"
    ),
    APIConfig(
        api_name="stock_company",
        description="上市公司基本信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_basic"
    ),
    # === 新增接口 ===
    APIConfig(
        api_name="stk_premarket",
        description="每日股本（盘前）",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_basic"
    ),
    # 以下API不存在于tushare文档中，已禁用
    # APIConfig(
    #     api_name="st_list",
    #     description="ST股票列表（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_basic",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="hk_hold_list",
    #     description="沪深港通股票列表（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_basic",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="bj_mapping",
    #     description="北交所新旧代码对照（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_basic",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="stock_basic_history",
    #     description="股票历史列表（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_basic",
    #     enabled=False
    # ),
]

# =====================================================
# 沪深股票 - 行情数据
# =====================================================
STOCK_QUOTE_APIS = [
    APIConfig(
        api_name="daily",
        description="日线行情",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="trade_date",
        code_field="ts_code",
        category="stock_quote",
        priority=1
    ),
    APIConfig(
        api_name="weekly",
        description="周线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="monthly",
        description="月线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="adj_factor",
        description="复权因子",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="trade_date",
        code_field="ts_code",
        category="stock_quote",
        priority=1
    ),
    APIConfig(
        api_name="suspend_d",
        description="每日停复牌信息",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        start_date_param="start_date",
        end_date_param="end_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="daily_basic",
        description="每日指标",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="trade_date",
        code_field="ts_code",
        category="stock_quote",
        priority=1
    ),
    APIConfig(
        api_name="stk_limit",
        description="每日涨跌停价格",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="trade_date",
        code_field="ts_code",
        category="stock_quote"
    ),
    APIConfig(
        api_name="hsgt_top10",
        description="沪深股通十大成交股",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="ggt_top10",
        description="港股通十大成交股",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="ggt_daily",
        description="港股通每日成交统计",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_quote"
    ),
    APIConfig(
        api_name="ggt_monthly",
        description="港股通每月成交统计",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_quote"
    ),
    # === 新增行情接口 ===
    APIConfig(
        api_name="stk_weekly_monthly",
        description="周/月线行情(每日更新)",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        required_params={"freq": "W"},  # 必填参数: W=周线, M=月线
        category="stock_quote"
    ),
    # 注意：stk_weekly_monthly_adj 不存在，应使用 wk_weekly_adj 和 wk_monthly_adj
    # APIConfig(
    #     api_name="stk_weekly_monthly_adj",
    #     description="周/月线复权行情(每日更新)（API不存在，使用wk_weekly_adj和wk_monthly_adj）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="stock_quote",
    #     enabled=False
    # ),
    APIConfig(
        api_name="bak_daily",
        description="备用行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_quote",
        priority=3
    ),
    # 高积分/爬虫接口 - 禁用
    APIConfig(
        api_name="stk_mins",
        description="历史分钟行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_quote",
        enabled=False  # 需要10000+积分
    ),
    APIConfig(
        api_name="realtime_tick",
        description="实时Tick（爬虫）",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_quote",
        enabled=False  # 爬虫接口
    ),
    APIConfig(
        api_name="realtime_trans",
        description="实时成交（爬虫）",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_quote",
        enabled=False  # 爬虫接口
    ),
    APIConfig(
        api_name="realtime_list",
        description="实时排名（爬虫）",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_quote",
        enabled=False  # 爬虫接口
    ),
]

# =====================================================
# 沪深股票 - 财务数据
# =====================================================
STOCK_FINANCE_APIS = [
    APIConfig(
        api_name="income",
        description="利润表",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance",
        priority=1
    ),
    APIConfig(
        api_name="balancesheet",
        description="资产负债表",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance",
        priority=1
    ),
    APIConfig(
        api_name="cashflow",
        description="现金流量表",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance",
        priority=1
    ),
    APIConfig(
        api_name="forecast",
        description="业绩预告",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="ann_date",
        code_field="ts_code",
        category="stock_finance"
    ),
    APIConfig(
        api_name="express",
        description="业绩快报",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="stock_finance"
    ),
    APIConfig(
        api_name="dividend",
        description="分红送股",
        chunk_strategy=ChunkStrategy.STOCK,
        code_field="ts_code",
        category="stock_finance"
    ),
    APIConfig(
        api_name="fina_indicator",
        description="财务指标数据",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance",
        priority=1
    ),
    APIConfig(
        api_name="fina_audit",
        description="财务审计意见",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance"
    ),
    APIConfig(
        api_name="fina_mainbz",
        description="主营业务构成",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要按股票代码分块
        code_field="ts_code",
        category="stock_finance"
    ),
    APIConfig(
        api_name="disclosure_date",
        description="财报披露日期表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="stock_finance"
    ),
]

# =====================================================
# 沪深股票 - 参考数据
# =====================================================
STOCK_REFERENCE_APIS = [
    APIConfig(
        api_name="top10_holders",
        description="前十大股东",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="stock_reference"
    ),
    APIConfig(
        api_name="top10_floatholders",
        description="前十大流通股东",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="stock_reference"
    ),
    APIConfig(
        api_name="pledge_stat",
        description="股权质押统计数据",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_reference"
    ),
    APIConfig(
        api_name="pledge_detail",
        description="股权质押明细数据",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要ts_code参数
        code_field="ts_code",
        category="stock_reference"
    ),
    APIConfig(
        api_name="repurchase",
        description="股票回购",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_reference"
    ),
    APIConfig(
        api_name="share_float",
        description="限售股解禁",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="ann_date",
        category="stock_reference"
    ),
    APIConfig(
        api_name="block_trade",
        description="大宗交易",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_reference"
    ),
    APIConfig(
        api_name="stk_holdernumber",
        description="股东人数",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="stock_reference"
    ),
    APIConfig(
        api_name="stk_holdertrade",
        description="股东增减持",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="ann_date",
        category="stock_reference"
    ),
]

# =====================================================
# 沪深股票 - 特色数据
# =====================================================
STOCK_SPECIAL_APIS = [
    APIConfig(
        api_name="cyq_perf",
        description="每日筹码及胜率",
        chunk_strategy=ChunkStrategy.STOCK,  # 注意：此接口要求 ts_code(必填)+日期范围
        code_field="ts_code",
        category="stock_special",
        enabled=False  # 禁用：需要特殊参数处理(ts_code+start_date+end_date)，且数据量极大
    ),
    APIConfig(
        api_name="cyq_chips",
        description="每日筹码分布",
        chunk_strategy=ChunkStrategy.STOCK,  # 注意：此接口要求 ts_code(必填)+日期范围
        code_field="ts_code",
        category="stock_special",
        priority=3,  # 数据量大，优先级低
        enabled=False  # 禁用：需要特殊参数处理(ts_code+start_date+end_date)，且数据量极大(单次2000行)
    ),
    APIConfig(
        api_name="ccass_hold",
        description="中央结算系统持股统计",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="ccass_hold_detail",
        description="中央结算系统持股明细",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_special",
        priority=3
    ),
    APIConfig(
        api_name="hk_hold",
        description="沪深股通持股明细",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="stk_surv",
        description="机构调研数据",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="surv_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="broker_recommend",
        description="券商月度金股",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_special"
    ),
    # === 新增特色数据接口 ===
    APIConfig(
        api_name="report_rc",
        description="券商盈利预测数据",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="report_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="stk_auction_o",
        description="股票开盘集合竞价数据",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="stk_auction_c",
        description="股票收盘集合竞价数据",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="stk_dkt",
        description="神奇九转指标",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_special"
    ),
    APIConfig(
        api_name="stk_ah_comparison",
        description="AH股比价",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_special"
    ),
    # 专业版接口 - 禁用
    APIConfig(
        api_name="stk_factor_pro",
        description="股票技术面因子(专业版)",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_special",
        enabled=False  # 需要专业版
    ),
]

# =====================================================
# 沪深股票 - 两融数据
# =====================================================
STOCK_MARGIN_APIS = [
    APIConfig(
        api_name="margin",
        description="融资融券交易汇总",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_margin"
    ),
    APIConfig(
        api_name="margin_detail",
        description="融资融券交易明细",
        chunk_strategy=ChunkStrategy.STOCK,
        date_field="trade_date",
        code_field="ts_code",
        category="stock_margin"
    ),
    APIConfig(
        api_name="slb_len_mm",
        description="转融资交易汇总",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_margin"
    ),
]

# =====================================================
# 沪深股票 - 资金流向
# =====================================================
STOCK_MONEYFLOW_APIS = [
    APIConfig(
        api_name="moneyflow",
        description="个股资金流向",
        chunk_strategy=ChunkStrategy.STOCK,  # 正确: 需要ts_code参数
        code_field="ts_code",
        category="stock_moneyflow"
    ),
    APIConfig(
        api_name="moneyflow_hsgt",
        description="沪深港通资金流向",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
    # === 新增资金流向接口 ===
    APIConfig(
        api_name="moneyflow_ths",
        description="个股资金流向（同花顺）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
    APIConfig(
        api_name="moneyflow_dc",
        description="个股资金流向（东财）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
    APIConfig(
        api_name="moneyflow_ind_ths",
        description="板块资金流向（同花顺）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
    APIConfig(
        api_name="moneyflow_ind_dc",
        description="板块资金流向（东财）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
    APIConfig(
        api_name="moneyflow_mkt_dc",
        description="大盘资金流向（东财）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_moneyflow"
    ),
]

# =====================================================
# 沪深股票 - 龙虎榜
# =====================================================
STOCK_BILLBOARD_APIS = [
    APIConfig(
        api_name="top_list",
        description="龙虎榜每日明细",
        chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需trade_date参数，改为DATE分块
        date_field="trade_date",
        category="stock_billboard"
    ),
    APIConfig(
        api_name="top_inst",
        description="龙虎榜机构明细",
        chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需trade_date参数，改为DATE分块
        date_field="trade_date",
        category="stock_billboard"
    ),
    # === 新增打板专题接口 ===
    # 以下API不存在于tushare文档中，已禁用
    # APIConfig(
    #     api_name="ths_limit_list",
    #     description="同花顺涨跌停榜单（API不存在）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="stock_billboard",
    #     enabled=False
    # ),
    APIConfig(
        api_name="limit_step",
        description="涨跌停和炸板数据",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_billboard"
    ),
    # APIConfig(
    #     api_name="limit_ladder",
    #     description="涨停股票连板天梯（API不存在）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="stock_billboard",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="limit_strong_ind",
    #     description="涨停最强板块统计（API不存在）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="stock_billboard",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="hot_money",
    #     description="市场游资最全名录（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_billboard",
    #     enabled=False
    # ),
    APIConfig(
        api_name="hm_detail",  # 修复: 正确的API名称
        description="游资交易每日明细",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_billboard"
    ),
    APIConfig(
        api_name="ths_hot",
        description="同花顺App热榜数据",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_billboard"
    ),
    APIConfig(
        api_name="dc_hot",
        description="东方财富App热榜",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="stock_billboard"
    ),
    APIConfig(
        api_name="kpl_list",
        description="榜单数据（开盘啦）",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_billboard"
    ),
]

# =====================================================
# 沪深股票 - 概念板块
# =====================================================
STOCK_CONCEPT_APIS = [
    APIConfig(
        api_name="ths_index",
        description="同花顺概念和行业列表",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="ths_daily",
        description="同花顺概念和行业指数行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_concept"
    ),
    APIConfig(
        api_name="ths_member",
        description="同花顺概念成分",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="limit_list_d",
        description="涨跌停统计",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_concept"
    ),
    # === 新增概念板块接口 ===
    APIConfig(
        api_name="dc_index",
        description="东方财富概念板块",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="dc_member",
        description="东方财富概念成分",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="dc_daily",
        description="东财概念和行业指数行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_concept"
    ),
    APIConfig(
        api_name="tdx_index",
        description="通达信板块信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="tdx_member",
        description="通达信板块成分",
        chunk_strategy=ChunkStrategy.NONE,
        category="stock_concept"
    ),
    APIConfig(
        api_name="tdx_daily",
        description="通达信板块行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="stock_concept"
    ),
    # 以下API不存在于tushare文档中，已禁用
    # APIConfig(
    #     api_name="kpl_subject",
    #     description="题材数据（开盘啦）（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_concept",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="kpl_member",
    #     description="题材成分（开盘啦）（API不存在）",
    #     chunk_strategy=ChunkStrategy.NONE,
    #     category="stock_concept",
    #     enabled=False
    # ),
]

# =====================================================
# 指数数据
# =====================================================
INDEX_APIS = [
    APIConfig(
        api_name="index_basic",
        description="指数基本信息",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"market": "SSE"},
        category="index"
    ),
    APIConfig(
        api_name="index_daily",
        description="指数日线行情",
        chunk_strategy=ChunkStrategy.STOCK,  # 修复: 需要ts_code参数
        code_field="ts_code",
        category="index",
        priority=1
    ),
    APIConfig(
        api_name="index_weekly",
        description="指数周线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="index_monthly",
        description="指数月线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="index_weight",
        description="指数成分和权重",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="index",
        priority=1
    ),
    APIConfig(
        api_name="index_dailybasic",
        description="大盘指数每日指标",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="index_classify",
        description="申万行业分类",
        chunk_strategy=ChunkStrategy.NONE,
        category="index"
    ),
    APIConfig(
        api_name="index_member",
        description="申万行业成分",
        chunk_strategy=ChunkStrategy.NONE,
        category="index"
    ),
    APIConfig(
        api_name="sw_daily",
        description="申万行业指数日行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="index_global",
        description="国际主要指数",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="daily_info",
        description="沪深市场每日交易统计",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="index"
    ),
    # === 新增指数接口 ===
    APIConfig(
        api_name="index_member_all",  # 修复: 正确的API名称
        description="申万行业成分（分级）",
        chunk_strategy=ChunkStrategy.NONE,
        category="index"
    ),
    APIConfig(
        api_name="ci_index_member",  # 修复: 正确的API名称
        description="中信行业成分",
        chunk_strategy=ChunkStrategy.NONE,
        category="index"
    ),
    APIConfig(
        api_name="ci_daily",
        description="中信行业指数日行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="index"
    ),
    APIConfig(
        api_name="sz_daily_info",
        description="深圳市场每日交易情况",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="index"
    ),
    # 专业版接口 - 禁用
    APIConfig(
        api_name="index_factor_pro",
        description="指数技术面因子(专业版)",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="index",
        enabled=False  # 需要专业版
    ),
]

# =====================================================
# ETF 数据
# ====================================================
ETF_APIS = [
    APIConfig(
        api_name="fund_basic",
        description="基金列表",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"market": "E"},
        category="etf"
    ),
    APIConfig(
        api_name="fund_daily",
        description="基金日线行情",
        chunk_strategy=ChunkStrategy.STOCK,  # 正确: 需要ts_code或trade_date
        code_field="ts_code",
        category="etf",
        priority=1
    ),
    APIConfig(
        api_name="fund_adj",
        description="基金复权因子",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="etf"
    ),
    APIConfig(
        api_name="fund_nav",
        description="基金净值",
        chunk_strategy=ChunkStrategy.STOCK,  # 正确: 需要ts_code或nav_date
        code_field="ts_code",
        category="etf"
    ),
    APIConfig(
        api_name="fund_div",
        description="基金分红",
        chunk_strategy=ChunkStrategy.STOCK,  # 正确: 需要ts_code等参数之一
        code_field="ts_code",
        category="etf"
    ),
    APIConfig(
        api_name="fund_portfolio",
        description="基金持仓",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="etf"
    ),
    APIConfig(
        api_name="fund_manager",
        description="基金经理",
        chunk_strategy=ChunkStrategy.NONE,
        category="etf"
    ),
    APIConfig(
        api_name="fund_share",
        description="基金规模",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="etf"
    ),
    # === 新增ETF/基金接口 ===
    APIConfig(
        api_name="fund_company",
        description="基金管理人",
        chunk_strategy=ChunkStrategy.NONE,
        category="etf"
    ),
    # 专业版接口 - 禁用
    APIConfig(
        api_name="fund_factor_pro",
        description="基金技术面因子(专业版)",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="etf",
        enabled=False  # 需要专业版
    ),
]

# =====================================================
# 期货数据
# =====================================================
FUTURES_APIS = [
    APIConfig(
        api_name="fut_basic",
        description="期货合约信息",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"exchange": "DCE"},
        category="futures"
    ),
    APIConfig(
        api_name="trade_cal",
        description="期货交易日历",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"exchange": "DCE"},
        category="futures"
    ),
    APIConfig(
        api_name="fut_daily",
        description="期货日线行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="futures",
        priority=1
    ),
    APIConfig(
        api_name="fut_holding",
        description="每日持仓排名",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="futures"
    ),
    APIConfig(
        api_name="fut_wsr",
        description="仓单日报",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="futures"
    ),
    APIConfig(
        api_name="fut_settle",
        description="每日结算参数",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="futures"
    ),
    APIConfig(
        api_name="fut_mapping",
        description="期货主力与连续合约",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="futures"
    ),
    # === 新增期货接口 ===
    # 注意：nh_index 不是独立API，南华期货指数应使用 index_daily
    # APIConfig(
    #     api_name="nh_index",
    #     description="南华期货指数行情（使用index_daily代替）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="futures",
    #     enabled=False
    # ),
    APIConfig(
        api_name="fut_weekly_monthly",
        description="期货周/月线行情(每日更新)",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        required_params={"freq": "W"},  # 修复: 必填参数 W=周线, M=月线
        category="futures"
    ),
    APIConfig(
        api_name="fut_weekly_detail",
        description="期货主要品种交易周报",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="futures"
    ),
    # 注意：fut_limit 可能不存在，需验证正确API名称
    # APIConfig(
    #     api_name="fut_limit",
    #     description="期货合约涨跌停价格（API名称需验证）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="futures",
    #     enabled=False
    # ),
    # 高积分接口 - 禁用
    APIConfig(
        api_name="ft_mins",
        description="期货历史分钟行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="futures",
        enabled=False  # 需要10000+积分
    ),
]

# =====================================================
# 期权数据
# =====================================================
OPTIONS_APIS = [
    APIConfig(
        api_name="opt_basic",
        description="期权合约信息",
        chunk_strategy=ChunkStrategy.NONE,
        required_params={"exchange": "SSE"},
        category="options"
    ),
    APIConfig(
        api_name="opt_daily",
        description="期权日线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="options",
        priority=1
    ),
]

# =====================================================
# 可转债数据
# =====================================================
CB_APIS = [
    APIConfig(
        api_name="cb_basic",
        description="可转债基础信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="cb"
    ),
    APIConfig(
        api_name="cb_issue",
        description="可转债发行",
        chunk_strategy=ChunkStrategy.NONE,
        category="cb"
    ),
    APIConfig(
        api_name="cb_daily",
        description="可转债行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="cb",
        priority=1
    ),
    APIConfig(
        api_name="cb_call",
        description="可转债赎回信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="cb"
    ),
    APIConfig(
        api_name="cb_rate",
        description="可转债票面利率",
        chunk_strategy=ChunkStrategy.NONE,
        category="cb"
    ),
    APIConfig(
        api_name="cb_share",
        description="可转债转股结果",
        chunk_strategy=ChunkStrategy.NONE,
        category="cb"
    ),
    APIConfig(
        api_name="repo_daily",
        description="债券回购日行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="cb"
    ),
    # === 新增债券接口 ===
    # 注释原因: yc_cb 接口超2000行限制，暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="yc_cb",
    #     description="柜台流通式债券报价",
    #     chunk_strategy=ChunkStrategy.DATE,  # 修复: YEAR策略会超2000行限制
    #     date_field="trade_date",
    #     category="cb"
    # ),
    # 注释原因: yc 接口暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="yc",  # 修复: 正确的API名称
    #     description="国债收益率曲线",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="trade_date",
    #     category="cb"
    # ),
    APIConfig(
        api_name="eco_cal",
        description="全球财经事件",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="cb"
    ),
    # 专业版接口 - 禁用
    APIConfig(
        api_name="cb_factor_pro",
        description="可转债技术面因子(专业版)",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="cb",
        enabled=False  # 需要专业版
    ),
]

# =====================================================
# 外汇数据
# =====================================================
FX_APIS = [
    APIConfig(
        api_name="fx_obasic",
        description="外汇基础信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="fx"
    ),
    APIConfig(
        api_name="fx_daily",
        description="外汇日线行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="fx"
    ),
]

# =====================================================
# 港股数据
# =====================================================
HK_APIS = [
    APIConfig(
        api_name="hk_basic",
        description="港股基础信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="hk"
    ),
    APIConfig(
        api_name="hk_tradecal",
        description="港股交易日历",
        chunk_strategy=ChunkStrategy.NONE,
        category="hk"
    ),
    APIConfig(
        api_name="hk_daily",
        description="港股日线行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="hk",
        priority=1
    ),
    APIConfig(
        api_name="hk_adjfactor",  # 修复: 正确的API名称
        description="港股复权因子",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="hk"
    ),
    # === 新增港股接口 ===
    APIConfig(
        api_name="hk_daily_adj",
        description="港股复权行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="hk"
    ),
    # 注释原因: hk_income 接口权限限制（每天最多访问2次），暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="hk_income",
    #     description="港股利润表",
    #     chunk_strategy=ChunkStrategy.QUARTER,
    #     date_field="end_date",
    #     category="hk"
    # ),
    APIConfig(
        api_name="hk_balancesheet",
        description="港股资产负债表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="hk"
    ),
    APIConfig(
        api_name="hk_cashflow",
        description="港股现金流量表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="hk"
    ),
    APIConfig(
        api_name="hk_fina_indicator",
        description="港股财务指标数据",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="hk"
    ),
]

# =====================================================
# 美股数据
# =====================================================
US_APIS = [
    APIConfig(
        api_name="us_basic",
        description="美股基础信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="us"
    ),
    APIConfig(
        api_name="us_tradecal",
        description="美股交易日历",
        chunk_strategy=ChunkStrategy.NONE,
        category="us"
    ),
    APIConfig(
        api_name="us_daily",
        description="美股日线行情",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="trade_date",
        category="us",
        priority=1
    ),
    APIConfig(
        api_name="us_adj_factor",
        description="美股复权因子",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="us"
    ),
    # === 新增美股接口 ===
    APIConfig(
        api_name="us_daily_adj",
        description="美股复权行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="us"
    ),
    APIConfig(
        api_name="us_income",
        description="美股利润表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="us"
    ),
    APIConfig(
        api_name="us_balancesheet",
        description="美股资产负债表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="us"
    ),
    APIConfig(
        api_name="us_cashflow",
        description="美股现金流量表",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="us"
    ),
    APIConfig(
        api_name="us_fina_indicator",
        description="美股财务指标数据",
        chunk_strategy=ChunkStrategy.QUARTER,
        date_field="end_date",
        category="us"
    ),
]

# =====================================================
# 宏观经济数据
# =====================================================
MACRO_APIS = [
    APIConfig(
        api_name="shibor",
        description="Shibor利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="shibor_quote",
        description="Shibor报价数据",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="shibor_lpr",
        description="LPR贷款基础利率",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="libor",
        description="Libor利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="hibor",
        description="Hibor利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="cn_gdp",
        description="国内生产总值",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="cn_cpi",
        description="居民消费价格指数",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="cn_ppi",
        description="工业生产者出厂价格指数",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="cn_m",
        description="货币供应量",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="sf_month",
        description="社融数据(月度)",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="cn_pmi",
        description="采购经理指数",
        chunk_strategy=ChunkStrategy.NONE,
        category="macro"
    ),
    APIConfig(
        api_name="us_tycr",
        description="美国国债收益率曲线利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="us_tbr",
        description="美国短期国债利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
    APIConfig(
        api_name="us_tltr",
        description="美国国债长期利率",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="macro"
    ),
]

# =====================================================
# 新闻资讯数据
# =====================================================
NEWS_APIS = [
    APIConfig(
        api_name="news",
        description="新闻快讯",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="datetime",
        start_date_param="start_date",
        end_date_param="end_date",
        category="news",
        priority=3,
        enabled=False
    ),
    APIConfig(
        api_name="major_news",
        description="新闻通讯(长篇)",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="pub_time",
        start_date_param="start_date",
        end_date_param="end_date",
        category="news",
        priority=3,
        enabled=False
    ),
    APIConfig(
        api_name="cctv_news",
        description="新闻联播文字稿",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        category="news",
        priority=2
    ),
    APIConfig(
        api_name="anns",
        description="上市公司公告",
        chunk_strategy=ChunkStrategy.DATE,
        date_field="ann_date",
        category="news",
        priority=3,
        enabled=False
    ),
]


# =====================================================
# 行业经济数据
# =====================================================
INDUSTRY_APIS = [
    APIConfig(
        api_name="tmt_twincome",  # 修复: 正确的API名称
        description="台湾电子产业月营收",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="date",
        required_params={"item": "1"},  # 必填参数
        category="industry"
    ),
    APIConfig(
        api_name="tmt_twincomedetail",  # 修复: 正确的API名称
        description="台湾电子产业月营收明细",
        chunk_strategy=ChunkStrategy.NONE,  # 改为不分块，因为接口需要item和symbol参数
        date_field="date",
        required_params={"item": "8", "symbol": "6156"},  # 使用文档示例中的参数
        category="industry"
    ),
    # 注释原因: bo_monthly 接口参数错误（需要date参数），暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="bo_monthly",
    #     description="电影月度票房",
    #     chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需date参数，改为DATE分块
    #     date_field="date",
    #     category="industry"
    # ),
    # 注释原因: bo_weekly 接口暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="bo_weekly",
    #     description="电影周度票房",
    #     chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需date参数，改为DATE分块
    #     date_field="date",
    #     category="industry"
    # ),
    # 注释原因: bo_daily 接口参数错误（需要date参数），暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="bo_daily",
    #     description="电影日度票房",
    #     chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需date参数，改为DATE分块
    #     date_field="date",
    #     category="industry"
    # ),
    # 注释原因: bo_cinema 接口参数错误（需要date参数），暂时禁用，以后可能会用到
    # APIConfig(
    #     api_name="bo_cinema",
    #     description="影院日度票房",
    #     chunk_strategy=ChunkStrategy.DATE,  # 修复: 必需date参数，改为DATE分块
    #     date_field="date",
    #     category="industry"
    # ),
    # 以下API不存在于tushare文档中，已禁用
    # APIConfig(
    #     api_name="film_record",
    #     description="全国电影剧本备案数据（API不存在）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="ann_date",
    #     category="industry",
    #     enabled=False
    # ),
    # APIConfig(
    #     api_name="tv_record",
    #     description="全国电视剧备案公示数据（API不存在）",
    #     chunk_strategy=ChunkStrategy.YEAR,
    #     date_field="ann_date",
    #     category="industry",
    #     enabled=False
    # ),
]

# =====================================================
# 现货数据
# =====================================================
SPOT_APIS = [
    APIConfig(
        api_name="sge_basic",
        description="上海黄金交易所基础信息",
        chunk_strategy=ChunkStrategy.NONE,
        category="spot"
    ),
    APIConfig(
        api_name="sge_daily",
        description="上海黄金交易所日行情",
        chunk_strategy=ChunkStrategy.YEAR,
        date_field="trade_date",
        category="spot"
    ),
]

# =====================================================
# 汇总所有 API
# =====================================================
ALL_APIS: List[APIConfig] = (
    STOCK_BASIC_APIS +
    STOCK_QUOTE_APIS +
    STOCK_FINANCE_APIS +
    STOCK_REFERENCE_APIS +
    STOCK_SPECIAL_APIS +
    STOCK_MARGIN_APIS +
    STOCK_MONEYFLOW_APIS +
    STOCK_BILLBOARD_APIS +
    STOCK_CONCEPT_APIS +
    INDEX_APIS +
    ETF_APIS +
    FUTURES_APIS +
    OPTIONS_APIS +
    CB_APIS +
    FX_APIS +
    HK_APIS +
    US_APIS +
    MACRO_APIS +
    NEWS_APIS +
    INDUSTRY_APIS +
    SPOT_APIS
)

# API 名称到配置的映射
API_REGISTRY: Dict[str, APIConfig] = {api.api_name: api for api in ALL_APIS}


# =====================================================
# 量化回测核心接口
# =====================================================
QUANT_APIS = [
    "stock_basic",      # 股票列表
    "trade_cal",        # 交易日历
    "namechange",       # 股票曾用名
    "hs_const",         # 沪深股通成分股
    "new_share",        # IPO新股上市
    "daily",            # 日线行情
    "adj_factor",       # 复权因子
    "suspend_d",        # 每日停复牌信息
    "daily_basic",      # 每日指标
    "stk_limit",        # 每日涨跌停价格
    "moneyflow",        # 个股资金流向
    "fina_indicator",   # 财务指标数据
    "income",           # 利润表
    "balancesheet",     # 资产负债表
    "cashflow",         # 现金流量表
    "forecast",         # 业绩预告
    "express",          # 业绩快报
    "disclosure_date",  # 财报披露日期表
    "dividend",         # 分红送股
    "share_float",      # 限售股解禁
    "top10_holders",    # 前十大股东
    "stk_holdernumber", # 股东人数
    "index_daily",      # 指数日线行情
    "index_weight",     # 指数成分和权重
    "daily_info",       # 沪深市场每日交易统计
    "hsgt_top10",       # 沪深股通十大成交股
    "margin",           # 融资融券交易汇总
    "margin_detail",    # 融资融券交易明细
    "top_list",         # 龙虎榜每日明细
    "cyq_perf",         # 每日筹码及胜率
]


def get_api_config(api_name: str) -> Optional[APIConfig]:
    """获取 API 配置"""
    return API_REGISTRY.get(api_name)


def get_apis_by_category(category: str) -> List[APIConfig]:
    """按类别获取 API 列表"""
    return [api for api in ALL_APIS if api.category == category]


def get_enabled_apis() -> List[APIConfig]:
    """获取所有启用的 API"""
    return [api for api in ALL_APIS if api.enabled]


def get_apis_by_priority(priority: int) -> List[APIConfig]:
    """按优先级获取 API 列表"""
    return [api for api in ALL_APIS if api.priority == priority and api.enabled]


def get_all_categories() -> List[str]:
    """获取所有类别"""
    return list(set(api.category for api in ALL_APIS))


# 打印统计信息
if __name__ == "__main__":
    print(f"Total APIs: {len(ALL_APIS)}")
    print(f"Enabled APIs: {len(get_enabled_apis())}")
    print("\nAPIs by category:")
    for cat in sorted(get_all_categories()):
        apis = get_apis_by_category(cat)
        print(f"  {cat}: {len(apis)}")
