# Algorithm.Python/AShareUniverseSelectionModel.py
from AlgorithmImports import *
from pathlib import Path
import os


class AShareUniverseSelectionModel(UniverseSelectionModel):
    """
    全A股 + ETF Universe Selection.
    硬筛选: cyq_perf 数据目录存在（数据可用性）。
    注册时套用 A 股本地化模型 (Fee/Fill/BuyingPower/Settlement)。
    """

    def __init__(self, data_folder: str, include_etf: bool = True):
        self.data_folder = Path(data_folder)
        self.include_etf = include_etf
        self._all_codes = None

    def create_universes(self, algorithm: QCAlgorithm):
        codes = self._load_all_codes(algorithm)
        symbols = []
        for code in codes:
            ticker = code.split('.')[0]
            market = Market.SSE if code.endswith('.SH') else Market.SZSE
            try:
                sec = algorithm.add_equity(ticker, Resolution.DAILY, market)
            except Exception as e:
                algorithm.debug(f'[AShareUniverse] skip {code}: {e}')
                continue
            symbol = sec.symbol
            s = algorithm.securities[symbol]
            s.set_fee_model(AShareStockFeeModel())
            s.set_fill_model(AShareStockFillModel())
            s.set_buying_power_model(AShareStockBuyingPowerModel())
            if self._is_etf(code):
                s.set_settlement_model(ImmediateSettlementModel())
            else:
                s.set_settlement_model(DelayedSettlementModel(1, timedelta(hours=9)))
            symbols.append(symbol)
        algorithm.log(f'[AShareUniverse] loaded {len(symbols)} symbols')
        # 全部 symbol 已通过 add_equity 显式注册并套用 A 股本地化模型，
        # 作为手动订阅证券永久存活，无需再返回 UserDefinedUniverse（其构造
        # 在本 LEAN 版本需 SubscriptionDataConfig，无法直接由 list 构造）。
        # AlphaModel 通过 algorithm.active_securities 访问全部已注册标的。
        return []

    def _load_all_codes(self, algorithm: QCAlgorithm) -> list:
        if self._all_codes is not None:
            return self._all_codes
        cyq_path = self.data_folder / 'cyq_perf'
        if not cyq_path.exists():
            algorithm.log(f'[AShareUniverse] cyq_perf path not found: {cyq_path}')
            return []
        codes = []
        for d in os.listdir(cyq_path):
            if '=' in d:
                ts_code = d.split('=')[1]
                if not self.include_etf and self._is_etf(ts_code):
                    continue
                codes.append(ts_code)
        self._all_codes = sorted(codes)
        algorithm.log(f'[AShareUniverse] discovered {len(self._all_codes)} codes with cyq_perf data')
        return self._all_codes

    @staticmethod
    def _is_etf(ts_code: str) -> bool:
        ticker = ts_code.split('.')[0]
        return ticker.startswith('51') or ticker.startswith('15')
