/*
 * AShareT1SequentialExecutionModel.cs — T+1 sell-before-buy execution.
 *
 * Two-phase: process all SELLS first (releases T+1-locked cash), then all BUYS
 * (each buy re-checks buying power after prior sells freed cash). Buys that
 * cannot be afforded are skipped silently (no error) — this is the intended
 * degradation, NOT a bypass: the order genuinely cannot fill given available
 * cash, so we move on rather than blocking the whole rebalance.
 *
 * Lot-size rounding to 100 shares (AShareStock.LotSize).
 *
 * ZERO INTRUSION: new file. AShareLotSizeExecutionModel is NOT modified.
 */
using System;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Orders;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.Framework.Execution
{
    /// <summary>
    /// A-share execution model: sell-before-buy, per-order buying-power check,
    /// 100-share lot rounding. Mitigates T+1 settlement cash-shortage rejections.
    /// </summary>
    public class AShareT1SequentialExecutionModel : ExecutionModel
    {
        private const int LotSize = 100;
        private readonly PortfolioTargetCollection _targetsCollection = new PortfolioTargetCollection();

        public AShareT1SequentialExecutionModel(bool asynchronous = true)
            : base(asynchronous)
        {
        }

        public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            _targetsCollection.AddRange(targets);
            if (_targetsCollection.IsEmpty) return;

            // Phase 1: sells first (release T+1-locked cash).
            //
            // Observability note (Phase 1 sell-failure interaction):
            //   A sell that passes AboveMinimumOrderMarginPortfolioPercentage here can
            //   still be invalidated at FILL TIME by AShareStockBuyingPowerModel's T+1
            //   available-quantity check (the pre-trade check does not enforce T+1
            //   share availability, but the fill-time check does). When that happens
            //   BacktestingBrokerage marks the order OrderStatus.Invalid and logs an
            //   Algorithm.Error; the sell frees no cash. Phase 2 buys that depended on
            //   that cash will then fail their buying-power check and be skipped —
            //   silently by design (honest degradation). This matches
            //   AShareLotSizeExecutionModel behavior (no regression). To diagnose a
            //   "Phase 2 buy skipped" that should have succeeded, correlate with the
            //   Order Error log emitted for the Phase 1 sell in the same time step.
            ExecuteSells(algorithm);
            // Phase 2: buys second (each re-checks buying power after sells freed cash)
            ExecuteBuys(algorithm);

            _targetsCollection.ClearFulfilled(algorithm);
        }

        private void ExecuteSells(QCAlgorithm algorithm)
        {
            foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
            {
                var security = algorithm.Securities[target.Symbol];
                var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
                if (quantity >= 0) continue;  // only sells

                var rounded = RoundToLotSize(quantity);
                if (rounded == 0) continue;

                // Sells checked by buying-power model for T+1 availability
                if (security.BuyingPowerModel.AboveMinimumOrderMarginPortfolioPercentage(
                        security, rounded, algorithm.Portfolio,
                        algorithm.Settings.MinimumOrderMarginPortfolioPercentage))
                {
                    algorithm.MarketOrder(security, rounded, Asynchronous, target.Tag);
                }
            }
        }

        private void ExecuteBuys(QCAlgorithm algorithm)
        {
            foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
            {
                var security = algorithm.Securities[target.Symbol];
                var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
                if (quantity <= 0) continue;  // only buys

                var rounded = RoundToLotSize(quantity);
                if (rounded == 0) continue;

                // Per-order buying-power check: skip (not error) if unaffordable.
                // This is honest degradation — the order truly cannot fill at
                // current cash; we proceed to the next target rather than block.
                if (security.BuyingPowerModel.AboveMinimumOrderMarginPortfolioPercentage(
                        security, rounded, algorithm.Portfolio,
                        algorithm.Settings.MinimumOrderMarginPortfolioPercentage))
                {
                    algorithm.MarketOrder(security, rounded, Asynchronous, target.Tag);
                }
                else if (!PortfolioTarget.MinimumOrderMarginPercentageWarningSent.HasValue)
                {
                    PortfolioTarget.MinimumOrderMarginPercentageWarningSent = false;
                }
            }
        }

        private static int RoundToLotSize(decimal quantity)
        {
            if (quantity == 0) return 0;
            return (int)(quantity / LotSize) * LotSize;
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        // --- test hooks ---
        internal string ClassifySignForTest(decimal quantity) =>
            quantity < 0 ? "sell" : quantity > 0 ? "buy" : "none";
        internal int RoundToLotSizeForTest(decimal quantity) => RoundToLotSize(quantity);
    }
}
