#!/usr/bin/env python3
"""
strategy_trace.py — 白盒化追踪记录器 (process traceability recorder).

为每个策略的"论文→复现→回测→live-paper"创建过程维护一份人类可读的 trace 文档
和结构化的 trace.jsonl，记录每一个流程步骤和每一道准入 gate 的决策与证据。

为什么存在：用户要求过程白盒化 —— 每一步、每一个准入判断都要留下可审计记录。

设计原则：
  - 每个 strategy_id 一个目录：Results/soloquant/strategy-traces/<id>/
  - trace.md   —— 人类可读时间线（append-only）
  - trace.jsonl —— 结构化记录（append-only）
  - record-step / record-gate / record-artifact 三种记录
  - gates 检查脚本不做判断，只忠实记录 Claude 的决策与证据

Usage:
    python3 Scripts/strategy_trace.py init --strategy-id <id> --title "<paper title>"
    python3 Scripts/strategy_trace.py record-step --strategy-id <id> --step 1.fetch_paper --status ok --detail '{"url":"..."}'
    python3 Scripts/strategy_trace.py record-gate --strategy-id <id> --gate data_availability --result PASS --threshold ">=50% data available" --observed "85% (17/20 fields)" --detail '{"missing":[...]}'
    python3 Scripts/strategy_trace.py show --strategy-id <id>
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

TRACE_REL = Path("Results/soloquant") / "strategy-traces"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def trace_dir(strategy_id: str, repo_root: Path | None = None) -> Path:
    repo_root = repo_root or _repo_root()
    d = repo_root / TRACE_REL / strategy_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_md(path: Path, block: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")


def _load_detail(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {"_raw": s}


def cmd_init(args: argparse.Namespace) -> int:
    d = trace_dir(args.strategy_id)
    ts = _now()
    md = d / "trace.md"
    if not md.exists() or args.force:
        md.write_text(
            f"# Strategy Creation Trace — {args.strategy_id}\n\n"
            f"- paper_title: {args.title}\n"
            f"- started_at: {ts}\n\n"
            f"白盒化记录：每一步流程与每一道准入 gate 的决策都追加在下方。\n\n---\n",
            encoding="utf-8",
        )
    print(json.dumps({"status": "ok", "trace_dir": str(d)}, ensure_ascii=False))
    return 0


def cmd_record_step(args: argparse.Namespace) -> int:
    d = trace_dir(args.strategy_id)
    ts = _now()
    detail = _load_detail(args.detail)
    entry = {"ts": ts, "type": "step", "step": args.step, "status": args.status, "detail": detail}
    _append_jsonl(d / "trace.jsonl", entry)
    _append_md(
        d / "trace.md",
        f"### [{ts}] STEP `{args.step}` → **{args.status}**\n\n"
        f"{json.dumps(detail, ensure_ascii=False, indent=2)}\n",
    )
    print(json.dumps({"status": "ok", "step": args.step, "result": args.status}, ensure_ascii=False))
    return 0


def cmd_record_gate(args: argparse.Namespace) -> int:
    d = trace_dir(args.strategy_id)
    ts = _now()
    detail = _load_detail(args.detail)
    entry = {
        "ts": ts,
        "type": "gate",
        "gate": args.gate,
        "result": args.result,
        "threshold": args.threshold,
        "observed": args.observed,
        "detail": detail,
    }
    _append_jsonl(d / "trace.jsonl", entry)
    marker = "✅ PASS — 进入下一阶段" if args.result == "PASS" else "🛑 BLOCK — 流程阻断"
    _append_md(
        d / "trace.md",
        f"## [{ts}] GATE `{args.gate}` → {marker}\n\n"
        f"- **准入条件**: {args.threshold}\n"
        f"- **观测结果**: {args.observed}\n"
        f"- **证据**:\n\n{json.dumps(detail, ensure_ascii=False, indent=2)}\n",
    )
    print(json.dumps({"status": "ok", "gate": args.gate, "result": args.result}, ensure_ascii=False))
    return 0


def cmd_record_artifact(args: argparse.Namespace) -> int:
    d = trace_dir(args.strategy_id)
    ts = _now()
    entry = {"ts": ts, "type": "artifact", "name": args.name, "path": args.path}
    _append_jsonl(d / "trace.jsonl", entry)
    _append_md(d / "trace.md", f"- 📎 artifact `{args.name}`: `{args.path}`\n")
    print(json.dumps({"status": "ok"}, ensure_ascii=False))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    md = trace_dir(args.strategy_id) / "trace.md"
    if not md.exists():
        print(json.dumps({"status": "not_found"}, ensure_ascii=False))
        return 1
    print(md.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="白盒化追踪记录器")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="初始化策略 trace 文档")
    pi.add_argument("--strategy-id", required=True)
    pi.add_argument("--title", required=True)
    pi.add_argument("--force", action="store_true")
    pi.set_defaults(func=cmd_init)

    ps = sub.add_parser("record-step", help="记录一个流程步骤")
    ps.add_argument("--strategy-id", required=True)
    ps.add_argument("--step", required=True, help="1.fetch_paper / 2.llm_extract / 3.code_gen / 4.factor / 5.backtest / 6.live_paper")
    ps.add_argument("--status", default="ok", help="ok / warn / fail")
    ps.add_argument("--detail", default=None, help="JSON 字符串")
    ps.set_defaults(func=cmd_record_step)

    pg = sub.add_parser("record-gate", help="记录一道准入 gate 的决策")
    pg.add_argument("--strategy-id", required=True)
    pg.add_argument("--gate", required=True, help="data_availability / code_smoke / backtest_sharpe")
    pg.add_argument("--result", required=True, choices=["PASS", "BLOCK"])
    pg.add_argument("--threshold", required=True, help="准入条件描述")
    pg.add_argument("--observed", required=True, help="实际观测值")
    pg.add_argument("--detail", default=None, help="JSON 证据")
    pg.set_defaults(func=cmd_record_gate)

    pa = sub.add_parser("record-artifact", help="记录产物文件路径")
    pa.add_argument("--strategy-id", required=True)
    pa.add_argument("--name", required=True)
    pa.add_argument("--path", required=True)
    pa.set_defaults(func=cmd_record_artifact)

    pv = sub.add_parser("show", help="打印策略 trace 文档")
    pv.add_argument("--strategy-id", required=True)
    pv.set_defaults(func=cmd_show)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
