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
    /// Buying power model for A-Share ETFs with lot size enforcement
    /// </summary>
    public class AShareETFBuyingPowerModel : BuyingPowerModel
    {
        /// <summary>
        /// Gets the buying power available for a trade
        /// </summary>
        public override GetMaximumOrderQuantityResult GetMaximumOrderQuantityForTargetBuyingPower(
            GetMaximumOrderQuantityForTargetBuyingPowerParameters parameters)
        {
            var result = base.GetMaximumOrderQuantityForTargetBuyingPower(parameters);

            // Round down to nearest lot size (100 shares)
            if (result.Quantity != 0)
            {
                var lotSize = AShareETF.LotSize;
                var lots = Math.Floor(Math.Abs(result.Quantity) / lotSize);
                var adjustedQuantity = lots * lotSize * Math.Sign(result.Quantity);

                return new GetMaximumOrderQuantityResult(adjustedQuantity, result.Reason, result.IsError);
            }

            return result;
        }

        /// <summary>
        /// Check if there is sufficient buying power for the order
        /// </summary>
        public override HasSufficientBuyingPowerForOrderResult HasSufficientBuyingPowerForOrder(
            HasSufficientBuyingPowerForOrderParameters parameters)
        {
            var order = parameters.Order;

            // Validate lot size
            if (!AShareETF.IsValidQuantity(Math.Abs(order.Quantity)))
            {
                return new HasSufficientBuyingPowerForOrderResult(false,
                    $"Order quantity must be a multiple of {AShareETF.LotSize} shares");
            }

            return base.HasSufficientBuyingPowerForOrder(parameters);
        }
    }
}
