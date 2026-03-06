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
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Logging;

namespace QuantConnect.Data
{
    /// <summary>
    /// Caches Tushare ETF metadata for performance optimization
    /// </summary>
    public class TushareDataCache
    {
        private readonly string _dataPath;
        private Dictionary<string, Dictionary<string, object>> _etfMetadata;
        private List<string> _t0ETFSymbols;
        private readonly object _lock = new object();

        /// <summary>
        /// Creates a new instance of TushareDataCache
        /// </summary>
        /// <param name="dataPath">Path to tushare data directory</param>
        public TushareDataCache(string dataPath)
        {
            _dataPath = dataPath;
        }

        /// <summary>
        /// Loads ETF metadata from tushare parquet file
        /// </summary>
        private void LoadETFMetadata()
        {
            if (_etfMetadata != null)
            {
                return;
            }

            lock (_lock)
            {
                if (_etfMetadata != null)
                {
                    return;
                }

                _etfMetadata = new Dictionary<string, Dictionary<string, object>>();
                _t0ETFSymbols = new List<string>();

                var etfBasicPath = Path.Combine(_dataPath, "etf_basic", "data.parquet");

                if (!File.Exists(etfBasicPath))
                {
                    Log.Error($"TushareDataCache.LoadETFMetadata(): ETF basic data file not found: {etfBasicPath}");
                    return;
                }

                try
                {
                    // Use Python to read parquet file
                    var pythonCode = $@"
import pandas as pd
import json

df = pd.read_parquet('{etfBasicPath}')

# Convert to JSON
result = df.to_json(orient='records')
print(result)
";

                    var pythonPath = "/root/miniconda3/envs/quant311/bin/python";
                    var tempFile = Path.GetTempFileName();
                    File.WriteAllText(tempFile, pythonCode);

                    var process = new System.Diagnostics.Process
                    {
                        StartInfo = new System.Diagnostics.ProcessStartInfo
                        {
                            FileName = pythonPath,
                            Arguments = tempFile,
                            RedirectStandardOutput = true,
                            RedirectStandardError = true,
                            UseShellExecute = false,
                            CreateNoWindow = true
                        }
                    };

                    process.Start();
                    var output = process.StandardOutput.ReadToEnd();
                    var error = process.StandardError.ReadToEnd();
                    process.WaitForExit();

                    File.Delete(tempFile);

                    if (!string.IsNullOrEmpty(error))
                    {
                        Log.Error($"TushareDataCache.LoadETFMetadata(): Python error: {error}");
                        return;
                    }

                    // Parse JSON output
                    var data = JsonConvert.DeserializeObject<List<Dictionary<string, object>>>(output);

                    foreach (var row in data)
                    {
                        var tsCode = row["ts_code"].ToString();
                        _etfMetadata[tsCode] = row;

                        // Identify T+0 ETFs
                        if (IsT0ETF(row))
                        {
                            _t0ETFSymbols.Add(tsCode);
                        }
                    }

                    Log.Trace($"TushareDataCache.LoadETFMetadata(): Loaded {_etfMetadata.Count} ETFs, {_t0ETFSymbols.Count} are T+0 tradable");
                }
                catch (Exception ex)
                {
                    Log.Error($"TushareDataCache.LoadETFMetadata(): Error loading ETF metadata: {ex.Message}");
                }
            }
        }

        /// <summary>
        /// Determines if an ETF supports T+0 trading
        /// </summary>
        /// <param name="metadata">ETF metadata row</param>
        /// <returns>True if T+0 tradable</returns>
        private bool IsT0ETF(Dictionary<string, object> metadata)
        {
            // T+0 ETF types:
            // 1. QDII ETFs (cross-border)
            // 2. Gold ETFs
            // 3. Money Market ETFs
            // 4. Bond ETFs (excluding convertible bonds)

            var etfType = metadata.ContainsKey("etf_type") ? metadata["etf_type"]?.ToString() : "";
            var csname = metadata.ContainsKey("csname") ? metadata["csname"]?.ToString() : "";

            if (string.IsNullOrEmpty(etfType) || string.IsNullOrEmpty(csname))
            {
                return false;
            }

            // QDII ETFs
            if (etfType.Contains("QDII"))
            {
                return true;
            }

            // Gold ETFs
            if (csname.Contains("黄金") || csname.Contains("黃金"))
            {
                return true;
            }

            // Money Market ETFs
            if (csname.Contains("货币") || csname.Contains("貨幣"))
            {
                return true;
            }

            // Bond ETFs (excluding convertible bonds)
            if ((csname.Contains("债") || csname.Contains("債")) &&
                !csname.Contains("可转债") && !csname.Contains("可轉債"))
            {
                return true;
            }

            return false;
        }

        /// <summary>
        /// Gets the list of T+0 tradable ETF symbols
        /// </summary>
        public List<string> GetT0ETFSymbols()
        {
            LoadETFMetadata();
            return _t0ETFSymbols.ToList();
        }

        /// <summary>
        /// Gets ETF metadata by ts_code
        /// </summary>
        public Dictionary<string, object> GetETFMetadata(string tsCode)
        {
            LoadETFMetadata();
            return _etfMetadata.TryGetValue(tsCode, out var metadata) ? metadata : null;
        }

        /// <summary>
        /// Checks if a symbol is a T+0 tradable ETF
        /// </summary>
        public bool IsT0ETF(string tsCode)
        {
            LoadETFMetadata();
            return _t0ETFSymbols.Contains(tsCode);
        }
    }
}
