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
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

namespace QuantConnect.Tests.Common.Orders.Fees
{
    [TestFixture]
    public class AShareStockFeeModelTests
    {
        [Test]
        public void CalculatesShanghaiBuyFeesWithMinimumCommission()
        {
            var security = CreateSecurity("600000", Market.SSE);
            security.SetMarketPrice(new Tick { Value = 10m, AskPrice = 10m, BidPrice = 10m });
            var order = new MarketOrder(security.Symbol, 1000m, new DateTime(2024, 3, 4));

            var fee = new AShareStockFeeModel().GetOrderFee(new OrderFeeParameters(security, order));

            Assert.AreEqual(5.1m, fee.Value.Amount);
            Assert.AreEqual(Currencies.CNY, fee.Value.Currency);
        }

        [Test]
        public void CalculatesSellFeesIncludingStampDuty()
        {
            var security = CreateSecurity("600000", Market.SSE);
            security.SetMarketPrice(new Tick { Value = 10m, AskPrice = 10m, BidPrice = 10m });
            var order = new MarketOrder(security.Symbol, -1000m, new DateTime(2024, 3, 4));

            var fee = new AShareStockFeeModel().GetOrderFee(new OrderFeeParameters(security, order));

            Assert.AreEqual(10.1m, fee.Value.Amount);
            Assert.AreEqual(Currencies.CNY, fee.Value.Currency);
        }

        [Test]
        public void CalculatesShenzhenBuyFeesWithTransferFee()
        {
            var security = CreateSecurity("000001", Market.SZSE);
            security.SetMarketPrice(new Tick { Value = 10m, AskPrice = 10m, BidPrice = 10m });
            var order = new MarketOrder(security.Symbol, 1000m, new DateTime(2024, 3, 4));

            var fee = new AShareStockFeeModel().GetOrderFee(new OrderFeeParameters(security, order));

            Assert.AreEqual(5.1m, fee.Value.Amount);
            Assert.AreEqual(Currencies.CNY, fee.Value.Currency);
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
