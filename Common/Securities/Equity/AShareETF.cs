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

using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders.Slippage;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// A-Share ETF security class with T+0 trading support
    /// </summary>
    public class AShareETF : Equity
    {
        /// <summary>
        /// Price limit percentage (10% up/down from previous close)
        /// </summary>
        public const decimal PriceLimitPercentage = 0.10m;

        /// <summary>
        /// Minimum lot size (100 shares)
        /// </summary>
        public const int LotSize = 100;

        /// <summary>
        /// Creates a new instance of AShareETF
        /// </summary>
        public AShareETF(Symbol symbol, SecurityExchangeHours exchangeHours, Cash quoteCurrency, SymbolProperties symbolProperties, ICurrencyConverter currencyConverter, IRegisteredSecurityDataTypesProvider registeredTypes, SecurityCache cache)
            : base(symbol, exchangeHours, quoteCurrency, symbolProperties, currencyConverter, registeredTypes, cache)
        {
            // Use immediate settlement for T+0 trading
            SettlementModel = new ImmediateSettlementModel();

            // Use A-Share ETF specific models
            FillModel = new AShareETFFillModel();
            FeeModel = new AShareETFFeeModel();
            BuyingPowerModel = new AShareETFBuyingPowerModel();
            SlippageModel = new ConstantSlippageModel(0);
        }

        /// <summary>
        /// Gets the upper price limit based on previous close
        /// </summary>
        public decimal GetUpperPriceLimit(decimal previousClose)
        {
            return previousClose * (1 + PriceLimitPercentage);
        }

        /// <summary>
        /// Gets the lower price limit based on previous close
        /// </summary>
        public decimal GetLowerPriceLimit(decimal previousClose)
        {
            return previousClose * (1 - PriceLimitPercentage);
        }

        /// <summary>
        /// Checks if a quantity is valid (multiple of lot size)
        /// </summary>
        public static bool IsValidQuantity(decimal quantity)
        {
            return quantity % LotSize == 0;
        }
    }
}
