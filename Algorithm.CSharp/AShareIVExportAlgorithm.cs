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
using System.IO;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.AShare;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Securities.Option;
using QuantConnect.ToolBox;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Computes A-share ETF IV using the LEAN-native ImpliedVolatility indicator
    /// and a CBOE model-free VIX, then exports a CSV in the existing format.
    /// </summary>
    public class AShareIVExportAlgorithm : QCAlgorithm
    {
        private const string Underlying = "510050";
        private Option _option;
        private AShareShiborRateModel _rateModel;
        private AShareDividendYieldModel _divModel;
        private AShareVixIndicator _vixIndicator;
        private readonly List<string> _csvRows = new();

        public override void Initialize()
        {
            SetStartDate(2024, 6, 1);
            SetEndDate(2024, 6, 28);
            SetCash(100000);

            AddEquity(Underlying, Resolution.Daily, market: Market.China);
            _option = AddOption(Underlying, Resolution.Daily, market: Market.China);
            _option.SetOptionChainProvider(new AShareOptionChainProvider(Globals.DataFolder));
            _option.SetFilter(u => u
                .Includes(OptionRight.Call)
                .Includes(OptionRight.Put)
                .FrontMonth()
                .BackMonths()
                .MovesAfter(20));

            var tusharePath = "/home/project/tushare-downloader/tushare_data_v2";
            _rateModel = new AShareShiborRateModel(tusharePath);
            _divModel = new AShareDividendYieldModel(tusharePath, Underlying);
            _vixIndicator = new AShareVixIndicator((double)_rateModel.GetInterestRate(Time));
        }

        public override void OnData(Slice data)
        {
            OptionChain chain;
            if (!data.OptionChains.TryGetValue(_option.Symbol, out chain)) return;

            var underlyingSymbol = _option.Symbol.Underlying;
            if (!data.Bars.TryGetValue(underlyingSymbol, out var bar)) return;
            var S = bar.Close;
            if (S <= 0m) return;

            // Split into near / next expiry
            var byExpiry = chain.GroupBy(c => c.Expiry).OrderBy(g => g.Key).ToList();
            if (byExpiry.Count < 2) return;
            var near = byExpiry[0];
            var next = byExpiry[1];

            // Native IV (LEAN ImpliedVolatility indicator, Brent solver)
            var atmIv = ComputeAtmIvNative(near, S);
            var (call25, put25) = Compute25DeltaIvNative(near, S);
            var skew = (call25.HasValue && put25.HasValue) ? put25.Value - call25.Value : (decimal?)null;

            // VIX model-free on real settlement prices
            var nearData = near.Select(ToContractData).ToList();
            var nextData = next.Select(ToContractData).ToList();
            var vix = _vixIndicator.Calculate(Time, near.Key, next.Key, nearData, nextData);

            _csvRows.Add(
                $"{Time:yyyyMMdd},{atmIv:0.00000000}," +
                $"{Format(call25)},{Format(put25)},{Format(skew)}," +
                $"{(near.Key - Time).Days},{(next.Key - Time).Days},{near.Count()}," +
                $"{FormatVix(vix.Vix)},{FormatVix(vix.SigmaNear)},{FormatVix(vix.SigmaNext)}," +
                $"{vix.TNear:0.00000000},{vix.TNext:0.00000000}");
        }

        private static OptionContractData ToContractData(OptionContract c)
        {
            return new OptionContractData
            {
                Strike = c.Strike,
                Right = c.Right,
                Price = c.Settlement != null && c.Settlement != 0m ? (decimal)c.Settlement : (decimal)c.Close
            };
        }

        private decimal ComputeAtmIvNative(IGrouping<DateTime, OptionContract> near, decimal S)
        {
            var atmContracts = near.Where(c => Math.Abs(c.Strike - S) / S < 0.05m).ToList();
            if (!atmContracts.Any()) atmContracts = near.ToList();

            var ivs = new List<decimal>();
            foreach (var c in atmContracts)
            {
                var iv = GetIv(c);
                if (iv > 0.01m && iv < 3m) ivs.Add(iv);
            }
            if (!ivs.Any()) return 0m;
            var ordered = ivs.OrderBy(x => x).ToList();
            return ordered[ordered.Count / 2];  // median
        }

        private (decimal?, decimal?) Compute25DeltaIvNative(IGrouping<DateTime, OptionContract> near, decimal S)
        {
            // 25-delta approximation: first OTM call above spot, first OTM put below spot
            var call = near.Where(c => c.Right == OptionRight.Call && c.Strike > S).OrderBy(c => c.Strike).FirstOrDefault();
            var put = near.Where(c => c.Right == OptionRight.Put && c.Strike < S).OrderByDescending(c => c.Strike).FirstOrDefault();

            decimal? callIv = call != null ? GetIv(call) : (decimal?)null;
            decimal? putIv = put != null ? GetIv(put) : (decimal?)null;
            return (callIv, putIv);
        }

        private decimal GetIv(OptionContract c)
        {
            var iv = new ImpliedVolatility(c.Symbol, _rateModel, _divModel,
                optionModel: OptionPricingModelType.BlackScholes);
            iv.Update(Time, c.Close);
            return iv.IsReady ? iv.Current.Value : 0m;
        }

        private static string Format(decimal? v) => v.HasValue ? $"{v.Value:0.00000000}" : "";
        private static string FormatVix(double v) => v > 0 ? $"{v:0.000000}" : "";

        public override void OnEndOfAlgorithm()
        {
            var csvPath = Path.Combine(Globals.DataFolder, "alternative",
                "ashare-implied-volatility", "sse", "daily", $"{Underlying}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(csvPath));
            var header = "trade_date,atm_iv,iv_call_25delta,iv_put_25delta,skew," +
                         "term_days_near,term_days_next,option_count,vix,sigma_near," +
                         "sigma_next,t_near,t_next";
            File.WriteAllLines(csvPath, new[] { header }.Concat(_csvRows));
            Log($"AShareIVExportAlgorithm: wrote {_csvRows.Count} rows to {csvPath}");
        }
    }
}
