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
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Risk management model for Barra CNE5 V4 strategy.
    /// Implements stop loss, trailing stop with profit activation, cooldown period,
    /// max single weight constraint, Sharpe-based exposure scaling, and vol targeting.
    /// All within LEAN's native IRiskManagementModel framework.
    /// </summary>
    public class AShareBarraCNE5V4RiskManagementModel : RiskManagementModel
    {
        private readonly decimal _stopLossPct;
        private readonly decimal _profitActivationPct;
        private readonly decimal _trailingStopPct;
        private readonly int _cooldownDays;
        private readonly int _minHoldDaysForProfitProtection;
        private readonly decimal _maxSingleWeight;

        // Sharpe-based exposure scaling
        private readonly decimal _sharpeExposureBase;
        private readonly decimal _sharpeExposureSensitivity;
        private readonly int _sharpeLookbackDays;
        private readonly decimal _exposureSmoothingAlpha;
        private readonly decimal _exposureMinScale;
        private readonly decimal _exposureMaxScale;

        // Vol targeting
        private readonly bool _volTargetEnabled;
        private readonly decimal _volTargetAnnual;
        private readonly int _volTargetLookbackDays;
        private readonly decimal _volTargetFloorScale;
        private readonly decimal _volTargetCapScale;

        // Internal state
        private readonly Dictionary<Symbol, decimal> _peakPrices = new();
        private readonly Dictionary<Symbol, DateTime> _cooldownExpiry = new();
        private readonly Dictionary<Symbol, int> _holdingDays = new();
        private decimal _smoothedExposureScale;
        private decimal _currentVolScale = 1.0m;
        private decimal _previousEquity;
        private readonly List<decimal> _dailyReturns = new();

        // Latest computed values for runtime stats
        private decimal _latestExposureScale;
        private decimal _latestEffectiveExposure;

        public AShareBarraCNE5V4RiskManagementModel(
            decimal stopLossPct = 0.12m,
            decimal profitActivationPct = 0.18m,
            decimal trailingStopPct = 0.08m,
            int cooldownDays = 5,
            int minHoldDaysForProfitProtection = 2,
            decimal maxSingleWeight = 0.10m,
            // Sharpe exposure scaling
            decimal sharpeExposureBase = 0.70m,
            decimal sharpeExposureSensitivity = 0.25m,
            int sharpeLookbackDays = 63,
            decimal exposureSmoothingAlpha = 0.20m,
            decimal exposureMinScale = 0.50m,
            decimal exposureMaxScale = 1.00m,
            // Vol targeting
            bool volTargetEnabled = true,
            decimal volTargetAnnual = 0.15m,
            int volTargetLookbackDays = 20,
            decimal volTargetFloorScale = 0.50m,
            decimal volTargetCapScale = 1.50m,
            decimal initialCapital = 1000000m)
        {
            _stopLossPct = stopLossPct;
            _profitActivationPct = profitActivationPct;
            _trailingStopPct = trailingStopPct;
            _cooldownDays = cooldownDays;
            _minHoldDaysForProfitProtection = minHoldDaysForProfitProtection;
            _maxSingleWeight = maxSingleWeight;

            _sharpeExposureBase = sharpeExposureBase;
            _sharpeExposureSensitivity = sharpeExposureSensitivity;
            _sharpeLookbackDays = sharpeLookbackDays;
            _exposureSmoothingAlpha = exposureSmoothingAlpha;
            _exposureMinScale = exposureMinScale;
            _exposureMaxScale = exposureMaxScale;

            _volTargetEnabled = volTargetEnabled;
            _volTargetAnnual = volTargetAnnual;
            _volTargetLookbackDays = volTargetLookbackDays;
            _volTargetFloorScale = volTargetFloorScale;
            _volTargetCapScale = volTargetCapScale;

            _smoothedExposureScale = sharpeExposureBase;
            _previousEquity = initialCapital;
            _latestExposureScale = sharpeExposureBase;
            _latestEffectiveExposure = sharpeExposureBase;
        }

        /// <summary>
        /// Get the latest exposure scale for runtime statistics.
        /// </summary>
        public decimal LatestExposureScale => _latestExposureScale;

        /// <summary>
        /// Get the latest effective exposure for runtime statistics.
        /// </summary>
        public decimal LatestEffectiveExposure => _latestEffectiveExposure;

        /// <summary>
        /// Get the current vol target scale for runtime statistics.
        /// </summary>
        public decimal CurrentVolScale => _currentVolScale;

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var sessionDate = algorithm.Time.Date;
            var riskAdjustedTargets = new List<IPortfolioTarget>();
            var symbolsToLiquidate = new HashSet<Symbol>();

            // Track daily returns for Sharpe and vol targeting
            var currentEquity = algorithm.Portfolio.TotalPortfolioValue;
            if (_previousEquity > 0m)
            {
                var dailyReturn = currentEquity / _previousEquity - 1m;
                _dailyReturns.Add(dailyReturn);
            }
            _previousEquity = currentEquity;

            // Increment holding days
            foreach (var key in _holdingDays.Keys.ToList())
            {
                _holdingDays[key] += 1;
            }

            // Prune expired cooldowns
            foreach (var pair in _cooldownExpiry.ToList())
            {
                if (pair.Value.Date < sessionDate) _cooldownExpiry.Remove(pair.Key);
            }

            // Per-security risk checks
            foreach (var kvp in algorithm.Securities)
            {
                var security = kvp.Value;
                if (!security.Invested) continue;

                var symbol = security.Symbol;
                var avgPrice = security.Holdings.AveragePrice;
                var currentPrice = security.Price;
                if (avgPrice <= 0m || currentPrice <= 0m) continue;

                // Update peak price
                if (!_peakPrices.ContainsKey(symbol) || currentPrice > _peakPrices[symbol])
                {
                    _peakPrices[symbol] = currentPrice;
                }
                var peakPrice = _peakPrices[symbol];

                // Check cooldown
                if (_cooldownExpiry.TryGetValue(symbol, out var cooldownUntil) && cooldownUntil.Date >= sessionDate)
                {
                    // In cooldown — force liquidation target to 0
                    algorithm.Insights.Cancel(new[] { symbol });
                    riskAdjustedTargets.Add(new PortfolioTarget(symbol, 0));
                    symbolsToLiquidate.Add(symbol);
                    continue;
                }

                // Stop loss
                if (_stopLossPct > 0m && currentPrice <= avgPrice * (1m - _stopLossPct))
                {
                    algorithm.Insights.Cancel(new[] { symbol });
                    riskAdjustedTargets.Add(new PortfolioTarget(symbol, 0));
                    symbolsToLiquidate.Add(symbol);
                    _peakPrices.Remove(symbol);
                    _holdingDays.Remove(symbol);
                    _cooldownExpiry[symbol] = sessionDate.AddDays(_cooldownDays);
                    continue;
                }

                // Trailing stop with profit activation
                var holdingDays = _holdingDays.TryGetValue(symbol, out var hd) ? hd : 0;
                var profitActivated = _profitActivationPct > 0m &&
                    holdingDays >= _minHoldDaysForProfitProtection &&
                    peakPrice >= avgPrice * (1m + _profitActivationPct);
                if (profitActivated && _trailingStopPct > 0m && currentPrice <= peakPrice * (1m - _trailingStopPct))
                {
                    algorithm.Insights.Cancel(new[] { symbol });
                    riskAdjustedTargets.Add(new PortfolioTarget(symbol, 0));
                    symbolsToLiquidate.Add(symbol);
                    _peakPrices.Remove(symbol);
                    _holdingDays.Remove(symbol);
                    _cooldownExpiry[symbol] = sessionDate.AddDays(_cooldownDays);
                    continue;
                }

                // Max single weight constraint
                var currentWeight = security.Holdings.HoldingsValue / currentEquity;
                if (_maxSingleWeight > 0m && currentWeight > _maxSingleWeight)
                {
                    var targetValue = currentEquity * _maxSingleWeight;
                    var targetQuantity = (int)(targetValue / currentPrice / 100m) * 100m;
                    if (targetQuantity > 0)
                    {
                        riskAdjustedTargets.Add(new PortfolioTarget(symbol, (decimal)targetQuantity));
                    }
                    else
                    {
                        algorithm.Insights.Cancel(new[] { symbol });
                        riskAdjustedTargets.Add(new PortfolioTarget(symbol, 0));
                        symbolsToLiquidate.Add(symbol);
                    }
                    continue;
                }
            }

            // Compute Sharpe-based exposure scaling
            _latestExposureScale = ComputeSharpeExposureScale();

            // Compute vol target scaling
            _currentVolScale = ComputeVolTargetScale();

            // Scale non-liquidated portfolio targets by exposure and vol
            var totalScale = _latestExposureScale * _currentVolScale;
            _latestEffectiveExposure = Math.Min(1m, totalScale);

            foreach (var target in targets)
            {
                if (symbolsToLiquidate.Contains(target.Symbol)) continue;

                // Scale the target quantity by the exposure/vol multiplier
                var scaledQuantity = target.Quantity * (decimal)totalScale;
                if (scaledQuantity != 0)
                {
                    riskAdjustedTargets.Add(new PortfolioTarget(target.Symbol, scaledQuantity));
                }
            }

            return riskAdjustedTargets;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var security in changes.AddedSecurities)
            {
                if (security.Invested && !_holdingDays.ContainsKey(security.Symbol))
                {
                    _holdingDays[security.Symbol] = 0;
                }
            }

            foreach (var security in changes.RemovedSecurities)
            {
                _peakPrices.Remove(security.Symbol);
                _holdingDays.Remove(security.Symbol);
                _cooldownExpiry.Remove(security.Symbol);
            }
        }

        private decimal ComputeSharpeExposureScale()
        {
            var minDays = Math.Max(20, _sharpeLookbackDays / 3);
            if (_dailyReturns.Count < minDays)
            {
                return _smoothedExposureScale;
            }

            var lookbackReturns = _dailyReturns
                .TakeLast(Math.Min(_sharpeLookbackDays, _dailyReturns.Count))
                .ToList();

            var meanDailyReturn = lookbackReturns.Average();
            var variance = lookbackReturns.Count > 1
                ? lookbackReturns.Select(r => Math.Pow((double)(r - meanDailyReturn), 2)).Average()
                : 0.0;
            var stdDailyReturn = (decimal)Math.Sqrt(variance);

            var annualizedSharpe = stdDailyReturn > 0m
                ? meanDailyReturn / stdDailyReturn * (decimal)Math.Sqrt(252)
                : 0m;

            var rawScale = Clamp(
                _sharpeExposureBase + _sharpeExposureSensitivity * annualizedSharpe,
                _exposureMinScale,
                _exposureMaxScale);

            var smoothedScale = _exposureSmoothingAlpha * rawScale
                              + (1m - _exposureSmoothingAlpha) * _smoothedExposureScale;
            _smoothedExposureScale = smoothedScale;

            return smoothedScale;
        }

        private decimal ComputeVolTargetScale()
        {
            if (!_volTargetEnabled) return 1.0m;
            if (_dailyReturns.Count < _volTargetLookbackDays) return 1.0m;

            var lookback = _dailyReturns.TakeLast(_volTargetLookbackDays).ToList();
            var mean = lookback.Average();
            var variance = lookback.Select(r => Math.Pow((double)(r - mean), 2)).Average();
            var std = (decimal)Math.Sqrt(variance);
            var annualizedVol = std * (decimal)Math.Sqrt(252);

            if (annualizedVol <= 0m) return 1.0m;
            var rawScale = _volTargetAnnual / annualizedVol;
            return Clamp(rawScale, _volTargetFloorScale, _volTargetCapScale);
        }

        private static decimal Clamp(decimal value, decimal minValue, decimal maxValue)
        {
            if (value < minValue) return minValue;
            return value > maxValue ? maxValue : value;
        }
    }
}
