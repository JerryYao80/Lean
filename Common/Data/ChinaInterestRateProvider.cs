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

using QuantConnect.Interfaces;
using QuantConnect.Util;
using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Data
{
    /// <summary>
    /// China SHIBOR 1-Year rate provider for A-share market risk-free rate calculations.
    /// Loads daily SHIBOR 1Y rates from Data/alternative/interest-rate/china/interest-rate.csv
    /// and fills gaps (weekends/holidays) with the previous available rate.
    /// </summary>
    public class ChinaInterestRateProvider : IRiskFreeInterestRateModel
    {
        private static readonly DateTime _firstInterestRateDate = new DateTime(2006, 10, 8);
        private static DateTime _lastInterestRateDate;
        private static Dictionary<DateTime, decimal> _riskFreeRateProvider;
        private static readonly object _lock = new();

        /// <summary>
        /// Default Risk Free Rate of 3% for China market
        /// </summary>
        public static readonly decimal DefaultRiskFreeRate = 0.03m;

        /// <summary>
        /// Lazily loads the interest rate provider from disk and returns it
        /// </summary>
        private IReadOnlyDictionary<DateTime, decimal> RiskFreeRateProvider
        {
            get
            {
                if (_riskFreeRateProvider != null)
                {
                    return _riskFreeRateProvider;
                }

                lock (_lock)
                {
                    _riskFreeRateProvider ??= GetInterestRateProvider();
                    return _riskFreeRateProvider;
                }
            }
        }

        /// <summary>
        /// Get interest rate by a given date
        /// </summary>
        /// <param name="date">The date</param>
        /// <returns>Interest rate on the given date</returns>
        public decimal GetInterestRate(DateTime date)
        {
            if (!RiskFreeRateProvider.TryGetValue(date.Date, out var interestRate))
            {
                return date < _firstInterestRateDate
                    ? RiskFreeRateProvider[_firstInterestRateDate]
                    : RiskFreeRateProvider[_lastInterestRateDate];
            }

            return interestRate;
        }

        /// <summary>
        /// Generate the daily historical China SHIBOR 1Y rate
        /// </summary>
        protected static Dictionary<DateTime, decimal> GetInterestRateProvider()
        {
            var directory = PathCombine(Globals.DataFolder, "alternative", "interest-rate", "china",
                "interest-rate.csv");
            var riskFreeRateProvider = InterestRateProvider.FromCsvFile(directory, out var previousInterestRate);

            _lastInterestRateDate = DateTime.UtcNow.Date;

            // Sparse the discrete data points into continuous rate data for every day
            for (var date = _firstInterestRateDate; date <= _lastInterestRateDate; date = date.AddDays(1))
            {
                if (!riskFreeRateProvider.TryGetValue(date, out var currentRate))
                {
                    riskFreeRateProvider[date] = previousInterestRate;
                    continue;
                }

                previousInterestRate = currentRate;
            }

            return riskFreeRateProvider;
        }

        private static string PathCombine(params string[] parts)
        {
            return System.IO.Path.Combine(parts);
        }
    }
}
