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
        private DateTime _lastSuccessUtc = DateTime.MinValue;
        // auto-update3.md 第1点: 结构性 kill-switch
        // 连续 N 次失败后, 永久降级到 fallbackAlpha 直到进程重启 (不再每 bar 重试)
        private bool _killSwitchTripped;
        private readonly int _killSwitchThreshold = 5;
        private DateTime _lastLogUtc = DateTime.MinValue;

        public string Name => "RlRiskModel";

        public RlRiskModel(RlRiskConfig config = null)
        {
            _config = config ?? new RlRiskConfig();
            _client = new RlRiskClient(_config.Endpoint, _config.TimeoutMs);
        }

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            // Kill-switch 已触发: 永久降级 (结构性 fail-safe, 不依赖逻辑判断)
            if (_killSwitchTripped)
            {
                return ApplyFallback(algorithm, targets, reason: "kill-switch tripped");
            }

            decimal alpha = _config.FallbackAlpha;
            try
            {
                var stateJson = (algorithm as IRlStateExportable)?.SerializeRlState(algorithm);
                if (string.IsNullOrEmpty(stateJson))
                {
                    return ApplyFallback(algorithm, targets, reason: "no state");
                }

                var action = _client.Request(stateJson);
                if (action == null)
                {
                    _consecutiveTimeouts++;
                    if (_consecutiveTimeouts >= _killSwitchThreshold)
                    {
                        _killSwitchTripped = true;
                        LogThrottled(algorithm,
                            $"[RlRiskModel] KILL-SWITCH tripped: 连续 {_consecutiveTimeouts} 次 IPC 失败, " +
                            $"永久降级到 fallback α={_config.FallbackAlpha} 直到进程重启");
                    }
                    else if (_consecutiveTimeouts % 3 == 0)
                    {
                        LogThrottled(algorithm,
                            $"[RlRiskModel] 连续 {_consecutiveTimeouts} 次 IPC 失败, 用 fallback α={_config.FallbackAlpha}");
                    }
                    return ApplyFallback(algorithm, targets, reason: $"ipc fail #{_consecutiveTimeouts}");
                }

                // action 非空: 校验 alpha 数值健康 (NaN/Inf/负数 → 单次降级, 不 trip kill-switch)
                if (!IsHealthyAlpha(action.Alpha))
                {
                    LogThrottled(algorithm,
                        $"[RlRiskModel] action alpha 异常: {action.Alpha}, 降级到 fallback");
                    return ApplyFallback(algorithm, targets, reason: "unhealthy alpha");
                }

                alpha = ClampAlpha(action.Alpha);
                _consecutiveTimeouts = 0;
                _lastSuccessUtc = DateTime.UtcNow;
            }
            catch (Exception ex)
            {
                LogThrottled(algorithm,
                    $"[RlRiskModel] IPC 异常: {ex.Message}, 用 fallback α={_config.FallbackAlpha}");
                return ApplyFallback(algorithm, targets, reason: "exception");
            }
            return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * alpha));
        }

        private IEnumerable<IPortfolioTarget> ApplyFallback(QCAlgorithm algo, IPortfolioTarget[] targets, string reason)
        {
            // 永不抛异常, 永远返回可执行 targets
            return targets.Select(t => new PortfolioTarget(t.Symbol, t.Quantity * _config.FallbackAlpha));
        }

        private static bool IsHealthyAlpha(decimal alpha)
        {
            return alpha > 0m && alpha <= 1m && decimal.TryParse(alpha.ToString(), out _);
        }

        private void LogThrottled(QCAlgorithm algo, string msg)
        {
            // 节流: 每 30 秒最多一条日志 (避免日志洪水)
            var now = DateTime.UtcNow;
            if ((now - _lastLogUtc).TotalSeconds < 30) return;
            _lastLogUtc = now;
            algo.Log(msg);
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
