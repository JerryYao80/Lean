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
using System.Diagnostics;
using System.IO;
using QuantConnect.Logging;

namespace QuantConnect.Data
{
    /// <summary>
    /// Invokes the Python TushareDownloader to download missing symbol data on demand
    /// </summary>
    public static class TushareDownloaderInvoker
    {
        private static readonly string PythonPath = "/root/miniconda3/envs/quant311/bin/python";
        private static readonly string DownloaderModuleDir = "/home/project/hope/Lean/data-source/tushare";

        /// <summary>
        /// Download data for a single symbol from Tushare API.
        /// After successful download, the parquet file will be available at the
        /// same path that TushareDataConverter.ResolveDailyDataPath reads from.
        /// </summary>
        /// <param name="tsCode">Tushare ts_code (e.g., "510300.SH")</param>
        /// <param name="startDate">Start date for download range</param>
        /// <param name="endDate">End date for download range</param>
        /// <param name="tushareDataPath">Path to tushare_data directory</param>
        /// <param name="timeoutSeconds">Maximum seconds to wait for download</param>
        /// <returns>True if download succeeded, false otherwise</returns>
        public static bool DownloadSymbol(string tsCode, DateTime startDate, DateTime endDate, string tushareDataPath, int timeoutSeconds = 120)
        {
            if (string.IsNullOrWhiteSpace(tsCode) || string.IsNullOrWhiteSpace(tushareDataPath))
            {
                return false;
            }

            // Determine if ETF (fund_daily) or stock (daily) based on ts_code prefix
            var isEtf = tsCode.StartsWith("5") || tsCode.StartsWith("15") || tsCode.StartsWith("16") || tsCode.StartsWith("18");
            var apiName = isEtf ? "fund_daily" : "daily";

            var escapedDataDir = tushareDataPath.Replace("\\", "\\\\").Replace("'", "\\'");
            var escapedTsCode = tsCode.Replace("'", "\\'");

            var pythonCode = $@"
import sys
sys.path.insert(0, '{DownloaderModuleDir.Replace("\\", "/").Replace("'", "\\'")}')

try:
    from downloader import TushareDownloader
    d = TushareDownloader(data_dir='{escapedDataDir}')
    api_name = '{apiName}'
    ts_code = '{escapedTsCode}'

    from api_registry import get_api_config
    api_config = get_api_config(api_name)
    if api_config is None:
        print('FAIL:api config not found for ' + api_name)
        sys.exit(0)

    # Download by stock (single ts_code)
    success, rows = d.download_api_by_stock(api_config, ts_code)
    if success and rows > 0:
        print('OK:' + str(rows))
    elif success:
        print('OK:0')
    else:
        print('FAIL:download returned failure')
except Exception as e:
    print(f'FAIL:{{e}}')
";

            var tempFile = Path.GetTempFileName();
            try
            {
                File.WriteAllText(tempFile, pythonCode);

                var process = new Process
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

                process.Start();
                var output = process.StandardOutput.ReadToEnd();
                var error = process.StandardError.ReadToEnd();

                if (!process.WaitForExit(timeoutSeconds * 1000))
                {
                    process.Kill();
                    Log.Error($"TushareDownloaderInvoker.DownloadSymbol(): Timeout downloading {tsCode} after {timeoutSeconds}s");
                    return false;
                }

                output = output.Trim();

                if (output.StartsWith("OK"))
                {
                    Log.Trace($"TushareDownloaderInvoker.DownloadSymbol(): Successfully downloaded {tsCode} ({apiName}): {output}");
                    return true;
                }

                Log.Error($"TushareDownloaderInvoker.DownloadSymbol(): Download failed for {tsCode}: {output}");
                if (!string.IsNullOrWhiteSpace(error))
                {
                    Log.Error($"TushareDownloaderInvoker.DownloadSymbol(): Python stderr: {error.Substring(0, Math.Min(error.Length, 500))}");
                }
                return false;
            }
            catch (Exception ex)
            {
                Log.Error($"TushareDownloaderInvoker.DownloadSymbol(): Exception downloading {tsCode}: {ex.Message}");
                return false;
            }
            finally
            {
                try { File.Delete(tempFile); } catch { }
            }
        }
    }
}