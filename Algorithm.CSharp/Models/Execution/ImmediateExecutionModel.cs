using QuantConnect.Algorithm.Framework.Execution;

namespace QuantConnect.Algorithm.CSharp.Models.Execution
{
    /// <summary>立即执行模型 - 薄封装 LEAN 原生</summary>
    public class ImmediateExecutionModel : QuantConnect.Algorithm.Framework.Execution.ImmediateExecutionModel
    {
        public new string Name => "ImmediateExecutionModel (Model Zoo)";
    }
}
