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

        [Test]
        public void RunPythonScript_ReturnsValidJson()
        {
            var converter = new AShareOptionDataConverter(TusharePath, LeanDataPath);
            var output = converter.RunPythonScript("print('[1, 2, 3]')");
            Assert.AreEqual("[1, 2, 3]", output.Trim());
        }
    }
}
