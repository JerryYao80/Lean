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
using NodaTime;
using QuantConnect.Data.Market;
using QuantConnect.Logging;

namespace QuantConnect.Data
{
    /// <summary>
    /// GBM calibration parameters for a single symbol
    /// </summary>
    public class GbmCalibration
    {
        public string TsCode { get; set; }
        public decimal PreClose { get; set; }
        public double Drift { get; set; }
        public double Volatility { get; set; }
        public double AvgDailyVolume { get; set; }
        public double VolumeLogSigma { get; set; }
        public double AmountPriceRatio { get; set; }
    }

    /// <summary>
    /// Generates synthetic daily TradeBar data using Geometric Brownian Motion.
    /// Ported from GbmSyntheticRtDailyClient in rt_daily_downloader.py, adapted for
    /// backtest use (one bar per trading day instead of intraday ticks).
    /// </summary>
    public static class GbmDailyBarGenerator
    {
        private static readonly DateTimeZone ChinaTimeZone = DateTimeZoneProviders.Tzdb["Asia/Shanghai"];

        /// <summary>
        /// Calibrate GBM parameters from historical daily bars.
        /// If no history is available, returns default calibration.
        /// </summary>
        public static GbmCalibration Calibrate(string tsCode, List<TradeBar> historyBars, int lookbackDays = 60, int minHistoryDays = 20)
        {
            if (historyBars == null || historyBars.Count < minHistoryDays)
            {
                return new GbmCalibration
                {
                    TsCode = tsCode,
                    PreClose = 10m,
                    Drift = 0.0,
                    Volatility = 0.02,
                    AvgDailyVolume = 1_000_000.0,
                    VolumeLogSigma = 0.35,
                    AmountPriceRatio = 100.0
                };
            }

            var startIndex = Math.Max(0, historyBars.Count - lookbackDays - 1);
            var usableBars = historyBars.GetRange(startIndex, historyBars.Count - startIndex);

            // Compute log returns
            var logReturns = new List<double>();
            for (var i = 1; i < usableBars.Count; i++)
            {
                var prevPrice = Math.Max(0.01, (double)usableBars[i - 1].Close);
                var currPrice = Math.Max(0.01, (double)usableBars[i].Close);
                logReturns.Add(Math.Log(currPrice / prevPrice));
            }

            var latestClose = (double)usableBars[usableBars.Count - 1].Close;
            var previousClose = usableBars.Count >= 2 ? (double)usableBars[usableBars.Count - 2].Close : latestClose;

            double drift, volatility;
            if (logReturns.Count >= minHistoryDays)
            {
                drift = 0.0;
                foreach (var r in logReturns) drift += r;
                drift /= logReturns.Count;

                var variance = 0.0;
                foreach (var r in logReturns) variance += (r - drift) * (r - drift);
                variance /= logReturns.Count;
                volatility = Math.Sqrt(Math.Max(variance, 1e-8));
            }
            else if (logReturns.Count > 0)
            {
                drift = 0.0;
                foreach (var r in logReturns) drift += r;
                drift /= logReturns.Count;
                volatility = 0.02;
            }
            else
            {
                drift = 0.0;
                volatility = 0.02;
            }

            // Compute volume statistics
            var positiveVolumes = new List<double>();
            var volumeStart = Math.Max(0, usableBars.Count - lookbackDays);
            for (var i = volumeStart; i < usableBars.Count; i++)
            {
                var vol = (double)usableBars[i].Volume;
                if (vol > 0) positiveVolumes.Add(vol);
            }

            var avgDailyVolume = positiveVolumes.Count > 0
                ? positiveVolumes.Count > 0 ? Sum(positiveVolumes) / positiveVolumes.Count : 1_000_000.0
                : 1_000_000.0;

            double volumeLogSigma = 0.35;
            if (positiveVolumes.Count >= 2)
            {
                var logVolumes = new List<double>();
                foreach (var v in positiveVolumes) logVolumes.Add(Math.Log(v));
                var logVolMean = Sum(logVolumes) / logVolumes.Count;
                var logVolVar = 0.0;
                foreach (var lv in logVolumes) logVolVar += (lv - logVolMean) * (lv - logVolMean);
                logVolVar /= logVolumes.Count;
                volumeLogSigma = Math.Sqrt(Math.Max(logVolVar, 1e-8));
            }

            return new GbmCalibration
            {
                TsCode = tsCode,
                PreClose = Math.Max(0.01m, (decimal)previousClose),
                Drift = Math.Max(-0.10, Math.Min(0.10, drift)),
                Volatility = Math.Max(0.005, Math.Min(0.12, volatility)),
                AvgDailyVolume = Math.Max(100.0, avgDailyVolume),
                VolumeLogSigma = Math.Max(0.05, Math.Min(1.20, volumeLogSigma)),
                AmountPriceRatio = 100.0
            };
        }

        /// <summary>
        /// Generate synthetic daily TradeBars for a date range using GBM.
        /// </summary>
        public static List<TradeBar> Generate(
            Symbol symbol,
            DateTime startDateUtc,
            DateTime endDateUtc,
            GbmCalibration calibration,
            double volatilityScale = 8.0,
            double minDailyVolatility = 0.80,
            double jumpProbability = 0.22,
            double jumpScale = 0.10,
            int randomSeed = 42)
        {
            var bars = new List<TradeBar>();
            var random = new Random(randomSeed + (calibration.TsCode ?? "").GetHashCode());

            var scaledDailyVolatility = Math.Min(1.50, Math.Max(minDailyVolatility, calibration.Volatility * volatilityScale));
            var scaledDrift = Math.Max(-0.25, Math.Min(0.25, calibration.Drift * Math.Max(1.0, volatilityScale * 0.5)));

            var currentClose = (double)calibration.PreClose;
            if (currentClose <= 0) currentClose = 10.0;

            var current = startDateUtc.Date;
            var end = endDateUtc.Date;

            while (current <= end)
            {
                // Skip weekends
                if (current.DayOfWeek == DayOfWeek.Saturday || current.DayOfWeek == DayOfWeek.Sunday)
                {
                    current = current.AddDays(1);
                    continue;
                }

                var open = currentClose;

                // GBM step: S(t+1) = S(t) * exp((mu - sigma^2/2) + sigma * Z + jump)
                var shock = NextGaussian(random);
                var jumpReturn = 0.0;
                if (jumpProbability > 0 && random.NextDouble() < jumpProbability)
                {
                    jumpReturn = NextGaussian(random) * jumpScale;
                }

                var exponent = (scaledDrift - 0.5 * scaledDailyVolatility * scaledDailyVolatility)
                    + scaledDailyVolatility * shock
                    + jumpReturn;

                var nextClose = Math.Max(0.01, currentClose * Math.Exp(exponent));

                // Estimate high/low from intraday span
                var intradaySpan = (Math.Abs(NextGaussian(random)) + Math.Abs(shock)) * scaledDailyVolatility;
                var high = Math.Max(open, nextClose) * (1.0 + intradaySpan * 0.35);
                var low = Math.Min(open, nextClose) * Math.Max(0.01, 1.0 - intradaySpan * 0.35);

                // Volume: lognormal
                var meanVolume = Math.Max(1.0, calibration.AvgDailyVolume);
                var volumeLogMean = Math.Log(meanVolume) - 0.5 * calibration.VolumeLogSigma * calibration.VolumeLogSigma;
                var incrementVolume = Math.Exp(volumeLogMean + calibration.VolumeLogSigma * NextGaussian(random));
                if (jumpReturn != 0.0)
                {
                    incrementVolume *= 1.0 + Math.Min(3.0, Math.Abs(jumpReturn) * 8.0);
                }
                incrementVolume = Math.Max(0.0, incrementVolume);

                // Bar timestamps: use TushareDataConverter.ConvertTradeDate convention (close at 15:00 CST)
                var localDateTime = new NodaTime.LocalDateTime(current.Year, current.Month, current.Day, 15, 0);
                var endTimeUtc = ChinaTimeZone.AtLeniently(localDateTime).ToDateTimeUtc();
                var barPeriod = TimeSpan.FromDays(1);

                var bar = new TradeBar
                {
                    Symbol = symbol,
                    Time = endTimeUtc - barPeriod,
                    EndTime = endTimeUtc,
                    Open = (decimal)open,
                    High = (decimal)high,
                    Low = (decimal)low,
                    Close = (decimal)nextClose,
                    Volume = (decimal)incrementVolume * 100m, // Convert to shares (lots → shares)
                    Period = barPeriod
                };

                bars.Add(bar);
                currentClose = nextClose;
                current = current.AddDays(1);
            }

            Log.Trace($"GbmDailyBarGenerator.Generate(): Generated {bars.Count} synthetic daily bars for {calibration.TsCode}");
            return bars;
        }

        private static double NextGaussian(Random random)
        {
            // Box-Muller transform
            double u1, u2;
            do { u1 = random.NextDouble(); } while (u1 <= 0.0);
            u2 = random.NextDouble();
            return Math.Sqrt(-2.0 * Math.Log(u1)) * Math.Cos(2.0 * Math.PI * u2);
        }

        private static double Sum(List<double> values)
        {
            var sum = 0.0;
            foreach (var v in values) sum += v;
            return sum;
        }
    }
}
