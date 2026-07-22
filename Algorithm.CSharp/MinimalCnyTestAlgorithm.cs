using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class MinimalCnyTestAlgorithm : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2024, 6, 26);
            SetEndDate(2024, 6, 28);
            SetAccountCurrency("CNY");
            SetCash("CNY", 1000000m, 1m);

            var eq = AddEquity("510050", Resolution.Daily, Market.SSE);
            eq.SetFeeModel(new AShareStockFeeModel());
            eq.SetFillModel(new AShareStockFillModel());
            eq.SetBuyingPowerModel(new AShareStockBuyingPowerModel());
            eq.SetSettlementModel(new ImmediateSettlementModel());

            Log($"[MinimalCnyTest] AccountCurrency: {Portfolio.CashBook.AccountCurrency}");
        }

        public override void OnData(Slice data)
        {
            if (!Portfolio.Invested)
            {
                MarketOrder("510050", 1000);
                Log($"[MinimalCnyTest] Ordered 1000 shares");
            }
        }
    }
}
