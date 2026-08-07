/*
 * BarraFactorRiskModel.cs — L4 Risk Management via Barra factor decomposition.
 *
 * Reads offline barra_risk_YYYY-MM-DD.json (Sigma_f 15x15, Delta per-symbol,
 * exposures B_t). Computes portfolio variance w'B Sigma_f B'w + w'Delta w,
 * applies factor-exposure budgets and volatility targeting.
 *
 * ZERO INTRUSION: new file. Does NOT modify AShareBarraCNE5V4RiskManagementModel
 * (Sharpe/vol scaling, NOT Barra decomposition) or any mature feature.
 *
 * L3 vs L4 boundary: L3 (MVO) optimizes weights using Sigma (historical or
 * barra). L4 is a risk-CONSTRAINT post-processor — factor exposure budget
 * prevents concentration, vol targeting prevents over-leverage.
 *
 * Mirrors MVOAlphaPortfolioConstructionModel JSON loading (latest <= asOf).
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Logging;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// L4 risk management model using Barra factor risk decomposition.
    /// Computes portfolio variance w'B Sigma_f B'w + w'Delta w from offline
    /// Barra risk files, then applies factor-exposure budget + vol targeting.
    /// </summary>
    public class BarraFactorRiskModel : RiskManagementModel
    {
        private readonly string _barraRiskDir;
        private readonly decimal _targetVol;
        private readonly decimal _maxFactorExposure;
        private readonly int _rebalanceMonths;

        private BarraRiskFile _riskCache;
        private DateTime _riskAsOf = DateTime.MinValue;
        private int _lastRebalanceYear = -1;
        private int _lastRebalanceMonth = -1;
        private Dictionary<string, double[]> _exposures = new Dictionary<string, double[]>();
        private Dictionary<string, double> _delta = new Dictionary<string, double>();
        private double[][] _sigmaF;

        public BarraFactorRiskModel(string barraRiskDir,
            decimal targetVol = 0.20m, decimal maxFactorExposure = 0.5m,
            int rebalanceMonths = 1)
        {
            _barraRiskDir = barraRiskDir ?? throw new ArgumentNullException(nameof(barraRiskDir));
            _targetVol = targetVol;
            _maxFactorExposure = maxFactorExposure;
            _rebalanceMonths = rebalanceMonths;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            if (targets == null || targets.Length == 0) return targets;
            if (!IsRebalanceMonth(algorithm.Time)) return targets;
            LoadLatestRisk(algorithm.Time);

            if (_riskCache == null || _sigmaF == null)
            {
                // VISIBLE degradation: log + return targets unchanged (do NOT silently zero out).
                algorithm.Debug($"[Barra-Risk] {algorithm.Time:yyyy-MM-dd}: no Barra risk data, skipping risk adjustment");
                return targets;
            }

            // Build weight vector from targets (target value / total portfolio value).
            var weights = new Dictionary<string, double>();
            var tpv = (double)algorithm.Portfolio.TotalPortfolioValue;
            if (tpv <= 0) return targets;
            foreach (var t in targets)
            {
                if (t == null || t.Symbol == null) continue;
                var ticker = t.Symbol.Value;
                var price = algorithm.Securities[t.Symbol].Price;
                var targetValue = (double)(t.Quantity * price);
                weights[ticker] = targetValue / tpv;
            }

            weights = ApplyFactorExposureBudget(weights);
            weights = ApplyVolTargeting(weights);

            var adjusted = new List<IPortfolioTarget>();
            foreach (var t in targets)
            {
                if (t == null || t.Symbol == null) continue;
                var ticker = t.Symbol.Value;
                if (weights.TryGetValue(ticker, out var w))
                {
                    var pt = PortfolioTarget.Percent(algorithm, t.Symbol, (decimal)w);
                    if (pt != null) adjusted.Add(pt);
                }
            }

            _lastRebalanceYear = algorithm.Time.Year;
            _lastRebalanceMonth = algorithm.Time.Month;
            return adjusted;
        }

        /// <summary>
        /// Scale weights so that each factor exposure |sum_s w_s * B_s[f]| stays
        /// within maxFactorExposure. Per-symbol scale = min over factors of the
        /// factor-level scale (only factors where the symbol has non-zero exposure).
        /// </summary>
        private Dictionary<string, double> ApplyFactorExposureBudget(Dictionary<string, double> weights)
        {
            if (_sigmaF == null || _sigmaF.Length == 0) return weights;
            int nFactors = _sigmaF.Length;
            var factorExp = new double[nFactors];
            foreach (var kv in weights)
            {
                if (_exposures.TryGetValue(kv.Key, out var b))
                {
                    for (int f = 0; f < nFactors && f < b.Length; f++)
                    {
                        factorExp[f] += kv.Value * b[f];
                    }
                }
            }

            bool overBudget = false;
            var scales = new double[nFactors];
            for (int f = 0; f < nFactors; f++)
            {
                if (Math.Abs(factorExp[f]) > (double)_maxFactorExposure)
                {
                    scales[f] = (double)_maxFactorExposure / Math.Abs(factorExp[f]);
                    overBudget = true;
                }
                else
                {
                    scales[f] = 1.0;
                }
            }
            if (!overBudget) return weights;

            var adjusted = new Dictionary<string, double>();
            foreach (var kv in weights)
            {
                double symScale = 1.0;
                if (_exposures.TryGetValue(kv.Key, out var b))
                {
                    for (int f = 0; f < nFactors && f < b.Length; f++)
                    {
                        if (b[f] != 0.0)
                        {
                            symScale = Math.Min(symScale, scales[f]);
                        }
                    }
                }
                adjusted[kv.Key] = kv.Value * symScale;
            }
            return adjusted;
        }

        /// <summary>
        /// Scale all weights down proportionally so portfolio vol <= targetVol.
        /// Never scales up (only de-leverages when vol exceeds target).
        /// </summary>
        private Dictionary<string, double> ApplyVolTargeting(Dictionary<string, double> weights)
        {
            var variance = ComputePortfolioVariance(weights);
            if (variance <= 0) return weights;
            var vol = Math.Sqrt(variance);
            if (vol <= (double)_targetVol) return weights;
            var scale = (double)_targetVol / vol;
            var result = new Dictionary<string, double>();
            foreach (var kv in weights)
            {
                result[kv.Key] = kv.Value * scale;
            }
            return result;
        }

        /// <summary>
        /// Portfolio variance = w'B Sigma_f B'w + w' Delta w.
        /// Public for testing; production path calls this from ApplyVolTargeting.
        /// </summary>
        public double ComputePortfolioVariance(Dictionary<string, double> weights)
        {
            if (_sigmaF == null || _sigmaF.Length == 0) return 0.0;
            int nFactors = _sigmaF.Length;
            var tickers = weights.Keys.ToList();

            // B' w  (nFactors vector)
            var Btw = new double[nFactors];
            foreach (var ticker in tickers)
            {
                if (_exposures.TryGetValue(ticker, out var b))
                {
                    for (int f = 0; f < nFactors && f < b.Length; f++)
                    {
                        Btw[f] += weights[ticker] * b[f];
                    }
                }
            }

            // Sigma_f * (B' w)
            var SfBtw = new double[nFactors];
            for (int i = 0; i < nFactors; i++)
            {
                double acc = 0.0;
                var row = _sigmaF[i];
                for (int j = 0; j < nFactors && j < row.Length; j++)
                {
                    acc += row[j] * Btw[j];
                }
                SfBtw[i] = acc;
            }

            // w' B Sigma_f B' w
            double factorVar = 0.0;
            for (int i = 0; i < nFactors; i++)
            {
                factorVar += Btw[i] * SfBtw[i];
            }

            // w' Delta w (Delta is diagonal: per-symbol specific variance)
            double specificVar = 0.0;
            foreach (var ticker in tickers)
            {
                if (_delta.TryGetValue(ticker, out var d))
                {
                    var w = weights[ticker];
                    specificVar += w * w * d;
                }
            }

            return factorVar + specificVar;
        }

        /// <summary>
        /// Scan barra_risk_*.json, pick the latest file with date <= asOf.
        /// Mirrors MVOAlphaPortfolioConstructionModel.LoadLatestWeights.
        /// Strips .SH/.SZ suffix from exposures/delta keys to match Symbol.Value.
        /// </summary>
        private void LoadLatestRisk(DateTime asOf)
        {
            if (_riskAsOf != DateTime.MinValue && _riskAsOf >= asOf) return;

            if (!Directory.Exists(_barraRiskDir))
            {
                Log.Error($"[Barra-Risk] Dir not found: {_barraRiskDir}");
                return;
            }

            var files = Directory.GetFiles(_barraRiskDir, "barra_risk_*.json");
            if (files.Length == 0)
            {
                Log.Error($"[Barra-Risk] No risk files in {_barraRiskDir}");
                return;
            }

            BarraRiskFile latest = null;
            DateTime latestDate = DateTime.MinValue;

            foreach (var file in files)
            {
                var name = Path.GetFileNameWithoutExtension(file);
                var prefix = "barra_risk_";
                if (name.Length <= prefix.Length) continue;
                var dateStr = name.Substring(prefix.Length);
                if (!DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out var fileDate)) continue;
                if (fileDate > asOf) continue;

                BarraRiskFile parsed = null;
                try
                {
                    parsed = JsonConvert.DeserializeObject<BarraRiskFile>(File.ReadAllText(file));
                }
                catch (Exception ex)
                {
                    Log.Error($"[Barra-Risk] Parse fail {file}: {ex.Message}");
                    continue;
                }
                if (parsed == null) continue;
                if (fileDate > latestDate)
                {
                    latestDate = fileDate;
                    latest = parsed;
                }
            }

            if (latest == null)
            {
                Log.Error($"[Barra-Risk] No risk file <= {asOf:yyyy-MM-dd}");
                return;
            }

            _riskCache = latest;
            _riskAsOf = latestDate;
            _sigmaF = latest.SigmaF;
            _exposures = (latest.Exposures ?? new Dictionary<string, double[]>())
                .ToDictionary(kv => StripExchange(kv.Key), kv => kv.Value);
            _delta = (latest.Delta ?? new Dictionary<string, double>())
                .ToDictionary(kv => StripExchange(kv.Key), kv => kv.Value);
        }

        private static string StripExchange(string tsCode)
        {
            if (string.IsNullOrEmpty(tsCode)) return tsCode;
            var idx = tsCode.IndexOf('.');
            return idx >= 0 ? tsCode.Substring(0, idx) : tsCode;
        }

        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true;
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }

        // --- test hooks (internal; used only by NUnit) ---
        internal void LoadLatestRiskForTest(DateTime asOf)
        {
            _riskAsOf = DateTime.MinValue;
            _riskCache = null;
            _sigmaF = null;
            _exposures = new Dictionary<string, double[]>();
            _delta = new Dictionary<string, double>();
            LoadLatestRisk(asOf);
        }

        internal DateTime RiskAsOfForTest() => _riskAsOf;

        internal double ComputePortfolioVarianceForTest(Dictionary<string, double> weights)
            => ComputePortfolioVariance(weights);

        internal Dictionary<string, double> ApplyFactorExposureBudgetForTest(Dictionary<string, double> weights)
            => ApplyFactorExposureBudget(weights);
    }

    /// <summary>
    /// DTO for barra_risk_YYYY-MM-DD.json produced by export_barra_risk.py.
    /// Snake_case JSON keys via [JsonProperty].
    /// </summary>
    public class BarraRiskFile
    {
        [JsonProperty("as_of")] public string AsOf { get; set; }
        [JsonProperty("factors")] public List<string> Factors { get; set; } = new List<string>();
        [JsonProperty("sigma_f")] public double[][] SigmaF { get; set; }
        [JsonProperty("delta")] public Dictionary<string, double> Delta { get; set; } = new Dictionary<string, double>();
        [JsonProperty("exposures")] public Dictionary<string, double[]> Exposures { get; set; } = new Dictionary<string, double[]>();
        [JsonProperty("est_window")] public int EstWindow { get; set; }
        [JsonProperty("decay_halflife")] public int DecayHalflife { get; set; }
        [JsonProperty("n_symbols")] public int NSymbols { get; set; }
        [JsonProperty("n_obs")] public int NObs { get; set; }
        [JsonProperty("fallback")] public string Fallback { get; set; }
    }
}
