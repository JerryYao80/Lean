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
using QuantConnect.Securities;

namespace QuantConnect.ToolBox
{
    /// <summary>
    /// Converts tushare option parquet data to LEAN format for Market.China.
    /// Uses subprocess to call Python/Pandas for parquet reading.
    /// </summary>
    public class AShareOptionDataConverter
    {
        protected readonly string TusharePath;
        protected readonly string LeanDataPath;
        protected const string PythonPath = "/root/miniconda3/envs/quant311/bin/python";

        public AShareOptionDataConverter(string tusharePath, string leanDataPath)
        {
            TusharePath = tusharePath;
            LeanDataPath = leanDataPath;
        }

        /// <summary>
        /// Execute Python code via subprocess and capture stdout.
        /// Used to read tushare parquet files with pandas.
        /// </summary>
        public string RunPythonScript(string pythonCode)
        {
            var tempFile = Path.GetTempFileName();
            File.WriteAllText(tempFile, pythonCode);
            try
            {
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
                process.WaitForExit(30000);
                if (process.ExitCode != 0)
                {
                    throw new InvalidOperationException($"Python failed: {error}");
                }
                return output;
            }
            finally
            {
                File.Delete(tempFile);
            }
        }

        /// <summary>
        /// Lightweight carrier for a single tushare option contract's static metadata.
        /// </summary>
        public class OptContract
        {
            public string TsCode { get; set; }
            public string Exchange { get; set; }
            public string OptCode { get; set; }
            public decimal ExercisePrice { get; set; }
            public string MaturityDate { get; set; }
            public string CallPut { get; set; }
        }

        /// <summary>
        /// Load SSE option contracts (e.g. OP510050.SH) from tushare opt_basic parquet.
        /// </summary>
        public List<OptContract> LoadOptBasic(string optCode)
        {
            var parquetPath = Path.Combine(TusharePath, "opt_basic", "data.parquet");
            var pythonCode = $@"
import pandas as pd
df = pd.read_parquet('{parquetPath}')
df = df[df['opt_code'] == '{optCode}']
df = df[df['exchange'] == 'SSE']
df['exercise_price'] = df['exercise_price'].astype(float)
df['maturity_date'] = df['maturity_date'].astype(str).str.replace('.0', '', regex=False)
print(df[['ts_code','exchange','opt_code','exercise_price','maturity_date','call_put']].to_json(orient='records'))
";
            var output = RunPythonScript(pythonCode).Trim();
            var json = JArray.Parse(output);

            return json.Select(item => new OptContract
            {
                TsCode = (string)item["ts_code"],
                Exchange = (string)item["exchange"],
                OptCode = (string)item["opt_code"],
                ExercisePrice = (decimal)item["exercise_price"],
                MaturityDate = (string)item["maturity_date"],
                CallPut = (string)item["call_put"]
            }).ToList();
        }

        /// <summary>
        /// Build a LEAN OSI-style option ticker: {underlying}{YYMMDD}{C/P}{strike*1000:00000000}
        /// </summary>
        public static string GenerateLeanSymbol(OptContract contract, string underlying)
        {
            var expiry = DateTime.ParseExact(contract.MaturityDate, "yyyyMMdd", null);
            var strikeStr = (contract.ExercisePrice * 1000m).ToString("00000000");
            return $"{underlying}{expiry:yyMMdd}{contract.CallPut}{strikeStr}";
        }

        /// <summary>
        /// Convert a single contract's daily row to LEAN CSV.
        /// Critical: tushare settle (settlement price) is written into the
        /// LEAN Close column, which ImpliedVolatility reads as the option price.
        /// </summary>
        public void ConvertSingleContract(string optCode, string tradeDate, string tsCode)
        {
            var contracts = LoadOptBasic(optCode);
            var contract = contracts.FirstOrDefault(c => c.TsCode == tsCode)
                ?? throw new ArgumentException($"Contract {tsCode} not found in {optCode}");

            var underlying = optCode.Replace("OP", "").Replace(".SH", "");
            var leanSymbol = GenerateLeanSymbol(contract, underlying);

            var pythonCode = $@"
import pandas as pd
df = pd.read_parquet('{TusharePath}/opt_daily/trade_date={tradeDate}/data.parquet')
row = df[df['ts_code'] == '{tsCode}']
if len(row) == 0:
    print('NOT_FOUND')
else:
    r = row.iloc[0]
    print(f""{{r['open']}},{{r['high']}},{{r['low']}},{{r['settle']}},{{r['vol']}}"")
";
            var output = RunPythonScript(pythonCode).Trim();
            if (output == "NOT_FOUND") return;

            var csvPath = Path.Combine(LeanDataPath, "option", "china", "daily",
                                        underlying, $"{leanSymbol}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(csvPath));

            const string header = "date,open,high,low,close,volume";
            File.WriteAllLines(csvPath, new[] { header, $"{tradeDate},{output}" });
        }

        /// <summary>
        /// Convert all contracts for one trade date and emit the universe CSV
        /// (daily contract list consumed by AShareOptionChainProvider).
        /// </summary>
        public void ConvertAllContracts(string optCode, string tradeDate)
        {
            var contracts = LoadOptBasic(optCode);
            var underlying = optCode.Replace("OP", "").Replace(".SH", "");

            foreach (var contract in contracts)
            {
                ConvertSingleContract(optCode, tradeDate, contract.TsCode);
            }

            var universePath = Path.Combine(LeanDataPath, "option", "china", "universes",
                                             underlying, $"{tradeDate}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(universePath));

            var lines = new List<string> { "symbol,expiration,strike,right,style" };
            foreach (var contract in contracts)
            {
                var leanSymbol = GenerateLeanSymbol(contract, underlying);
                lines.Add($"{leanSymbol},{contract.MaturityDate},{contract.ExercisePrice},{contract.CallPut},European");
            }
            File.WriteAllLines(universePath, lines);
        }

        /// <summary>
        /// Lightweight carrier for a single tushare option contract's static metadata.
        /// </summary>
        public class OptContract
        {
            public string TsCode { get; set; }
            public string Exchange { get; set; }
            public string OptCode { get; set; }
            public decimal ExercisePrice { get; set; }
            public string MaturityDate { get; set; }
            public string CallPut { get; set; }
        }

        /// <summary>
        /// Load SSE option contracts (e.g. OP510050.SH) from tushare opt_basic parquet.
        /// </summary>
        public List<OptContract> LoadOptBasic(string optCode)
        {
            var parquetPath = Path.Combine(TusharePath, "opt_basic", "data.parquet");
            var pythonCode = $@"
import pandas as pd
df = pd.read_parquet('{parquetPath}')
df = df[df['opt_code'] == '{optCode}']
df = df[df['exchange'] == 'SSE']
df['exercise_price'] = df['exercise_price'].astype(float)
df['maturity_date'] = df['maturity_date'].astype(str).str.replace('.0', '', regex=False)
print(df[['ts_code','exchange','opt_code','exercise_price','maturity_date','call_put']].to_json(orient='records'))
";
            var output = RunPythonScript(pythonCode).Trim();
            var json = JArray.Parse(output);

            return json.Select(item => new OptContract
            {
                TsCode = (string)item["ts_code"],
                Exchange = (string)item["exchange"],
                OptCode = (string)item["opt_code"],
                ExercisePrice = (decimal)item["exercise_price"],
                MaturityDate = (string)item["maturity_date"],
                CallPut = (string)item["call_put"]
            }).ToList();
        }

        /// <summary>
        /// Build a LEAN OSI-style option ticker: {underlying}{YYMMDD}{C/P}{strike*1000:00000000}
        /// </summary>
        public static string GenerateLeanSymbol(OptContract contract, string underlying)
        {
            var expiry = DateTime.ParseExact(contract.MaturityDate, "yyyyMMdd", null);
            var strikeStr = (contract.ExercisePrice * 1000m).ToString("00000000");
            return $"{underlying}{expiry:yyMMdd}{contract.CallPut}{strikeStr}";
        }

        /// <summary>
        /// Convert a single contract's daily row to LEAN CSV.
        /// Critical: tushare settle (settlement price) is written into the
        /// LEAN Close column, which ImpliedVolatility reads as the option price.
        /// </summary>
        public void ConvertSingleContract(string optCode, string tradeDate, string tsCode)
        {
            var contracts = LoadOptBasic(optCode);
            var contract = contracts.FirstOrDefault(c => c.TsCode == tsCode)
                ?? throw new ArgumentException($"Contract {tsCode} not found in {optCode}");

            var underlying = optCode.Replace("OP", "").Replace(".SH", "");
            var leanSymbol = GenerateLeanSymbol(contract, underlying);

            var pythonCode = $@"
import pandas as pd
df = pd.read_parquet('{TusharePath}/opt_daily/trade_date={tradeDate}/data.parquet')
row = df[df['ts_code'] == '{tsCode}']
if len(row) == 0:
    print('NOT_FOUND')
else:
    r = row.iloc[0]
    print(f""{{r['open']}},{{r['high']}},{{r['low']}},{{r['settle']}},{{r['vol']}}"")
";
            var output = RunPythonScript(pythonCode).Trim();
            if (output == "NOT_FOUND") return;

            var csvPath = Path.Combine(LeanDataPath, "option", "china", "daily",
                                        underlying, $"{leanSymbol}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(csvPath));

            const string header = "date,open,high,low,close,volume";
            File.WriteAllLines(csvPath, new[] { header, $"{tradeDate},{output}" });
        }

        /// <summary>
        /// Convert all contracts for one trade date and emit the universe CSV
        /// (daily contract list consumed by AShareOptionChainProvider).
        /// </summary>
        public void ConvertAllContracts(string optCode, string tradeDate)
        {
            var contracts = LoadOptBasic(optCode);
            var underlying = optCode.Replace("OP", "").Replace(".SH", "");

            foreach (var contract in contracts)
            {
                ConvertSingleContract(optCode, tradeDate, contract.TsCode);
            }

            var universePath = Path.Combine(LeanDataPath, "option", "china", "universes",
                                             underlying, $"{tradeDate}.csv");
            Directory.CreateDirectory(Path.GetDirectoryName(universePath));

            var lines = new List<string> { "symbol,expiration,strike,right,style" };
            foreach (var contract in contracts)
            {
                var leanSymbol = GenerateLeanSymbol(contract, underlying);
                lines.Add($"{leanSymbol},{contract.MaturityDate},{contract.ExercisePrice},{contract.CallPut},European");
            }
            File.WriteAllLines(universePath, lines);
        }
    }
}
