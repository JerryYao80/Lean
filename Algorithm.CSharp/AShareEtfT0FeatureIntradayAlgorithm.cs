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
using System.Globalization;
using System.IO;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Multi-field T+0 ETF strategy that consumes exported feature snapshots through LEAN custom data.
    /// It enters at the daily open and exits at the daily close of the same trading session.
    /// </summary>
    public class AShareEtfT0FeatureIntradayAlgorithm : QCAlgorithm
    {
        private readonly Dictionary<Symbol, Symbol> _featureToUnderlying = new Dictionary<Symbol, Symbol>();
        private readonly Dictionary<Symbol, AShareEtfT0FeatureData> _latestFeaturesByUnderlying = new Dictionary<Symbol, AShareEtfT0FeatureData>();
        private Symbol _anchorSymbol;
        private int _topN;
        private decimal _targetPortfolioExposure;
        private bool _excludeMoneyMarketEtfs;
        private DateTime _lastTradeDate;

        public override void Initialize()
        {
            var startDate = GetDateParameter("start-date", new DateTime(2024, 1, 1));
            var endDate = GetDateParameter("end-date", new DateTime(2025, 12, 31));
            _topN = GetIntParameter("top-n", 3);
            _targetPortfolioExposure = GetDecimalParameter("target-portfolio-exposure", 0.95m);
            _excludeMoneyMarketEtfs = GetBoolParameter("exclude-money-market-etfs", true);

            SetStartDate(startDate.Year, startDate.Month, startDate.Day);
            SetEndDate(endDate.Year, endDate.Month, endDate.Day);
            SetAccountCurrency("CNY");
            SetCash(1000000);
            Portfolio.CashBook.Add("CNY", 1000000, 1.0m);
            SetBenchmark(_ => 0);

            var featureDataPath = GetParameter("feature-data-path");
            if (!string.IsNullOrWhiteSpace(featureDataPath))
            {
                AShareEtfT0FeatureData.SetBaseDirectory(featureDataPath);
            }

            foreach (var ticker in AShareETFRegistry.GetT0ETFs().OrderBy(ticker => ticker))
            {
                if (_excludeMoneyMarketEtfs && IsMoneyMarketTicker(ticker))
                {
                    continue;
                }

                var metadata = AShareETFRegistry.GetMetadata(ticker);
                if (metadata == null)
                {
                    continue;
                }

                var market = metadata.Market == "SSE" ? Market.SSE : Market.SZSE;
                var underlyingSymbol = QuantConnect.Symbol.Create(ticker, SecurityType.Equity, market);
                if (!HasRequiredLocalData(underlyingSymbol))
                {
                    Log($"Skipping {ticker}: missing local price or feature data");
                    continue;
                }

                var equity = AddEquity(ticker, Resolution.Daily, market);
                equity.FeeModel = new AShareETFFeeModel();
                equity.FillModel = new AShareETFFillModel();
                equity.BuyingPowerModel = new AShareETFBuyingPowerModel();
                equity.Session.Size = 2;

                var featureSecurity = AddData<AShareEtfT0FeatureData>(equity.Symbol, Resolution.Daily, TimeZones.Shanghai, false);
                _featureToUnderlying[featureSecurity.Symbol] = equity.Symbol;
                _anchorSymbol ??= equity.Symbol;
            }

            if (_anchorSymbol == null)
            {
                throw new InvalidOperationException("No eligible A-share T+0 ETF symbols were added.");
            }

            Schedule.On(
                DateRules.EveryDay(_anchorSymbol),
                TimeRules.BeforeMarketOpen(_anchorSymbol, 1),
                TradeSession);

            Log($"AShareEtfT0FeatureIntradayAlgorithm initialized with {_featureToUnderlying.Count} feature subscriptions");
        }

        public override void OnData(Slice slice)
        {
            foreach (var pair in slice.Get<AShareEtfT0FeatureData>())
            {
                if (pair.Value == null)
                {
                    continue;
                }

                if (_featureToUnderlying.TryGetValue(pair.Key, out var underlying))
                {
                    _latestFeaturesByUnderlying[underlying] = pair.Value;
                }
            }
        }

        private void TradeSession()
        {
            if (_lastTradeDate == Time.Date)
            {
                return;
            }
            _lastTradeDate = Time.Date;

            var dailyFeatures = _latestFeaturesByUnderlying
                .Where(pair => pair.Value != null && pair.Value.HasSignals)
                .ToDictionary(pair => pair.Key, pair => pair.Value);
            if (dailyFeatures.Count == 0)
            {
                Log($"{Time:yyyy-MM-dd} no feature snapshots available");
                return;
            }

            var scores = AShareEtfT0FeatureSignalModel.ComputeScores(dailyFeatures);
            var ranked = scores
                .OrderByDescending(pair => pair.Value)
                .Take(_topN)
                .Where(pair => Securities.ContainsKey(pair.Key) && Securities[pair.Key].Price > 0)
                .ToList();
            if (ranked.Count == 0)
            {
                Log($"{Time:yyyy-MM-dd} no tradable symbols after ranking");
                return;
            }

            var targetValuePerSymbol = Portfolio.TotalPortfolioValue * _targetPortfolioExposure / ranked.Count;
            var selections = new List<string>();

            foreach (var pair in ranked)
            {
                var price = Securities[pair.Key].Price;
                var quantity = (int)(Math.Floor(targetValuePerSymbol / price / 100m) * 100m);
                if (quantity <= 0)
                {
                    continue;
                }

                MarketOnOpenOrder(pair.Key, quantity, tag: $"feature-open score={pair.Value:F4}");
                MarketOnCloseOrder(pair.Key, -quantity, tag: "feature-close");
                selections.Add($"{pair.Key.Value}:{pair.Value:F4}");
            }

            if (selections.Count > 0)
            {
                Log($"{Time:yyyy-MM-dd} selected {string.Join(", ", selections)}");
            }
        }

        private static bool HasRequiredLocalData(Symbol underlyingSymbol)
        {
            var customSymbol = QuantConnect.Symbol.CreateBase(typeof(AShareEtfT0FeatureData), underlyingSymbol, underlyingSymbol.ID.Market);
            var featurePath = AShareEtfT0FeatureData.ResolveSourcePath(customSymbol);
            var pricePath = Path.Combine(Globals.DataFolder, "equity", underlyingSymbol.ID.Market.ToLowerInvariant(), "daily", $"{underlyingSymbol.Value}.zip");
            return File.Exists(featurePath) && File.Exists(pricePath);
        }

        private static bool IsMoneyMarketTicker(string ticker)
        {
            return ticker.StartsWith("511", StringComparison.Ordinal)
                || ticker == "159001"
                || ticker == "159003"
                || ticker == "159005";
        }

        private int GetIntParameter(string name, int defaultValue)
        {
            return int.TryParse(GetParameter(name), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)
                ? value
                : defaultValue;
        }

        private decimal GetDecimalParameter(string name, decimal defaultValue)
        {
            return decimal.TryParse(GetParameter(name), NumberStyles.Any, CultureInfo.InvariantCulture, out var value)
                ? value
                : defaultValue;
        }

        private bool GetBoolParameter(string name, bool defaultValue)
        {
            return bool.TryParse(GetParameter(name), out var value)
                ? value
                : defaultValue;
        }

        private DateTime GetDateParameter(string name, DateTime defaultValue)
        {
            return DateTime.TryParseExact(GetParameter(name), "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var value)
                ? value
                : defaultValue;
        }
    }
}
