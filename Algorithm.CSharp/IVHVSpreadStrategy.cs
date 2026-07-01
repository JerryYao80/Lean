using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Volatility;

namespace QuantConnect.Algorithm.CSharp
{
    public class IVHVSpreadAlphaModel : IAlphaModel
    {
        private readonly IVPercentileFactor _iv;
        private readonly HVFactor _hv;
        private readonly decimal _threshold;
        public string Name => "IVHVSpread";
        
        public IVHVSpreadAlphaModel(IVPercentileFactor iv, HVFactor hv, decimal threshold = 0.05m)
        { _iv = iv; _hv = hv; _threshold = threshold; }
        
        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var result = new List<Insight>();
            foreach (var symbol in data.Keys)
            {
                var bars = algorithm.History<TradeBar>(symbol, 20, Resolution.Daily);
                var ivR = _iv.Compute(symbol, algorithm.Time, null);
                var hvR = _hv.Compute(symbol, algorithm.Time, bars);
                if (ivR.Quality == FactorDataQuality.Valid && hvR.Quality == FactorDataQuality.Valid && ivR.RawValue - hvR.RawValue > _threshold)
                    result.Add(Insight.Price(symbol, algorithm.Time.AddDays(1), InsightDirection.Up, 0.5, 0.7));
            }
            return result;
        }
        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }

    public class IVHVSpreadStrategy : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2020, 1, 1);
            SetEndDate(2025, 4, 1);
            SetAccountCurrency("CNY");
            SetCash(1000000);
            AddEquity("510300", Resolution.Daily, Market.SSE);

            var iv = new IVPercentileFactor(252);
            var hv = new HVFactor(20);
            SetAlpha(new IVHVSpreadAlphaModel(iv, hv, 0.05m));
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            SetRiskManagement(new NullRiskManagementModel());
        }
    }
}
