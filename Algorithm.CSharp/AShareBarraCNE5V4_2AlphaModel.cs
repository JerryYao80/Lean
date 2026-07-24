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
using System.Collections.Generic;
using System.Linq;
using MathNet.Numerics.LinearAlgebra.Double;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data.Market;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Barra CNE5 V4.2 Alpha Model — extends V4 with Symmetric Gram-Schmidt factor orthogonalization.
    ///
    /// Resolves cross-factor collinearity (beta↔momentum, growth↔earnyld, quality↔leverage, etc.)
    /// by computing the correlation matrix of the 15 z-scored factors, performing Cholesky
    /// decomposition C = L·Lᵀ, then transforming Z_orth = Z · inv(Lᵀ).
    ///
    /// This is the MSCI Barra recommended symmetric orthogonalization method — order-invariant,
    /// treats all factors equally, and eliminates signal double-counting in composite scores.
    ///
    /// V4.2 is completely independent from V4. When orthogonalizeFactors=false, falls back to
    /// the V4 base class behavior (no orthogonalization).
    /// </summary>
    public class AShareBarraCNE5V4_2AlphaModel : AShareBarraCNE5V4AlphaModel
    {
        private readonly bool _orthogonalizeFactors;

        private static readonly (string name, Func<AShareBarraCNE5V2FactorData, decimal?> selector)[] FactorSelectors =
        {
            ("beta", f => f.Beta), ("momentum", f => f.Momentum), ("size", f => f.Size),
            ("earnyld", f => f.EarningsYield), ("resvol", f => f.ResidualVolatility),
            ("growth", f => f.Growth), ("btop", f => f.BookToPrice), ("leverage", f => f.Leverage),
            ("liquidity", f => f.Liquidity), ("nlsize", f => f.NonLinearSize),
            ("moneyflow", f => f.MoneyFlow), ("quality", f => f.Quality),
            ("northbound", f => f.Northbound), ("margin", f => f.Margin), ("chipcost", f => f.ChipCost)
        };

        public AShareBarraCNE5V4_2AlphaModel(
            int topN = 25,
            decimal minScoreSpread = 0.5m,
            string rebalanceFrequency = "monthly",
            int minimumPresentFactors = 8,
            int minListedDays = 250,
            int maxMissingFactorCount = 3,
            decimal minTurnoverRate = 0m,
            decimal? minTotalMv = null,
            decimal betaWeight = -0.05m,
            decimal momentumWeight = 0.25m,
            decimal sizeWeight = -0.05m,
            decimal earningsYieldWeight = 0.20m,
            decimal residualVolatilityWeight = -0.10m,
            decimal growthWeight = 0.15m,
            decimal bookToPriceWeight = 0.10m,
            decimal leverageWeight = -0.05m,
            decimal liquidityWeight = 0.05m,
            decimal nonLinearSizeWeight = 0.00m,
            decimal moneyFlowWeight = 0.10m,
            decimal qualityWeight = 0.20m,
            decimal northboundWeight = 0.08m,
            decimal marginWeight = 0.05m,
            decimal chipCostWeight = 0.07m,
            bool regimeSwitchingEnabled = true,
            int regimeVolLookbackDays = 20,
            decimal regimeLowVolThreshold = 0.15m,
            decimal regimeHighVolThreshold = 0.25m,
            decimal regimeTransitionAlpha = 0.30m,
            Dictionary<string, decimal> lowVolWeights = null,
            Dictionary<string, decimal> midVolWeights = null,
            Dictionary<string, decimal> highVolWeights = null,
            int icLookbackPeriods = 60,
            int icMinObservations = 12,
            decimal irSensitivity = 0.50m,
            bool stratifiedSelectionEnabled = true,
            string industryClassificationPath = null,
            bool orthogonalizeFactors = true)
            : base(topN, minScoreSpread, rebalanceFrequency, minimumPresentFactors,
                   minListedDays, maxMissingFactorCount, minTurnoverRate, minTotalMv,
                   betaWeight, momentumWeight, sizeWeight, earningsYieldWeight,
                   residualVolatilityWeight, growthWeight, bookToPriceWeight, leverageWeight,
                   liquidityWeight, nonLinearSizeWeight, moneyFlowWeight, qualityWeight,
                   northboundWeight, marginWeight, chipCostWeight,
                   regimeSwitchingEnabled, regimeVolLookbackDays, regimeLowVolThreshold,
                   regimeHighVolThreshold, regimeTransitionAlpha,
                   lowVolWeights, midVolWeights, highVolWeights,
                   icLookbackPeriods, icMinObservations, irSensitivity,
                   stratifiedSelectionEnabled, industryClassificationPath)
        {
            _orthogonalizeFactors = orthogonalizeFactors;
            Name = $"{nameof(AShareBarraCNE5V4_2AlphaModel)}({topN},{rebalanceFrequency},ortho={orthogonalizeFactors})";
        }

        /// <summary>
        /// Override ComputeScores to add Symmetric Gram-Schmidt orthogonalization.
        /// When _orthogonalizeFactors is false, falls back to base class behavior.
        /// </summary>
        protected override Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            if (!_orthogonalizeFactors)
                return base.ComputeScores(factors);

            try
            {
                return ComputeScoresOrthogonalized(factors);
            }
            catch (Exception ex)
            {
                // Fallback to non-orthogonalized on any numerical failure
                Console.Error.WriteLine(
                    $"[V4.2 Alpha] Orthogonalization failed, falling back to raw scoring: {ex.Message}");
                return base.ComputeScores(factors);
            }
        }

        private Dictionary<Symbol, decimal> ComputeScoresOrthogonalized(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5V2FactorData> factors)
        {
            var eligible = factors
                .Where(pair => pair.Value != null && pair.Value.PresentFactorCount >= _minimumPresentFactors)
                .ToList();

            if (eligible.Count == 0) return new Dictionary<Symbol, decimal>();

            var nFactors = FactorSelectors.Length;
            var nStocks = eligible.Count;

            // Phase 1: Build z-score matrix
            var zScoreMatrix = BuildZScoreMatrix(eligible, nStocks, nFactors);

            // Phase 2: Symmetric Gram-Schmidt via Cholesky
            var orthoMatrix = SymmetricGramSchmidt(zScoreMatrix, nStocks, nFactors);

            // Phase 3: Apply weights to orthogonalized z-scores
            var weights = GetEffectiveWeights();
            var scores = new Dictionary<Symbol, decimal>();

            for (var s = 0; s < nStocks; s++)
            {
                var compositeScore = 0m;
                for (var k = 0; k < nFactors; k++)
                {
                    if (orthoMatrix[s, k].HasValue && weights.TryGetValue(FactorSelectors[k].name, out var w))
                    {
                        compositeScore += orthoMatrix[s, k]!.Value * w;
                    }
                }
                scores[eligible[s].Key] = compositeScore;
            }

            return scores;
        }

        #region Phase 1: Z-Score Matrix

        /// <summary>
        /// Build the z-score matrix (N stocks × K factors) with null handling.
        /// Each factor column is z-scored independently across stocks that have non-null values.
        /// </summary>
        private static decimal?[,] BuildZScoreMatrix(
            IReadOnlyList<KeyValuePair<Symbol, AShareBarraCNE5V2FactorData>> eligible,
            int nStocks, int nFactors)
        {
            var matrix = new decimal?[nStocks, nFactors];

            for (var k = 0; k < nFactors; k++)
            {
                var selector = FactorSelectors[k].selector;

                // Collect values and indices for stocks with non-null values
                var indices = new List<int>();
                var values = new List<decimal>();
                for (var s = 0; s < nStocks; s++)
                {
                    var val = selector(eligible[s].Value);
                    if (val.HasValue)
                    {
                        indices.Add(s);
                        values.Add(val.Value);
                    }
                }

                if (values.Count <= 1) continue;

                // Z-score the values
                var zScores = SafeZScores(values);
                for (var i = 0; i < indices.Count; i++)
                {
                    matrix[indices[i], k] = zScores[i];
                }
            }

            return matrix;
        }

        /// <summary>
        /// Population z-score identical to the base class SafeZScores.
        /// </summary>
        private static List<decimal> SafeZScores(IReadOnlyList<decimal> values)
        {
            if (values.Count <= 1) return values.Select(_ => 0m).ToList();
            var mean = values.Average();
            var variance = values.Select(v => Math.Pow((double)(v - mean), 2)).Average();
            var std = Math.Sqrt(variance);
            if (std <= double.Epsilon) return values.Select(_ => 0m).ToList();
            return values.Select(v => (decimal)(((double)v - (double)mean) / std)).ToList();
        }

        #endregion

        #region Phase 2: Symmetric Gram-Schmidt

        /// <summary>
        /// Symmetric Gram-Schmidt orthogonalization via Cholesky decomposition.
        /// Computes the 15×15 correlation matrix C from pairwise complete observations,
        /// decomposes C = L·Lᵀ, then returns Z_orth = Z · inv(Lᵀ).
        /// </summary>
        private decimal?[,] SymmetricGramSchmidt(
            decimal?[,] zScoreMatrix, int nStocks, int nFactors)
        {
            // Step 1: Compute pairwise correlation matrix
            var C = ComputeCorrelationMatrix(zScoreMatrix, nStocks, nFactors);

            // Step 2: Cholesky decomposition C = L·Lᵀ
            var cMatrix = DenseMatrix.OfArray(C);
            var cholesky = cMatrix.Cholesky();
            if (cholesky == null)
            {
                throw new InvalidOperationException(
                    "Cholesky decomposition failed — correlation matrix is not positive definite. " +
                    "This may indicate near-perfect collinearity between factors.");
            }
            var L = cholesky.Factor;

            // Step 3: Compute inv(Lᵀ) by solving Lᵀ · X = I
            var identity = DenseMatrix.CreateIdentity(nFactors);
            var invLT = L.TransposeThisAndMultiply(identity);
            var result = L.Transpose().Solve(identity); // inv(Lᵀ)

            // Step 4: Apply transformation Z_orth = Z · inv(Lᵀ)
            var orthoMatrix = new decimal?[nStocks, nFactors];

            for (var s = 0; s < nStocks; s++)
            {
                // Collect available factor indices for this stock
                var available = new List<int>();
                for (var k = 0; k < nFactors; k++)
                {
                    if (zScoreMatrix[s, k].HasValue)
                        available.Add(k);
                }

                if (available.Count == 0) continue;

                if (available.Count == nFactors)
                {
                    // Full orthogonalization: all 15 factors present
                    var zVec = new double[nFactors];
                    for (var k = 0; k < nFactors; k++)
                        zVec[k] = (double)zScoreMatrix[s, k]!.Value;

                    var zOrtho = result * DenseVector.OfArray(zVec);
                    for (var k = 0; k < nFactors; k++)
                        orthoMatrix[s, k] = (decimal)zOrtho[k];
                }
                else
                {
                    // Partial orthogonalization: extract submatrix for available factors
                    var subSize = available.Count;
                    var subInvLT = new double[subSize, subSize];
                    for (var i = 0; i < subSize; i++)
                        for (var j = 0; j < subSize; j++)
                            subInvLT[i, j] = result[available[i], available[j]];

                    var subZ = new double[subSize];
                    for (var i = 0; i < subSize; i++)
                        subZ[i] = (double)zScoreMatrix[s, available[i]]!.Value;

                    var subMatrix = DenseMatrix.OfArray(subInvLT);
                    var subVec = DenseVector.OfArray(subZ);
                    var subOrtho = subMatrix * subVec;

                    for (var i = 0; i < subSize; i++)
                        orthoMatrix[s, available[i]] = (decimal)subOrtho[i];
                }
            }

            return orthoMatrix;
        }

        /// <summary>
        /// Compute the K×K Pearson correlation matrix using pairwise complete observations.
        /// Each pair (i,j) uses only stocks where both factors are non-null.
        /// Diagonal is 1.0; off-diagonal clamped to [-0.99, 0.99] for numerical stability.
        /// </summary>
        private static double[,] ComputeCorrelationMatrix(
            decimal?[,] zScoreMatrix, int nStocks, int nFactors)
        {
            var C = new double[nFactors, nFactors];

            for (var i = 0; i < nFactors; i++)
            {
                C[i, i] = 1.0;
                for (var j = i + 1; j < nFactors; j++)
                {
                    var pairs = new List<(double zi, double zj)>();
                    for (var s = 0; s < nStocks; s++)
                    {
                        if (zScoreMatrix[s, i].HasValue && zScoreMatrix[s, j].HasValue)
                            pairs.Add(((double)zScoreMatrix[s, i]!.Value, (double)zScoreMatrix[s, j]!.Value));
                    }

                    var corr = pairs.Count < 5 ? 0.0 : PearsonCorrelation(pairs);
                    // Clamp for Cholesky numerical stability
                    corr = Math.Max(-0.99, Math.Min(0.99, corr));
                    C[i, j] = corr;
                    C[j, i] = corr;
                }
            }

            return C;
        }

        /// <summary>
        /// Pearson correlation coefficient between paired observations.
        /// </summary>
        private static double PearsonCorrelation(IReadOnlyList<(double x, double y)> pairs)
        {
            var n = pairs.Count;
            if (n < 3) return 0.0;

            var sumX = 0.0;
            var sumY = 0.0;
            for (var i = 0; i < n; i++)
            {
                sumX += pairs[i].x;
                sumY += pairs[i].y;
            }
            var meanX = sumX / n;
            var meanY = sumY / n;

            double cov = 0, varX = 0, varY = 0;
            for (var i = 0; i < n; i++)
            {
                var dx = pairs[i].x - meanX;
                var dy = pairs[i].y - meanY;
                cov += dx * dy;
                varX += dx * dx;
                varY += dy * dy;
            }

            var denom = Math.Sqrt(varX * varY);
            return denom > 0 ? cov / denom : 0.0;
        }

        #endregion
    }
}
