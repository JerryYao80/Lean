#!/usr/bin/env bash
# 停止单个 live-paper 策略（粘性：pipeline 后续 tick 不会自动重启）
# 用法: ./lp-stop.sh <strategy_id>
set -euo pipefail
LEAN_DIR="${LEAN_DIR:-/home/project/hope/Lean}"
TOKEN="$(cat "$LEAN_DIR/Results/soloquant/.strategy-api-token")"
SID="${1:-}"
if [[ -z "$SID" ]]; then
  echo "Usage: $0 <strategy_id>" >&2
  echo "先运行 ./lp-list.sh 查看 strategy_id" >&2
  exit 1
fi
curl -s -X POST "http://localhost:5000/api/strategies/$SID/stop" -H "Authorization: Bearer $TOKEN"
echo
