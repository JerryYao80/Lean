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

    数据缺失时的行为（fail_closed 参数）：
    - fail_closed=True（默认）：数据缺失 → 拦截 Insight（fail-closed）。
      下行保护机制在数据质量下降时应自动收紧，而非失效（cmf-audit2.md 第3点）。
      原设计 fail-open（放行）等于"数据质量下降时风险控制自动失效"。
    - fail_closed=False：保留旧行为（缺数据保守保留），用于回测对齐历史。

    数据基础: cyq_perf。
    """

    def __init__(self, data_folder: str, params: dict = None, fail_closed: bool = True):
        super().__init__()
        self.data_folder = data_folder
        self.params = params or ChipPeakFactors.DEFAULT_PARAMS.copy()
        self.fail_closed = fail_closed
        self.loader = ChipDataLoader(data_folder, max_batch_size=50)

    def manage_risk(self, algorithm: QCAlgorithm, insights: list) -> list:
        """过滤掉派发区 (HIGH_SINGLE_PEAK) 股票的 Insight。

        方法名必须为 manage_risk (snake_case of C# ManageRisk)，
        否则框架调用基类抛 NotImplementedException，风控不生效。
        """
        if not insights:
            algorithm.debug('[ChipPeakRisk] no insights to filter')
            return insights

        current_date = algorithm.time.strftime('%Y%m%d')
        filtered = []
        filtered_count = 0
        blocked_count = 0

        for insight in insights:
            symbol = insight.symbol
            security = algorithm.securities.get(symbol)
            if security is None:
                if self.fail_closed:
                    blocked_count += 1
                    continue
                filtered.append(insight)
                continue
            ts_code = self._symbol_to_ts_code(symbol)
            chip_row = self.loader.load_single(ts_code, current_date)
            if chip_row is None:
                if self.fail_closed:
                    blocked_count += 1
                    continue
                filtered.append(insight)
                continue
            pattern = ChipPeakFactors.classify_peak(chip_row, float(security.price), self.params)
            if pattern == PeakPattern.HIGH_SINGLE_PEAK:
                algorithm.debug(f'[ChipPeakRisk] filtered {symbol} (HIGH_SINGLE_PEAK 派发区)')
                filtered_count += 1
                continue
            # DIVERGENT/LOW_SINGLE_PEAK 且数据完整 → 保留
            filtered.append(insight)

        if filtered_count > 0:
            algorithm.debug(f'[ChipPeakRisk] filtered {filtered_count}/{len(insights)} insights (HIGH_SINGLE_PEAK)')
        if blocked_count > 0:
            algorithm.debug(f'[ChipPeakRisk] blocked {blocked_count}/{len(insights)} insights (fail-closed: missing cyq_perf)')

        return filtered

    @staticmethod
    def _symbol_to_ts_code(symbol: Symbol) -> str:
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6') or t.startswith('51'):
            return f'{t}.SH'
        return f'{t}.SZ'
