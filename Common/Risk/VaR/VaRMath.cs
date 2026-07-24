// file: Common/Risk/VaR/VaRMath.cs
using System;
using System.Collections.Generic;
using System.Linq;
using Complex = System.Numerics.Complex;
using MathNet.Numerics.Distributions;
using MathNet.Numerics.LinearAlgebra;
using MathNet.Numerics.LinearAlgebra.Double;
using MathNet.Numerics.LinearAlgebra.Factorization;
using MathNet.Numerics.Random;
using MathNet.Numerics.Statistics;

namespace QuantConnect.Risk.VaR
{
    /// <summary>Internal math helpers for VarEngine.</summary>
    internal static class VaRMath
    {
        /// <summary>Drop NaN/Inf, check count, detect constant/zero-variance.</summary>
        public static (double[] Clean, bool AllConstant, bool ZeroVariance, int Count) PrepareReturns(
            IEnumerable<double> returns, int minHistoryDays)
        {
            var clean = (returns ?? Array.Empty<double>())
                .Where(r => !double.IsNaN(r) && !double.IsInfinity(r))
                .ToArray();

            if (clean.Length == 0)
                return (Array.Empty<double>(), true, true, 0);

            var allConstant = clean.All(r => Math.Abs(r - clean[0]) < 1e-14);
            var variance = clean.Variance();
            var zeroVariance = variance < 1e-14;

            return (clean, allConstant, zeroVariance, clean.Length);
        }

        /// <summary>Sample moments: mean, sd, skew, excess kurtosis (MathNet Kurtosis is excess).</summary>
        public static (double Mean, double Sd, double Skew, double ExKurt) SampleMoments(double[] returns)
        {
            if (returns == null || returns.Length < 2)
                return (0, 0, 0, 0);

            return (returns.Mean(), returns.StandardDeviation(), returns.Skewness(), returns.Kurtosis());
        }

        /// <summary>Build overlapping k-day log returns: s_t = exp(sum ln(1+r)) - 1.</summary>
        public static double[] BuildOverlappingKDayReturns(double[] dailyReturns, int k)
        {
            if (dailyReturns == null || dailyReturns.Length < k)
                return Array.Empty<double>();

            var n = dailyReturns.Length - k + 1;
            var result = new double[n];
            for (int t = 0; t < n; t++)
            {
                double logSum = 0;
                for (int i = 0; i < k; i++)
                    logSum += Math.Log(1 + dailyReturns[t + i]);
                result[t] = Math.Exp(logSum) - 1;
            }
            return result;
        }

        /// <summary>Build sample covariance [nAssets,nAssets] from [nObs,nAssets] matrix, pairwise complete, n-1.</summary>
        public static double[,] BuildCovariance(double[,] returnsMatrix, out string warning)
        {
            warning = null;
            int nObs = returnsMatrix.GetLength(0);
            int nAssets = returnsMatrix.GetLength(1);
            if (nObs < 2) { warning = "Insufficient observations"; return null; }

            var cov = new double[nAssets, nAssets];
            for (int i = 0; i < nAssets; i++)
            {
                for (int j = i; j < nAssets; j++)
                {
                    var (c, count) = PairwiseCovariance(returnsMatrix, i, j);
                    cov[i, j] = cov[j, i] = c;
                    if (i != j && count < 30)
                        warning = $"Pair ({i},{j}) has only {count} overlapping obs";
                }
            }
            return cov;
        }

        private static (double Cov, int Count) PairwiseCovariance(double[,] m, int colI, int colJ)
        {
            int nObs = m.GetLength(0);
            var li = new List<double>();
            var lj = new List<double>();
            for (int t = 0; t < nObs; t++)
            {
                var vi = m[t, colI]; var vj = m[t, colJ];
                if (!double.IsNaN(vi) && !double.IsInfinity(vi) && !double.IsNaN(vj) && !double.IsInfinity(vj))
                { li.Add(vi); lj.Add(vj); }
            }
            if (li.Count < 2) return (0, li.Count);
            var mi = li.Average(); var mj = lj.Average();
            double sum = 0;
            for (int k = 0; k < li.Count; k++) sum += (li[k] - mi) * (lj[k] - mj);
            return (sum / (li.Count - 1), li.Count);
        }

        /// <summary>Ensure PD: symmetrize -> Cholesky -> Evd floor -> diagonal fallback.</summary>
        public static (double[,] Matrix, bool Repaired, string Message) EnsurePd(double[,] cov, double eigenvalueFloor)
        {
            if (cov == null) return (null, false, "Null covariance");
            int n = cov.GetLength(0);
            var matrix = (Matrix<double>)DenseMatrix.OfArray(cov);
            matrix = (matrix + matrix.TransposeAndMultiply(matrix)) / 2.0;

            if (TryCholesky(matrix, out _))
                return (cov, false, "Cholesky OK");

            var evd = matrix.Evd();
            var eigvals = evd.EigenValues;
            var eigvecs = evd.EigenVectors;
            bool repaired = false;
            for (int i = 0; i < eigvals.Count; i++)
            {
                if (eigvals[i].Real < eigenvalueFloor)
                { eigvals[i] = new Complex(eigenvalueFloor, 0); repaired = true; }
            }
            var D = Matrix<double>.Build.Dense(n, n);
            for (int i = 0; i < n; i++) D[i, i] = eigvals[i].Real;
            matrix = eigvecs * D * eigvecs.TransposeAndMultiply(eigvecs);

            if (TryCholesky(matrix, out _))
                return (matrix.ToArray(), true, "Evd repair succeeded");

            var diag = new double[n, n];
            for (int i = 0; i < n; i++)
                diag[i, i] = matrix[i, i] > 0 ? matrix[i, i] : eigenvalueFloor;
            return (diag, true, "Non-PD: diagonal fallback");
        }

        private static bool TryCholesky(Matrix<double> matrix, out Cholesky<double> chol)
        {
            try
            {
                chol = matrix.Cholesky();
                return chol != null;
            }
            catch (ArgumentException)
            {
                chol = null;
                return false;
            }
        }

        /// <summary>Cholesky-simulate correlated normals: x = L * z, cov = L * L^T. Returns [paths, nAssets].</summary>
        public static double[,] CholeskySimulate(double[,] cov, int paths, int seed)
        {
            int n = cov.GetLength(0);
            var matrix = (Matrix<double>)DenseMatrix.OfArray(cov);
            var chol = matrix.Cholesky();
            if (chol == null) return null;
            var L = chol.Factor;

            var rng = new MersenneTwister(seed, true);
            var normal = new Normal(0, 1, rng);
            var result = new double[paths, n];
            for (int p = 0; p < paths; p++)
            {
                var z = Vector<double>.Build.Random(n, normal);
                var x = L * z;  // column-vector convention: x = L * z
                for (int i = 0; i < n; i++) result[p, i] = x[i];
            }
            return result;
        }
    }
}
