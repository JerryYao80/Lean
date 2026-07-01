using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Core
{
    public interface IFactor
    {
        string Id { get; }
        string Name { get; }
        FactorCategory Category { get; }
        FactorScope Scope { get; }
        FactorComputeMode ComputeMode { get; }
        string DataSource { get; }

        FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null);
        FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time);
        bool IsAvailable(Symbol symbol, DateTime time);
    }
}