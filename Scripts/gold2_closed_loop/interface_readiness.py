"""Proof interface readiness recheck (Task 12, Phase 2 STOP gate).

Static scan of the proof module tree that REJECTS any proof module which:

* imports production daemon / evolution / generation / layer-state
  (``evolution_scheduler``, ``layer_state``, ``inspiration`` generation
  machinery);
* globs the global ``Results`` directory or selects artifacts by mtime
  (spec §9 forbids it);
* hard-codes an ``OptionVolArb`` trace/policy path (spec §6 line 158);
* hard-codes an ONNX observation dimension (spec §6 line 159);
* enables a fallback (``fallback_download_enabled=True`` /
  ``fallback_gbm_enabled=True``) — the proof must force these False;
* has a WRITABLE blind mount (spec §6 line 118: blind partition must be
  read-only / invisible during construction).

This is a READ-ONLY static scan; it never imports or executes the modules
under inspection (so a module with a syntax error or a forbidden import is
still scannable). It returns a typed :class:`InterfaceReadiness` whose
``ready`` flag gates ``preregister``.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Forbidden IMPORT/module references (production machinery the proof must
# not depend on). Matched as whole-identifier tokens.
_FORBIDDEN_REFERENCES: tuple[str, ...] = (
    "evolution_scheduler",
    "layer_state",
    "evolution_state",
    "generation_state",
)

# Forbidden SOURCE PATTERNS (regex, applied to each .py file's text). Each
# has a stable key so the readiness result is actionable.
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("global_results_glob",
     re.compile(r"glob\s*\.\s*glob\s*\(\s*[^)]*Results", re.IGNORECASE)),
    # mtime_select: forbid mtime-based ARTIFACT SELECTION (sorting/globbing
    # Results by .st_mtime). The bare ``.st_mtime`` token is too broad: it
    # false-positives on legitimate file-identity checks like
    # ``st_mtime_ns`` used for dedup in data_sources.py (whole-tree review).
    # Narrow to mtime used as a sort/glob key, which is the actual §9
    # violation. ``.st_mtime_ns`` (a distinct attribute) is NOT matched.
    ("mtime_select",
     re.compile(r"sorted\s*\(.*(?:st_mtime|mtime).*\)|"
                r"\.st_mtime\b(?!\w).*sort|"
                r"glob\s*\.\s*glob\s*\(.*(?:st_mtime|mtime)",
                re.IGNORECASE)),
    ("optionvolarb_path",
     re.compile(r"OptionVolArb", re.IGNORECASE)),
    ("hardcoded_obs_dim",
     re.compile(r"\bobs[_-]?dim\s*=\s*\d", re.IGNORECASE)),
    ("enabled_fallback",
     re.compile(r"fallback[_-]?(download|gbm)[_-]?enabled\s*=\s*True",
                re.IGNORECASE)),
)

_PY_EXT = ".py"


@dataclass(frozen=True)
class InterfaceReadiness:
    """Result of :func:`inspect_proof_interfaces`.

    Fields
    ------
    ready:
        True iff no forbidden references or patterns were found AND the
        blind mount (if checked) is read-only.
    forbidden_references:
        Set of forbidden module identifiers found in imports.
    forbidden_patterns:
        Set of forbidden-pattern keys found in source.
    files_scanned:
        Number of .py files scanned.
    """

    ready: bool
    forbidden_references: set[str] = field(default_factory=set)
    forbidden_patterns: set[str] = field(default_factory=set)
    files_scanned: int = 0


def inspect_proof_interfaces(
    root: Path | str,
    *,
    blind_mount: Path | str | None = None,
) -> InterfaceReadiness:
    """Statically scan ``root`` for forbidden references/patterns.

    Parameters
    ----------
    root:
        Directory of proof modules to scan (recursively, .py files only).
    blind_mount:
        Optional blind-partition mount directory. If provided and writable
        by the owner (write bit set on the directory mode), a
        ``writable_blind_mount`` forbidden pattern is recorded and
        ``ready`` is False.
    """
    root_path = Path(root)
    forbidden_refs: set[str] = set()
    forbidden_pats: set[str] = set()
    files_scanned = 0
    # The scanner's own source (this file) defines the forbidden patterns
    # as STRING LITERALS; scanning it would self-trigger. Skip it (the
    # scanner is trusted infrastructure, not a proof module under test).
    self_path = Path(__file__).resolve()
    if root_path.is_dir():
        for path in sorted(root_path.rglob(f"*{_PY_EXT}")):
            if not path.is_file():
                continue
            if path.resolve() == self_path:
                # Don't scan the scanner itself.
                files_scanned += 1
                continue
            files_scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # A .py file that cannot be read is itself a readiness
                # failure (whole-tree review): silently skipping it would
                # let a module with forbidden imports + an unreadable
                # encoding pass. Record a forbidden pattern so the caller
                # sees the file was not scannable.
                forbidden_pats.add("unreadable_module")
                continue
            for ref in _FORBIDDEN_REFERENCES:
                # Whole-token match: ``import evolution_scheduler``,
                # ``from Scripts.inspiration import layer_state``.
                if re.search(r"\b" + re.escape(ref) + r"\b", text):
                    forbidden_refs.add(ref)
            for key, pattern in _FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    forbidden_pats.add(key)

    # Blind mount must be read-only (no owner write bit). A writable blind
    # mount violates spec §6 line 118 (blind partition invisible/read-only
    # during construction).
    if blind_mount is not None:
        bpath = Path(blind_mount)
        try:
            mode = stat.S_IMODE(bpath.stat().st_mode)
        except OSError:
            # A missing blind mount is treated as not-ready (no mount to
            # protect). Callers pass an existing dir.
            forbidden_pats.add("writable_blind_mount")
        else:
            if mode & stat.S_IWUSR:
                forbidden_pats.add("writable_blind_mount")

    ready = not forbidden_refs and not forbidden_pats
    return InterfaceReadiness(
        ready=ready,
        forbidden_references=forbidden_refs,
        forbidden_patterns=forbidden_pats,
        files_scanned=files_scanned,
    )
