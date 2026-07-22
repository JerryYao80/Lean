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

using QuantConnect.Orders;
using QuantConnect.Securities;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Algorithm.Framework.Portfolio;

namespace QuantConnect.Algorithm.Framework.Execution
{
    /// <summary>
    /// A-share execution model that rounds order quantities to lot size (100 shares)
    /// before submitting market orders. Prevents "Order quantity must be a multiple of 100 shares" errors.
    /// </summary>
    public class AShareLotSizeExecutionModel : ExecutionModel
    {
        private const int LotSize = 100;
        private readonly PortfolioTargetCollection _targetsCollection = new PortfolioTargetCollection();

        public AShareLotSizeExecutionModel(bool asynchronous = true)
            : base(asynchronous)
        {
        }

        public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            _targetsCollection.AddRange(targets);
            if (!_targetsCollection.IsEmpty)
            {
                foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
                {
                    var security = algorithm.Securities[target.Symbol];

                    // Calculate remaining quantity to be ordered
                    var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);

                    if (quantity != 0)
                    {
                        // Round to lot size (100 shares)
                        var roundedQuantity = (int)(quantity / LotSize) * LotSize;
                        if (roundedQuantity == 0) continue;

                        if (security.BuyingPowerModel.AboveMinimumOrderMarginPortfolioPercentage(security, roundedQuantity,
                            algorithm.Portfolio, algorithm.Settings.MinimumOrderMarginPortfolioPercentage))
                        {
                            algorithm.MarketOrder(security, roundedQuantity, Asynchronous, target.Tag);
                        }
                        else if (!PortfolioTarget.MinimumOrderMarginPercentageWarningSent.HasValue)
                        {
                            PortfolioTarget.MinimumOrderMarginPercentageWarningSent = false;
                        }
                    }
                }

                _targetsCollection.ClearFulfilled(algorithm);
            }
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
        }
    }
}
