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
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Data; // for Globals
using QuantConnect.Data.UniverseSelection; // for UniverseSettings
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
    public class OptionVolArb5LayerStrategy : QCAlgorithm
    {
        private OptionVolArbUniverseSelectionModel _universeModel;
        private List<string> _candidateTickers;

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
                ivRvZScoreThreshold: 2.0m,
                ivtsThreshold: 1.3m,
                skewPercentileHigh: 0.90m,
                skewPercentileLow: 0.10m));

            // Layer 3: Portfolio Construction — 等权组合
            SetPortfolioConstruction(new EqualWeightPortfolioModel());

            // Layer 4: Risk Management — VaR → MaxDrawdown → PositionLimit 三级风控链
            SetRiskManagement(CompositeRiskModel.FromVaR(
                varBudgetFraction: 0.02m,
                maxDrawdown: 0.20m,
                maxPositionWeight: 0.30m,
                method: VaRMethod.BootstrapHistorical,
                scenario: VaRScenario.OneDay99,
                lookbackDays: 252));

            // Layer 5: Execution — 立即执行 (用 LEAN 原生, 避免与 Models.Execution 同名歧义)
            SetExecution(new QuantConnect.Algorithm.Framework.Execution.ImmediateExecutionModel());

            // Security initializer: 为 SSE/SZSE 标的统一设置 A股 ETF 费率/结算/保证金
            SetSecurityInitializer(new AShareETFSecurityInitializer());

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