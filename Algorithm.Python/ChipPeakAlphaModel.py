# Algorithm.Python/ChipPeakAlphaModel.py
from AlgorithmImports import *
import importlib.util
import sys
from pathlib import Path

# 加载 ChipPeakFactors (Algorithm.Python 目录名含点，无法作为包导入)
_ROOT = Path(__file__).resolve().parent.parent
_FACTORS_PATH = _ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'
_spec = importlib.util.spec_from_file_location('ChipPeakFactors', _FACTORS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ChipPeakFactors = _mod.ChipPeakFactors

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from ToolBox.ChipDataLoader import ChipDataLoader


class ChipPeakAlphaModel(AlphaModel):
    """
    筹码峰 Alpha：流式扫描全A股，低位单峰密集者得正 Insight。

    内存受限：分批 500 只，chip_row 在循环内用完即释放。
    数据基础: cyq_perf (筹码及胜率)。
    """

    INSIGHT_HORIZON_DAYS = 7   # 周频，insight有效期7天
    TOP_N = 20
    BATCH_SIZE = 500

    def __init__(self, data_folder: str, top_n: int = 20,
                 batch_size: int = 500, params: dict = None):
        super().__init__()
        self.data_folder = data_folder
        self.top_n = int(top_n)
        self.batch_size = int(batch_size)
        self.params = params or ChipPeakFactors.DEFAULT_PARAMS.copy()
        self.loader = ChipDataLoader(data_folder, max_batch_size=batch_size)

    def on_securities_changed(self, algorithm: QCAlgorithm, changes: SecurityChanges):
        # 筹码 Alpha 无需响应 universe 变化：update() 时按当前 active_securities 流式扫描
        pass

    def update(self, algorithm: QCAlgorithm, data: Slice) -> list:
        """流式扫描：分批加载 cyq_perf → 计算得分 → Top-N → Insight

        数据流：ChipDataLoader.load_batch yields (ts_code, pd.Series)
              chip_row 即该 Series（单行），用完即丢以控制内存。
        """
        current_date = algorithm.time.strftime('%Y%m%d')
        candidates = list(algorithm.active_securities.keys())
        if len(candidates) == 0:
            return []

        # 映射 symbol <-> ts_code
        symbol_to_code = {}
        code_to_symbol = {}
        for sym in candidates:
            ts_code = self._symbol_to_ts_code(sym)
            symbol_to_code[sym] = ts_code
            code_to_symbol[ts_code] = sym

        scores = {}
        codes = [symbol_to_code[s] for s in candidates]

        # 分批流式处理（内存控制核心）
        for i in range(0, len(codes), self.batch_size):
            batch_codes = codes[i:i + self.batch_size]
            # load_batch 内部按 batch 读取 cyq_perf，yield 单行 Series
            for ts_code, chip_row in self.loader.load_batch(batch_codes, current_date):
                if chip_row is None:
                    continue
                symbol = code_to_symbol.get(ts_code)
                if symbol is None:
                    continue
                security = algorithm.securities.get(symbol)
                if security is None:
                    continue
                current_price = float(security.price)
                # chip_row 是 pd.Series，传给标量 API
                score = ChipPeakFactors.composite_score(chip_row, current_price, self.params)
                if score > 0:
                    scores[symbol] = score
                # chip_row 在循环结束后自动释放

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:self.top_n]
        insights = []
        for symbol, score in ranked:
            # magnitude 必须设置：TopNEqualWeightPCM 过滤 magnitude is None 的 insight
            insights.append(
                Insight.price(symbol, timedelta(days=self.INSIGHT_HORIZON_DAYS),
                              InsightDirection.UP, float(score), None)
            )
        algorithm.debug(f'[ChipPeakAlpha] scanned {len(scores)}, emitted {len(insights)} insights')
        return insights

    @staticmethod
    def _symbol_to_ts_code(symbol: Symbol) -> str:
        """LEAN Symbol → tushare ts_code
        A股规则: 6xx (上交所股票) 与 51x (上交所ETF) → .SH；其余 (含 0xx/3xx/15x ETF) → .SZ
        """
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6') or t.startswith('51'):
            return f'{t}.SH'
        return f'{t}.SZ'
