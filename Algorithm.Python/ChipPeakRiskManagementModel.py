# Algorithm.Python/ChipPeakRiskManagementModel.py
from AlgorithmImports import *
import importlib.util
import sys
from pathlib import Path

# 加载 ChipPeakFactors (Algorithm.Python 目录名含点)
_ROOT = Path(__file__).resolve().parent.parent
_FACTORS_PATH = _ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'
_spec = importlib.util.spec_from_file_location('ChipPeakFactors', _FACTORS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ChipPeakFactors = _mod.ChipPeakFactors
PeakPattern = _mod.PeakPattern

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from ToolBox.ChipDataLoader import ChipDataLoader


class ChipPeakRiskManagementModel(RiskManagementModel):
    """
    筹码峰风控：派发区过滤。
    仅检查已持仓/待建的 Insight (≤TOP_N 只)，惰性 load_single，内存压力小。
    HIGH_SINGLE_PEAK (派发区) → 砍 Insight（阻止新建仓，符合 LEAN 风控语义）。
    缺数据时保留 Insight（保守不误杀）。
    数据基础: cyq_perf。
    """

    def __init__(self, data_folder: str, params: dict = None):
        super().__init__()
        self.data_folder = data_folder
        self.params = params or ChipPeakFactors.DEFAULT_PARAMS.copy()
        self.loader = ChipDataLoader(data_folder, max_batch_size=50)

    def manage(self, algorithm: QCAlgorithm, insights: list) -> list:
        """过滤掉派发区 (HIGH_SINGLE_PEAK) 股票的 Insight"""
        if not insights:
            return insights

        current_date = algorithm.time.strftime('%Y%m%d')
        filtered = []

        for insight in insights:
            symbol = insight.symbol
            security = algorithm.securities.get(symbol)
            if security is None:
                filtered.append(insight)
                continue
            ts_code = self._symbol_to_ts_code(symbol)
            chip_row = self.loader.load_single(ts_code, current_date)
            if chip_row is None:
                filtered.append(insight)  # 缺数据保守保留
                continue
            pattern = ChipPeakFactors.classify_peak(chip_row, float(security.price), self.params)
            if pattern == PeakPattern.HIGH_SINGLE_PEAK:
                algorithm.debug(f'[ChipPeakRisk] filtered {symbol} (HIGH_SINGLE_PEAK 派发区)')
                continue
            filtered.append(insight)
        return filtered

    @staticmethod
    def _symbol_to_ts_code(symbol: Symbol) -> str:
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6') or t.startswith('51'):
            return f'{t}.SH'
        return f'{t}.SZ'
