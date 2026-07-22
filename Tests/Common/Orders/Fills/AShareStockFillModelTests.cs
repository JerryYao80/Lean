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
using System.Linq;
using NUnit.Framework;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;
using QuantConnect.Tests.Common.Data;

namespace QuantConnect.Tests.Common.Orders.Fills
{
    [TestFixture]
    public class AShareStockFillModelTests
    {
        [Test]
        public void RejectsSameDaySellOrdersForUnavailableQuantity()
        {
            var localTime = new DateTime(2024, 3, 4, 10, 0, 0);
            var timeKeeper = new TimeKeeper(localTime.ConvertToUtc(TimeZones.Shanghai), new[] { TimeZones.Shanghai });
            var securities = new SecurityManager(timeKeeper);
            var transactions = new SecurityTransactionManager(null, securities);
            var portfolio = new SecurityPortfolioManager(securities, transactions, new AlgorithmSettings());
            portfolio.SetAccountCurrency(Currencies.CNY, 100000m);
            portfolio.SetCash(Currencies.CNY, 100000m, 1m);

            var security = CreateSecurity("600000", Market.SSE);
            securities.Add(security);
            security.SetMarketPrice(new TradeBar(localTime, security.Symbol, 10m, 10m, 10m, 10m, 10000));

            var buyOrder = new MarketOrder(security.Symbol, 1000m, localTime.ConvertToUtc(TimeZones.Shanghai));
            var buyFill = new OrderEvent(buyOrder, buyOrder.Time, OrderFee.Zero)
            {
                Status = OrderStatus.Filled,
                FillPrice = 10m,
                FillQuantity = 1000m
            };

            security.PortfolioModel.ProcessFill(portfolio, security, buyFill);

            var sellOrder = new MarketOrder(security.Symbol, -1000m, localTime.ConvertToUtc(TimeZones.Shanghai));
            var config = new SubscriptionDataConfig(typeof(TradeBar), security.Symbol, Resolution.Minute, TimeZones.Shanghai, TimeZones.Shanghai, false, true, false);
            var fill = ((AShareStockFillModel)security.FillModel).Fill(new FillModelParameters(
                security,
                sellOrder,
                new MockSubscriptionDataConfigProvider(config),
                Time.OneHour,
                null)).Single();

            Assert.AreEqual(OrderStatus.Invalid, fill.Status);
            StringAssert.Contains("available", fill.Message.ToLowerInvariant());
        }

        [Test]
        public void RejectsBuyOrdersForSpecialTreatmentStocks()
        {
            var localTime = new DateTime(2024, 3, 4, 10, 0, 0);
            var security = CreateSecurity("600000", Market.SSE, "*ST浦发");
            security.SetMarketPrice(new TradeBar(localTime, security.Symbol, 10m, 10m, 10m, 10m, 10000));
            var buyOrder = new MarketOrder(security.Symbol, 100m, localTime.ConvertToUtc(TimeZones.Shanghai));
            var config = new SubscriptionDataConfig(typeof(TradeBar), security.Symbol, Resolution.Minute, TimeZones.Shanghai, TimeZones.Shanghai, false, true, false);

            var fill = ((AShareStockFillModel)security.FillModel).Fill(new FillModelParameters(
                security,
                buyOrder,
                new MockSubscriptionDataConfigProvider(config),
                Time.OneHour,
                null)).Single();

            Assert.AreEqual(OrderStatus.Invalid, fill.Status);
            StringAssert.Contains("st", fill.Message.ToLowerInvariant());
        }

        private static AShareStock CreateSecurity(string ticker, string market, string description = null)
        {
            var symbol = Symbol.Create(ticker, SecurityType.Equity, market);
            var exchangeHours = MarketHoursDatabase.FromDataFolder().GetExchangeHours(market, symbol, SecurityType.Equity);

            return new AShareStock(
                symbol,
                exchangeHours,
                new Cash(Currencies.CNY, 0m, 1m),
                new SymbolProperties(description ?? ticker, Currencies.CNY, 1m, AShareStock.DefaultMinimumPriceVariation, AShareStock.LotSize, ticker),
                ErrorCurrencyConverter.Instance,
                RegisteredSecurityDataTypesProvider.Null,
                new SecurityCache());
        }
    }
}
