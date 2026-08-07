using System.Collections.Generic;

namespace QuantConnect.Algorithm.CSharp.Common
{
    /// <summary>标记策略支持 Layer A 超参扫描。GetTunableParameterNames 与 manifest.parameter_space 一致（manifest_lint 校验）。</summary>
    public interface IOptimizableStrategy
    {
        IEnumerable<string> GetTunableParameterNames();
    }
}
