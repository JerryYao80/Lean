/*
 * AShareCSI300MVOStrategy — MVO variant of the Enhanced 5-layer strategy.
 *
 * Identical to AShareCSI300EnhancedV2Strategy EXCEPT:
 *   L3 uses MVOAlphaPortfolioConstructionModel (offline MVO weights)
 *   L5 uses AShareT1SequentialExecutionModel (sell-before-buy)
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):  AShareCSI300UniverseSelectionModel (沪深300 月度刷新)
 *   Layer 2 (Alpha):     ICWeightedAlphaModelV2 (JSON IC report)
 *   Layer 3 (Portfolio): MVOAlphaPortfolioConstructionModel (JSON MVO weights)
 *   Layer 4 (Risk):      MaximumDrawdownPercentPortfolio (0.20)
 *   Layer 5 (Execution): AShareT1SequentialExecutionModel (先卖后买整手)
 *
 * ZERO INTRUSION: V2 strategy is NOT modified.
 */
using System;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.UniverseSelection;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Python;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareCSI300MVOStrategy : QCAlgorithm
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
            // stack trace). Idempotent (guarded by _isInitialized).
            PythonInitializer.Initialize();

            var dataRoot = GetParameterOrDefault("dataRoot",
                "/home/project/tushare-downloader/tushare_data_v2");
            var rootPath = GetParameterOrDefault("rootPath", Globals.DataFolder);
            var icReportDir = GetParameterOrDefault("icReportDir",
                "/home/project/hope/Lean/result/ic-reports");
            var mvoWeightsDir = GetParameterOrDefault("mvoWeightsDir",
                "/home/project/hope/Lean/result/mvo-weights");

            SetUniverseSelection(new AShareCSI300UniverseSelectionModel(
                dataRoot: dataRoot,
                rootPath: rootPath,
                indexCode: "000300.SH",
                refreshMonths: 1));

            int rebalanceDays = GetParameter("rebalance-days", 21);
            SetAlpha(new ICWeightedAlphaModelV2(
                icReportDir: icReportDir,
                rebalanceMonths: 1,
                insightPeriodDays: rebalanceDays,
                topQuantile: 0.10m));

            SetPortfolioConstruction(new MVOAlphaPortfolioConstructionModel(
                mvoWeightsDir: mvoWeightsDir,
                minWeight: 0.0m,
                rebalanceResolution: Resolution.Daily));

            SetRiskManagement(new MaximumDrawdownPercentPortfolio(0.20m));
            SetExecution(new AShareT1SequentialExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCSI300-MVO] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH)");
            Log($"  L2 Alpha:        ICWeightedAlphaModelV2 (JSON: {icReportDir})");
            Log($"  L3 Portfolio:    MVOAlphaPortfolioConstructionModel (JSON: {mvoWeightsDir})");
            Log("  L4 Risk:         MaximumDrawdownPercentPortfolio (0.20)");
            Log("  L5 Execution:    AShareT1SequentialExecutionModel (先卖后买整手)");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[AShareCSI300-MVO] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCSI300-MVO] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }
    }
}
