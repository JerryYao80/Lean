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
using QuantConnect.Data.AShare;
using QuantConnect.Data.Market;
using QuantConnect.Indicators;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities.Option;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// A-share ETF option volatility arbitrage strategy.
    ///
    /// Core signals:
    /// 1. IV-RV spread: sell volatility when IV >> RV, buy when IV << RV
    /// 2. Term structure: calendar spread when near-term vol inverted
    /// 3. Skew extreme: risk reversal when skew at extreme percentiles
    ///
    /// LEAN native architecture: AddOption() + AShareOptionChainProvider
    /// Delta hedging with underlying ETF.
    /// </summary>
    public class AShareOptionVolatilityArbitrageAlgorithm : QCAlgorithm
    {
        private const string Underlying = "510050";
        private const int LotSize = 10000; // A-share option lot size

        private Option _option;
        private Symbol _underlyingSymbol;
        private AShareShiborRateModel _rateModel;
        private AShareDividendYieldModel _divModel;

        // Parameters
        private decimal _initialCapital = 1000000m;
        private decimal _ivRvZScoreThreshold = 2.0m;
        private decimal _ivtsThreshold = 1.3m;
        private decimal _skewPercentileHigh = 0.90m;
        private decimal _skewPercentileLow = 0.10m;
        private int _rvLookbackDays = 20;
        private int _ivLookbackDays = 60;
        private bool _deltaHedgeEnabled = true;
        private decimal _maxPositionValue = 200000m;
        private decimal _stopLossPercent = 0.20m;

        // State
        private RollingWindow<decimal> _ivHistory;
        private RollingWindow<decimal> _rvHistory;
        private RollingWindow<decimal> _skewHistory;
        private List<OptionPosition> _optionPositions;
        private decimal _entryPortfolioValue;

        public override void Initialize()
        {
            SetStartDate(2024, 6, 3);
            SetEndDate(2024, 6, 28);
            SetCash(_initialCapital); // USD nominal, treat as CNY for A-share options

            // Underlying ETF
            var equity = AddEquity(Underlying, Resolution.Daily, market: Market.SSE);
            equity.FeeModel = new ConstantFeeModel(5m);
            _underlyingSymbol = equity.Symbol;

            // Options chain - relaxed filter to include all contracts
            _option = AddOption(Underlying, Resolution.Daily, market: Market.SSE);
            _option.FeeModel = new ConstantFeeModel(1.3m);
            SetOptionChainProvider(new AShareOptionChainProvider(Globals.DataFolder));
            // VERY relaxed filter: all strikes, all expiries
            _option.SetFilter(u => u.Strikes(-100, +100).Expiration(0, 365));

            // Rate models for IV calculation
            var tusharePath = "/home/project/tushare-downloader/tushare_data_v2";
            _rateModel = new AShareShiborRateModel(tusharePath);
            _divModel = new AShareDividendYieldModel(tusharePath, Underlying);

            // History windows
            _ivHistory = new RollingWindow<decimal>(_ivLookbackDays);
            _rvHistory = new RollingWindow<decimal>(_rvLookbackDays);
            _skewHistory = new RollingWindow<decimal>(60);
            _optionPositions = new List<OptionPosition>();
            _entryPortfolioValue = _initialCapital;

            SetWarmUp(60, Resolution.Daily);
        }

        public override void OnData(Slice data)
        {
            if (IsWarmingUp) return;

            // Get option chain
            if (!data.OptionChains.TryGetValue(_option.Symbol, out var chain) || chain.Count() == 0)
            {
                // DEBUG: log missing chain
                if (Time.Day <= 10)
                    Log($"[{Time:yyyyMMdd}] NO CHAIN for {_option.Symbol}, OptionChains keys: {string.Join(',', data.OptionChains.Keys.Select(k => k.Value))}");
                return;
            }

            // DEBUG: log chain arrival
            Log($"[{Time:yyyyMMdd}] CHAIN LOADED: {chain.Count()} contracts, first expiry: {chain.First().Expiry:yyyyMMdd}");

            // Get underlying price
            if (!data.Bars.TryGetValue(_underlyingSymbol, out var underlyingBar))
            {
                Log($"[{Time:yyyyMMdd}] NO underlying bar for {_underlyingSymbol}");
                return;
            }
            var S = underlyingBar.Close;
            Log($"[{Time:yyyyMMdd}] Underlying price: {S}");

            // Compute signals
            var (iv, rv, skew, ivts) = ComputeSignals(chain, S);
            if (iv == null || rv == null) return;

            // Update history
            _ivHistory.Add(iv.Value);
            _rvHistory.Add(rv.Value);
            if (skew.HasValue) _skewHistory.Add(skew.Value);

            // Check stop loss
            if (CheckStopLoss())
            {
                LiquidateAll();
                return;
            }

            // Generate trading signals
            var signals = GenerateSignals(iv.Value, rv.Value, skew, ivts);

            // Execute trades
            ExecuteTrades(signals, chain, S);

            // Delta hedge
            if (_deltaHedgeEnabled)
                DeltaHedge(S);

            // Runtime stats
            SetRuntimeStatistic("IV", $"{iv.Value:P2}");
            SetRuntimeStatistic("RV", $"{rv.Value:P2}");
            SetRuntimeStatistic("IV-RV", $"{(iv.Value - rv.Value):P2}");
            SetRuntimeStatistic("IVTS", $"{ivts:F2}");
            SetRuntimeStatistic("Positions", _optionPositions.Count.ToString());
        }

        private (decimal? iv, decimal? rv, decimal? skew, decimal ivts) ComputeSignals(
            OptionChain chain, decimal S)
        {
            // Split by expiry
            var byExpiry = chain.GroupBy(c => c.Expiry).OrderBy(g => g.Key).ToList();
            if (byExpiry.Count < 2) return (null, null, null, 1.0m);

            var near = byExpiry[0];
            var next = byExpiry[1];
            var T_near = (double)(near.Key - Time).Days / 242.0;
            var T_next = (double)(next.Key - Time).Days / 242.0;

            // ATM IV (near-term)
            var atmIv = ComputeAtmIv(near, S, T_near);
            if (atmIv == null) return (null, null, null, 1.0m);

            // Realized vol (from underlying history)
            var rv = ComputeRealizedVol();

            // Skew (25-delta)
            var skew = ComputeSkew(near, S, T_near);

            // IVTS (near vs next term IV)
            var nextIv = ComputeAtmIv(next, S, T_next);
            var ivts = nextIv.HasValue && nextIv.Value > 0 ? atmIv.Value / nextIv.Value : 1.0m;

            return (atmIv, rv, skew, ivts);
        }

        private decimal? ComputeAtmIv(IGrouping<DateTime, OptionContract> expiry, decimal S, double T)
        {
            var atmContracts = expiry.Where(c => Math.Abs(c.Strike - S) / S < 0.02m).ToList();
            if (!atmContracts.Any()) atmContracts = expiry.ToList();

            var ivs = new List<decimal>();
            foreach (var c in atmContracts)
            {
                var iv = GetImpliedVolatility(c, S, T);
                if (iv.HasValue && iv.Value > 0.05m && iv.Value < 2.0m)
                    ivs.Add(iv.Value);
            }

            if (!ivs.Any()) return null;
            ivs.Sort();
            return ivs[ivs.Count / 2]; // Median
        }

        private decimal? GetImpliedVolatility(OptionContract contract, decimal S, double T)
        {
            var iv = new ImpliedVolatility(contract.Symbol, _rateModel, _divModel,
                optionModel: OptionPricingModelType.BlackScholes);

            var optBar = new TradeBar(Time, contract.Symbol, contract.LastPrice, contract.LastPrice,
                contract.LastPrice, contract.LastPrice, 0);
            var undBar = new TradeBar(Time, _underlyingSymbol, S, S, S, S, 0);

            iv.Update(optBar);
            iv.Update(undBar);

            return iv.IsReady ? iv.Current.Value : (decimal?)null;
        }

        private decimal? ComputeRealizedVol()
        {
            var history = History<TradeBar>(_underlyingSymbol, _rvLookbackDays, Resolution.Daily);
            var prices = history.Select(b => b.Close).ToList();
            if (prices.Count < 10) return null;

            var returns = new List<decimal>();
            for (int i = 1; i < prices.Count; i++)
            {
                if (prices[i - 1] > 0)
                    returns.Add((prices[i] - prices[i - 1]) / prices[i - 1]);
            }

            if (returns.Count < 5) return null;
            var mean = returns.Average();
            var variance = returns.Select(r => (r - mean) * (r - mean)).Sum() / (returns.Count - 1);
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(242);
        }

        private decimal? ComputeSkew(IGrouping<DateTime, OptionContract> expiry, decimal S, double T)
        {
            var calls = expiry.Where(c => c.Right == OptionRight.Call && c.Strike > S)
                .OrderBy(c => c.Strike).ToList();
            var puts = expiry.Where(c => c.Right == OptionRight.Put && c.Strike < S)
                .OrderByDescending(c => c.Strike).ToList();

            if (!calls.Any() || !puts.Any()) return null;

            var callIv = GetImpliedVolatility(calls[0], S, T);
            var putIv = GetImpliedVolatility(puts[0], S, T);

            if (!callIv.HasValue || !putIv.HasValue) return null;
            return putIv.Value - callIv.Value; // Risk reversal
        }

        private List<TradeSignal> GenerateSignals(decimal iv, decimal rv, decimal? skew, decimal ivts)
        {
            var signals = new List<TradeSignal>();

            // IV-RV spread (z-score)
            if (_ivHistory.Count >= 20 && _rvHistory.Count >= 20)
            {
                var ivRvSpread = _ivHistory.Select((iv_, i) => iv_ - _rvHistory[i]).ToList();
                var meanSpread = ivRvSpread.Average();
                var stdSpread = (decimal)Math.Sqrt((double)ivRvSpread.Select(x => (x - meanSpread) * (x - meanSpread)).Sum() / (ivRvSpread.Count - 1));
                var currentSpread = iv - rv;
                var zScore = stdSpread > 0.001m ? (currentSpread - meanSpread) / stdSpread : 0m;

                if (zScore > _ivRvZScoreThreshold)
                    signals.Add(new TradeSignal { Type = SignalType.SellVolatility, Strength = Math.Abs(zScore) });
                else if (zScore < -_ivRvZScoreThreshold)
                    signals.Add(new TradeSignal { Type = SignalType.BuyVolatility, Strength = Math.Abs(zScore) });
            }

            // Term structure
            if (ivts > _ivtsThreshold)
                signals.Add(new TradeSignal { Type = SignalType.CalendarSpreadSell, Strength = ivts - 1.0m });
            else if (ivts < 1.0m / _ivtsThreshold)
                signals.Add(new TradeSignal { Type = SignalType.CalendarSpreadBuy, Strength = 1.0m - ivts });

            // Skew extreme
            if (skew.HasValue && _skewHistory.Count >= 40)
            {
                var sorted = _skewHistory.OrderBy(x => x).ToList();
                var highPct = sorted[(int)(_skewHistory.Count * _skewPercentileHigh)];
                var lowPct = sorted[(int)(_skewHistory.Count * _skewPercentileLow)];

                if (skew.Value > highPct)
                    signals.Add(new TradeSignal { Type = SignalType.SellSkew, Strength = skew.Value - highPct });
                else if (skew.Value < lowPct)
                    signals.Add(new TradeSignal { Type = SignalType.BuySkew, Strength = lowPct - skew.Value });
            }

            return signals;
        }

        private void ExecuteTrades(List<TradeSignal> signals, OptionChain chain, decimal S)
        {
            foreach (var signal in signals.OrderByDescending(s => s.Strength))
            {
                if (_optionPositions.Sum(p => p.MarketValue) > _maxPositionValue)
                    break;

                switch (signal.Type)
                {
                    case SignalType.SellVolatility:
                        SellStraddle(chain, S);
                        break;
                    case SignalType.BuyVolatility:
                        BuyStraddle(chain, S);
                        break;
                    case SignalType.CalendarSpreadSell:
                        SellCalendarSpread(chain, S);
                        break;
                    case SignalType.CalendarSpreadBuy:
                        BuyCalendarSpread(chain, S);
                        break;
                    case SignalType.SellSkew:
                        SellRiskReversal(chain, S);
                        break;
                    case SignalType.BuySkew:
                        BuyRiskReversal(chain, S);
                        break;
                }
            }
        }

        private void SellStraddle(OptionChain chain, decimal S)
        {
            var atmStrike = FindAtmStrike(chain, S);
            if (atmStrike == null) return;

            var call = FindContract(chain, atmStrike.Value, OptionRight.Call, 0);
            var put = FindContract(chain, atmStrike.Value, OptionRight.Put, 0);

            if (call != null && put != null)
            {
                Sell(call.Symbol, 1);
                Sell(put.Symbol, 1);
                _optionPositions.Add(new OptionPosition { Symbol = call.Symbol, Quantity = -1, EntryDate = Time });
                _optionPositions.Add(new OptionPosition { Symbol = put.Symbol, Quantity = -1, EntryDate = Time });
                Log($"[{Time:yyyyMMdd}] Sell ATM straddle: {call.Symbol} + {put.Symbol}");
            }
        }

        private void BuyStraddle(OptionChain chain, decimal S)
        {
            var atmStrike = FindAtmStrike(chain, S);
            if (atmStrike == null) return;

            var call = FindContract(chain, atmStrike.Value, OptionRight.Call, 0);
            var put = FindContract(chain, atmStrike.Value, OptionRight.Put, 0);

            if (call != null && put != null)
            {
                Buy(call.Symbol, 1);
                Buy(put.Symbol, 1);
                _optionPositions.Add(new OptionPosition { Symbol = call.Symbol, Quantity = 1, EntryDate = Time });
                _optionPositions.Add(new OptionPosition { Symbol = put.Symbol, Quantity = 1, EntryDate = Time });
                Log($"[{Time:yyyyMMdd}] Buy ATM straddle: {call.Symbol} + {put.Symbol}");
            }
        }

        private void SellCalendarSpread(OptionChain chain, decimal S)
        {
            var atmStrike = FindAtmStrike(chain, S);
            if (atmStrike == null) return;

            var nearCall = FindContract(chain, atmStrike.Value, OptionRight.Call, 0);
            var farCall = FindContract(chain, atmStrike.Value, OptionRight.Call, 1);

            if (nearCall != null && farCall != null)
            {
                Sell(nearCall.Symbol, 1);
                Buy(farCall.Symbol, 1);
                Log($"[{Time:yyyyMMdd}] Sell calendar spread: -{nearCall.Symbol} +{farCall.Symbol}");
            }
        }

        private void BuyCalendarSpread(OptionChain chain, decimal S)
        {
            var atmStrike = FindAtmStrike(chain, S);
            if (atmStrike == null) return;

            var nearCall = FindContract(chain, atmStrike.Value, OptionRight.Call, 0);
            var farCall = FindContract(chain, atmStrike.Value, OptionRight.Call, 1);

            if (nearCall != null && farCall != null)
            {
                Buy(nearCall.Symbol, 1);
                Sell(farCall.Symbol, 1);
                Log($"[{Time:yyyyMMdd}] Buy calendar spread: +{nearCall.Symbol} -{farCall.Symbol}");
            }
        }

        private void SellRiskReversal(OptionChain chain, decimal S)
        {
            var otmPut = FindOtmPut(chain, S, 0.05m);
            var otmCall = FindOtmCall(chain, S, 0.05m);

            if (otmPut != null && otmCall != null)
            {
                Sell(otmPut.Symbol, 1);
                Buy(otmCall.Symbol, 1);
                Log($"[{Time:yyyyMMdd}] Sell risk reversal: -{otmPut.Symbol} +{otmCall.Symbol}");
            }
        }

        private void BuyRiskReversal(OptionChain chain, decimal S)
        {
            var otmPut = FindOtmPut(chain, S, 0.05m);
            var otmCall = FindOtmCall(chain, S, 0.05m);

            if (otmPut != null && otmCall != null)
            {
                Buy(otmPut.Symbol, 1);
                Sell(otmCall.Symbol, 1);
                Log($"[{Time:yyyyMMdd}] Buy risk reversal: +{otmPut.Symbol} -{otmCall.Symbol}");
            }
        }

        private void DeltaHedge(decimal S)
        {
            decimal totalDelta = 0;
            foreach (var pos in _optionPositions)
            {
                var delta = GetDelta(pos.Symbol, S);
                totalDelta += delta * pos.Quantity;
            }

            // Hedge with underlying
            var hedgeQty = -(int)Math.Round(totalDelta * LotSize);
            if (hedgeQty != 0)
            {
                MarketOrder(_underlyingSymbol, hedgeQty);
                Log($"[{Time:yyyyMMdd}] Delta hedge: {hedgeQty} shares (delta={totalDelta:F2})");
            }
        }

        private decimal GetDelta(Symbol optionSymbol, decimal S)
        {
            var security = Securities[optionSymbol];
            var price = security.GetLastData()?.Price ?? 0m;
            if (price <= 0) return 0m;

            var delta = new Delta(optionSymbol, _rateModel, _divModel);
            delta.Update(new TradeBar(Time, optionSymbol, price, price, price, price, 0));
            delta.Update(new TradeBar(Time, _underlyingSymbol, S, S, S, S, 0));

            return delta.IsReady ? delta.Current.Value : 0m;
        }

        private bool CheckStopLoss()
        {
            var pnl = (Portfolio.TotalPortfolioValue - _entryPortfolioValue) / _entryPortfolioValue;
            return pnl < -_stopLossPercent;
        }

        private void LiquidateAll()
        {
            foreach (var pos in _optionPositions.ToList())
            {
                if (Portfolio[pos.Symbol].Quantity != 0)
                    Liquidate(pos.Symbol);
            }
            _optionPositions.Clear();
            Liquidate(_underlyingSymbol);
            Log($"[{Time:yyyyMMdd}] STOP LOSS triggered, liquidated all positions");
        }

        private decimal? FindAtmStrike(OptionChain chain, decimal S)
        {
            return chain.OrderBy(c => Math.Abs(c.Strike - S)).FirstOrDefault()?.Strike;
        }

        private OptionContract FindContract(OptionChain chain, decimal strike, OptionRight right, int expiryIndex)
        {
            var byExpiry = chain.GroupBy(c => c.Expiry).OrderBy(g => g.Key).ToList();
            if (expiryIndex >= byExpiry.Count) return null;

            return byExpiry[expiryIndex].FirstOrDefault(c => c.Strike == strike && c.Right == right);
        }

        private OptionContract FindOtmPut(OptionChain chain, decimal S, decimal moneyness)
        {
            var targetStrike = S * (1 - moneyness);
            return chain.Where(c => c.Right == OptionRight.Put && c.Strike < S)
                .OrderBy(c => Math.Abs(c.Strike - targetStrike)).FirstOrDefault();
        }

        private OptionContract FindOtmCall(OptionChain chain, decimal S, decimal moneyness)
        {
            var targetStrike = S * (1 + moneyness);
            return chain.Where(c => c.Right == OptionRight.Call && c.Strike > S)
                .OrderBy(c => Math.Abs(c.Strike - targetStrike)).FirstOrDefault();
        }

        public override void OnEndOfAlgorithm()
        {
            var totalReturn = (Portfolio.TotalPortfolioValue - _initialCapital) / _initialCapital;
            Log($"[OptionVolArb] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[OptionVolArb] Total return: {totalReturn:P2}");
            Log($"[OptionVolArb] Total trades: {Transactions.OrdersCount}");
        }
    }

    internal enum SignalType
    {
        SellVolatility,
        BuyVolatility,
        CalendarSpreadSell,
        CalendarSpreadBuy,
        SellSkew,
        BuySkew
    }

    internal class TradeSignal
    {
        public SignalType Type { get; set; }
        public decimal Strength { get; set; }
    }

    internal class OptionPosition
    {
        public Symbol Symbol { get; set; }
        public int Quantity { get; set; }
        public DateTime EntryDate { get; set; }
        public decimal MarketValue { get; set; }
    }
}
