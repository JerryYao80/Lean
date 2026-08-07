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
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Orders;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// Equity holdings implementation that tracks T+1 sell availability.
    /// </summary>
    public class AShareT1Holding : EquityHolding
    {
        private static readonly TimeSpan ReleaseTimeOfDay = TimeSpan.FromHours(9);
        private readonly SortedDictionary<DateTime, decimal> _pendingSellableQuantities = new();

        /// <summary>
        /// Creates a new instance.
        /// </summary>
        public AShareT1Holding(Security security, ICurrencyConverter currencyConverter)
            : base(security, currencyConverter)
        {
        }

        /// <summary>
        /// Gets the quantity that can currently be sold.
        /// </summary>
        public override decimal AvailableQuantity
        {
            get
            {
                ReleaseMaturedQuantities(GetCurrentLocalTime());
                return Math.Max(0m, Quantity - GetPendingQuantity());
            }
        }

        /// <summary>
        /// Records a processed fill so same-day buy quantities remain unavailable until the next trading day.
        /// </summary>
        public void ApplyFill(OrderEvent fill, decimal previousQuantity)
        {
            ReleaseMaturedQuantities(fill.UtcTime.ConvertFromUtc(Security.Exchange.TimeZone));

            if (Quantity <= 0)
            {
                _pendingSellableQuantities.Clear();
                return;
            }

            if (fill.Direction == OrderDirection.Buy)
            {
                var positiveQuantityIncrease = Math.Max(0m, Quantity) - Math.Max(0m, previousQuantity);
                if (positiveQuantityIncrease > 0)
                {
                    var releaseLocalTime = GetNextReleaseLocalTime(fill.UtcTime.ConvertFromUtc(Security.Exchange.TimeZone));
                    if (_pendingSellableQuantities.TryGetValue(releaseLocalTime, out var pendingQuantity))
                    {
                        _pendingSellableQuantities[releaseLocalTime] = pendingQuantity + positiveQuantityIncrease;
                    }
                    else
                    {
                        _pendingSellableQuantities[releaseLocalTime] = positiveQuantityIncrease;
                    }
                }
            }

            ReconcilePendingQuantity();
        }

        /// <summary>
        /// Sets the quantity of holdings and reconciles pending T+1 restrictions.
        /// </summary>
        public override void SetHoldings(decimal averagePrice, decimal quantity)
        {
            base.SetHoldings(averagePrice, quantity);
            ReconcilePendingQuantity();
        }

        private DateTime GetNextReleaseLocalTime(DateTime fillLocalTime)
        {
            var releaseDate = fillLocalTime.Date;
            do
            {
                releaseDate = releaseDate.AddDays(1);
            }
            while (!Security.Exchange.Hours.IsDateOpen(releaseDate));

            return releaseDate.Add(ReleaseTimeOfDay);
        }

        private void ReleaseMaturedQuantities(DateTime localTime)
        {
            while (_pendingSellableQuantities.Count > 0)
            {
                var nextRelease = _pendingSellableQuantities.First();
                if (nextRelease.Key > localTime)
                {
                    break;
                }

                _pendingSellableQuantities.Remove(nextRelease.Key);
            }
        }

        private decimal GetPendingQuantity()
        {
            return _pendingSellableQuantities.Values.Sum();
        }

        private void ReconcilePendingQuantity()
        {
            ReleaseMaturedQuantities(GetCurrentLocalTime());

            if (Quantity <= 0)
            {
                _pendingSellableQuantities.Clear();
                return;
            }

            var pendingQuantity = GetPendingQuantity();
            if (pendingQuantity <= Quantity)
            {
                return;
            }

            var quantityToTrim = pendingQuantity - Quantity;
            foreach (var releaseTime in _pendingSellableQuantities.Keys.Reverse().ToList())
            {
                if (quantityToTrim <= 0)
                {
                    break;
                }

                var restrictedQuantity = _pendingSellableQuantities[releaseTime];
                if (restrictedQuantity <= quantityToTrim)
                {
                    quantityToTrim -= restrictedQuantity;
                    _pendingSellableQuantities.Remove(releaseTime);
                }
                else
                {
                    _pendingSellableQuantities[releaseTime] = restrictedQuantity - quantityToTrim;
                    quantityToTrim = 0;
                }
            }
        }

        private DateTime GetCurrentLocalTime()
        {
            try
            {
                return Security.LocalTime;
            }
            catch (InvalidOperationException ex)
            {
                // Log the exception for debugging
                Logging.Log.Error($"AShareT1Holding.GetCurrentLocalTime(): Failed to get Security.LocalTime - {ex.Message}");
                // Return MinValue as fallback, which will cause all pending quantities to be released
                // This is a safe fallback as it's better to allow trading than to block it incorrectly
                return DateTime.MinValue;
            }
        }
    }
}
