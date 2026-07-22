using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models.Risk;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// 黄金 ETF (518880) 隔夜溢价 T+0 日内策略。
    /// Z > 1.5 开盘做多，ATR 止损，0.6×|Gap_expected| 止盈，14:45 强制平仓。
    /// long-only（518880 不可做空）。regime=RISING_FAST 仓位上限 0.3×。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md。
    /// </summary>
    public class GoldOvernightPremiumAlgorithm : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private const int LotSize = 100;
        private Symbol _gold;
        private Symbol _signalSym;
        private GoldOvernightPremiumFactor _zFactor;
        private GoldRealRateRegimeFactor _regimeFactor;
        private decimal _positionSize;
        private decimal _targetVol;
        private int _volLookbackDays;
        private decimal _atrMultiple;
        private decimal _tpFraction;
        private decimal _zThreshold;
        private decimal _initialCapital;
        private readonly List<decimal> _dailyEquity = new();
        private decimal _entryPrice;
        private decimal _stopLoss;
        private decimal _takeProfit;

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1_000_000m);
            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2026, 6, 23)));
            SetBenchmark(x => 0m);

            _positionSize = GetDecimalParameter("position-size", 0.30m);
            _targetVol = GetDecimalParameter("target-vol", 0.12m);
            _volLookbackDays = GetIntParameter("vol-lookback-days", 60);
            _atrMultiple = GetDecimalParameter("atr-multiple", 0.5m);
            _tpFraction = GetDecimalParameter("tp-fraction", 0.6m);
            _zThreshold = GetDecimalParameter("z-threshold", 1.5m);

            var gold = AddEquity("518880", Resolution.Daily, Market.SSE);
            gold.FeeModel = new AShareStockFeeModel();
            gold.FillModel = new AShareStockFillModel();
            gold.BuyingPowerModel = new AShareStockBuyingPowerModel();
            gold.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
            _gold = gold.Symbol;

            _signalSym = AddData<GoldOvernightSignal>("GOLD_OVN", Resolution.Daily).Symbol;

            _zFactor = new GoldOvernightPremiumFactor();
            _regimeFactor = new GoldRealRateRegimeFactor();

            // 风控链：14:45 硬平仓 + 仓位上限
            AddRiskManagement(new NoOvernightPositionRiskModel());
            AddRiskManagement(new PositionLimitRiskModel(_positionSize));

            // SSE 市场日历在 LEAN 中配置为 00:00-24:00（全天），AfterMarketOpen/BeforeMarketClose 无法定位具体开收盘。
            // 改用 TimeRules.At 显式指定北京时间：09:35 开盘后 5 分钟评估进场，14:45 强制平仓。
            Schedule.On(DateRules.EveryDay(_gold), TimeRules.At(9, 35, TimeZones.Shanghai), EvaluateEntry);
            Schedule.On(DateRules.EveryDay(_gold), TimeRules.At(14, 45, TimeZones.Shanghai), ForceCloseCheck);
        }

        public override void OnData(Slice data)
        {
            if (data.ContainsKey(_signalSym))
            {
                var sig = data.Get<GoldOvernightSignal>(_signalSym);
                if (sig != null)
                {
                    _zFactor.InjectValue(_gold, sig.ZSignal);
                    if (Enum.TryParse<GoldRegime>(sig.Regime, out var regime))
                        _regimeFactor.InjectRegime(_gold, regime);
                }
            }
            _dailyEquity.Add(Portfolio.TotalPortfolioValue);
        }

        private void EvaluateEntry()
        {
            var z = _zFactor.Compute(_gold, Time).Value;
            var regime = (GoldRegime)(int)_regimeFactor.Compute(_gold, Time).Value;
            var dir = EntryDirection(z);
            if (dir != 1) return;  // long-only

            var cap = RegimeCap(regime);
            var price = Securities[_gold].Price;
            if (price <= 0) return;

            var realizedVol = ComputeAnnualizedVol(DailyReturns(_dailyEquity, _volLookbackDays));
            var volScale = realizedVol > 0.01m ? Math.Max(0.25m, Math.Min(2.0m, _targetVol / realizedVol)) : 1m;
            var alloc = _positionSize * volScale * cap;
            var qty = (int)(Math.Floor(_initialCapital * alloc / (price * LotSize)) * LotSize);
            if (qty >= LotSize)
            {
                MarketOrder(_gold, qty);
                _entryPrice = price;
                _stopLoss = price - _atrMultiple * ATR(_gold, 14);
                var gapExp = _zFactor.Compute(_gold, Time).RawValue;
                _takeProfit = price + _tpFraction * Math.Abs(gapExp);
            }
        }

        private void ForceCloseCheck()
        {
            // 14:45 由 NoOvernightPositionRiskModel 强制；此处只做止盈止损
            if (!Portfolio.Invested) return;
            var price = Securities[_gold].Price;
            if (price <= _stopLoss || price >= _takeProfit)
                Liquidate(_gold);
        }

        /// <summary>Z > threshold → 1 (多)；Z < -threshold → 0 (观望，不可做空)；否则 0。</summary>
        public static int EntryDirection(decimal z) => z > 1.5m ? 1 : 0;

        /// <summary>regime=RISING_FAST → 0.3×；其余（含 UNAVAILABLE）→ 1.0×。</summary>
        public static decimal RegimeCap(GoldRegime regime) =>
            regime == GoldRegime.RISING_FAST ? 0.3m : 1.0m;

        public static decimal ComputeAnnualizedVol(IReadOnlyList<decimal> dailyReturns)
        {
            if (dailyReturns.Count < 5) return 0.2m;
            var mean = dailyReturns.Average();
            var variance = dailyReturns.Select(r => (r - mean) * (r - mean)).Average();
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
        }

        public static List<decimal> DailyReturns(IReadOnlyList<decimal> equity, int lookback)
        {
            var returns = new List<decimal>();
            var start = Math.Max(1, equity.Count - lookback);
            for (int i = start; i < equity.Count; i++)
                if (equity[i - 1] > 0) returns.Add(equity[i] / equity[i - 1] - 1m);
            return returns;
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[GoldOvernightPremium] Final equity: {Portfolio.TotalPortfolioValue:C2}");
        }

        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "position-size", "target-vol", "vol-lookback-days", "z-threshold",
            "atr-multiple", "tp-fraction"
        };

        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new { sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv, pnl_1d = 0m, days_held = 0 }).ToList();
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "GoldOvernightPremiumAlgorithm",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                drawdown = 0m, n_open_positions = positions.Count,
                z_signal = _zFactor.Compute(_gold, algo.Time).Value,
                regime = ((GoldRegime)(int)_regimeFactor.Compute(_gold, algo.Time).Value).ToString()
            });
        }

        private decimal GetDecimalParameter(string name, decimal def) =>
            decimal.TryParse(GetParameter(name), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : def;
        private int GetIntParameter(string name, int def) =>
            int.TryParse(GetParameter(name), out var v) ? v : def;
        private DateTime GetDateParameter(string name, DateTime def) =>
            DateTime.TryParse(GetParameter(name), CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : def;
    }
}
