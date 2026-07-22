using QuantConnect.Algorithm;
using QuantConnect.Data;

namespace QuantConnect.Algorithm.CSharp
{
    public class MinimalTestAlgorithm : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2024, 6, 3);
            SetEndDate(2024, 6, 7);
            SetCash(1000000);

            AddEquity("510050", Resolution.Daily, market: Market.SSE);
            Log("MinimalTestAlgorithm initialized");
        }

        public override void OnData(Slice data)
        {
            Log($"OnData called: {Time:yyyyMMdd}, Bars={data.Bars.Count}");

            if (Time.Day == 3 && Portfolio["510050"].Quantity == 0)
            {
                var qty = 1000;
                MarketOrder("510050", qty);
                Log($"BUY {qty} shares at {Securities["510050"].Price}");
            }

            if (Time.Day == 7)
            {
                Liquidate();
                Log("LIQUIDATE all");
            }
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"Final portfolio: {Portfolio.TotalPortfolioValue}");
            Log($"Total trades: {Transactions.OrdersCount}");
        }
    }
}