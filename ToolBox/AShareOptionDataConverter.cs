// <copyright file="AShareOptionDataConverter.cs" company="QuantConnect Corporation">
// Copyright (c) QuantConnect Corporation. All rights reserved.
// </copyright>

using System;
using System.IO;

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
    }
}
