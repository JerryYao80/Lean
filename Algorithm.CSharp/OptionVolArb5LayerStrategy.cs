/*
 * 期权波动率套利策略 — 真正的五层架构版.
 *
 * LEAN Framework 五层:
 *   Layer 1 (Universe):     OptionVolArbUniverseSelectionModel (数据可用性门控选股)
 *   Layer 2 (Alpha):        OptionVolArbFactorZooAlphaModel (IV-RV z-score / IVTS / Skew 多因子)
 *   Layer 3 (Portfolio):    EqualWeightPortfolioModel (等权组合)
 *   Layer 4 (Risk):         CompositeRiskModel.FromVaR() (VaR → MaxDrawdown → PositionLimit 三级风控链)
 *   Layer 5 (Execution):    ImmediateExecutionModel (立即执行)
 *
 * 候选池: A股 ETF 期权标的 (510050/510300/510500/588000/588080)
 * 选股逻辑: L1 审计数据可用性 → 仅保留现货+IV 数据完整的标的
 * 账户方案: USD==CNY 1:1 (零 FX 数据依赖, 避免原生引擎崩溃)
 *
 * 不以 AShare 开头 — 符合用户命名约定.
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Data; // for Globals
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection; // for UniverseSettings
using QuantConnect.Factors.Risk;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Execution;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.CSharp.Models.Universe;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Orders.Fees;
using QuantConnect.Risk.VaR;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// 期权波动率套利策略 (五层架构版).
    /// 彻底解耦命令式 OnData → LEAN Framework 分层模块.
    /// </summary>
    public class OptionVolArb5LayerStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private OptionVolArbUniverseSelectionModel _universeModel;
        private List<string> _candidateTickers;

        // RL state tracking (auto-update2.md 第一步a: 真实 var_1d99/var_regime/drawdown/pnl_1d)
        private VaRFactor _varFactor;
        private decimal _peakTpv;
        private DateTime _peakDate;
        private readonly Dictionary<Symbol, decimal> _prevClose = new();
        private readonly Dictionary<Symbol, int> _entryDay = new();
        private int _dayIndex = 0;
        // RL risk model 引用 (rl mode 下用于读取当期实际 alpha, 写入 trace 供离线 RL 训练)
        private RlRiskModel _rlRiskModel;

        public override void Initialize()
        {
            // Universe 候选池: 默认 5 个 A股 ETF 期权标的, 可经 "universe-tickers" 参数覆盖
            // 支持任意子集 / 任意提供 IV 数据的标的 (由 L1 门控自动剔除无数据者)
            var tickersParam = GetParameterOrDefault("universe-tickers", "510050,510300,510500,588000,588080");
            _candidateTickers = tickersParam.Split(',')
                .Select(t => t.Trim())
                .Where(t => t.Length > 0)
                .ToList();

            // 回测窗口: "auto" => 取入选标的 IV 交集窗口 (最长可用跨度);
            // 否则解析 "universe-start"/"universe-end" 显式日期 (yyyy-MM-dd)
            var windowMode = GetParameterOrDefault("universe-window", "auto");
            DateTime? explicitStart = null, explicitEnd = null;
            if (windowMode != "auto")
            {
                var s = GetParameterOrDefault("universe-start", "");
                var e = GetParameterOrDefault("universe-end", "");
                if (DateTime.TryParse(s, out var sd)) explicitStart = sd;
                if (DateTime.TryParse(e, out var ed)) explicitEnd = ed;
            }

            // 先用 null 窗口做一次选股门控, 拿到入选标的 + IV 交集窗口
            _universeModel = new OptionVolArbUniverseSelectionModel(
                candidateTickers: _candidateTickers,
                startDate: null,
                endDate: null,
                market: Market.SSE,
                universeSettings: new UniverseSettings(Resolution.Daily, 1m, false, false, TimeSpan.Zero));

            DateTime backtestStart, backtestEnd;
            if (windowMode == "auto")
            {
                if (!_universeModel.IvWindowStart.HasValue || !_universeModel.IvWindowEnd.HasValue)
                    throw new InvalidOperationException(
                        $"L1 Universe 自动窗口失败: 候选 {_candidateTickers.Count} 标的 IV 交集为空 (检查 IV csv 是否存在)");
                backtestStart = _universeModel.IvWindowStart.Value;
                backtestEnd = _universeModel.IvWindowEnd.Value;
            }
            else
            {
                // 显式窗口: 重新用窗口做 Gate3 校验, 剔除覆盖不全的标的
                _universeModel = new OptionVolArbUniverseSelectionModel(
                    candidateTickers: _candidateTickers,
                    startDate: explicitStart,
                    endDate: explicitEnd,
                    market: Market.SSE,
                    universeSettings: new UniverseSettings(Resolution.Daily, 1m, false, false, TimeSpan.Zero));
                if (!_universeModel.SelectedSymbols.Any())
                    throw new InvalidOperationException("L1 Universe 选股失败: 所有候选标的被显式窗口剔除");
                backtestStart = explicitStart ?? _universeModel.IvWindowStart ?? new DateTime(2024, 2, 8);
                backtestEnd = explicitEnd ?? _universeModel.IvWindowEnd ?? new DateTime(2024, 6, 18);
            }

            SetStartDate(backtestStart);
            SetEndDate(backtestEnd);

            // 账户方案: USD==CNY 1:1 (零 FX 数据依赖)
            // 不做外汇, 没有 USDCNY 汇率数据 → 只关心数值, 标签不重要
            SetCash(1000000);
            Portfolio.CashBook.Add(Currencies.CNY, 0m, 1.0m);

            SetTimeZone("Asia/Shanghai");
            SetBenchmark(x => 0);

            // Disable minimum order size check (allow small positions)
            Settings.MinimumOrderMarginPortfolioPercentage = 0;

            // === LEAN Native Five-Layer Architecture ===

            // Layer 1: Universe Selection — 数据可用性门控选股 (已在上方构造)
            SetUniverseSelection(_universeModel);

            // Audit: 打印选股结果 + 自动窗口
            Log($"[OptionVolArb-5Layer] L1 Universe 选股结果 (候选 {_candidateTickers.Count}, 入选 {_universeModel.SelectedSymbols.Count}):");
            Log($"  回测窗口 ({windowMode}): {backtestStart:yyyy-MM-dd} ~ {backtestEnd:yyyy-MM-dd} ({(backtestEnd - backtestStart).TotalDays:0} 天)");
            if (_universeModel.IvWindowStart.HasValue)
                Log($"  IV 交集窗口: {_universeModel.IvWindowStart:yyyy-MM-dd} ~ {_universeModel.IvWindowEnd:yyyy-MM-dd}");
            foreach (var sym in _universeModel.SelectedSymbols)
                Log($"  ✓ {sym.Value} (入选)");
            foreach (var drop in _universeModel.DroppedSymbols)
                Log($"  ✗ {drop.Ticker} (剔除: {drop.Reason})");

            if (_universeModel.SelectedSymbols.Count == 0)
                throw new InvalidOperationException("L1 Universe 选股失败: 所有候选标的被剔除");

            // Layer 2: Alpha — IV-RV z-score / IVTS / Skew 多因子
            SetAlpha(new OptionVolArbFactorZooAlphaModel(
                ivRvZScoreThreshold: GetDecimalParameter("iv-rv-z-score-threshold", 2.0m),
                ivtsThreshold: GetDecimalParameter("ivts-threshold", 1.3m),
                skewPercentileHigh: GetDecimalParameter("skew-percentile-high", 0.90m),
                skewPercentileLow: GetDecimalParameter("skew-percentile-low", 0.10m)));

            // Layer 3: Portfolio Construction — 等权组合
            SetPortfolioConstruction(new EqualWeightPortfolioModel());

            // Layer 4: Risk Management — VaR → MaxDrawdown → PositionLimit 三级风控链
            // risk-mode: "composite" (default, CompositeRiskModel.FromVaR) | "rl" (RlRiskModel ZeroMQ IPC)
            var riskMode = GetParameterOrDefault("risk-mode", "composite");
            if (riskMode == "rl")
            {
                _rlRiskModel = new RlRiskModel(this, new RlRiskConfig
                {
                    Endpoint = GetParameterOrDefault("rl-server-endpoint", "tcp://127.0.0.1:5555"),
                    PolicyName = GetParameterOrDefault("rl-policy-name", "default"),
                    FallbackAlpha = GetDecimalParameter("rl-fallback-alpha", 0.5m),
                    TimeoutMs = GetIntParameter("rl-timeout-ms", 200),
                    AlphaTracePath = GetParameterOrDefault("rl-alpha-trace-path", null),
                });
                SetRiskManagement(_rlRiskModel);
                Log("[OptionVolArb-5Layer] L4 Risk: RlRiskModel (rl mode)");
            }
            else
            {
                SetRiskManagement(CompositeRiskModel.FromVaR(
                    varBudgetFraction: GetDecimalParameter("var-budget", 0.02m),
                    maxDrawdown: GetDecimalParameter("max-drawdown", 0.20m),
                    maxPositionWeight: GetDecimalParameter("max-position-weight", 0.30m),
                    method: VaRMethod.BootstrapHistorical,
                    scenario: VaRScenario.OneDay99,
                    lookbackDays: GetIntParameter("var-lookback-days", 252)));
                Log("[OptionVolArb-5Layer] L4 Risk: CompositeRiskModel (composite mode, default)");
            }

            // Layer 5: Execution — 立即执行 (用 LEAN 原生, 避免与 Models.Execution 同名歧义)
            SetExecution(new QuantConnect.Algorithm.Framework.Execution.ImmediateExecutionModel());

            // Security initializer: 为 SSE/SZSE 标的统一设置 A股 ETF 费率/结算/保证金
            SetSecurityInitializer(new AShareETFSecurityInitializer());

            // RL state: VaRFactor for SerializeRlState (auto-update2.md 第一步a)
            // 用短窗口 (60d history / 30d minUsable) 与风控 VaR (252d) 解耦,
            // 确保短回测窗口下 RL state 也能拿到真实 var_1d99 (非 0).
            _varFactor = new VaRFactor(
                historyDays: 60,
                minUsable: 30,
                scenario: VaRScenario.OneDay99,
                method: VaRMethod.BootstrapHistorical);
            _peakTpv = Portfolio.TotalPortfolioValue;
            _peakDate = StartDate;

            SetWarmUp(60, Resolution.Daily);

            Log($"[OptionVolArb-5Layer] 五层架构初始化完成:");
            Log($"  L1 Universe: {_universeModel.SelectedSymbols.Count} 标入选");
            Log($"  L2 Alpha:    IV-RV z-score ({2.0m}) / IVTS ({1.3m}) / Skew ({0.90m}/{0.10m})");
            Log($"  L3 Portfolio: EqualWeight");
            Log($"  L4 Risk:     VaR 1D99 → MaxDrawdown 20% → PositionLimit 30%");
            Log($"  L5 Execution: Immediate");
        }

        public override void OnEndOfAlgorithm()
        {
            var totalReturn = (Portfolio.TotalPortfolioValue - 1000000m) / 1000000m;
            Log($"[OptionVolArb-5Layer] Final portfolio value: {Portfolio.TotalPortfolioValue:N2}");
            Log($"[OptionVolArb-5Layer] Total return: {totalReturn:P2}");
            Log($"[OptionVolArb-5Layer] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return decimal.TryParse(value, NumberStyles.Any,
                CultureInfo.InvariantCulture, out var parsed) ? parsed : defaultValue;
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            return int.TryParse(value, NumberStyles.Any,
                CultureInfo.InvariantCulture, out var parsed) ? parsed : defaultValue;
        }

        public override void OnData(Slice slice)
        {
            if (IsWarmingUp) return;
            _dayIndex++;
            var tpv = Portfolio.TotalPortfolioValue;
            if (tpv > _peakTpv) { _peakTpv = tpv; _peakDate = Time; }
            // auto-update2.md 第一步b: 写真实 state_trace.jsonl 供 Layer C 训练
            // 必须在更新 _prevClose 之前写, 这样 SerializeRlState 用的是昨收 → 真实 pnl_1d
            WriteRlStateTraceIfNeeded(slice.Time);
            foreach (var sym in _universeModel.SelectedSymbols)
            {
                if (slice.Bars.ContainsKey(sym))
                {
                    _prevClose[sym] = slice.Bars[sym].Close;
                }
            }
        }

        // auto-update2.md 第一步b: RL_TRACE_PATH env 触发逐 bar 状态写盘
        private string _rlTracePath;
        private void WriteRlStateTraceIfNeeded(DateTime barTime)
        {
            if (_rlTracePath == null)
            {
                _rlTracePath = Environment.GetEnvironmentVariable("RL_TRACE_PATH") ?? "";
            }
            if (string.IsNullOrEmpty(_rlTracePath)) return;
            try
            {
                var stateJson = SerializeRlState(this);
                var line = stateJson + "\n";
                File.AppendAllText(_rlTracePath, line);
            }
            catch (Exception ex)
            {
                Log($"[OptionVolArb-5Layer] RL_TRACE_PATH 写入失败: {ex.Message}");
            }
        }

        /// <summary>IOptimizableStrategy: 与 manifest.parameter_space 一致 (manifest_lint 校验).</summary>
        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "iv-rv-z-score-threshold", "ivts-threshold", "skew-percentile-high", "skew-percentile-low",
            "var-budget", "max-drawdown", "max-position-weight", "var-lookback-days"
        };

        /// <summary>IRlStateExportable: 序列化 RL 状态 JSON, 字段须与 manifest.state_schema 一致.
        /// auto-update2.md 第一步a: 真实 var_1d99/var_regime/drawdown/pnl_1d.
        /// </summary>
        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var drawdown = _peakTpv > 0 ? Math.Max(0m, (_peakTpv - tpv) / _peakTpv) : 0m;
            var daysToPeak = Math.Max(0, (algo.Time.Date - _peakDate.Date).Days);

            // 真实 VaR: 用组合内持仓最重的标的的 VaR 近似 (单标的 VaR 的代理)
            decimal var1d99 = 0m, varRegime = 0m;
            try
            {
                var holdSym = Securities.Values
                    .Where(s => s.Holdings.Quantity != 0)
                    .OrderByDescending(s => Math.Abs(s.Holdings.Quantity * s.Price))
                    .Select(s => s.Symbol)
                    .FirstOrDefault();
                if (holdSym != null)
                {
                    var hist = History<TradeBar>(holdSym, GetIntParameter("var-lookback-days", 252), Resolution.Daily).ToList();
                    var fr = _varFactor.Compute(holdSym, algo.Time, hist);
                    if (fr.Quality == QuantConnect.Factors.Core.FactorDataQuality.Valid)
                    {
                        var1d99 = fr.RawValue;        // ValueAtRisk (decimal fraction)
                        varRegime = fr.Value;         // RegimePercentile [0,1]
                    }
                    else
                    {
                        Log($"[OptionVolArb-5Layer] VaR quality={fr.Quality} for {holdSym.Value} at {algo.Time:yyyy-MM-dd}, hist.Count={hist.Count}");
                    }
                }
            }
            catch (Exception ex)
            {
                Log($"[OptionVolArb-5Layer] SerializeRlState VaR 计算失败: {ex.Message}");
            }

            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => {
                    var pnl1d = (_prevClose.TryGetValue(s.Symbol, out var pc) && pc > 0)
                        ? (s.Price - pc) / pc : 0m;
                    var daysHeld = _entryDay.TryGetValue(s.Symbol, out var ed) ? Math.Max(0, _dayIndex - ed) : 0;
                    return new {
                        sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv,
                        pnl_1d = pnl1d, days_held = daysHeld
                    };
                }).ToList();
            var state = new {
                ts = algo.Time.ToString("o"),
                strategy = "OptionVolArb5Layer",
                tpv, cash_pct = Portfolio.Cash / tpv,
                positions,
                var_1d99 = var1d99, var_regime = varRegime,
                drawdown, days_to_peak = daysToPeak, n_open_positions = positions.Count,
                // auto-evolution A1.5: 写入当期实际 alpha (供离线 RL 训练 trace_to_mdp_dataset).
                // composite mode 无 RlRiskModel → alpha=1.0 (满仓); rl mode 读 LastAppliedAlpha.
                alpha = _rlRiskModel?.LastAppliedAlpha ?? 1.0m,
            };
            return JsonConvert.SerializeObject(state);
        }

        /// <summary>
        /// A股 ETF SecurityInitializer (统一设置费率/结算/保证金模型).
        /// 零侵入 — 仅通过 SetSecurityInitializer 注册, 不修改 LEAN 原生.
        /// </summary>
        private class AShareETFSecurityInitializer : ISecurityInitializer
        {
            public void Initialize(Security security)
            {
                if (security.Symbol.ID.Market != Market.SSE && security.Symbol.ID.Market != Market.SZSE) return;
                if (security.Type != SecurityType.Equity) return;

                // ETF 费率: 万分之五佣金 (单向)
                security.FeeModel = new AShareETFFeeModel();
                // 全额保证金 (无杠杆)
                security.SetBuyingPowerModel(new SecurityMarginModel(1m));
                // T+0 结算 (ETF 允许当日回转)
                security.SetSettlementModel(new ImmediateSettlementModel());
            }
        }
    }
}