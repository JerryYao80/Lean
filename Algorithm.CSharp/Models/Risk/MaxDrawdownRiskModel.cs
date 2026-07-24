using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>最大回撤风控模型 - 超阈值清仓</summary>
    public class MaxDrawdownRiskModel : IRiskManagementModel
    {
        private readonly decimal _maxDD;
        private decimal _peak;
        private bool _liquidated;

        public string Name => "MaxDrawdownRiskModel";

        public MaxDrawdownRiskModel(decimal maxDrawdown = 0.10m) => _maxDD = maxDrawdown;

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var pv = algorithm.Portfolio.TotalPortfolioValue;
            if (pv > _peak) { _peak = pv; _liquidated = false; }
            if (_peak > 0 && (_peak - pv) / _peak >= _maxDD && !_liquidated)
            {
                _liquidated = true;
                return algorithm.Securities.Keys.Where(s => algorithm.Portfolio[s].Quantity > 0).Select(s => new PortfolioTarget(s, 0m)).ToList();
            }
            return targets;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
