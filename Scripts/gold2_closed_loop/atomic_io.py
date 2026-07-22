"""Atomic JSON / bytes writers with directory fsync for the Gold2 closed-loop
proof (Task 9, Phase 2 STOP gate).

Mirrors the pattern in ``Scripts/gold2_closed_loop/run_experiment.py``:
``_canonical_json`` (lines 128-160ish) and ``_fsync_directory``
(lines 155-160) — unique same-directory temp file, write, flush, fsync the
file descriptor, ``os.replace`` to the target, then fsync the parent dir so
the rename is durable.

On ANY exception during write, the temp file is unlinked and the existing
target (if any) is left untouched. This is the contract that callers such as
the event journal and the seal helper rely on for crash safety.

A ``_writer`` keyword is exposed for test injection: callers may pass a
callable that, when invoked, raises — exercising the cleanup path without
needing to monkey-patch ``open``. Production callers must not use it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


def _fsync_directory(path: Path | str) -> None:
    """fsync the directory inode at ``path`` (must exist and be a directory)."""
    descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_quiet(path: Path) -> None:
    """Best-effort unlink; suppress FileNotFoundError."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    _writer: Callable[[int, bytes], None] | None = None,
) -> None:
    """Atomically write ``data`` to ``path``.

    Flow: ``tempfile.mkstemp`` in the same directory (so ``os.replace`` is
    atomic on POSIX), ``os.write`` (or ``_writer(fd, data)`` for tests),
    ``os.fsync(fd)``, close, ``os.replace`` to target, fsync parent dir.

    If ``_writer`` raises, the temp file is removed and the original target
    (if any) is preserved.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        if _writer is not None:
            _writer(fd, data)
        else:
            remaining = memoryview(data)
            while remaining:
                written = os.write(fd, remaining)
                remaining = remaining[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1  # mark closed so the finally block does not double-close
        os.replace(tmp_path, target)
        _fsync_directory(target.parent)
    except BaseException:
        # Close the fd if we still hold it; never raise over the primary error.
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        _unlink_quiet(tmp_path)
        # No partial target on the disk: os.replace never ran.
        raise


def atomic_write_json(
    path: Path | str,
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
    _writer: Callable[[int, bytes], None] | None = None,
) -> None:
    """Atomically write ``obj`` as JSON to ``path``.

    Mirrors ``run_experiment.py:_canonical_json`` for byte-stable output
    (``sort_keys=True``, ``ensure_ascii=False``, trailing newline).

    The ``_writer`` injection point is honored at the file-write step: when
    ``_writer`` raises, the temp file is removed and the existing target is
    preserved. The JSON serialization happens BEFORE the temp file is
    opened, so a serialization failure leaves no temp file on disk at all
    (and never reaches the writer).
    """
    text = json.dumps(
        obj,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
    ) + "\n"
    data = text.encode("utf-8")
    atomic_write_bytes(path, data, _writer=_writer)
