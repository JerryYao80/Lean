using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Data.AShare;
using QuantConnect.Securities.Option;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Computes HO (上证50股指期权) IV and VIX using LEAN native indicators
    /// </summary>
    public class AShareHOVixAlgorithm : QCAlgorithm
    {
        private const string Underlying = "HO";
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

            // HO is IndexOption - first add the underlying index
            var index = AddIndex(Underlying, Resolution.Daily, market: global::QuantConnect.Market.CFE);
            _option = AddIndexOption(index.Symbol, "HO", Resolution.Daily);
            Log($"HO option symbol: {_option.Symbol}, underlying: {_option.Symbol.Underlying}, canonical: {_option.Symbol.HasUnderlying}");
            SetOptionChainProvider(new AShareOptionChainProvider(Globals.DataFolder));
            _option.SetFilter(u => u.Strikes(-20, +20).Expiration(0, 365));

            var tusharePath = "/home/project/tushare-downloader/tushare_data_v2";
            _rateModel = new AShareShiborRateModel(tusharePath);
            _divModel = new AShareDividendYieldModel(tusharePath, Underlying);
            _vixIndicator = new AShareVixIndicator((double)_rateModel.GetInterestRate(Time));
        }

        public override void OnData(Slice data)
        {
            // DEBUG: dump option chains and bars status
            if (Time.Day == 3)
            {
                var symbolList = string.Join(", ", data.OptionChains.Keys.Select(k => k.Value));
                Log($"DBG OnData {Time:yyyyMMdd}: optionChains count={data.OptionChains.Count}, symbols={symbolList}");
                foreach (var kvp in data.OptionChains)
                {
                    Log($"  chain={kvp.Key} contracts={kvp.Value.Count()}");
                }
                var debugUnderlying = _option.Symbol.Underlying;
                Log($"DBG underlying={debugUnderlying}, bar exists={data.Bars.ContainsKey(debugUnderlying)}");
            }

            OptionChain chain;
            if (!data.OptionChains.TryGetValue(_option.Symbol, out chain))
            {
                if (Time.Day == 3) Log($"OnData {Time:yyyyMMdd}: no chain for {_option.Symbol}");
                return;
            }
            Log($"OnData {Time:yyyyMMdd}: chain contracts={chain.Count()}");
            if (chain.Count() == 0) return;

            var underlyingSymbol = _option.Symbol.Underlying;
            if (!data.Bars.TryGetValue(underlyingSymbol, out var bar)) return;
            var S = bar.Close;
            if (S <= 0m) return;

            var byExpiry = chain.GroupBy(c => c.Expiry).OrderBy(g => g.Key).ToList();
            if (byExpiry.Count < 2) return;
            var near = byExpiry[0];
            var next = byExpiry[1];

            var atmIv = ComputeAtmIvNative(near, S);
            var (call25, put25) = Compute25DeltaIvNative(near, S);
            var skew = (call25.HasValue && put25.HasValue) ? put25.Value - call25.Value : (decimal?)null;

            var nearData = near.Select(ToContractData).ToList();
            var nextData = next.Select(ToContractData).ToList();
            var vix = _vixIndicator.Calculate(Time, near.Key, next.Key, nearData, nextData);

            _csvRows.Add($"{Time:yyyyMMdd},{atmIv:0.00000000},{Format(call25)},{Format(put25)},{Format(skew)}," +
                $"{(near.Key - Time).Days},{(next.Key - Time).Days},{near.Count()}," +
                $"{FormatVix(vix.Vix)},{FormatVix(vix.SigmaNear)},{FormatVix(vix.SigmaNext)},{vix.TNear:0.00000000},{vix.TNext:0.00000000}");
        }

        private static OptionContractData ToContractData(OptionContract c) => new() { Strike = c.Strike, Right = c.Right, Price = c.LastPrice };

        private decimal ComputeAtmIvNative(IGrouping<DateTime, OptionContract> near, decimal S)
        {
            var atmContracts = near.Where(c => Math.Abs(c.Strike - S) / S < 0.05m).ToList();
            if (!atmContracts.Any()) atmContracts = near.ToList();
            var ivs = new List<decimal>();
            foreach (var c in atmContracts)
            {
                var iv = GetIv(c, S);
                if (iv > 0.01m && iv < 3m) ivs.Add(iv);
            }
            if (!ivs.Any()) return 0m;
            var ordered = ivs.OrderBy(x => x).ToList();
            return ordered[ordered.Count / 2];
        }

        private (decimal?, decimal?) Compute25DeltaIvNative(IGrouping<DateTime, OptionContract> near, decimal S)
        {
            var call = near.Where(c => c.Right == OptionRight.Call && c.Strike > S).OrderBy(c => c.Strike).FirstOrDefault();
            var put = near.Where(c => c.Right == OptionRight.Put && c.Strike < S).OrderByDescending(c => c.Strike).FirstOrDefault();
            return (call != null ? GetIv(call, S) : null, put != null ? GetIv(put, S) : null);
        }

        private decimal GetIv(OptionContract c, decimal underlyingPrice)
        {
            var iv = new ImpliedVolatility(c.Symbol, _rateModel, _divModel, optionModel: OptionPricingModelType.BlackScholes);
            iv.Update(new TradeBar(Time, c.Symbol, c.LastPrice, c.LastPrice, c.LastPrice, c.LastPrice, 0));
            iv.Update(new TradeBar(Time, c.Symbol.Underlying, underlyingPrice, underlyingPrice, underlyingPrice, underlyingPrice, 0));
            return iv.IsReady ? iv.Current.Value : 0m;
        }

        private static string Format(decimal? v) => v.HasValue ? $"{v.Value:0.00000000}" : "";
        private static string FormatVix(double v) => v > 0 ? $"{v:0.000000}" : "";

        public override void OnEndOfAlgorithm()
        {
            var csvPath = Path.Combine(Globals.DataFolder, "alternative", "ashare-implied-volatility", "cffex", "daily", $"{Underlying}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(csvPath));
            File.WriteAllLines(csvPath, new[] { "trade_date,atm_iv,iv_call_25delta,iv_put_25delta,skew,term_days_near,term_days_next,option_count,vix,sigma_near,sigma_next,t_near,t_next" }.Concat(_csvRows));
            Log($"AShareHOVixAlgorithm: wrote {_csvRows.Count} rows to {csvPath}");
        }
    }
}