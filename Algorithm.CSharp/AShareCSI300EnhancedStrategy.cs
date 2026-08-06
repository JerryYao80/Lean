/*
 * AShareCSI300EnhancedStrategy — Enhanced 5-layer A-share strategy with
 * IC-weighted Alpha (49 factors) + MVO Portfolio Optimization.
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):  AShareCSI300UniverseSelectionModel (沪深300 月度刷新)
 *   Layer 2 (Alpha):     ICWeightedAlphaModel (49 effective factors, IC-weighted)
 *   Layer 3 (Portfolio): AlphaWeightedMVOPortfolioConstructionModel (MVO)
 *   Layer 4 (Risk):      MaximumDrawdownPercentPortfolio (0.20 回撤清仓)
 *   Layer 5 (Execution): AShareLotSizeExecutionModel (100 股整手)
 */

using System;
using System.Linq;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.UniverseSelection;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Python;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// A股 Alpha101 增强策略 — 沪深300 月度刷新, IC加权49因子,
    /// MVO优化组合, 五层 LEAN Framework 架构.
    /// </summary>
    public class AShareCSI300EnhancedStrategy : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2024, 1, 2);
            SetEndDate(2026, 6, 29);

            SetAccountCurrency(Currencies.CNY);
            SetCash(1_000_000);
            SetTimeZone(TimeZones.Shanghai);
            SetBenchmark(_ => 0m);

            Settings.MinimumOrderMarginPortfolioPercentage = 0;
            UniverseSettings.Resolution = Resolution.Daily;

            SetSecurityInitializer(new AShareStockSecurityInitializer());
            // L1 Universe (AShareCSI300UniverseSelectionModel) uses pythonnet to call
            // barra_cne5_data_loader.py for CSI300 constituents, so PythonEngine MUST be
            // initialized before Py.GIL() — otherwise Py.GIL() segfaults (exit 139, no
            // stack trace). Mirrors AShareCSI300Alpha101CompositeStrategy & AShareCrowdingFactorZooStrategy.
            // Idempotent (guarded by _isInitialized).
            PythonInitializer.Initialize();

            var dataRoot = GetParameterOrDefault("dataRoot",
                "/home/project/tushare-downloader/tushare_data_v2");
            var rootPath = GetParameterOrDefault("rootPath", Globals.DataFolder);

            // === Layer 1: Universe ===
            SetUniverseSelection(new AShareCSI300UniverseSelectionModel(
                dataRoot: dataRoot,
                rootPath: rootPath,
                indexCode: "000300.SH",
                refreshMonths: 1));

            // === Layer 2: Alpha — IC-Weighted 49 Factor Model ===
            int rebalanceDays = GetParameter("rebalance-days", 21);
            SetAlpha(new ICWeightedAlphaModel(
                topNFactors: 30,
                rebalanceMonths: 1,
                insightPeriodDays: rebalanceDays,
                topQuantile: 0.10m));

            // === Layer 3: Portfolio — Alpha-Weighted Optimization ===
            SetPortfolioConstruction(new AlphaWeightedMVOPortfolioConstructionModel(
                maxWeight: 0.10m,
                minWeight: 0.0m,
                rebalanceResolution: Resolution.Daily));

            // === Layer 4: Risk ===
            SetRiskManagement(new MaximumDrawdownPercentPortfolio(0.20m));

            // === Layer 5: Execution ===
            SetExecution(new AShareLotSizeExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCSI300-Enhanced] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH)");
            Log("  L2 Alpha:        ICWeightedAlphaModel (49 factors, IC-weighted)");
            Log("  L3 Portfolio:    AlphaWeightedMVOPortfolioConstructionModel (MVO)");
            Log("  L4 Risk:         MaximumDrawdownPercentPortfolio (0.20)");
            Log("  L5 Execution:    AShareLotSizeExecutionModel (100股整手)");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[AShareCSI300-Enhanced] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCSI300-Enhanced] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }

        // AShareStockSecurityInitializer is now a shared public class in
        // QuantConnect.Securities (Common/Securities/AShareStockSecurityInitializer.cs),
        // eliminating the copy-pasted private nested class across the 3 CSI300 strategies.
    }
}
