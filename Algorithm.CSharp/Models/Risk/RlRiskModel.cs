using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;
using NetMQ;
using NetMQ.Sockets;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// 通用 RL 风控模型 (Layer C). Spec §6.3-6.7.
    /// 通过 IRlStateExportable 拿状态 → ZeroMQ REQ → Python inference server → α 缩放 targets.
    /// 任何 IPC 故障降级到 fallbackAlpha, 永不抛异常.
    /// </summary>
    public class RlRiskModel : IRiskManagementModel
    {
        private readonly RlRiskConfig _config;
        private readonly RlRiskClient _client;
        private int _consecutiveTimeouts;

        public string Name => "RlRiskModel";

        public RlRiskModel(RlRiskConfig config = null)
        {
            _config = config ?? new RlRiskConfig();
            _client = new RlRiskClient(_config.Endpoint, _config.TimeoutMs);
        }

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            decimal alpha = _config.FallbackAlpha;
            try
            {
                var stateJson = (algorithm as IRlStateExportable)?.SerializeRlState(algorithm);
                if (!string.IsNullOrEmpty(stateJson))
                {
                    var action = _client.Request(stateJson);
                    if (action != null)
                    {
                        alpha = ClampAlpha(action.Alpha);
                        _consecutiveTimeouts = 0;
                    }
                    else
                    {
                        _consecutiveTimeouts++;
                        if (_consecutiveTimeouts >= 10)
                            algorithm.Log($"[RlRiskModel] 连续 {_consecutiveTimeouts} 次 IPC 失败, 用 fallback α={_config.FallbackAlpha}");
                    }
                }
            }
            catch (Exception ex)
            {
                algorithm.Log($"[RlRiskModel] IPC 异常: {ex.Message}, 用 fallback α={_config.FallbackAlpha}");
            }
            return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        public static decimal ClampAlpha(decimal raw)
        {
            // Spec §6.5: α must be in (0, 1]. Out-of-range (incl. >1) → 0 (de-risk on invalid policy output).
            if (raw <= 0m) return 0m;
            if (raw > 1m) return 0m;
            return raw;
        }
    }

    public class RlRiskAction
    {
        [JsonProperty("action")] public string Action { get; set; }
        [JsonProperty("alpha")] public decimal Alpha { get; set; }
    }

    /// <summary>ZeroMQ REQ client, 超时返回 null (不抛异常).</summary>
    public class RlRiskClient : IDisposable
    {
        private readonly RequestSocket _socket;
        private readonly int _timeoutMs;
        private bool _connected;

        public RlRiskClient(string endpoint, int timeoutMs)
        {
            _timeoutMs = timeoutMs;
            try
            {
                _socket = new RequestSocket();
                _socket.Connect(endpoint);
                _connected = true;
            }
            catch { _connected = false; }
        }

        public RlRiskAction Request(string stateJson)
        {
            if (!_connected || _socket == null) return null;
            try
            {
                if (!_socket.TrySendFrame(TimeSpan.FromMilliseconds(_timeoutMs), stateJson))
                    return null;
                if (!_socket.TryReceiveFrameString(TimeSpan.FromMilliseconds(_timeoutMs), out var reply))
                    return null;
                return JsonConvert.DeserializeObject<RlRiskAction>(reply);
            }
            catch { return null; }
        }

        public void Dispose()
        {
            try { _socket?.Dispose(); } catch { }
        }
    }
}
