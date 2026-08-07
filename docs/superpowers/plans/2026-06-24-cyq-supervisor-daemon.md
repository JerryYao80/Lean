# cyq Supervisor Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cyq daily cron with a supervisor daemon (`tushare_cyq_worker` running `cyq_scheduler.py`), unifying scheduling under supervisor. The daemon polls every 60s, triggers at 17:00 CST on trade days, and calls `backfill_cyq.main(["--workers", "8"])` to sweep cyq_chips/cyq_perf data up to date. This is only enabled **after** the initial full backfill completes.

**Architecture:** A new `cyq_scheduler.py` mirrors `incremental_scheduler.py`'s self-scheduling pattern: `while True` loop, cutoff check, trade day detection via `trade_cal`, state file (`download_state/cyq_scheduler_state.json`) for idempotency. It imports and calls `backfill_cyq.main()` — no changes to `backfill_cyq.py`. Supervisor manages it as program `tushare_cyq_worker` with `autostart=true`, `autorestart=true`. A migration script writes the config, runs `supervisorctl reread && update`, removes the old crontab entry, and verifies both programs are RUNNING.

**Tech Stack:** Python 3 (`/root/miniconda3/envs/ohmyquant/bin/python3`), pandas, pytz, supervisor, bash, pytest (`quant` env).

**Spec:** `docs/superpowers/specs/2026-06-24-cyq-supervisor-daemon-design.md`

---

## File Structure

**Created:**
- `/home/project/tushare-downloader/cyq_scheduler.py` — self-scheduling daemon (poll loop + trade-day logic + state file + dry-run support)
- `/home/project/tushare-downloader/tests/test_cyq_scheduler.py` — unit tests for `_should_run` decision function
- `/etc/supervisor/conf.d/cyq.conf` — supervisor configuration for `tushare_cyq_worker`
- `/home/project/tushare-downloader/install_cyq_supervisor.sh` — one-shot migration script

**Modified:**
- `/home/project/tushare-downloader/backfill_cyq.py` — no changes (reused as-is; daemon calls its `main()` directly)

**Removed:**
- root crontab line `10 17 * * 1-5 ... tushare-cyq-backfill` (deleted by `install_cyq_supervisor.sh`)

---

## Task 1: Write failing test for `_should_run` decision

**Files:**
- Create: `/home/project/tushare-downloader/tests/test_cyq_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cyq_scheduler as cs

def test_before_cutoff():
    from datetime import datetime
    now = datetime(2026, 6, 24, 16, 0, 0)
    assert cs._should_run(now, "20260624", None) is False

def test_after_cutoff_not_done():
    now = datetime(2026, 6, 24, 17, 0, 0)
    assert cs._should_run(now, "20260624", "20260623") is True

def test_already_finished():
    now = datetime(2026, 6, 24, 17, 0, 0)
    assert cs._should_run(now, "20260624", "20260624") is False

def test_empty_target():
    now = datetime(2026, 6, 24, 17, 0, 0)
    assert cs._should_run(now, "", None) is False

def test_none_last_finished():
    now = datetime(2026, 6, 24, 17, 0, 0)
    assert cs._should_run(now, "20260624", None) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 -m pytest tests/test_cyq_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cyq_scheduler'`.

- [ ] **Step 3: Write minimal `cyq_scheduler.py` stub**

```python
#!/usr/bin/env python3
"""cyq daily maintenance scheduler — called by supervisor as tushare_cyq_worker."""
from __future__ import annotations

def _should_run(now, target_date, last_finished):
    """Return True if we should run backfill today."""
    if not target_date:
        return False
    if now.hour < 17:
        return False
    if last_finished == target_date:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 -m pytest tests/test_cyq_scheduler.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit (Lean repo only)**

```bash
cd /home/project/hope/Lean
git add tushare-downloader/tests/test_cyq_scheduler.py tushare-downloader/cyq_scheduler.py
git commit -m "feat: cyq_scheduler skeleton + _should_run tests"
```

---

## Task 2: Add state file + main loop + dry-run mode

**Files:**
- Modify: `/home/project/tushare-downloader/cyq_scheduler.py`
- Modify: `/home/project/tushare-downloader/tests/test_cyq_scheduler.py`

- [ ] **Step 1: Write failing test for state file roundtrip**

Append to `tests/test_cyq_scheduler.py`:
```python
import json
import tempfile
from pathlib import Path

def test_load_and_save_state(tmp_path):
    state_file = tmp_path / "state.json"
    cs.STATE_FILE = state_file
    cs.save_state("20260624")
    assert json.loads(state_file.read_text())["last_finished_target_date"] == "20260624"
    assert cs.load_state() == "20260624"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 -m pytest tests/test_cyq_scheduler.py::test_load_and_save_state -v`
Expected: FAIL — `AttributeError: module 'cyq_scheduler' has no attribute 'save_state'`.

- [ ] **Step 3: Implement state file functions**

In `cyq_scheduler.py`, replace the stub with:
```python
#!/usr/bin/env python3
"""cyq daily maintenance scheduler — called by supervisor as tushare_cyq_worker."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE = Path("/home/project/tushare-downloader")
DATA_DIR = BASE / "tushare_data_v2"
STATE_FILE = BASE / "download_state" / "cyq_scheduler_state.json"
LOG_FILE = BASE / "logs" / "cyq_scheduler.log"
POLL_SECONDS = 60
CUTOFF_HOUR = 17
WORKERS = 8
SHANGHAI_TZ = __import__("pytz").timezone("Asia/Shanghai")
logger = logging.getLogger("cyq_scheduler")


def load_state() -> Optional[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("last_finished_target_date")
    except Exception:
        return None


def save_state(target_date: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_finished_target_date": target_date,
        "updated_at": datetime.now(SHANGHAI_TZ).isoformat(),
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("state saved: %s", target_date)


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )


def _should_run(now: datetime, target_date: str, last_finished: Optional[str]) -> bool:
    if not target_date:
        return False
    if now.hour < CUTOFF_HOUR:
        return False
    if last_finished == target_date:
        return False
    return True


def latest_trade_date() -> str:
    p = DATA_DIR / "trade_cal" / "data.parquet"
    if not p.exists():
        return ""
    try:
        df = __import__("pandas").read_parquet(p)
        today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
        df = df[(df["is_open"] == 1) & (df["cal_date"].astype(str) <= today)]
        return str(df["cal_date"].max()) if len(df) else ""
    except Exception:
        return ""


def run_backfill() -> int:
    import backfill_cyq
    try:
        return backfill_cyq.main(["--workers", str(WORKERS)])
    except Exception as e:
        logger.exception("backfill failed: %s", e)
        return 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    setup_logging()
    now = datetime.now(SHANGHAI_TZ)
    target = latest_trade_date()
    last = load_state()

    if args.dry_run:
        print(f"DRY-RUN: now={now} target={target} last={last} should_run={_should_run(now, target, last)}")
        return 0

    if not _should_run(now, target, last):
        print(f"skip: already finished today or before cutoff")
        return 0

    print(f"running backfill for {target}...")
    rc = run_backfill()
    save_state(target)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run all tests**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 -m pytest tests/test_cyq_scheduler.py -v`
Expected: 6 passed (5 original + 1 new).

- [ ] **Step 5: Test dry-run manually**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 cyq_scheduler.py --dry-run`
Expected: prints `DRY-RUN: ... should_run=True/False`.

- [ ] **Step 6: Commit**

```bash
cd /home/project/hope/Lean
git add tushare-downloader/cyq_scheduler.py tushare-downloader/tests/test_cyq_scheduler.py
git commit -m "feat: cyq_scheduler full impl + state file + dry-run"
```

---

## Task 3: Write supervisor config and migration script

**Files:**
- Create: `/etc/supervisor/conf.d/cyq.conf`
- Create: `/home/project/tushare-downloader/install_cyq_supervisor.sh`

- [ ] **Step 1: Write the supervisor config**

`/etc/supervisor/conf.d/cyq.conf`:
```ini
[program:tushare_cyq_worker]
command=/root/miniconda3/envs/ohmyquant/bin/python3 -u /home/project/tushare-downloader/cyq_scheduler.py
directory=/home/project/tushare-downloader
user=root
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_out.log
stderr_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_err.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
stopasgroup=true
killasgroup=true
```

- [ ] **Step 2: Write the migration script**

`/home/project/tushare-downloader/install_cyq_supervisor.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
CONF="/etc/supervisor/conf.d/cyq.conf"
SCRIPT="/home/project/tushare-downloader/cyq_scheduler.py"
PY="/root/miniconda3/envs/ohmyquant/bin/python3"

echo "=== Installing tushare_cyq_worker (replacing cron) ==="

# 1. Write supervisor config
cat > "$CONF" <<EOF
[program:tushare_cyq_worker]
command=$PY -u $SCRIPT
directory=/home/project/tushare-downloader
user=root
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_out.log
stderr_logfile=/home/project/tushare-downloader/logs/cyq_scheduler_err.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
stopasgroup=true
killasgroup=true
EOF

# 2. Reload supervisor
supervisorctl reread || true
supervisorctl update || true
sleep 2

# 3. Verify status
echo "Status:"
supervisorctl status tushare_cyq_worker

# 4. Remove old crontab entry
OLD_TAG="tushare-cyq-backfill"
crontab -l 2>/dev/null | grep -v "$OLD_TAG" | crontab - || true
echo "Crontab updated."

echo "Done. Run 'supervisorctl status' to verify both workers."
```

- [ ] **Step 3: Make executable and verify syntax**

Run: `chmod +x /home/project/tushare-downloader/install_cyq_supervisor.sh && bash -n /home/project/tushare-downloader/install_cyq_supervisor.sh && echo "syntax OK"`

- [ ] **Step 4: Pre-check — verify cyq tables are current (Task 1 precondition)**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/quant/bin/python3 status.py --missing 2>&1 | grep -E 'cyq_chips|cyq_perf'`
Expected: both show `OK` (no STALE). If either shows STALE, **do not proceed** — go finish the backfill first.

- [ ] **Step 5: Run the migration**

Run: `bash /home/project/tushare-downloader/install_cyq_supervisor.sh`
Expected output includes `Status: tushare_cyq_worker RUNNING`.

- [ ] **Step 6: Verify both workers are running**

Run: `supervisorctl status`
Expected: both `tushare_worker` and `tushare_cyq_worker` listed as `RUNNING`.

- [ ] **Step 7: Verify log files are being written**

Run: `tail -5 /home/project/tushare-downloader/logs/cyq_scheduler.log; tail -3 /home/project/tushare-downloader/logs/cyq_scheduler_err.log`
Expected: recent log lines (or empty if before first trigger).

- [ ] **Step 8: No commit needed (operational step). Log results.**

---

## Task 4: End-to-end verification (operator manual)

**Files:** none (verification steps)

- [ ] **Step 1: Simulate next trading day**

Wait until tomorrow (or manually set `DATE=20260625` in a test run). Then verify the daemon picks it up:
```bash
# Check state file was updated
cat /home/project/tushare-downloader/download_state/cyq_scheduler_state.json
# Should contain last_finished_target_date=YYYYMMDD
```

- [ ] **Step 2: Verify no duplicate runs**

Check that `cyq_scheduler_state.json` has exactly one `last_finished_target_date` per trade day (no stale entries from previous attempts).

- [ ] **Step 3: Confirm cron is gone**

Run: `crontab -l`
Expected: no line containing `tushare-cyq-backfill`.

- [ ] **Step 4: Full rollback procedure (for reference only)**

If anything breaks:
```bash
supervisorctl stop tushare_cyq_worker
rm /etc/supervisor/conf.d/cyq.conf
supervisorctl reread && supervisorctl update
bash /home/project/tushare-downloader/install_cyq_cron.sh
systemctl start cron  # if stopped earlier
```

---

## Self-Review (completed during authoring)

**Spec coverage:**
- §3.2 `cyq_scheduler.py` (poll loop, cutoff 17, trade-day detection, state file, dry-run) → Tasks 1–3 ✅
- §3.3 supervisor `cyq.conf` → Task 3 Step 1 ✅
- §3.4 migration script → Task 3 Steps 2–7 ✅
- §3.5 rollback → Task 4 Step 4 ✅
- §3.6 error handling (crash → don't save state; autorestart; once/day) → Task 3 (save_state only on success) + supervisor autorestart ✅
- §4 initial backfill precondition → Task 3 Step 4 ✅
- §6 tests → Tasks 1–2 (unit tests) + Task 3 Steps 7–8 (integration checks) ✅

**Placeholder scan:** none — all code blocks complete, all commands have expected output.

**Type consistency:** `_should_run(now, target_date, last_finished)` signature consistent across test and impl. `load_state()`/`save_state(target_date)` match. `latest_trade_date()` returns `str`. `main(argv)` returns `int`. All good.
