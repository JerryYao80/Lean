using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>单标的仓位限制模型 - 限制最大单标的权重</summary>
    public class PositionLimitRiskModel : IRiskManagementModel
    {
        private readonly decimal _maxPosition;
        public string Name => "PositionLimitRiskModel";

        public PositionLimitRiskModel(decimal maxPosition = 0.4m) => _maxPosition = maxPosition;

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var result = new List<IPortfolioTarget>();
            foreach (var t in targets)
            {
                if (t.Quantity == 0) { result.Add(t); continue; }
                var pv = algorithm.Portfolio.TotalPortfolioValue;
                var price = algorithm.Securities[t.Symbol].Price;
                if (price <= 0 || pv <= 0) { result.Add(t); continue; }
                var currentWeight = t.Quantity * price / pv;
                if (currentWeight > _maxPosition)
                {
                    var newQty = (int)(_maxPosition * pv / price);
                    result.Add(new PortfolioTarget(t.Symbol, newQty));
                }
                else result.Add(t);
            }
            return result;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
