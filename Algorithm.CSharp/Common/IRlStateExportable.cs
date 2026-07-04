namespace QuantConnect.Algorithm.CSharp.Common
{
    /// <summary>标记策略支持 Layer C 状态导出。SerializeRlState 输出 JSON 字段须与 manifest.state_schema 一致。</summary>
    public interface IRlStateExportable
    {
        string SerializeRlState(QCAlgorithm algo);
    }
}
