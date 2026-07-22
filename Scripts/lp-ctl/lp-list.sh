#!/usr/bin/env bash
# 列出所有 live-paper 策略（strategy_id / 状态 / pid / 内存 / 粘性）
set -euo pipefail
LEAN_DIR="${LEAN_DIR:-/home/project/hope/Lean}"
TOKEN="$(cat "$LEAN_DIR/Results/soloquant/.strategy-api-token")"
curl -s "http://localhost:5000/api/strategies?group=soloquant-lp" | python3 -c "
import json,sys
ss=json.load(sys.stdin)['strategies']
for s in ss:
    print(f\"[{s['status']:7}] {s['strategy_id']}\")
    if s.get('pid'):
        print(f\"           pid={s['pid']} rss={s.get('rss_mb')}MB cpu={s.get('cpu_percent')}% hold={s.get('manual_hold')}\")
print(f'共 {len(ss)} 个，{sum(1 for s in ss if s[\"status\"]==\"running\")} 个 running')
"
