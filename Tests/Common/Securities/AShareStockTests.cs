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
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Tests.Common.Securities
{
    [TestFixture]
    public class AShareStockTests
    {
        [Test]
        public void ConstructorConfiguresT1TradingModels()
        {
            var security = CreateSecurity("600000", Market.SSE);

            Assert.IsInstanceOf<DelayedSettlementModel>(security.SettlementModel);
            Assert.IsInstanceOf<AShareStockFeeModel>(security.FeeModel);
            Assert.IsInstanceOf<AShareStockFillModel>(security.FillModel);
            Assert.IsInstanceOf<AShareStockBuyingPowerModel>(security.BuyingPowerModel);
            Assert.AreEqual(0m, security.Holdings.AvailableQuantity);
        }

        [TestCase("000001", 0.10)]
        [TestCase("600000", 0.10)]
        [TestCase("300750", 0.20)]
        [TestCase("301001", 0.20)]
        [TestCase("688981", 0.20)]
        [TestCase("430001", 0.30)]
        [TestCase("830001", 0.30)]
        [TestCase("870001", 0.30)]
        public void ReturnsExpectedPriceLimitPercentage(string ticker, decimal expectedPriceLimit)
        {
            var market = ticker.StartsWith("6", StringComparison.Ordinal)
                ? Market.SSE
                : ticker.StartsWith("0", StringComparison.Ordinal) || ticker.StartsWith("3", StringComparison.Ordinal)
                    ? Market.SZSE
                    : Market.China;
            var symbol = Symbol.Create(ticker, SecurityType.Equity, market);

            Assert.AreEqual(expectedPriceLimit, AShareStock.GetPriceLimitPercentage(symbol));
        }

        [Test]
        public void ReturnsFivePercentPriceLimitForSpecialTreatmentStocks()
        {
            var symbol = Symbol.Create("600000", SecurityType.Equity, Market.SSE);

            Assert.AreEqual(0.05m, AShareStock.GetPriceLimitPercentage(symbol, "*ST浦发"));
            Assert.IsTrue(AShareStock.IsSpecialTreatment(symbol, "*ST浦发"));
        }

        [Test]
        public void SameDayBuyCannotBeSoldUntilNextTradingDayReleaseTime()
        {
            var buyLocalTime = new DateTime(2024, 3, 4, 10, 0, 0);
            var timeKeeper = new TimeKeeper(buyLocalTime.ConvertToUtc(TimeZones.Shanghai), new[] { TimeZones.Shanghai });
            var securities = new SecurityManager(timeKeeper);
            var transactions = new SecurityTransactionManager(null, securities);
            var portfolio = new SecurityPortfolioManager(securities, transactions, new AlgorithmSettings());
            portfolio.SetAccountCurrency(Currencies.CNY, 100000m);
            portfolio.SetCash(Currencies.CNY, 100000m, 1m);

            var security = CreateSecurity("600000", Market.SSE);
            securities.Add(security);

            var order = new MarketOrder(security.Symbol, 1000m, buyLocalTime.ConvertToUtc(TimeZones.Shanghai));
            var fill = new OrderEvent(order, order.Time, OrderFee.Zero)
            {
                Status = OrderStatus.Filled,
                FillPrice = 10m,
                FillQuantity = 1000m
            };

            security.PortfolioModel.ProcessFill(portfolio, security, fill);

            Assert.AreEqual(1000m, security.Holdings.Quantity);
            Assert.AreEqual(0m, security.Holdings.AvailableQuantity);

            timeKeeper.SetUtcDateTime(new DateTime(2024, 3, 5, 8, 59, 0).ConvertToUtc(TimeZones.Shanghai));
            Assert.AreEqual(0m, security.Holdings.AvailableQuantity);

            timeKeeper.SetUtcDateTime(new DateTime(2024, 3, 5, 9, 0, 0).ConvertToUtc(TimeZones.Shanghai));
            Assert.AreEqual(1000m, security.Holdings.AvailableQuantity);
        }

        private static AShareStock CreateSecurity(string ticker, string market)
        {
            var symbol = Symbol.Create(ticker, SecurityType.Equity, market);
            var exchangeHours = MarketHoursDatabase.FromDataFolder().GetExchangeHours(market, symbol, SecurityType.Equity);

            return new AShareStock(
                symbol,
                exchangeHours,
                new Cash(Currencies.CNY, 0m, 1m),
                new SymbolProperties(ticker, Currencies.CNY, 1m, AShareStock.DefaultMinimumPriceVariation, AShareStock.LotSize, ticker),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
        }
    }
}
