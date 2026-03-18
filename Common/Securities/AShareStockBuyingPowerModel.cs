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
using QuantConnect.Securities.Equity;

namespace QuantConnect.Securities
{
    /// <summary>
    /// Buying power model for A-share stocks with lot size and T+1 sellability constraints.
    /// </summary>
    public class AShareStockBuyingPowerModel : BuyingPowerModel
    {
        /// <summary>
        /// Creates a new instance with cash-equity leverage.
        /// </summary>
        public AShareStockBuyingPowerModel()
            : base(1m)
        {
        }

        /// <summary>
        /// Gets the maximum order quantity for a target buying power while respecting T+1 availability.
        /// </summary>
        public override GetMaximumOrderQuantityResult GetMaximumOrderQuantityForTargetBuyingPower(GetMaximumOrderQuantityForTargetBuyingPowerParameters parameters)
        {
            var result = base.GetMaximumOrderQuantityForTargetBuyingPower(parameters);
            var adjustedQuantity = AdjustToLotSize(result.Quantity);
            var description = parameters.Security.SymbolProperties?.Description;

            if (adjustedQuantity > 0 && AShareStock.IsSpecialTreatment(parameters.Security.Symbol, description))
            {
                return new GetMaximumOrderQuantityResult(0m, "Cannot buy ST stock", result.IsError);
            }

            if (adjustedQuantity < 0)
            {
                adjustedQuantity = -Math.Min(Math.Abs(adjustedQuantity), parameters.Security.Holdings.AvailableQuantity);
            }

            return new GetMaximumOrderQuantityResult(adjustedQuantity, result.Reason, result.IsError);
        }

        /// <summary>
        /// Checks whether there is sufficient buying power for the order while enforcing T+1 sellability.
        /// </summary>
        public override HasSufficientBuyingPowerForOrderResult HasSufficientBuyingPowerForOrder(HasSufficientBuyingPowerForOrderParameters parameters)
        {
            var order = parameters.Order;
            var description = parameters.Security.SymbolProperties?.Description;

            if (order.Direction == OrderDirection.Buy && AShareStock.IsSpecialTreatment(parameters.Security.Symbol, description))
            {
                return parameters.Insufficient("Cannot buy ST stock");
            }

            if (!AShareStock.IsValidQuantity(Math.Abs(order.Quantity)))
            {
                return parameters.Insufficient($"Order quantity must be a multiple of {AShareStock.LotSize} shares");
            }

            if (order.Direction == OrderDirection.Sell)
            {
                var availableQuantity = parameters.Security.Holdings.AvailableQuantity;
                if (Math.Abs(order.Quantity) > availableQuantity)
                {
                    return parameters.Insufficient($"Sell quantity {Math.Abs(order.Quantity)} exceeds available quantity {availableQuantity}");
                }
            }

            return base.HasSufficientBuyingPowerForOrder(parameters);
        }

        private static decimal AdjustToLotSize(decimal quantity)
        {
            if (quantity == 0)
            {
                return 0m;
            }

            var lots = Math.Floor(Math.Abs(quantity) / AShareStock.LotSize);
            return lots * AShareStock.LotSize * Math.Sign(quantity);
        }
    }
}
