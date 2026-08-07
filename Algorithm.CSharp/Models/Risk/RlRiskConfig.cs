namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>Layer C RlRiskModel 配置。Spec §6.7 fallback 规则。</summary>
    public class RlRiskConfig
    {
        /// <summary>ZeroMQ endpoint, e.g. tcp://127.0.0.1:5555</summary>
        public string Endpoint { get; set; } = "tcp://127.0.0.1:5555";
        /// <summary>policy 名（用于多策略 endpoint 路由）</summary>
        public string PolicyName { get; set; } = "default";
        /// <summary>server 不可达时的 fallback α (default 0.5 = 半仓)</summary>
        public decimal FallbackAlpha { get; set; } = 0.5m;
        /// <summary>请求超时 ms</summary>
        public int TimeoutMs { get; set; } = 200;
        /// <summary>alpha trace 回放模式: 读 jsonl 序列逐 bar 回放 alpha (供离线 RL 数据生成). null 则走 IPC.</summary>
        public string AlphaTracePath { get; set; } = null;
    }
}
