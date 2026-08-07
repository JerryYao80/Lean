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

using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Securities.Equity
{
    /// <summary>
    /// Trading mode for A-Share ETFs
    /// </summary>
    public enum ETFTradingMode
    {
        /// <summary>
        /// T+0: Can buy and sell on the same day
        /// </summary>
        T0,

        /// <summary>
        /// T+1: Must wait until next day to sell after buying
        /// </summary>
        T1
    }

    /// <summary>
    /// Metadata for A-Share ETF securities
    /// </summary>
    public class AShareETFMetadata
    {
        /// <summary>
        /// ETF ticker symbol
        /// </summary>
        public string Ticker { get; set; }

        /// <summary>
        /// ETF name
        /// </summary>
        public string Name { get; set; }

        /// <summary>
        /// Trading mode (T+0 or T+1)
        /// </summary>
        public ETFTradingMode TradingMode { get; set; }

        /// <summary>
        /// Market (SSE or SZSE)
        /// </summary>
        public string Market { get; set; }

        /// <summary>
        /// Whether this ETF supports T+0 trading
        /// </summary>
        public bool IsT0 => TradingMode == ETFTradingMode.T0;

        /// <summary>
        /// Daily price limit percentage for this ETF
        /// </summary>
        public decimal PriceLimitPercentage { get; set; } = AShareETF.DefaultPriceLimitPercentage;
    }

    /// <summary>
    /// Registry of A-Share ETF metadata - Auto-generated from tushare data
    /// Total: 145 T+0 ETFs
    /// </summary>
    public static class AShareETFRegistry
    {
        private static readonly Dictionary<string, AShareETFMetadata> _etfMetadata = new Dictionary<string, AShareETFMetadata>
        {
            { "510050", new AShareETFMetadata { Ticker = "510050", Name = "华夏上证50ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "510300", new AShareETFMetadata { Ticker = "510300", Name = "华泰柏瑞沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "510500", new AShareETFMetadata { Ticker = "510500", Name = "南方中证500ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511600", new AShareETFMetadata { Ticker = "511600", Name = "华安日日鑫货币H", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511620", new AShareETFMetadata { Ticker = "511620", Name = "国泰瞬利货币A", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511650", new AShareETFMetadata { Ticker = "511650", Name = "华夏快线货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511660", new AShareETFMetadata { Ticker = "511660", Name = "建信现金添益货币H", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511670", new AShareETFMetadata { Ticker = "511670", Name = "华泰紫金天天金货币ETFA", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511690", new AShareETFMetadata { Ticker = "511690", Name = "大成添益交易型货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511700", new AShareETFMetadata { Ticker = "511700", Name = "场内货币", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511800", new AShareETFMetadata { Ticker = "511800", Name = "易方达货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511810", new AShareETFMetadata { Ticker = "511810", Name = "南方理财金货币ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511820", new AShareETFMetadata { Ticker = "511820", Name = "鹏华添利交易型货币B", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511830", new AShareETFMetadata { Ticker = "511830", Name = "华泰货币", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511860", new AShareETFMetadata { Ticker = "511860", Name = "博时保证金货币ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511880", new AShareETFMetadata { Ticker = "511880", Name = "银华日利", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511900", new AShareETFMetadata { Ticker = "511900", Name = "富国收益宝交易型货币H", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511910", new AShareETFMetadata { Ticker = "511910", Name = "融通易支付货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511920", new AShareETFMetadata { Ticker = "511920", Name = "广发货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511950", new AShareETFMetadata { Ticker = "511950", Name = "广发添利货币A", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511960", new AShareETFMetadata { Ticker = "511960", Name = "嘉实快线货币H", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511970", new AShareETFMetadata { Ticker = "511970", Name = "国寿安保货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511980", new AShareETFMetadata { Ticker = "511980", Name = "汇添富添富通货币E", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "511990", new AShareETFMetadata { Ticker = "511990", Name = "华宝添益A", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513020", new AShareETFMetadata { Ticker = "513020", Name = "国泰中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513040", new AShareETFMetadata { Ticker = "513040", Name = "易方达中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513070", new AShareETFMetadata { Ticker = "513070", Name = "易方达中证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513090", new AShareETFMetadata { Ticker = "513090", Name = "易方达中证香港证券投资主题(港股通)ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513190", new AShareETFMetadata { Ticker = "513190", Name = "华夏中证港股通内地金融ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513200", new AShareETFMetadata { Ticker = "513200", Name = "易方达中证港股通医药卫生综合ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513230", new AShareETFMetadata { Ticker = "513230", Name = "华夏中证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513320", new AShareETFMetadata { Ticker = "513320", Name = "易方达恒生港股通新经济ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513550", new AShareETFMetadata { Ticker = "513550", Name = "港股通50", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513560", new AShareETFMetadata { Ticker = "513560", Name = "兴银中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513630", new AShareETFMetadata { Ticker = "513630", Name = "摩根标普港股通低波红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513720", new AShareETFMetadata { Ticker = "513720", Name = "国泰中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513750", new AShareETFMetadata { Ticker = "513750", Name = "广发中证港股通非银ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513770", new AShareETFMetadata { Ticker = "513770", Name = "华宝中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513780", new AShareETFMetadata { Ticker = "513780", Name = "景顺长城中证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513820", new AShareETFMetadata { Ticker = "513820", Name = "汇添富中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513830", new AShareETFMetadata { Ticker = "513830", Name = "嘉实中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513860", new AShareETFMetadata { Ticker = "513860", Name = "海富通中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513900", new AShareETFMetadata { Ticker = "513900", Name = "华安CES港股通精选100ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513910", new AShareETFMetadata { Ticker = "513910", Name = "华夏中证港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513920", new AShareETFMetadata { Ticker = "513920", Name = "华安恒生港股通中国央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513980", new AShareETFMetadata { Ticker = "513980", Name = "景顺长城中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "513990", new AShareETFMetadata { Ticker = "513990", Name = "招商上证港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "517400", new AShareETFMetadata { Ticker = "517400", Name = "国泰中证沪深港黄金产业股票ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "517520", new AShareETFMetadata { Ticker = "517520", Name = "黄金股ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518660", new AShareETFMetadata { Ticker = "518660", Name = "黄金ETF工银", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518800", new AShareETFMetadata { Ticker = "518800", Name = "国泰黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518850", new AShareETFMetadata { Ticker = "518850", Name = "华夏黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518880", new AShareETFMetadata { Ticker = "518880", Name = "华安黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "518890", new AShareETFMetadata { Ticker = "518890", Name = "中银黄金", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520510", new AShareETFMetadata { Ticker = "520510", Name = "华夏中证港股通医疗主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520530", new AShareETFMetadata { Ticker = "520530", Name = "港股通科技ETF东财", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520550", new AShareETFMetadata { Ticker = "520550", Name = "招商恒生港股通高股息低波动ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520560", new AShareETFMetadata { Ticker = "520560", Name = "华宝港股通恒生中国(香港上市)30ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520600", new AShareETFMetadata { Ticker = "520600", Name = "广发中证港股通汽车ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520630", new AShareETFMetadata { Ticker = "520630", Name = "广发中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520650", new AShareETFMetadata { Ticker = "520650", Name = "南方中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520660", new AShareETFMetadata { Ticker = "520660", Name = "南方中证国新港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520670", new AShareETFMetadata { Ticker = "520670", Name = "嘉实恒生港股通科技主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520690", new AShareETFMetadata { Ticker = "520690", Name = "博时恒生港股通创新药精选ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520700", new AShareETFMetadata { Ticker = "520700", Name = "万家中证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520720", new AShareETFMetadata { Ticker = "520720", Name = "国泰中证港股通汽车产业主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520770", new AShareETFMetadata { Ticker = "520770", Name = "建信恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520780", new AShareETFMetadata { Ticker = "520780", Name = "华宝中证港股通汽车产业主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520810", new AShareETFMetadata { Ticker = "520810", Name = "易方达中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520820", new AShareETFMetadata { Ticker = "520820", Name = "汇添富恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520840", new AShareETFMetadata { Ticker = "520840", Name = "华安恒生港股通科技主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520850", new AShareETFMetadata { Ticker = "520850", Name = "易方达中证港股通医疗主题ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520860", new AShareETFMetadata { Ticker = "520860", Name = "富国中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520880", new AShareETFMetadata { Ticker = "520880", Name = "华宝恒生港股通创新药精选ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520900", new AShareETFMetadata { Ticker = "520900", Name = "广发中证国新港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520910", new AShareETFMetadata { Ticker = "520910", Name = "华夏中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520940", new AShareETFMetadata { Ticker = "520940", Name = "华安恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520950", new AShareETFMetadata { Ticker = "520950", Name = "摩根恒生港股通50ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520960", new AShareETFMetadata { Ticker = "520960", Name = "嘉实恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520970", new AShareETFMetadata { Ticker = "520970", Name = "嘉实中证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520980", new AShareETFMetadata { Ticker = "520980", Name = "汇添富恒生港股通中国科技ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "520990", new AShareETFMetadata { Ticker = "520990", Name = "景顺长城中证国新港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SSE" } },
            { "159001", new AShareETFMetadata { Ticker = "159001", Name = "易方达保证金货币A", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159005", new AShareETFMetadata { Ticker = "159005", Name = "汇添富收益快钱货币A", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159101", new AShareETFMetadata { Ticker = "159101", Name = "华夏国证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159109", new AShareETFMetadata { Ticker = "159109", Name = "景顺长城恒生港股通50ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159117", new AShareETFMetadata { Ticker = "159117", Name = "鹏华港股通低波红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159118", new AShareETFMetadata { Ticker = "159118", Name = "华夏标普港股通低波红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159120", new AShareETFMetadata { Ticker = "159120", Name = "国联安港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159121", new AShareETFMetadata { Ticker = "159121", Name = "易方达恒生港股通汽车主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159125", new AShareETFMetadata { Ticker = "159125", Name = "招商国证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159126", new AShareETFMetadata { Ticker = "159126", Name = "南方中证港股通50ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159127", new AShareETFMetadata { Ticker = "159127", Name = "南方中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159128", new AShareETFMetadata { Ticker = "159128", Name = "天弘国证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159131", new AShareETFMetadata { Ticker = "159131", Name = "华宝中证港股通信息技术综合ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159137", new AShareETFMetadata { Ticker = "159137", Name = "华宝中证港股通医疗主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159152", new AShareETFMetadata { Ticker = "159152", Name = "平安恒生港股通科技主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159210", new AShareETFMetadata { Ticker = "159210", Name = "汇添富中证港股通汽车产业主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159217", new AShareETFMetadata { Ticker = "159217", Name = "港股通创新药ETF工银", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159220", new AShareETFMetadata { Ticker = "159220", Name = "华宝标普港股通低波红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159239", new AShareETFMetadata { Ticker = "159239", Name = "富国恒生港股通汽车主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159245", new AShareETFMetadata { Ticker = "159245", Name = "富国国证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159251", new AShareETFMetadata { Ticker = "159251", Name = "万家国证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159262", new AShareETFMetadata { Ticker = "159262", Name = "广发恒生港股通科技主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159265", new AShareETFMetadata { Ticker = "159265", Name = "鹏华国证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159266", new AShareETFMetadata { Ticker = "159266", Name = "港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159268", new AShareETFMetadata { Ticker = "159268", Name = "汇添富国证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159269", new AShareETFMetadata { Ticker = "159269", Name = "南方中证港股通科技ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159277", new AShareETFMetadata { Ticker = "159277", Name = "富国中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159280", new AShareETFMetadata { Ticker = "159280", Name = "汇添富国证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159281", new AShareETFMetadata { Ticker = "159281", Name = "天弘中证港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159285", new AShareETFMetadata { Ticker = "159285", Name = "华安国证港股通消费主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159286", new AShareETFMetadata { Ticker = "159286", Name = "鹏华国证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159297", new AShareETFMetadata { Ticker = "159297", Name = "南方国证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159312", new AShareETFMetadata { Ticker = "159312", Name = "广发恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159315", new AShareETFMetadata { Ticker = "159315", Name = "黄金股ETF工银", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159316", new AShareETFMetadata { Ticker = "159316", Name = "易方达恒生港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159318", new AShareETFMetadata { Ticker = "159318", Name = "恒生港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159321", new AShareETFMetadata { Ticker = "159321", Name = "华安中证沪深港黄金产业股票ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159322", new AShareETFMetadata { Ticker = "159322", Name = "平安中证沪深港黄金产业ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159323", new AShareETFMetadata { Ticker = "159323", Name = "华夏中证港股通汽车产业主题ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159331", new AShareETFMetadata { Ticker = "159331", Name = "国泰中证港股通高股息投资ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159333", new AShareETFMetadata { Ticker = "159333", Name = "万家中证港股通央企红利ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159365", new AShareETFMetadata { Ticker = "159365", Name = "富国恒指港股通ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159506", new AShareETFMetadata { Ticker = "159506", Name = "富国恒生港股通创新药及医疗保健ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159545", new AShareETFMetadata { Ticker = "159545", Name = "易方达恒生港股通高股息低波动ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159562", new AShareETFMetadata { Ticker = "159562", Name = "华夏中证沪深港黄金产业股票ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159568", new AShareETFMetadata { Ticker = "159568", Name = "博时港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159569", new AShareETFMetadata { Ticker = "159569", Name = "景顺长城国证港股通红利低波动率ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159570", new AShareETFMetadata { Ticker = "159570", Name = "汇添富国证港股通创新药ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159636", new AShareETFMetadata { Ticker = "159636", Name = "港股通科技30ETF工银", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159649", new AShareETFMetadata { Ticker = "159649", Name = "华安中债1-5年国开行债券ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159651", new AShareETFMetadata { Ticker = "159651", Name = "平安中债-0-3年国开行债券ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159711", new AShareETFMetadata { Ticker = "159711", Name = "华夏中证港股通50ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159712", new AShareETFMetadata { Ticker = "159712", Name = "国泰中证港股通50ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159726", new AShareETFMetadata { Ticker = "159726", Name = "华夏恒生港股通中国内地企业高股息率ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159776", new AShareETFMetadata { Ticker = "159776", Name = "港股通医药ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159788", new AShareETFMetadata { Ticker = "159788", Name = "易方达中证港股通中国100ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159792", new AShareETFMetadata { Ticker = "159792", Name = "富国中证港股通互联网ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159812", new AShareETFMetadata { Ticker = "159812", Name = "前海开源黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159915", new AShareETFMetadata { Ticker = "159915", Name = "易方达创业板ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE", PriceLimitPercentage = AShareETF.GrowthBoardPriceLimitPercentage } },
            { "159919", new AShareETFMetadata { Ticker = "159919", Name = "嘉实沪深300ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159934", new AShareETFMetadata { Ticker = "159934", Name = "易方达黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159937", new AShareETFMetadata { Ticker = "159937", Name = "博时黄金ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE" } },
            { "159949", new AShareETFMetadata { Ticker = "159949", Name = "华安创业板50ETF", TradingMode = ETFTradingMode.T0, Market = "SZSE", PriceLimitPercentage = AShareETF.GrowthBoardPriceLimitPercentage } },

            // T+1 ETFs - Examples (for filtering out)
            { "510180", new AShareETFMetadata { Ticker = "510180", Name = "180ETF", TradingMode = ETFTradingMode.T1, Market = "SSE" } },
            { "159901", new AShareETFMetadata { Ticker = "159901", Name = "深100ETF", TradingMode = ETFTradingMode.T1, Market = "SZSE" } },
        };

        /// <summary>
        /// Gets metadata for an ETF by ticker
        /// </summary>
        public static AShareETFMetadata GetMetadata(string ticker)
        {
            return _etfMetadata.TryGetValue(ticker, out var metadata) ? metadata : null;
        }

        /// <summary>
        /// Gets the daily price limit percentage for an ETF
        /// </summary>
        public static decimal GetPriceLimitPercentage(string ticker)
        {
            return GetMetadata(ticker)?.PriceLimitPercentage ?? AShareETF.DefaultPriceLimitPercentage;
        }

        /// <summary>
        /// Checks if an ETF supports T+0 trading
        /// </summary>
        public static bool IsT0ETF(string ticker)
        {
            var metadata = GetMetadata(ticker);
            return metadata?.IsT0 ?? false;
        }

        /// <summary>
        /// Gets all T+0 ETF tickers
        /// </summary>
        public static List<string> GetT0ETFs()
        {
            return _etfMetadata.Values
                .Where(m => m.IsT0)
                .Select(m => m.Ticker)
                .ToList();
        }

        /// <summary>
        /// Gets all T+0 ETF tickers for a specific market
        /// </summary>
        public static List<string> GetT0ETFs(string market)
        {
            return _etfMetadata.Values
                .Where(m => m.IsT0 && m.Market == market)
                .Select(m => m.Ticker)
                .ToList();
        }
    }
}
