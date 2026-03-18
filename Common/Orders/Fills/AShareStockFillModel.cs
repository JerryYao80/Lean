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
    /// Fill model for A-share stocks with T+1, lot-size, market-hours and price-limit validation.
    /// </summary>
    public class AShareStockFillModel : EquityFillModel
    {
        /// <summary>
        /// Validates and fills an order for A-share stocks.
        /// </summary>
        public override Fill Fill(FillModelParameters parameters)
        {
            var order = parameters.Order;
            var security = parameters.Security;
            var description = security.SymbolProperties?.Description;

            if (order.Direction == OrderDirection.Buy && AShareStock.IsSpecialTreatment(security.Symbol, description))
            {
                return InvalidFill(order, security, "Cannot buy ST stock");
            }

            if (!AShareStock.IsValidQuantity(Math.Abs(order.Quantity)))
            {
                return InvalidFill(order, security, $"Order quantity must be a multiple of {AShareStock.LotSize} shares");
            }

            // For market orders, check at placement time
            // For limit orders, check at fill time (handled by base class and verified below)
            if (order.Type == OrderType.Market || order.Type == OrderType.MarketOnOpen || order.Type == OrderType.MarketOnClose)
            {
                if (!IsWithinMarketHours(security))
                {
                    return InvalidFill(order, security, "Market order placed outside market hours (9:30-11:30, 13:00-15:00 CST)");
                }
            }

            if (order.Direction == OrderDirection.Sell && Math.Abs(order.Quantity) > security.Holdings.AvailableQuantity)
            {
                return InvalidFill(order, security, $"Sell quantity {Math.Abs(order.Quantity)} exceeds available quantity {security.Holdings.AvailableQuantity}");
            }

            var previousClose = GetPreviousClose(security);
            if (previousClose.HasValue)
            {
                var minimumPriceVariation = security.SymbolProperties.MinimumPriceVariation;
                var upperLimit = AShareStock.GetUpperPriceLimit(security.Symbol, previousClose.Value, minimumPriceVariation, description);
                var lowerLimit = AShareStock.GetLowerPriceLimit(security.Symbol, previousClose.Value, minimumPriceVariation, description);

                // Validate limit orders
                if (order is LimitOrder limitOrder)
                {
                    if (order.Direction == OrderDirection.Buy && limitOrder.LimitPrice > upperLimit)
                    {
                        return InvalidFill(order, security, $"Buy limit price {limitOrder.LimitPrice} exceeds upper limit {upperLimit}");
                    }

                    if (order.Direction == OrderDirection.Sell && limitOrder.LimitPrice < lowerLimit)
                    {
                        return InvalidFill(order, security, $"Sell limit price {limitOrder.LimitPrice} is below lower limit {lowerLimit}");
                    }
                }
                // Validate market orders - check if current price is at limit
                else if (order.Type == OrderType.Market || order.Type == OrderType.MarketOnOpen || order.Type == OrderType.MarketOnClose)
                {
                    var currentPrice = security.Price;

                    // If buying and price is at upper limit, order cannot be filled
                    if (order.Direction == OrderDirection.Buy && Math.Abs(currentPrice - upperLimit) < minimumPriceVariation)
                    {
                        return InvalidFill(order, security, $"Cannot buy at upper price limit {upperLimit} - no liquidity available");
                    }

                    // If selling and price is at lower limit, order cannot be filled
                    if (order.Direction == OrderDirection.Sell && Math.Abs(currentPrice - lowerLimit) < minimumPriceVariation)
                    {
                        return InvalidFill(order, security, $"Cannot sell at lower price limit {lowerLimit} - no liquidity available");
                    }
                }
            }

            var fill = base.Fill(parameters);
            var fillEvent = fill.FirstOrDefault();
            if (fillEvent != null && (fillEvent.Status == OrderStatus.Filled || fillEvent.Status == OrderStatus.PartiallyFilled))
            {
                // Verify fill occurred during market hours
                if (!IsWithinMarketHours(security))
                {
                    return InvalidFill(order, security, "Fill occurred outside market hours (9:30-11:30, 13:00-15:00 CST)");
                }

                // Verify fill price is within price limits
                if (previousClose.HasValue)
                {
                    var minimumPriceVariation = security.SymbolProperties.MinimumPriceVariation;
                    var upperLimit = AShareStock.GetUpperPriceLimit(security.Symbol, previousClose.Value, minimumPriceVariation, description);
                    var lowerLimit = AShareStock.GetLowerPriceLimit(security.Symbol, previousClose.Value, minimumPriceVariation, description);

                    if (fillEvent.FillPrice > upperLimit || fillEvent.FillPrice < lowerLimit)
                    {
                        return InvalidFill(order, security, $"Fill price {fillEvent.FillPrice} outside price limits [{lowerLimit}, {upperLimit}]");
                    }
                }
            }

            return fill;
        }

        private static Fill InvalidFill(Order order, Security security, string message)
        {
            var utcTime = security.LocalTime.ConvertToUtc(security.Exchange.TimeZone);
            var orderEvent = new OrderEvent(order, utcTime, OrderFee.Zero)
            {
                Status = OrderStatus.Invalid,
                Message = message
            };

            return new Fill(orderEvent);
        }

        private static decimal? GetPreviousClose(Security security)
        {
            if (security.Session != null && security.Session.Count > 1 && security.Session[1] != null && security.Session[1].Close > 0)
            {
                return security.Session[1].Close;
            }

            return security.Cache.GetData<TradeBar>()?.Close;
        }

        private static bool IsWithinMarketHours(Security security)
        {
            var time = security.Exchange.LocalTime.TimeOfDay;
            return (time >= new TimeSpan(9, 30, 0) && time <= new TimeSpan(11, 30, 0))
                || (time >= new TimeSpan(13, 0, 0) && time <= new TimeSpan(15, 0, 0));
        }
    }
}
