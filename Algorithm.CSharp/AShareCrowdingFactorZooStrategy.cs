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
 */

/*
 * A股拥挤度因子动物园策略 (C-Task 5).
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):     AShareCSI300UniverseSelectionModel (沪深300 成分股)
 *   Layer 2 (Alpha):        CrowdingFactorZooAlphaModel (低拥挤 30% 等权 多头)
 *   Layer 3 (Portfolio):    EqualWeightPortfolioModel
 *   Layer 4 (Risk):         MaxDrawdownRiskModel (0.20 回撤清仓)
 *   Layer 5 (Execution):    AShareLotSizeExecutionModel (100 股整手)
 *
 * 镜像 OptionVolArbFactorZooStrategy 结构, 但标的是 A股股票 (非 ETF/期权).
 *
 * 关键修复 (C-Task 3 review handoff):
 *   AShareCSI300UniverseSelectionModel 通过 ManualUniverseSelectionModel 添加
 *   equities, LEAN 默认装的是 Equity 默认 Fee/Fill/BuyingPower/Settlement —
 *   对 A股是错的 (T+1 settlement, 印花税, 整手 100, 涨跌停板等). 必须在
 *   AShareCSI300UniverseSelectionModel 添加 equity 之前调用 security initializer 安装 A股股票模型,
 *   这样 universe 后续添加的每只 equity 都会自动套上:
 *     - AShareStockFeeModel        (佣金万2.5 + 印花税 + 过户费)
 *     - AShareStockFillModel       (涨跌停板不成交)
 *     - AShareStockBuyingPowerModel(100 股整手, T+1 不可卖)
 *     - DelayedSettlementModel(1, 09:00)  (T+1 结算, 隔日 09:00 到账)
 *
 *   镜像 OptionVolArb5LayerStrategy:194,366-380 (AShareETFSecurityInitializer
 *   模式) 但用于 STOCKS 而非 ETF — 参考 AShareBarraCNE5Algorithm:201-204.
 */

using System;
using System.Globalization;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.CSharp.Models;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;
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
    /// A股拥挤度因子动物园策略 — 沪深300 低拥挤 30% 等权 多头, 月度调仓.
    /// 镜像 OptionVolArbFactorZooStrategy 三层管线, 但 universe 改为 CSI300.
    /// </summary>
    public class AShareCrowdingFactorZooStrategy : QCAlgorithm
    {
        public override void Initialize()
        {
            // 回测窗口 + 资金 (默认可被 config parameters 覆盖).
            // NOTE: SetAccountCurrency MUST come before SetCash — LEAN throws
            // "Cannot change AccountCurrency after setting cash" otherwise
            // (SecurityPortfolioManager.cs:650). Mirrors AShareBarraCNE5Algorithm
            // init order.
            SetStartDate(GetDateParameter("start-date", new DateTime(2024, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2024, 6, 28)));

            // A股账户: CNY 计价, 上海时区.
            SetAccountCurrency(Currencies.CNY);
            SetCash(GetDecimalParameter("initial-cash", 1000000m));
            SetTimeZone(TimeZones.Shanghai);
            SetBenchmark(x => 0m);

            // 允许小单 (等权 300 只时单笔占比可能极小).
            Settings.MinimumOrderMarginPortfolioPercentage = 0;

            // === 关键: 必须先安装 A股 security initializer, 再注册 CSI300 universe ===
            // AShareCSI300UniverseSelectionModel 内部用 ManualUniverseSelectionModel
            // 添加 plain Equity (默认模型对 A股错). 此 initializer 在每只 security
            // 添加时回调, 为 SSE/SZSE Equity 安装 A股股票模型.
            SetSecurityInitializer(new AShareStockSecurityInitializer());

            // CRITICAL: Initialize the Python runtime BEFORE SetUniverseSelection/SetAlpha.
            // Both AShareCSI300UniverseSelectionModel and CrowdingFactorZooAlphaModel use
            // pythonnet (Py.GIL) to read parquet via pandas and to load
            // barra_cne5_data_loader. For C# algorithms, LEAN does NOT auto-initialize
            // PythonEngine (only Python algorithms get that via Loader.cs:171). Without
            // this call, the first Py.GIL() in CreateUniverses (called from
            // FrameworkPostInitialize) crashes the process with a native segfault — no
            // exception, no stack trace, just exit 139. PythonInitializer.Initialize is
            // idempotent (guarded by _isInitialized).
            PythonInitializer.Initialize();

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

            // Layer 2: Alpha — CrowdingFactorZooAlphaModel (低拥挤 30% 等权 多头).
            var resultRoot = GetParameterOrDefault("resultRoot",
                "/home/project/hope/Lean/result");
            var lowQuantile = GetDecimalParameter("lowQuantile", 0.30m);
            SetAlpha(new CrowdingFactorZooAlphaModel(
                resultRoot: resultRoot,
                lowQuantile: lowQuantile,
                rebalanceMonths: 1));

            // Layer 3: Portfolio — 等权组合.
            SetPortfolioConstruction(new EqualWeightPortfolioModel());

            // Layer 4: Risk — 最大回撤 20% 清仓.
            SetRiskManagement(new MaxDrawdownRiskModel(maxDrawdown: 0.20m));

            // Layer 5: Execution — A股 100 股整手.
            SetExecution(new AShareLotSizeExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCrowding-FactorZoo] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH, 月度刷新)");
            Log($"  L2 Alpha:        CrowdingFactorZooAlphaModel (lowQuantile={lowQuantile:F2}, 月度调仓)");
            Log("  L3 Portfolio:    EqualWeightPortfolioModel");
            Log("  L4 Risk:         MaxDrawdownRiskModel (0.20)");
            Log("  L5 Execution:    AShareLotSizeExecutionModel (100 股整手)");
            Log("  SecurityInit:    AShareStockSecurityInitializer (Fee/Fill/BuyingPower/T+1)");
        }

        public override void OnEndOfAlgorithm()
        {
            var initialCash = Portfolio.TotalPortfolioValue;
            Log($"[AShareCrowding-FactorZoo] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCrowding-FactorZoo] Total trades: {Transactions.OrdersCount}");
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

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            var value = GetParameter(name);
            if (string.IsNullOrWhiteSpace(value)) return defaultValue;
            // 支持 yyyy-MM-dd 和 yyyyMMdd 两种格式.
            if (DateTime.TryParseExact(value, "yyyyMMdd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var parsed)) return parsed;
            return DateTime.TryParse(value, CultureInfo.InvariantCulture,
                DateTimeStyles.None, out parsed) ? parsed : defaultValue;
        }

        /// <summary>
        /// A股股票 SecurityInitializer — 为 SSE/SZSE Equity 安装 A股股票模型.
        /// 零侵入 — 仅通过 SetSecurityInitializer 注册, 不修改 LEAN 原生. 安全: universe 后续
        /// 添加 plain Equity 时此 initializer 回调, 为每只 SSE/SZSE 股票装 A-share 模型.
        /// 镜像 OptionVolArb5LayerStrategy.AShareETFSecurityInitializer (line 366-380)
        /// 但用于 STOCKS 而非 ETF, 参考 AShareBarraCNE5Algorithm:201-204:
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
