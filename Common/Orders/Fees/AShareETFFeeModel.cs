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
    /// Fee model for A-Share ETFs
    /// </summary>
    public class AShareETFFeeModel : FeeModel
    {
        /// <summary>
        /// Commission rate (default 0.03%)
        /// </summary>
        public decimal CommissionRate { get; set; } = 0.0003m;

        /// <summary>
        /// Minimum commission (5 CNY)
        /// </summary>
        public decimal MinimumCommission { get; set; } = 5m;

        /// <summary>
        /// Transfer fee rate for Shanghai exchange (0.002%)
        /// </summary>
        public decimal TransferFeeRate { get; set; } = 0.00002m;

        /// <summary>
        /// Gets the order fee
        /// </summary>
        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;

            // Calculate commission
            var orderValue = Math.Abs(order.Quantity * order.Price);
            var commission = Math.Max(orderValue * CommissionRate, MinimumCommission);

            // Add transfer fee for Shanghai exchange
            var transferFee = 0m;
            if (security.Symbol.ID.Market == Market.SSE)
            {
                transferFee = Math.Abs(order.Quantity) * TransferFeeRate;
            }

            var totalFee = commission + transferFee;

            return new OrderFee(new CashAmount(totalFee, Currencies.CNY));
        }
    }
}
