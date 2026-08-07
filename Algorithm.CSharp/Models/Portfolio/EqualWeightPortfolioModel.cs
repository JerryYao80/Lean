using System.Collections.Generic;
using System.Linq;
using QuantConnect.Algorithm.Framework.Portfolio;

namespace QuantConnect.Algorithm.CSharp.Models.Portfolio
{
    /// <summary>等权组合模型 - 薄封装 LEAN 原生 EqualWeightingPortfolioConstructionModel</summary>
    public class EqualWeightPortfolioModel : EqualWeightingPortfolioConstructionModel
    {
        public new string Name => "EqualWeightPortfolioModel (Model Zoo)";
    }
}
