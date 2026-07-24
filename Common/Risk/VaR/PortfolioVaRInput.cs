// file: Common/Risk/VaR/PortfolioVaRInput.cs
using System;
using System.Collections.Generic;

namespace QuantConnect.Risk.VaR
{
    /// <summary>Input for portfolio VaR (multi-asset).</summary>
    public readonly struct PortfolioVaRInput
    {
        /// <summary>Asset returns [nAssets][nObs], aligned by date. NaN only at leading edge.</summary>
        public IReadOnlyList<IReadOnlyList<double>> AssetReturns { get; }

        /// <summary>Weights (length nAssets; sum need not be 1 — engine normalizes).</summary>
        public IReadOnlyList<double> Weights { get; }

        public IReadOnlyList<Symbol> Symbols { get; }
        public DateTime AsOfDate { get; }

        public PortfolioVaRInput(
            IReadOnlyList<IReadOnlyList<double>> assetReturns,
            IReadOnlyList<double> weights,
            IReadOnlyList<Symbol> symbols,
            DateTime asOfDate)
        {
            AssetReturns = assetReturns;
            Weights = weights;
            Symbols = symbols;
            AsOfDate = asOfDate;
        }
    }
}
