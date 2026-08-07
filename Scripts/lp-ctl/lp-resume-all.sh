#!/usr/bin/env bash
# 清除所有粘性标志（manual_hold），下一个 pipeline tick 自动拉起全部 serving 策略
set -euo pipefail
LEAN_DIR="${LEAN_DIR:-/home/project/hope/Lean}"
CTRL="$LEAN_DIR/Results/soloquant/live-paper-control.json"
echo '{}' > "$CTRL"
echo "已清空 $CTRL —— 所有策略恢复为可自动拉起"
