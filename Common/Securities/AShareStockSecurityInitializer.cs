/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * AShareStockSecurityInitializer — single source of truth for wiring A-share
 * (SSE/SZSE Equity) market models onto a Security. Previously copy-pasted as a
 * private nested class in AShareCSI300EnhancedStrategy /
 * AShareCSI300Alpha101CompositeStrategy / AShareCrowdingFactorZooStrategy.
 *
 * Zero-intrusion: registered via SetSecurityInitializer, so LEAN invokes it for
 * every equity the universe adds. Installs:
 *   - AShareStockFeeModel        (佣金万3 + 印花税千0.5 + 过户费万0.1)
 *   - AShareStockFillModel       (涨跌停板不成交)
 *   - AShareStockBuyingPowerModel(100 股整手, T+1 不可卖)
 *   - DelayedSettlementModel(1, 09:00)  (T+1 结算, 隔日 09:00 到账)
 */
using System;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Securities
{
    /// <summary>
    /// Wires the four A-share (SSE/SZSE Equity) market models onto a Security:
    /// fee, fill, buying-power, and T+1 settlement. Non-A-share securities are
    /// skipped (guard on Market + SecurityType). Register via
    /// <c>SetSecurityInitializer(new AShareStockSecurityInitializer())</c> so LEAN
    /// applies it to every equity the universe adds.
    /// </summary>
    public class AShareStockSecurityInitializer : ISecurityInitializer
    {
        public void Initialize(Security security)
        {
            if (security.Symbol.ID.Market != Market.SSE && security.Symbol.ID.Market != Market.SZSE) return;
            if (security.Type != SecurityType.Equity) return;

            // A股股票费率: 佣金万3 (min 5元) + 印花税千0.5 (卖) + 过户费万0.1.
            // (印花税 2023-08 从千1降至千0.5; 佣金对齐 AShareStockFeeModel.DefaultCommissionRate=0.0003)
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
