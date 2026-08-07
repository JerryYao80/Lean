/*
 * AShareCSI300BarraStrategy — Barra risk variant of the MVO 5-layer strategy.
 *
 * Identical to AShareCSI300MVOStrategy EXCEPT:
 *   L4 uses BarraFactorRiskModel (Barra factor risk decomposition + vol targeting)
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):  AShareCSI300UniverseSelectionModel (沪深300 月度刷新)
 *   Layer 2 (Alpha):     ICWeightedAlphaModelV2 (JSON IC report)
 *   Layer 3 (Portfolio): MVOAlphaPortfolioConstructionModel (JSON MVO weights)
 *   Layer 4 (Risk):      BarraFactorRiskModel (Barra Sigma_f + Delta, vol targeting)
 *   Layer 5 (Execution): AShareT1SequentialExecutionModel (先卖后买整手)
 *
 * ZERO INTRUSION: MVO strategy and V2 strategy are NOT modified.
 */
using System;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
using QuantConnect.Algorithm.CSharp.UniverseSelection;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Python;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareCSI300BarraStrategy : QCAlgorithm
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
            var barraRiskDir = GetParameterOrDefault("barraRiskDir",
                "/home/project/hope/Lean/result/barra-risk");

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

            // L4: Barra factor risk decomposition (replaces MaximumDrawdownPercentPortfolio).
            // target-vol: de-leverage when annualized portfolio vol exceeds this (0.20 = 20%).
            // max-factor-exposure: cap |sum_s w_s * B_s[f]| per factor (0.5 default).
            var targetVol = GetParameter("target-vol", 0.20m);
            var maxFactorExposure = GetParameter("max-factor-exposure", 0.5m);
            SetRiskManagement(new BarraFactorRiskModel(
                barraRiskDir: barraRiskDir,
                targetVol: targetVol,
                maxFactorExposure: maxFactorExposure));

            SetExecution(new AShareT1SequentialExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCSI300-Barra] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH)");
            Log($"  L2 Alpha:        ICWeightedAlphaModelV2 (JSON: {icReportDir})");
            Log($"  L3 Portfolio:    MVOAlphaPortfolioConstructionModel (JSON: {mvoWeightsDir})");
            Log($"  L4 Risk:         BarraFactorRiskModel (dir={barraRiskDir}, targetVol={targetVol}, maxFactorExp={maxFactorExposure})");
            Log("  L5 Execution:    AShareT1SequentialExecutionModel (先卖后买整手)");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[AShareCSI300-Barra] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCSI300-Barra] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }
    }
}
