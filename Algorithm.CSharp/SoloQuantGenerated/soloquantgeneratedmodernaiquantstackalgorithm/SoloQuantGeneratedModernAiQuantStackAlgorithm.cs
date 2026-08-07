using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders;
using QuantConnect.Indicators;
using QuantConnect.Securities;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Data.Fundamental;

namespace QuantConnect.Algorithm.CSharp
{
    public class SoloQuantGeneratedModernAiQuantStackAlgorithm : QCAlgorithm
    {
        private Dictionary<string, SoloQuantSymbolData> _symbolData = new Dictionary<string, SoloQuantSymbolData>();
        private List<string> _tickers = new List<string>();
        private decimal _trailingStopPct = 0.10m;
        private decimal _maxDrawdownPct = 0.15m;
        private decimal _peakPortfolioValue = 0m;
        private bool _rebalanced = false;
        private int _rebalanceMonth = -1;

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetCash(decimal.Parse(GetParameter("initial-capital") ?? "1000000"));
            SetStartDate(DateTime.ParseExact(GetParameter("start-date") ?? "20200101", "yyyyMMdd", null));
            SetEndDate(DateTime.ParseExact(GetParameter("end-date") ?? "20231231", "yyyyMMdd", null));
            SetBenchmark(_ => 0m);

            string universeParam = GetParameter("universe") ?? "600519.SSE,000858.SZSE,601318.SSE,000333.SZSE,600036.SSE";
            _tickers = universeParam.Split(',').Select(t => t.Trim()).ToList();

            foreach (var ticker in _tickers)
            {
                var parts = ticker.Split('.');
                string symbolStr = parts[0];
                string marketStr = parts.Length > 1 ? parts[1] : "SSE";
                var market = marketStr == "SZSE" ? Market.SZSE : Market.SSE;

                var security = AddEquity(symbolStr, Resolution.Daily, market);
                security.FeeModel = new AShareStockFeeModel();
                security.FillModel = new AShareStockFillModel();
                security.BuyingPowerModel = new AShareStockBuyingPowerModel();
                security.SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));

                _symbolData[symbolStr] = new SoloQuantSymbolData
                {
                    Symbol = security.Symbol,
                    HighestPrice = 0m,
                    TrailingStopTriggered = false
                };
            }
        }

        public override void OnData(Slice data)
        {
            if (Portfolio.TotalPortfolioValue > _peakPortfolioValue)
            {
                _peakPortfolioValue = Portfolio.TotalPortfolioValue;
            }

            decimal currentDrawdown = (_peakPortfolioValue - Portfolio.TotalPortfolioValue) / _peakPortfolioValue;
            if (_peakPortfolioValue > 0 && currentDrawdown > _maxDrawdownPct)
            {
                foreach (var ticker in _tickers)
                {
                    var parts = ticker.Split('.');
                    string symbolStr = parts[0];
                    if (_symbolData.ContainsKey(symbolStr) && Portfolio[_symbolData[symbolStr].Symbol].Invested)
                    {
                        Liquidate(_symbolData[symbolStr].Symbol);
                    }
                }
                Log($"Max drawdown triggered: {currentDrawdown:P2} > {_maxDrawdownPct:P2}");
                return;
            }

            foreach (var kvp in _symbolData)
            {
                var sd = kvp.Value;
                if (data.ContainsKey(sd.Symbol) && data[sd.Symbol] != null)
                {
                    decimal price = data[sd.Symbol].Close;
                    if (price > sd.HighestPrice)
                    {
                        sd.HighestPrice = price;
                    }

                    if (Portfolio[sd.Symbol].Invested && sd.HighestPrice > 0)
                    {
                        decimal trailingDrop = (sd.HighestPrice - price) / sd.HighestPrice;
                        if (trailingDrop > _trailingStopPct)
                        {
                            Liquidate(sd.Symbol);
                            sd.TrailingStopTriggered = true;
                            Log($"Trailing stop triggered for {sd.Symbol}: drop {trailingDrop:P2} > {_trailingStopPct:P2}");
                        }
                    }
                }
            }

            int currentMonth = Time.Month;
            if (_rebalanceMonth == currentMonth)
            {
                return;
            }
            _rebalanceMonth = currentMonth;

            decimal weight = 1m / _tickers.Count;
            foreach (var ticker in _tickers)
            {
                var parts = ticker.Split('.');
                string symbolStr = parts[0];
                if (!_symbolData.ContainsKey(symbolStr)) continue;

                var sd = _symbolData[symbolStr];
                if (sd.TrailingStopTriggered) continue;

                if (!data.ContainsKey(sd.Symbol) || data[sd.Symbol] == null) continue;

                var security = Securities[sd.Symbol];
                if (security.Price == 0) continue;

                int targetQuantity = (int)(Portfolio.TotalPortfolioValue * weight / security.Price / 100m) * 100;
                int currentQuantity = (int)Portfolio[sd.Symbol].Quantity;
                int delta = targetQuantity - currentQuantity;

                if (delta != 0)
                {
                    MarketOrder(sd.Symbol, delta);
                }
            }
        }

        public override void OnEndOfAlgorithm()
        {
            Log("Algorithm ended. Note: Source article provided no specific strategy; this is a placeholder equal-weight template.");
        }
    }

    public class SoloQuantSymbolData
    {
        public Symbol Symbol { get; set; }
        public decimal HighestPrice { get; set; }
        public bool TrailingStopTriggered { get; set; }
    }
}