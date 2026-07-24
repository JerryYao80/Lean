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
using System.Diagnostics;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using QuantConnect.Data;

namespace QuantConnect.Data.AShare
{
    /// <summary>
    /// Risk-free interest rate model sourced from tushare SHIBOR 1Y.
    /// Real market data, no hardcoding. Falls back to 2% if date missing.
    /// </summary>
    public class AShareShiborRateModel : IRiskFreeInterestRateModel
    {
        private const string PythonPath = "/root/miniconda3/envs/quant311/bin/python";
        private readonly Dictionary<DateTime, decimal> _rates;

        public AShareShiborRateModel(string tusharePath)
        {
            _rates = LoadShiborRates(tusharePath);
        }

        /// <summary>
        /// Get the 1Y SHIBOR-derived risk-free rate for the given date.
        /// </summary>
        public decimal GetInterestRate(DateTime date)
        {
            return _rates.TryGetValue(date.Date, out var rate) ? rate : 0.02m;
        }

        private static Dictionary<DateTime, decimal> LoadShiborRates(string tusharePath)
        {
            var pythonCode = $@"
import pandas as pd
from pathlib import Path
frames = []
for p in sorted(Path('{tusharePath}/shibor').glob('year=*')):
    df = pd.read_parquet(p / 'data.parquet')
    frames.append(df)
if not frames:
    print('[]')
else:
    combined = pd.concat(frames, ignore_index=True)
    combined['date'] = combined['date'].astype(str).str.replace('.0','',regex=False)
    print(combined[['date','1y']].to_json(orient='records'))
";
            var output = RunPython(pythonCode).Trim();
            if (output == "[]" || string.IsNullOrEmpty(output)) return new Dictionary<DateTime, decimal>();

            var json = JArray.Parse(output);
            return json.ToDictionary(
                item => DateTime.ParseExact((string)item["date"], "yyyyMMdd", null),
                item => (decimal)(double)item["1y"] / 100m);  // SHIBOR is in percent
        }

        private static string RunPython(string code)
        {
            var tempFile = Path.GetTempFileName();
            File.WriteAllText(tempFile, code);
            try
            {
                var p = new Process
                {
                    StartInfo = new ProcessStartInfo
                    {
                        FileName = PythonPath,
                        Arguments = tempFile,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true,
                        UseShellExecute = false,
                        CreateNoWindow = true
                    }
                };
                p.Start();
                var output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(30000);
                return output;
            }
            finally { File.Delete(tempFile); }
        }
    }
}
