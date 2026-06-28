// <copyright file="AShareOptionDataConverterTests.cs" company="QuantConnect Corporation">
// Copyright (c) QuantConnect Corporation. All rights reserved.
// </copyright>

using NUnit.Framework;
using QuantConnect.ToolBox;

namespace QuantConnect.Tests.AShare
{
    [TestFixture]
    public class AShareOptionDataConverterTests
    {
        private const string TusharePath = "/home/project/tushare-downloader/tushare_data_v2";
        private const string LeanDataPath = "Data";

        [Test]
        public void Constructor_InitializesSuccessfully()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            Assert.IsNotNull(converter);
        }
    }
}
