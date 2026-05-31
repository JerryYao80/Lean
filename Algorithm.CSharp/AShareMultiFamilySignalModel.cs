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

namespace QuantConnect.Algorithm.CSharp
{
    public sealed class AShareMultiFamilySignalSettings
    {
        public decimal MomentumReversalWeight { get; set; } = 1.0m;
        public decimal ValueQualityWeight { get; set; } = 1.0m;
        public decimal MoneyFlowWeight { get; set; } = 0.8m;
        public decimal EarningsSurpriseWeight { get; set; } = 0.8m;
        public decimal ChipCostWeight { get; set; } = 0.6m;
        public decimal EtfPremiumWeight { get; set; } = 0.3m;
        public decimal SectorRotationWeight { get; set; } = 0.5m;
        public decimal MarginSignalWeight { get; set; } = 0.8m;
        public decimal NorthboundFlowWeight { get; set; } = 0.8m;
        public decimal MultiFactorWeight { get; set; } = 1.0m;
        public decimal AnalystSignalWeight { get; set; } = 0.3m;
        public decimal MacroRateWeight { get; set; } = 0.5m;
        public decimal BarraMomentumWeight { get; set; } = 0.8m;
        public decimal BarraValueWeight { get; set; } = 0.6m;
        public decimal BarraQualityWeight { get; set; } = 0.4m;
        public decimal LowVolatilityWeight { get; set; } = 1.0m;
        public decimal SizeTiltWeight { get; set; } = 0.6m;
        public decimal LiquidityPremiumWeight { get; set; } = 0.5m;
        public decimal ChipConcentrationWeight { get; set; } = 0.8m;
        public decimal RateSensitivityWeight { get; set; } = 0.4m;

        public int TopN { get; set; } = 30;
        public int RetentionBuffer { get; set; } = 6;
        public decimal MinPrice { get; set; } = 5m;
        public decimal MinCircMv { get; set; } = 1_000_000m;
        public decimal MinTurnoverRate { get; set; } = 0.3m;
        public int MinListedDays { get; set; } = 250;
        public int MinPresentFamilies { get; set; } = 4;
        public decimal MaxSingleWeight { get; set; } = 0.10m;
        public string WeightingMode { get; set; } = "black-litterman";
        public decimal BlackLittermanTau { get; set; } = 0.05m;
        public decimal BlackLittermanRiskAversion { get; set; } = 2.20m;
        public decimal BlackLittermanViewScale { get; set; } = 0.08m;
        public decimal BlackLittermanViewConfidence { get; set; } = 0.65m;
        public decimal BlackLittermanPriorBlend { get; set; } = 0.40m;
        public decimal KellyWeightFraction { get; set; } = 0.50m;
        public decimal KellyVarianceFloor { get; set; } = 0.35m;
        public decimal MinRegimeAdjustment { get; set; } = 0.55m;
    }

    public sealed class AShareMultiFamilyTarget
    {
        public Symbol Symbol { get; init; }
        public decimal Weight { get; init; }
        public decimal Score { get; init; }
        public Dictionary<string, decimal> FamilyScores { get; init; }
    }

    /// <summary>
    /// Multi-family signal model that composites 12 strategy families into a unified scoring model.
    /// </summary>
    public static class AShareMultiFamilySignalModel
    {
        private static readonly string[] FamilyNames = new[]
        {
            "momentum_reversal", "value_quality", "money_flow", "earnings_surprise",
            "chip_cost", "etf_premium", "sector_rotation", "margin_signal",
            "northbound_flow", "multi_factor", "analyst_signal", "macro_rate",
            "barra_momentum", "barra_value", "barra_quality",
            "low_volatility", "size_tilt", "liquidity_premium", "chip_concentration", "rate_sensitivity"
        };

        public static Dictionary<Symbol, decimal> ComputeScores(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors,
            AShareMultiFamilySignalSettings settings,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> barraFactors = null)
        {
            var familyScores = ComputeFamilyScores(factors, settings, barraFactors);
            return ComputeComposite(familyScores, settings);
        }

        public static Dictionary<Symbol, Dictionary<string, decimal>> ComputeFamilyScores(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors,
            AShareMultiFamilySignalSettings settings,
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> barraFactors = null)
        {
            var result = new Dictionary<Symbol, Dictionary<string, decimal>>();
            if (factors == null || factors.Count == 0)
            {
                return result;
            }

            var momentum = ComputeMomentumReversalScore(factors);
            var value = ComputeValueQualityScore(factors);
            var money = ComputeMoneyFlowScore(factors);
            var earnings = ComputeEarningsSurpriseScore(factors);
            var chip = ComputeChipCostScore(factors);
            var etf = ComputeEtfPremiumScore(factors);
            var sector = ComputeSectorRotationScore(factors);
            var margin = ComputeMarginSignalScore(factors);
            var north = ComputeNorthboundFlowScore(factors);
            var multi = ComputeMultiFactorScore(factors);
            var analyst = ComputeAnalystSignalScore(factors);
            var macro = ComputeMacroRateScore(factors);
            var lowVol = ComputeLowVolatilityScore(factors);
            var sizeTilt = ComputeSizeTiltScore(factors);
            var liqPrem = ComputeLiquidityPremiumScore(factors);
            var chipConc = ComputeChipConcentrationScore(factors);
            var rateSens = ComputeRateSensitivityScore(factors);

            var allSymbols = factors.Keys.ToHashSet();
            foreach (var symbol in allSymbols)
            {
                var scores = new Dictionary<string, decimal>(StringComparer.Ordinal);
                scores["momentum_reversal"] = momentum.TryGetValue(symbol, out var ms) ? ms : 0m;
                scores["value_quality"] = value.TryGetValue(symbol, out var vs) ? vs : 0m;
                scores["money_flow"] = money.TryGetValue(symbol, out var mo) ? mo : 0m;
                scores["earnings_surprise"] = earnings.TryGetValue(symbol, out var es) ? es : 0m;
                scores["chip_cost"] = chip.TryGetValue(symbol, out var cs) ? cs : 0m;
                scores["etf_premium"] = etf.TryGetValue(symbol, out var ep) ? ep : 0m;
                scores["sector_rotation"] = sector.TryGetValue(symbol, out var sr) ? sr : 0m;
                scores["margin_signal"] = margin.TryGetValue(symbol, out var mg) ? mg : 0m;
                scores["northbound_flow"] = north.TryGetValue(symbol, out var nf) ? nf : 0m;
                scores["multi_factor"] = multi.TryGetValue(symbol, out var mf) ? mf : 0m;
                scores["analyst_signal"] = analyst.TryGetValue(symbol, out var an) ? an : 0m;
                scores["macro_rate"] = macro.TryGetValue(symbol, out var mc) ? mc : 0m;

                // Barra CNE5 families (V3+)
                if (barraFactors != null)
                {
                    var barraMomentum = ComputeBarraMomentumScore(barraFactors);
                    var barraValue = ComputeBarraValueScore(barraFactors);
                    var barraQuality = ComputeBarraQualityScore(barraFactors);
                    scores["barra_momentum"] = barraMomentum.TryGetValue(symbol, out var bm) ? bm : 0m;
                    scores["barra_value"] = barraValue.TryGetValue(symbol, out var bv) ? bv : 0m;
                    scores["barra_quality"] = barraQuality.TryGetValue(symbol, out var bq) ? bq : 0m;
                }
                else
                {
                    scores["barra_momentum"] = 0m;
                    scores["barra_value"] = 0m;
                    scores["barra_quality"] = 0m;
                }

                // Barra-inspired mined families (V5+)
                scores["low_volatility"] = lowVol.TryGetValue(symbol, out var lv) ? lv : 0m;
                scores["size_tilt"] = sizeTilt.TryGetValue(symbol, out var st) ? st : 0m;
                scores["liquidity_premium"] = liqPrem.TryGetValue(symbol, out var lp) ? lp : 0m;
                scores["chip_concentration"] = chipConc.TryGetValue(symbol, out var cc) ? cc : 0m;
                scores["rate_sensitivity"] = rateSens.TryGetValue(symbol, out var rs) ? rs : 0m;

                result[symbol] = scores;
            }

            return result;
        }

        public static Dictionary<Symbol, decimal> ComputeComposite(
            IReadOnlyDictionary<Symbol, Dictionary<string, decimal>> familyScores,
            AShareMultiFamilySignalSettings settings)
        {
            var weights = new Dictionary<string, decimal>(StringComparer.Ordinal)
            {
                ["momentum_reversal"] = settings.MomentumReversalWeight,
                ["value_quality"] = settings.ValueQualityWeight,
                ["money_flow"] = settings.MoneyFlowWeight,
                ["earnings_surprise"] = settings.EarningsSurpriseWeight,
                ["chip_cost"] = settings.ChipCostWeight,
                ["etf_premium"] = settings.EtfPremiumWeight,
                ["sector_rotation"] = settings.SectorRotationWeight,
                ["margin_signal"] = settings.MarginSignalWeight,
                ["northbound_flow"] = settings.NorthboundFlowWeight,
                ["multi_factor"] = settings.MultiFactorWeight,
                ["analyst_signal"] = settings.AnalystSignalWeight,
                ["macro_rate"] = settings.MacroRateWeight,
                ["barra_momentum"] = settings.BarraMomentumWeight,
                ["barra_value"] = settings.BarraValueWeight,
                ["barra_quality"] = settings.BarraQualityWeight,
                ["low_volatility"] = settings.LowVolatilityWeight,
                ["size_tilt"] = settings.SizeTiltWeight,
                ["liquidity_premium"] = settings.LiquidityPremiumWeight,
                ["chip_concentration"] = settings.ChipConcentrationWeight,
                ["rate_sensitivity"] = settings.RateSensitivityWeight,
            };

            var composite = new Dictionary<Symbol, decimal>();
            foreach (var pair in familyScores)
            {
                decimal weightedSum = 0m;
                decimal absWeightSum = 0m;
                foreach (var family in pair.Value)
                {
                    var w = weights.TryGetValue(family.Key, out var fw) ? fw : 0m;
                    weightedSum += w * family.Value;
                    absWeightSum += Math.Abs(w);
                }
                composite[pair.Key] = absWeightSum > 0m ? weightedSum / absWeightSum : 0m;
            }
            return composite;
        }

        public static List<AShareMultiFamilyTarget> SelectPortfolio(
            IReadOnlyDictionary<Symbol, decimal> compositeScores,
            IReadOnlyDictionary<Symbol, Dictionary<string, decimal>> familyScores,
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors,
            int topN,
            decimal minScoreSpread,
            decimal targetExposure,
            string weightingMode,
            AShareMultiFamilySignalSettings settings)
        {
            if (compositeScores == null || compositeScores.Count == 0 || topN <= 0 || targetExposure <= 0m)
            {
                return new List<AShareMultiFamilyTarget>();
            }

            var ranked = compositeScores
                .OrderByDescending(pair => pair.Value)
                .ThenBy(pair => pair.Key.Value, StringComparer.Ordinal)
                .ToList();

            var orderedScores = ranked.Select(pair => pair.Value).OrderBy(v => v).ToList();
            var median = GetMedian(orderedScores);
            var spread = ranked.Count > 0 ? ranked[0].Value - median : 0m;
            if (spread < minScoreSpread)
            {
                return new List<AShareMultiFamilyTarget>();
            }

            var selected = ranked.Take(topN).ToList();
            var mode = (weightingMode ?? settings.WeightingMode ?? "equal").Trim().ToLowerInvariant();
            Dictionary<Symbol, decimal> rawWeights;
            switch (mode)
            {
                case "black-litterman":
                case "blacklitterman":
                case "bl":
                    rawWeights = BuildBlackLittermanKellyWeights(selected, factors, settings);
                    break;
                case "market-cap":
                case "marketcap":
                    rawWeights = BuildMarketCapWeights(selected, factors);
                    break;
                default:
                    rawWeights = BuildEqualWeights(selected);
                    break;
            }

            var normalized = NormalizeAndCapWeights(rawWeights, targetExposure, settings.MaxSingleWeight);
            return selected
                .Where(item => normalized.TryGetValue(item.Key, out var weight) && weight > 0m)
                .Select(item => new AShareMultiFamilyTarget
                {
                    Symbol = item.Key,
                    Weight = normalized[item.Key],
                    Score = item.Value,
                    FamilyScores = familyScores.TryGetValue(item.Key, out var fs) ? fs : new Dictionary<string, decimal>()
                })
                .ToList();
        }

        public static Dictionary<string, decimal> ComputeFamilyExposure(
            IReadOnlyDictionary<Symbol, decimal> weights,
            IReadOnlyDictionary<Symbol, Dictionary<string, decimal>> familyScores)
        {
            var result = FamilyNames.ToDictionary(f => f, _ => 0m);
            if (weights == null || familyScores == null || weights.Count == 0)
            {
                return result;
            }

            var totalWeight = weights.Values.Select(Math.Abs).Sum();
            if (totalWeight <= 0m)
            {
                return result;
            }

            foreach (var pair in weights)
            {
                var normalizedWeight = pair.Value / totalWeight;
                if (!familyScores.TryGetValue(pair.Key, out var scores))
                {
                    continue;
                }
                foreach (var family in FamilyNames)
                {
                    if (scores.TryGetValue(family, out var score))
                    {
                        result[family] += normalizedWeight * score;
                    }
                }
            }
            return result;
        }

        // 5 Barra-inspired mined family score methods (V5)

        private static Dictionary<Symbol, decimal> ComputeLowVolatilityScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            // Low volatility anomaly: negative z-score on volatility_20
            ApplyFactorDict(eligible, scores, f => Negate(f.GetDecimal("volatility_20")), 1.0m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeSizeTiltScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            // Small-cap tilt: LogInverse of total_mv (smaller cap = higher score)
            ApplyFactorDict(eligible, scores, f => LogInverse(f.GetDecimal("total_mv")), 0.5m);
            // Circulation constraint: higher circ_mv/total_mv ratio = more freely traded
            ApplyFactorDict(eligible, scores, f => CircRatio(f), 0.5m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeLiquidityPremiumScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            // Amihud illiquidity: |return| / (volume * price) — higher = less liquid = premium
            ApplyFactorDict(eligible, scores, f => Illiquidity(f), 0.5m);
            // Low turnover rate also signals illiquidity premium
            ApplyFactorDict(eligible, scores, f => Negate(f.GetDecimal("turnover_rate")), 0.5m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeChipConcentrationScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            // Chip spread: -(cost_95pct - cost_5pct) / cost_50pct — tighter = more concentrated
            ApplyFactorDict(eligible, scores, f => ChipSpread(f), 0.4m);
            // Winner rate: higher = more profitable holders
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("winner_rate"), 0.4m);
            // Cost proximity: (cost_50pct / close - 1) — closer to 0 = better support
            ApplyFactorDict(eligible, scores, f => CostProximity(f), 0.2m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeRateSensitivityScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            // High dividend yield stocks are more rate-sensitive (benefit from falling rates)
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("dv_ttm"), 1.0m);
            return scores;
        }

        // Helper functions for V5 mined families

        private static decimal? Negate(decimal? value)
        {
            if (!value.HasValue) return null;
            return -value.Value;
        }

        private static decimal? CircRatio(AShareTushareFactorData f)
        {
            var circMv = f.GetDecimal("circ_mv");
            var totalMv = f.GetDecimal("total_mv");
            if (!circMv.HasValue || !totalMv.HasValue || totalMv.Value <= 0m) return null;
            return circMv.Value / totalMv.Value;
        }

        private static decimal? Illiquidity(AShareTushareFactorData f)
        {
            var pctChg = f.GetDecimal("pct_chg");
            var vol = f.GetDecimal("vol");
            var close = f.GetDecimal("close");
            if (!pctChg.HasValue || !vol.HasValue || !close.HasValue || vol.Value <= 0m || close.Value <= 0m) return null;
            return Math.Abs(pctChg.Value) / (vol.Value * close.Value);
        }

        private static decimal? ChipSpread(AShareTushareFactorData f)
        {
            var cost5 = f.GetDecimal("cost_5pct");
            var cost50 = f.GetDecimal("cost_50pct");
            var cost95 = f.GetDecimal("cost_95pct");
            if (!cost5.HasValue || !cost50.HasValue || !cost95.HasValue || cost50.Value <= 0m) return null;
            return -(cost95.Value - cost5.Value) / cost50.Value;
        }

        // 12 family score methods

        private static Dictionary<Symbol, decimal> ComputeMomentumReversalScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("momentum_120_20"), 1.0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("return_5"), -0.5m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("turnover_rate"), -0.3m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("total_mv"), -0.2m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeValueQualityScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => LogInverse(f.GetDecimal("pe_ttm")), 0.3m);
            ApplyFactorDict(eligible, scores, f => LogInverse(f.GetDecimal("pb")), 0.3m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("dv_ttm"), 0.2m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("roe"), 0.3m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("grossprofit_margin"), 0.2m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("debt_to_assets"), -0.2m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeMoneyFlowScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("net_mf_amount"), 0.6m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("flow_ratio"), 0.4m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("big_flow_ratio"), 0.4m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeEarningsSurpriseScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("dt_netprofit_yoy"), 0.5m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("forecast_p_change_max"), 0.3m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("express_yoy_net_profit"), 0.3m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeChipCostScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("winner_rate"), 0.5m);
            // Cost proximity: (cost_50pct / close - 1) — closer to 0 = better
            ApplyFactorDict(eligible, scores, f => CostProximity(f), 0.5m);
            return scores;
        }

        private static decimal? CostProximity(AShareTushareFactorData f)
        {
            var cost = f.GetDecimal("cost_50pct");
            var close = f.GetDecimal("close");
            if (!cost.HasValue || !close.HasValue || close.Value <= 0m) return null;
            return -(Math.Abs(cost.Value / close.Value - 1m));
        }

        private static Dictionary<Symbol, decimal> ComputeEtfPremiumScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            // ETF-only family; all stocks get 0
            return factors.Keys.ToDictionary(k => k, _ => 0m);
        }

        private static Dictionary<Symbol, decimal> ComputeSectorRotationScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            // Group by sector_code; compute within-sector relative scores
            var eligible = factors.Where(p => p.Value != null && !string.IsNullOrEmpty(p.Value.GetString("sector_code"))).ToList();
            if (eligible.Count <= 1)
            {
                return factors.Keys.ToDictionary(k => k, _ => 0m);
            }

            var sectorGroups = eligible
                .GroupBy(p => p.Value.GetString("sector_code") ?? "unknown")
                .ToList();

            var scores = factors.Keys.ToDictionary(k => k, _ => 0m);

            foreach (var group in sectorGroups)
            {
                var groupList = group.ToList();
                if (groupList.Count <= 1) continue;
                var groupScores = groupList.ToDictionary(p => p.Key, _ => 0m);
                ApplyFactorDict(groupList, groupScores, f => LogInverse(f.GetDecimal("pe_ttm")), 0.4m);
                ApplyFactorDict(groupList, groupScores, f => f.GetDecimal("momentum_120_20"), 0.6m);
                foreach (var pair in groupScores)
                {
                    scores[pair.Key] = pair.Value;
                }
            }
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeMarginSignalScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => MarginChangeRate(f), 0.5m);
            ApplyFactorDict(eligible, scores, f => MarginRatio(f), 0.5m);
            return scores;
        }

        private static decimal? MarginChangeRate(AShareTushareFactorData f)
        {
            var rzye = f.GetDecimal("rzye");
            // We approximate change rate via current level — no lag available in snapshot
            return rzye;
        }

        private static decimal? MarginRatio(AShareTushareFactorData f)
        {
            var rzmre = f.GetDecimal("rzmre");
            var rqye = f.GetDecimal("rqye");
            if (!rzmre.HasValue || !rqye.HasValue || (rzmre.Value + rqye.Value) <= 0m) return null;
            return rzmre.Value / (rzmre.Value + rqye.Value);
        }

        private static Dictionary<Symbol, decimal> ComputeNorthboundFlowScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("hk_hold_ratio"), 0.5m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("hsgt_north_money"), 0.5m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeMultiFactorScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => LogInverse(f.GetDecimal("pe_ttm")), 0.2m);
            ApplyFactorDict(eligible, scores, f => LogInverse(f.GetDecimal("pb")), 0.2m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("roe"), 0.2m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("net_mf_amount"), 0.2m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("turnover_rate"), -0.2m);
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeAnalystSignalScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var eligible = factors.Where(p => p.Value != null).ToList();
            var scores = eligible.ToDictionary(p => p.Key, _ => 0m);
            ApplyFactorDict(eligible, scores, f => MapRating(f.GetDecimal("report_rc_rating")), 0.5m);
            ApplyFactorDict(eligible, scores, f => f.GetDecimal("top_inst_net_buy"), 0.5m);
            return scores;
        }

        private static decimal? MapRating(decimal? rating)
        {
            if (!rating.HasValue) return null;
            // Common mapping: 1=强推, 2=推荐, 3=中性, 4=回避
            return (5m - rating.Value);
        }

        private static Dictionary<Symbol, decimal> ComputeMacroRateScore(
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            // Macro is market-level; apply uniform z-score from macro_rate family
            // Since all stocks share the same macro environment, they all get the same score
            var eligible = factors.Where(p => p.Value != null).ToList();
            if (eligible.Count == 0) return new Dictionary<Symbol, decimal>();

            // Sample one stock to get macro values
            var sample = eligible.First().Value;
            decimal macroScore = 0m;
            int macroFields = 0;

            var cpiYoy = sample.GetDecimal("cn_cpi_nt_yoy");
            if (cpiYoy.HasValue) { macroScore -= cpiYoy.Value * 0.3m; macroFields++; }

            var m2Yoy = sample.GetDecimal("cn_m_m2_yoy");
            if (m2Yoy.HasValue) { macroScore += m2Yoy.Value * 0.3m; macroFields++; }

            var pmi = sample.GetDecimal("cn_pmi_PMI020201");
            if (pmi.HasValue) { macroScore += (pmi.Value - 50m) * 0.02m; macroFields++; }

            var lpr1y = sample.GetDecimal("shibor_lpr_1y");
            if (lpr1y.HasValue) { macroScore -= lpr1y.Value * 0.1m; macroFields++; }

            if (macroFields == 0) macroScore = 0m;

            return factors.Keys.ToDictionary(k => k, _ => macroScore);
        }

        private static Dictionary<Symbol, decimal> ComputeBarraMomentumScore(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> barraFactors)
        {
            if (barraFactors == null || barraFactors.Count == 0)
            {
                return new Dictionary<Symbol, decimal>();
            }
            var eligible = barraFactors.Where(p => p.Value != null && p.Value.Momentum.HasValue).ToList();
            var scores = barraFactors.Keys.ToDictionary(k => k, _ => 0m);
            if (eligible.Count <= 1) return scores;

            var values = eligible.Select(p => p.Value.Momentum.Value).ToList();
            var zScores = SafeZScores(values);
            for (var i = 0; i < eligible.Count; i++)
            {
                scores[eligible[i].Key] = zScores[i];
            }
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeBarraValueScore(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> barraFactors)
        {
            if (barraFactors == null || barraFactors.Count == 0)
            {
                return new Dictionary<Symbol, decimal>();
            }
            var eligible = barraFactors.Where(p => p.Value != null && p.Value.BookToPrice.HasValue).ToList();
            var scores = barraFactors.Keys.ToDictionary(k => k, _ => 0m);
            if (eligible.Count <= 1) return scores;

            var values = eligible.Select(p => p.Value.BookToPrice.Value).ToList();
            var zScores = SafeZScores(values);
            for (var i = 0; i < eligible.Count; i++)
            {
                scores[eligible[i].Key] = zScores[i];
            }
            return scores;
        }

        private static Dictionary<Symbol, decimal> ComputeBarraQualityScore(
            IReadOnlyDictionary<Symbol, AShareBarraCNE5FactorData> barraFactors)
        {
            if (barraFactors == null || barraFactors.Count == 0)
            {
                return new Dictionary<Symbol, decimal>();
            }
            var eligible = barraFactors.Where(p => p.Value != null).ToList();
            var scores = barraFactors.Keys.ToDictionary(k => k, _ => 0m);
            if (eligible.Count <= 1) return scores;

            var compositeValues = new List<decimal>();
            var indicesWithValues = new List<int>();
            for (var i = 0; i < eligible.Count; i++)
            {
                var f = eligible[i].Value;
                var ey = f.EarningsYield ?? 0m;
                var growth = f.Growth ?? 0m;
                var leverage = f.Leverage ?? 0m;
                compositeValues.Add(ey + growth - leverage);
                indicesWithValues.Add(i);
            }

            if (compositeValues.Count <= 1) return scores;
            var zScores = SafeZScores(compositeValues);
            for (var i = 0; i < indicesWithValues.Count; i++)
            {
                scores[eligible[indicesWithValues[i]].Key] = zScores[i];
            }
            return scores;
        }

        // Shared utilities

        private static void ApplyFactorDict(
            IReadOnlyList<KeyValuePair<Symbol, AShareTushareFactorData>> eligible,
            IDictionary<Symbol, decimal> scores,
            Func<AShareTushareFactorData, decimal?> selector,
            decimal weight)
        {
            if (weight == 0m) return;

            var indicesWithValues = new List<int>();
            var values = new List<decimal>();
            for (var i = 0; i < eligible.Count; i++)
            {
                var value = selector(eligible[i].Value);
                if (!value.HasValue) continue;
                indicesWithValues.Add(i);
                values.Add(value.Value);
            }

            if (values.Count <= 1) return;

            var zScores = SafeZScores(values);
            for (var i = 0; i < indicesWithValues.Count; i++)
            {
                scores[eligible[indicesWithValues[i]].Key] += zScores[i] * weight;
            }
        }

        private static decimal? LogInverse(decimal? value)
        {
            if (!value.HasValue || value.Value <= 0m) return null;
            try
            {
                return -(decimal)Math.Log((double)value.Value);
            }
            catch
            {
                return null;
            }
        }

        private static List<decimal> SafeZScores(IReadOnlyList<decimal> values)
        {
            if (values.Count <= 1)
            {
                return values.Select(_ => 0m).ToList();
            }

            var mean = values.Average();
            var variance = values.Select(v => Math.Pow((double)(v - mean), 2)).Average();
            var std = Math.Sqrt(variance);
            if (std <= double.Epsilon)
            {
                return values.Select(_ => 0m).ToList();
            }

            return values.Select(v => (decimal)(((double)v - (double)mean) / std)).ToList();
        }

        private static decimal GetMedian(IReadOnlyList<decimal> values)
        {
            if (values == null || values.Count == 0) return 0m;
            var mid = values.Count / 2;
            return values.Count % 2 == 0
                ? (values[mid - 1] + values[mid]) / 2m
                : values[mid];
        }

        private static void Accumulate(IDictionary<string, decimal> result, string key, decimal? value, decimal normalizedWeight)
        {
            if (!value.HasValue) return;
            result[key] += normalizedWeight * value.Value;
        }

        // Portfolio construction

        private static Dictionary<Symbol, decimal> BuildEqualWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected)
        {
            return selected.ToDictionary(p => p.Key, _ => 1m);
        }

        private static Dictionary<Symbol, decimal> BuildMarketCapWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors)
        {
            var weights = new Dictionary<Symbol, decimal>();
            var totalMv = selected
                .Sum(item => factors.TryGetValue(item.Key, out var f) ? f.GetDecimal("total_mv") ?? 0m : 0m);
            if (totalMv <= 0m)
            {
                return BuildEqualWeights(selected);
            }
            foreach (var item in selected)
            {
                var mv = factors.TryGetValue(item.Key, out var f) ? f.GetDecimal("total_mv") ?? 0m : 0m;
                weights[item.Key] = mv > 0m ? mv / totalMv : 0m;
            }
            return weights;
        }

        private static Dictionary<Symbol, decimal> BuildBlackLittermanKellyWeights(
            IReadOnlyList<KeyValuePair<Symbol, decimal>> selected,
            IReadOnlyDictionary<Symbol, AShareTushareFactorData> factors,
            AShareMultiFamilySignalSettings settings)
        {
            var priorWeights = BuildMarketCapWeights(selected, factors);
            var viewScores = SafeZScores(selected.Select(item => item.Value).ToList());
            var posteriorReturns = new Dictionary<Symbol, decimal>();
            var rawKellyWeights = new Dictionary<Symbol, decimal>();
            var tau = Clamp(settings.BlackLittermanTau, 0.01m, 1.0m);
            var confidence = Clamp(settings.BlackLittermanViewConfidence, 0.05m, 0.95m);
            var omega = Math.Max(0.05m, 1m - confidence);
            var priorBlend = Clamp(settings.BlackLittermanPriorBlend, 0m, 1m);
            var kellyFraction = Clamp(settings.KellyWeightFraction, 0m, 1m);

            for (var i = 0; i < selected.Count; i++)
            {
                var symbol = selected[i].Key;
                var priorWeight = priorWeights.TryGetValue(symbol, out var pw) ? pw : 0m;
                var priorReturn = settings.BlackLittermanRiskAversion * priorWeight;
                var viewReturn = settings.BlackLittermanViewScale * viewScores[i];
                var posteriorReturn = (priorReturn / tau + viewReturn / omega) / (1m / tau + 1m / omega);
                posteriorReturns[symbol] = posteriorReturn;

                var factor = factors.TryGetValue(symbol, out var f) ? f : null;
                var riskProxy = ComputeKellyRiskProxy(factor);
                var kellyWeight = posteriorReturn > 0m && riskProxy > 0m
                    ? (posteriorReturn / riskProxy) * kellyFraction
                    : 0m;
                rawKellyWeights[symbol] = Math.Max(0m, kellyWeight);
            }

            var normalizedKelly = NormalizePositiveWeights(rawKellyWeights);
            if (normalizedKelly.Count == 0)
            {
                return priorWeights;
            }

            var blended = new Dictionary<Symbol, decimal>();
            foreach (var item in selected)
            {
                var symbol = item.Key;
                var priorWeight = priorWeights.TryGetValue(symbol, out var pw) ? pw : 0m;
                var kellyWeight = normalizedKelly.TryGetValue(symbol, out var kw) ? kw : 0m;
                blended[symbol] = priorBlend * priorWeight + (1m - priorBlend) * kellyWeight;
            }
            return blended;
        }

        private static decimal ComputeKellyRiskProxy(AShareTushareFactorData factor)
        {
            var riskProxy = 0.35m;
            if (factor != null)
            {
                var vol = factor.GetDecimal("volatility_20");
                if (vol.HasValue) riskProxy += Math.Abs(vol.Value) * 0.4m;
            }
            return Math.Max(0.35m, riskProxy);
        }

        private static Dictionary<Symbol, decimal> NormalizePositiveWeights(IReadOnlyDictionary<Symbol, decimal> rawWeights)
        {
            var filtered = rawWeights.Where(p => p.Value > 0m).ToList();
            if (filtered.Count == 0) return new Dictionary<Symbol, decimal>();
            var total = filtered.Sum(p => p.Value);
            if (total <= 0m) return new Dictionary<Symbol, decimal>();
            return filtered.ToDictionary(p => p.Key, p => p.Value / total);
        }

        private static Dictionary<Symbol, decimal> NormalizeAndCapWeights(
            IReadOnlyDictionary<Symbol, decimal> rawWeights,
            decimal targetExposure,
            decimal maxSingleWeight)
        {
            var exposure = Math.Max(0m, targetExposure);
   if (rawWeights == null || rawWeights.Count == 0 || exposure <= 0m)
            {
                return new Dictionary<Symbol, decimal>();
            }

            var normalized = NormalizePositiveWeights(rawWeights);
            if (normalized.Count == 0) return new Dictionary<Symbol, decimal>();

            var cap = maxSingleWeight > 0m ? Math.Min(maxSingleWeight, exposure) : exposure;
            var result = normalized.ToDictionary(p => p.Key, _ => 0m);
            var remaining = normalized.ToDictionary(p => p.Key, p => p.Value);
            var remainingExposure = exposure;

            while (remaining.Count > 0 && remainingExposure > 0m)
            {
                var totalRemaining = remaining.Values.Sum();
                if (totalRemaining <= 0m) break;

                var cappedAny = false;
                foreach (var pair in remaining.ToList())
                {
                    var proposed = remainingExposure * pair.Value / totalRemaining;
                    if (proposed <= cap) continue;
                    result[pair.Key] = cap;
                    remainingExposure -= cap;
                    remaining.Remove(pair.Key);
                    cappedAny = true;
                }

                if (cappedAny) continue;
                foreach (var pair in remaining)
                {
                    result[pair.Key] = remainingExposure * pair.Value / totalRemaining;
                }
                break;
            }

            return result.Where(p => p.Value > 0m).ToDictionary(p => p.Key, p => p.Value);
        }

        private static decimal Clamp(decimal value, decimal min, decimal max)
        {
            if (value < min) return min;
            if (value > max) return max;
            return value;
        }
    }
}