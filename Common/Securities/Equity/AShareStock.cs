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
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders.Slippage;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// A-share stock security with T+1 trading rules.
    /// </summary>
    public class AShareStock : Equity
    {
        /// <summary>
        /// Default price limit percentage for main-board A-shares.
        /// </summary>
        public const decimal DefaultPriceLimitPercentage = 0.10m;

        /// <summary>
        /// Price limit percentage for growth-board/STAR-board A-shares.
        /// </summary>
        public const decimal GrowthBoardPriceLimitPercentage = 0.20m;

        /// <summary>
        /// Price limit percentage for Beijing Stock Exchange A-shares.
        /// </summary>
        public const decimal BeijingExchangePriceLimitPercentage = 0.30m;

        /// <summary>
        /// Price limit percentage for special-treatment A-shares.
        /// </summary>
        public const decimal SpecialTreatmentPriceLimitPercentage = 0.05m;

        /// <summary>
        /// Default minimum price variation for A-share stocks.
        /// </summary>
        public const decimal DefaultMinimumPriceVariation = 0.01m;

        /// <summary>
        /// Minimum trading lot size.
        /// </summary>
        public const int LotSize = 100;

        /// <summary>
        /// Creates a new instance of <see cref="AShareStock"/>.
        /// </summary>
        public AShareStock(Symbol symbol, SecurityExchangeHours exchangeHours, Cash quoteCurrency, SymbolProperties symbolProperties, ICurrencyConverter currencyConverter, IRegisteredSecurityDataTypesProvider registeredTypes, SecurityCache cache)
            : base(symbol, exchangeHours, quoteCurrency, symbolProperties, currencyConverter, registeredTypes, cache)
        {
            SettlementModel = new DelayedSettlementModel(1, TimeSpan.FromHours(9));
            PortfolioModel = new AShareT1PortfolioModel();
            Holdings = new AShareT1Holding(this, currencyConverter);

            FeeModel = new AShareStockFeeModel();
            FillModel = new AShareStockFillModel();
            BuyingPowerModel = new AShareStockBuyingPowerModel();
            SlippageModel = new ConstantSlippageModel(0m);
        }

        /// <summary>
        /// Gets the symbol-specific daily price limit percentage.
        /// </summary>
        public static decimal GetPriceLimitPercentage(Symbol symbol, string description = null)
        {
            return AShareStockMetadataRegistry.GetPriceLimitPercentage(symbol?.Value, description);
        }

        /// <summary>
        /// Determines whether the specified A-share should be treated as an ST stock.
        /// </summary>
        public static bool IsSpecialTreatment(Symbol symbol, string description = null)
        {
            return AShareStockMetadataRegistry.IsSpecialTreatment(symbol?.Value, description);
        }

        /// <summary>
        /// Gets the effective minimum price variation for A-share stocks.
        /// </summary>
        public static decimal GetMinimumPriceVariation(decimal minimumPriceVariation)
        {
            if (minimumPriceVariation <= 0 || minimumPriceVariation > DefaultMinimumPriceVariation)
            {
                return DefaultMinimumPriceVariation;
            }

            return minimumPriceVariation;
        }

        /// <summary>
        /// Gets the upper price limit rounded to the stock tick size.
        /// </summary>
        public static decimal GetUpperPriceLimit(Symbol symbol, decimal previousClose, decimal minimumPriceVariation, string description = null)
        {
            return RoundToPriceVariation(previousClose * (1 + GetPriceLimitPercentage(symbol, description)), GetMinimumPriceVariation(minimumPriceVariation));
        }

        /// <summary>
        /// Gets the lower price limit rounded to the stock tick size.
        /// </summary>
        public static decimal GetLowerPriceLimit(Symbol symbol, decimal previousClose, decimal minimumPriceVariation, string description = null)
        {
            return RoundToPriceVariation(previousClose * (1 - GetPriceLimitPercentage(symbol, description)), GetMinimumPriceVariation(minimumPriceVariation));
        }

        /// <summary>
        /// Rounds a price to the nearest valid price variation.
        /// </summary>
        public static decimal RoundToPriceVariation(decimal price, decimal minimumPriceVariation)
        {
            var priceVariation = GetMinimumPriceVariation(minimumPriceVariation);
            return Math.Round(price / priceVariation, 0, MidpointRounding.AwayFromZero) * priceVariation;
        }

        /// <summary>
        /// Checks whether the order quantity respects the lot size.
        /// </summary>
        public static bool IsValidQuantity(decimal quantity)
        {
            return quantity % LotSize == 0;
        }
    }
}
