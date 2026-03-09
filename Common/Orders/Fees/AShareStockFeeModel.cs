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

using System;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantConnect.Orders.Fees
{
    /// <summary>
    /// Fee model for A-share stocks.
    /// </summary>
    public class AShareStockFeeModel : FeeModel
    {
        /// <summary>
        /// Commission rate.
        /// </summary>
        public decimal CommissionRate { get; set; } = 0.0003m;

        /// <summary>
        /// Minimum commission in CNY.
        /// </summary>
        public decimal MinimumCommission { get; set; } = 5m;

        /// <summary>
        /// Stamp duty rate applied on sells.
        /// </summary>
        public decimal StampDutyRate { get; set; } = 0.001m;

        /// <summary>
        /// Transfer fee rate for Shanghai-listed stocks.
        /// </summary>
        public decimal TransferFeeRate { get; set; } = 0.00002m;

        /// <summary>
        /// Gets the order fee.
        /// </summary>
        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;

            // Get the price for fee calculation
            var price = GetOrderPrice(order, security);
            var orderValue = Math.Abs(order.Quantity * price);

            var commission = Math.Max(orderValue * CommissionRate, MinimumCommission);
            var stampDuty = order.Direction == OrderDirection.Sell ? orderValue * StampDutyRate : 0m;
            var transferFee = security.Symbol.ID.Market == Market.SSE ? orderValue * TransferFeeRate : 0m;

            return new OrderFee(new CashAmount(commission + stampDuty + transferFee, Currencies.CNY));
        }

        /// <summary>
        /// Gets the price to use for fee calculation based on order type
        /// </summary>
        private static decimal GetOrderPrice(Order order, Security security)
        {
            switch (order.Type)
            {
                case OrderType.Market:
                case OrderType.MarketOnOpen:
                case OrderType.MarketOnClose:
                    // For market orders, use bid/ask price
                    return order.Direction == OrderDirection.Buy ? security.AskPrice : security.BidPrice;

                case OrderType.Limit:
                case OrderType.StopLimit:
                    // For limit orders, use the limit price
                    return order.Price;

                case OrderType.StopMarket:
                    // For stop market orders, use the stop price
                    var stopOrder = order as StopMarketOrder;
                    return stopOrder?.StopPrice ?? security.Price;

                default:
                    // Fallback to security price
                    return security.Price;
            }
        }
    }
}
