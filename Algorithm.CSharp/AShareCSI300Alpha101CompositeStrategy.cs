/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 */

/*
 * AShareCSI300Alpha101CompositeStrategy — 5-layer A-share composite (C-Task 3).
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):  AShareCSI300UniverseSelectionModel (沪深300 月度刷新)
 *   Layer 2 (Alpha):     Alpha101CompositeAlphaModel (8 alpha101 ids, z-score,
 *                        composite, top 10% long — FactorStore pull API consumer)
 *   Layer 3 (Portfolio): EqualWeightingPortfolioConstructionModel (Daily rebalance)
 *   Layer 4 (Risk):      MaximumDrawdownPercentPortfolio (0.20 回撤清仓)
 *   Layer 5 (Execution): AShareLotSizeExecutionModel (100 股整手)
 *
 * Reads optimizer-injected blend weights via GetParameter("w_alphaNNN", 0.125).
 * Weights normalized to sum=1 in C# (manifest declares raw [0,1] for TPE).
 * NormalizedWeights is a public property consumed by the optimizer reward path
 * and by the unit test. The AlphaModel uses equal-weight internally (Task 2's
 * ctor has no weights param — a future task can pass them in; do NOT modify
 * Task 2's AlphaModel).
 *
 * 镜像 AShareCrowdingFactorZooStrategy 五层结构 + AShareStockSecurityInitializer
 * private nested class (zero-intrusion — only SetSecurityInitializer registers,
 * no LEAN core edits).
 */

using System;
using System.Linq;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
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
    /// A股 Alpha101 复合策略 — 沪深300 月度刷新, FactorStore 拉 8 alpha101
    /// 横截面 z-score 复合, top 10% 等权 多头. 五层 LEAN Framework 架构.
    /// </summary>
    public class AShareCSI300Alpha101CompositeStrategy : QCAlgorithm
    {
        /// <summary>
        /// Optimizer-injected alpha blend weights, normalized to sum=1. Read by
        /// the optimizer reward path and by the unit test (Task 3 TDD). Set by
        /// ReadAndNormalizeWeights() (called from Initialize()).
        /// </summary>
        public double[] NormalizedWeights { get; private set; }

        public override void Initialize()
        {
            // 回测窗口 + 资金 (默认可被 config parameters 覆盖).
            // NOTE: SetAccountCurrency MUST come before SetCash — LEAN throws
            // "Cannot change AccountCurrency after setting cash" otherwise
            // (SecurityPortfolioManager.cs:650). Mirrors AShareCrowdingFactorZooStrategy
            // init order.
            SetStartDate(2024, 1, 2);
            SetEndDate(2026, 6, 29);

            // A股账户: CNY 计价, 上海时区.
            SetAccountCurrency(Currencies.CNY);
            SetCash(1_000_000);
            SetTimeZone(TimeZones.Shanghai);
            SetBenchmark(_ => 0m);

            // 允许小单 (等权 30 只时单笔占比可能极小).
            Settings.MinimumOrderMarginPortfolioPercentage = 0;

            // Universe resolution: Daily. A-share daily data lives at
            // Data/equity/{sse,szse}/daily/*.zip (NO minute data on disk). The
            // default UniverseSettings.Resolution is Minute (QCAlgorithm.cs:225),
            // which causes 1.46M failed minute-bar requests + security.Price==0
            // → PortfolioTarget.Percent returns null (PortfolioTarget.cs:156-160)
            // → 0 orders reach execution. Daily matches the monthly/21d design.
            UniverseSettings.Resolution = Resolution.Daily;

            // === 关键: 必须先安装 A股 security initializer, 再注册 CSI300 universe ===
            // AShareCSI300UniverseSelectionModel 内部用 ManualUniverseSelectionModel
            // 添加 plain Equity (默认模型对 A股错). 此 initializer 在每只 security
            // 添加时回调, 为 SSE/SZSE Equity 安装 A股股票模型. 镜像
            // AShareCrowdingFactorZooStrategy.AShareStockSecurityInitializer.
            SetSecurityInitializer(new AShareStockSecurityInitializer());

            // CRITICAL: Initialize the Python runtime BEFORE SetUniverseSelection/SetAlpha.
            // Both AShareCSI300UniverseSelectionModel and Alpha101CompositeAlphaModel's
            // underlying FactorStore (RParquetAdapter) use pythonnet (Py.GIL) to read
            // parquet via pandas. For C# algorithms, LEAN does NOT auto-initialize
            // PythonEngine (only Python algorithms get that via Loader.cs:171). Without
            // this call, the first Py.GIL() in CreateUniverses (called from
            // FrameworkPostInitialize) crashes the process with a native segfault.
            // PythonInitializer.Initialize is idempotent (guarded by _isInitialized).
            PythonInitializer.Initialize();

            // === Read + normalize optimizer-injected alpha blend weights ===
            // Manifest declares raw [0,1] for TPE; C# normalizes to sum=1 so the
            // strategy is robust to non-normalized optimizer outputs.
            ReadAndNormalizeWeights();

            // === LEAN Native Five-Layer Architecture ===

            // Layer 1: Universe — 沪深300 成分股 (月度刷新).
            var dataRoot = GetParameterOrDefault("dataRoot",
                "/home/project/tushare-downloader/tushare_data_v2");
            var rootPath = GetParameterOrDefault("rootPath", Globals.DataFolder);
            SetUniverseSelection(new AShareCSI300UniverseSelectionModel(
                dataRoot: dataRoot,
                rootPath: rootPath,
                indexCode: "000300.SH",
                refreshMonths: 1));

            // Layer 2: Alpha — Alpha101CompositeAlphaModel (8 alpha101 ids, z-score,
            // composite, top 10% long). Task 2's ctor has no weights param — the
            // model uses equal-weight internally; NormalizedWeights is stored for
            // the optimizer reward path. Do NOT modify Task 2's AlphaModel.
            int rebalanceDays = GetParameter("rebalance-days", 21);
            SetAlpha(new Alpha101CompositeAlphaModel(
                rebalanceMonths: 1,
                insightPeriodDays: rebalanceDays,
                topQuantile: 0.10m));

            // Layer 3: Portfolio — 等权组合 (Daily rebalance).
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel(
                Resolution.Daily));

            // Layer 4: Risk — 最大回撤 20% 清仓.
            SetRiskManagement(new MaximumDrawdownPercentPortfolio(0.20m));

            // Layer 5: Execution — A股 100 股整手 (lot-rounded, T+1 不可卖).
            // NOTE: 0-orders root cause was NOT execution-model (verified end-to-end
            // 2026-07-28) — it was upstream truncated/10000x-scaled daily .zip data.
            // With real-yuan zips (export_ashare_stock_data.py from daily parquet),
            // AShareLotSizeExecutionModel produces orders correctly. Do NOT swap to
            // ImmediateExecutionModel as a "fix" — that masks data bugs.
            SetExecution(new AShareLotSizeExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCSI300-Alpha101Composite] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH, 月度刷新)");
            Log($"  L2 Alpha:        Alpha101CompositeAlphaModel (8 ids, top 10% long, {rebalanceDays}d insight)");
            Log("  L3 Portfolio:    EqualWeightingPortfolioConstructionModel (Daily)");
            Log("  L4 Risk:         MaximumDrawdownPercentPortfolio (0.20)");
            Log("  L5 Execution:    AShareLotSizeExecutionModel (100 股整手)");
            Log("  SecurityInit:    AShareStockSecurityInitializer (Fee/Fill/BuyingPower/T+1)");
        }

        /// <summary>
        /// Reads the 8 optimizer-injected alpha blend weights (w_alphaNNN) from
        /// parameters and normalizes them to sum=1. All-zero (or all-missing)
        /// falls back to equal 1/8 weighting. Exposed as a public method so the
        /// unit test can verify the weight-normalization contract in isolation
        /// from pythonnet/CSI300 universe loading. Initialize() calls this.
        ///
        /// The 8 alpha ids mirror Alpha101CompositeAlphaModel.AlphaIds (Task 2):
        /// alpha001, alpha006, alpha030, alpha040, alpha042, alpha055, alpha058, alpha101.
        /// </summary>
        public void ReadAndNormalizeWeights()
        {
            var raw = new[]
            {
                GetParameter("w_alpha001", 0.125),
                GetParameter("w_alpha006", 0.125),
                GetParameter("w_alpha030", 0.125),
                GetParameter("w_alpha040", 0.125),
                GetParameter("w_alpha042", 0.125),
                GetParameter("w_alpha055", 0.125),
                GetParameter("w_alpha058", 0.125),
                GetParameter("w_alpha101", 0.125),
            };
            double sum = raw.Sum();
            NormalizedWeights = (sum > 1e-9)
                ? raw.Select(w => w / sum).ToArray()
                : raw.Select(_ => 1.0 / raw.Length).ToArray();
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[AShareCSI300-Alpha101Composite] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCSI300-Alpha101Composite] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }

        /// <summary>
        /// A股股票 SecurityInitializer — 为 SSE/SZSE Equity 安装 A股股票模型.
        /// 零侵入 — 仅通过 SetSecurityInitializer 注册, 不修改 LEAN 原生. 安全: universe 后续
        /// 添加 plain Equity 时此 initializer 回调, 为每只 SSE/SZSE 股票装 A-share 模型.
        /// 镜像 AShareCrowdingFactorZooStrategy.AShareStockSecurityInitializer (private nested):
        ///   - AShareStockFeeModel        (佣金万2.5 + 印花税 + 过户费)
        ///   - AShareStockFillModel       (涨跌停板不成交)
        ///   - AShareStockBuyingPowerModel(100 股整手, T+1 不可卖)
        ///   - DelayedSettlementModel(1, 09:00)  (T+1 结算, 隔日 09:00 到账)
        /// </summary>
        private class AShareStockSecurityInitializer : ISecurityInitializer
        {
            public void Initialize(Security security)
            {
                if (security.Symbol.ID.Market != Market.SSE && security.Symbol.ID.Market != Market.SZSE) return;
                if (security.Type != SecurityType.Equity) return;

                // A股股票费率: 佣金万2.5 (min 5元) + 印花税千1 (卖) + 过户费万0.1.
                security.FeeModel = new AShareStockFeeModel();
                // A股股票成交: 涨跌停板不成交, 集合竞价等.
                security.FillModel = new AShareStockFillModel();
                // A股股票购买力: 100 股整手, T+1 不可卖 (当日买入不可卖).
                security.BuyingPowerModel = new AShareStockBuyingPowerModel();
                // A股股票结算: T+1 (隔日 09:00 资金到账).
                security.SetSettlementModel(new DelayedSettlementModel(1, TimeSpan.FromHours(9)));
            }
        }
    }
}
