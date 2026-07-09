using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Factors.Forward;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// gold2 Beta+波动率目标策略。五层 Framework: ManualUniverse(518880) + TrendAlpha + VolTargetPortfolio
    /// + ExtremeRisk/RealRateCap Risk + AShareLotSize Execution。beta 暴露+风险叠加,不追 alpha。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md。
    /// </summary>
    public class Gold2BetaVolTargetStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private Symbol _gold, _auSym, _vixSym, _dfii10Sym;
        private Gold2TrendFactor _trend;
        private Gold2VolRegimeFactor _vol;
        private Gold2ExtremeRiskFactor _ext;
        private Gold2RealRateCapFactor _realrate;
        private GoldRealRateRegimeFactor _realrateInner;
        // RVol_60d window is fixed at 60 days per spec §4.3, decoupled from vol-warmup
        // (which seeds the EWMA variance). Tuning vol-warmup must NOT silently resize RVol.
        private const int RvolWindow = 60;
        // Simplified DFII10 regime thresholds (完整 5d/20d 斜率留作已知局限,spec §4.4)。
        // ±0.5 are not in the spec's tunable table; kept as named constants for clarity.
        private const decimal RealRateRisingFastThreshold = 0.5m;
        private const decimal RealRateFallingFastThreshold = -0.5m;
        private readonly Queue<decimal> _rvolReturns = new();

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetCash(GetDecimalParameter("initial-capital", 1_000_000m));
            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2026, 6, 23)));

            // SetBenchmark must be called AFTER AddEquity so SymbolCache resolves "518880"
            // to the SSE symbol; otherwise QCAlgorithm.SetBenchmark(string) falls through
            // to Symbol.Create("518880", Equity, Market.USA) and the benchmark security
            // has no A-share data → flat-0 benchmark → wrong Beta/Alpha/IR stats.
            var eq = AddEquity("518880", Resolution.Daily, Market.SSE);
            eq.FeeModel = new AShareStockFeeModel();
            eq.FillModel = new AShareStockFillModel();
            eq.BuyingPowerModel = new AShareStockBuyingPowerModel();
            eq.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
            _gold = eq.Symbol;
            SetBenchmark(_gold);

            _auSym = AddData<AuShfDailyBar>("AU.SHF", Resolution.Daily).Symbol;
            _vixSym = AddData<FredMacroData>("VIX", Resolution.Daily).Symbol;
            _dfii10Sym = AddData<FredMacroData>("DFII10", Resolution.Daily).Symbol;

            _trend = new Gold2TrendFactor(GetIntParameter("trend-ma-short", 20), GetIntParameter("trend-ma-long", 120));
            _vol = new Gold2VolRegimeFactor(GetDecimalParameter("ewma-lambda", 0.94m),
                GetDecimalParameter("vol-target", 0.11m), GetIntParameter("vol-warmup", 60),
                GetDecimalParameter("smooth-alpha", 0.25m));
            _ext = new Gold2ExtremeRiskFactor();
            _realrateInner = new GoldRealRateRegimeFactor();
            _realrate = new Gold2RealRateCapFactor(_realrateInner, GetDecimalParameter("realrate-cap", 0.6m));

            SetUniverseSelection(new Gold2UniverseSelectionModel());
            SetAlpha(new Gold2TrendAlphaModel(_trend, _gold, GetDecimalParameter("trend-floor", 0.2m)));
            SetPortfolioConstruction(new Gold2VolTargetPortfolioModel(_vol, _gold, GetDecimalParameter("rebalance-threshold", 0.05m)));
            AddRiskManagement(new Gold2ExtremeRiskModel(_ext, _gold, GetDecimalParameter("extreme-vol-cap", 0.3m)));
            AddRiskManagement(new Gold2RealRateCapModel(_realrate, _gold));
            SetExecution(new AShareLotSizeExecutionModel());
        }

        public override void OnData(Slice data)
        {
            if (data.Bars.TryGetValue(_gold, out var bar518880))
            {
                _trend.Update518880(bar518880.Close, bar518880.EndTime);
                _vol.Update(bar518880.Close, bar518880.EndTime);
                // RVol uses intraday open→close log return (excludes overnight gaps, by design:
                // RVol_60d is meant as a session-volatility extreme-risk trigger distinct from
                // the close→close EWMA vol in Gold2VolRegimeFactor). See spec §4.3.
                if (bar518880.Open > 0)
                    _rvolReturns.Enqueue((decimal)Math.Log((double)(bar518880.Close / bar518880.Open)));
                while (_rvolReturns.Count > RvolWindow) _rvolReturns.Dequeue();
                if (_rvolReturns.Count >= RvolWindow)
                {
                    var variance = Gold2VolRegimeFactor.Variance(_rvolReturns);
                    var rvol = (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
                    _ext.UpdateRvol60(rvol, bar518880.EndTime);
                }
            }
            if (data.ContainsKey(_auSym))
            {
                var auBar = data.Get<AuShfDailyBar>(_auSym);
                if (auBar != null) _trend.UpdateAu(auBar.Close, auBar.EndTime);
            }
            if (data.ContainsKey(_vixSym))
            {
                var vix = data.Get<FredMacroData>(_vixSym);
                if (vix != null) _ext.UpdateVix(vix.MacroValue, vix.EndTime);
            }
            if (data.ContainsKey(_dfii10Sym))
            {
                var dfii10 = data.Get<FredMacroData>(_dfii10Sym);
                if (dfii10 != null) _realrateInner.InjectRegime(_gold, ClassifyRealRateRegime(dfii10.MacroValue));
            }
        }

        private static GoldRegime ClassifyRealRateRegime(decimal dfii10)
        {
            // 简化阈值分类(完整 5d/20d 斜率留作已知局限,spec §4.4 包装已有 inner)。
            // ±0.5 是 named constants(见字段),非 spec tunable table 参数;Gold2RealRateCapFactor
            // 仅在 RISING_FAST 时降仓,其余 regime 均 cap=1.0,故 STABLE/DRIFTING 行为等价。
            if (dfii10 > RealRateRisingFastThreshold) return GoldRegime.RISING_FAST;
            if (dfii10 < RealRateFallingFastThreshold) return GoldRegime.FALLING_FAST;
            return GoldRegime.STABLE;
        }

        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "trend-ma-short","trend-ma-long","ewma-lambda","vol-target","vol-warmup",
            "smooth-alpha","rebalance-threshold","extreme-vol-cap","realrate-cap","trend-floor"
        };

        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new { sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv }).ToList();
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "Gold2BetaVolTargetStrategy",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                w_smooth = _vol.Compute(_gold, algo.Time).Value,
                trend_dir = (int)_trend.Compute(_gold, algo.Time).Value,
                extreme_triggered = (int)_ext.Compute(_gold, algo.Time).Value == 1,
                realrate_cap = _realrate.Compute(_gold, algo.Time).Value
            });
        }

        private decimal GetDecimalParameter(string n, decimal d) =>
            decimal.TryParse(GetParameter(n), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : d;
        private int GetIntParameter(string n, int d) =>
            int.TryParse(GetParameter(n), out var v) ? v : d;
        private DateTime GetDateParameter(string n, DateTime d) =>
            DateTime.TryParse(GetParameter(n), CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : d;
    }
}
