using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// 强制无隔夜持仓风控。14:45（默认）后任何持仓归零，硬执行不靠策略自觉。
    /// 这是 INoOvernightPosition marker 意图的落地，但不新增 C# marker 接口（risk model 即强制点）。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §5。
    /// </summary>
    public class NoOvernightPositionRiskModel : IRiskManagementModel
    {
        private readonly TimeSpan _forcedCloseTime;
        public string Name => "NoOvernightPositionRiskModel";

        public NoOvernightPositionRiskModel(TimeSpan? forcedCloseTime = null)
            => _forcedCloseTime = forcedCloseTime ?? new TimeSpan(14, 45, 0);

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var now = algorithm.Time.TimeOfDay;
            var forceClose = now >= _forcedCloseTime;
            var result = new List<IPortfolioTarget>();
            foreach (var t in targets)
            {
                if (t.Quantity == 0) { result.Add(t); continue; }
                result.Add(forceClose ? new PortfolioTarget(t.Symbol, 0) : t);
            }
            return result;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
