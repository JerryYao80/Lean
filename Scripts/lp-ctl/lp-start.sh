#!/usr/bin/env bash
# 启动单个 live-paper 策略（清除粘性，pipeline 恢复维护）
# 用法: ./lp-start.sh <strategy_id>
set -euo pipefail
LEAN_DIR="${LEAN_DIR:-/home/project/hope/Lean}"
TOKEN="$(cat "$LEAN_DIR/Results/soloquant/.strategy-api-token")"
SID="${1:-}"
if [[ -z "$SID" ]]; then
  echo "Usage: $0 <strategy_id>" >&2
  echo "先运行 ./lp-list.sh 查看 strategy_id" >&2
  exit 1
fi
curl -s -X POST "http://localhost:5000/api/strategies/$SID/start" -H "Authorization: Bearer $TOKEN"
echo
