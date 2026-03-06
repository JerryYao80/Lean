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
using System.Linq;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Orders.Fills
{
    /// <summary>
    /// Fill model for A-Share ETFs with price limit and lot size validation
    /// </summary>
    public class AShareETFFillModel : EquityFillModel
    {
        /// <summary>
        /// Validates and fills an order for A-Share ETF
        /// </summary>
        public override Fill Fill(FillModelParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;

            // Validate lot size (100 shares minimum, multiples of 100)
            if (!AShareETF.IsValidQuantity(Math.Abs(order.Quantity)))
            {
                var utcTime = security.LocalTime.ConvertToUtc(security.Exchange.TimeZone);
                var orderEvent = new OrderEvent(order, utcTime, OrderFee.Zero)
                {
                    Status = OrderStatus.Invalid,
                    Message = $"Order quantity must be a multiple of {AShareETF.LotSize} shares"
                };
                return new Fill(orderEvent);
            }

            // Validate market hours
            if (!IsWithinMarketHours(security))
            {
                var utcTime = security.LocalTime.ConvertToUtc(security.Exchange.TimeZone);
                var orderEvent = new OrderEvent(order, utcTime, OrderFee.Zero)
                {
                    Status = OrderStatus.Invalid,
                    Message = "Order placed outside market hours (9:30-11:30, 13:00-15:00 CST)"
                };
                return new Fill(orderEvent);
            }

            // Get base fill from parent class
            var fill = base.Fill(parameters);

            // Validate price limits (10% up/down from previous close)
            var fillEvent = fill.FirstOrDefault();
            if (fillEvent != null && (fillEvent.Status == OrderStatus.Filled || fillEvent.Status == OrderStatus.PartiallyFilled))
            {
                var tradeBar = security.Cache.GetData<TradeBar>();
                if (tradeBar != null)
                {
                    var previousClose = tradeBar.Close;
                    var upperLimit = previousClose * (1 + AShareETF.PriceLimitPercentage);
                    var lowerLimit = previousClose * (1 - AShareETF.PriceLimitPercentage);

                    if (fillEvent.FillPrice > upperLimit || fillEvent.FillPrice < lowerLimit)
                    {
                        var utcTime = security.LocalTime.ConvertToUtc(security.Exchange.TimeZone);
                        var orderEvent = new OrderEvent(order, utcTime, OrderFee.Zero)
                        {
                            Status = OrderStatus.Invalid,
                            Message = $"Fill price {fillEvent.FillPrice} outside price limits [{lowerLimit}, {upperLimit}]"
                        };
                        return new Fill(orderEvent);
                    }
                }
            }

            return fill;
        }

        /// <summary>
        /// Checks if current time is within A-Share market hours
        /// </summary>
        private bool IsWithinMarketHours(Security security)
        {
            var localTime = security.Exchange.LocalTime;
            var time = localTime.TimeOfDay;

            // Morning session: 9:30 - 11:30
            if (time >= new TimeSpan(9, 30, 0) && time <= new TimeSpan(11, 30, 0))
            {
                return true;
            }

            // Afternoon session: 13:00 - 15:00
            if (time >= new TimeSpan(13, 0, 0) && time <= new TimeSpan(15, 0, 0))
            {
                return true;
            }

            return false;
        }
    }
}
