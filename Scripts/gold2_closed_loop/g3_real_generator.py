"""G3RealGenerator: LLM -> compile -> train-period gate -> GenerationResponse.

PROOF-ONLY orchestrator that replaces ``construct_p3._StaticGen`` as the
injected ``generator`` callable for ``G3Builder.build``.

Flow (spec §3.4 / plan Task 5):

1. Build the LLM prompt from the TRAIN-period review bundle (no blind-year
   data enters; spec §6 anti-p-hacking).
2. Call the LLM (glm-5.2 via mydamoxing.cn) via ``_call_llm``.
3. Strip markdown fences (``_strip_fences``); sanity-check the result looks
   like a C# subclass extending ``Gold2ReconstructionCandidateBase``
   (``_looks_like_csharp``).
4. Extract the candidate class name (``_extract_class_name``).
5. Compute the candidate directory as ``proof_root/candidates/<candidate_id>``
   — matching ``g3_builder.G3Builder.build``'s candidate_dir convention
   (g3_builder.py:236-238). The generator runs FIRST as the injected
   ``generator`` callable (g3_builder.py:199), so it writes G3.cs here
   BEFORE ``G3Builder.build`` re-writes the same bytes atomically
   (g3_builder.py:263-267, idempotent).
6. Compile via ``compile_candidate`` -> dll + sha256 ``compiler_hash``.
7. Run the training gate (``_run_train_gate``). Task 5 leaves this as a stub
   raising ``NotImplementedError``; Task 7 implements the real LEAN train
   run + pythonnet reflect. The Task 5 unit tests monkeypatch it.
8. Return a ``_Response`` dataclass that satisfies the
   ``g3_builder.GenerationResponse`` Protocol (source_text / compiler_hash /
   config_hash / training_gate_passed) PLUS the ``candidate_class`` /
   ``dll_path`` extras Task 6 threads into ``G3BuildResult``.

The generator does NOT itself run the LEAN backtest or open the blind
partition; it produces a frozen candidate descriptor that the blind
evaluator (Task 15) executes.

Spec: docs/superpowers/specs/2026-07-21-gold2-real-llm-reconstruction-design.md §3.4-3.6.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Scripts.gold2_closed_loop.evidence import canonical_hash
from Scripts.gold2_closed_loop.g3_real_compile import CompileResult, compile_candidate
from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt, generate_reconstruction

# Minimum train-period DSR the candidate must beat to pass the gate
# (spec §3.6: "candidate must NOT underperform G0 on the train period").
# Task 8's anti-p-hacking regression asserts this stays at 0.0 (no
# silent tightening of the gate across runs).
MIN_DSR: float = 0.0

# C# subclass signature: must extend Gold2ReconstructionCandidateBase and
# override BuildRiskModels. The reflect check (Task 7) enforces the override
# at runtime; this static check is a fast pre-filter.
_SUBCLASS_RE = re.compile(
    r":\s*Gold2ReconstructionCandidateBase\b",
    re.MULTILINE,
)
# Matches ``class <Name>`` so the base-list (everything up to the opening
# ``{``) can be scanned for ``Gold2ReconstructionCandidateBase``. Does NOT
# consume anything after the class name; ``_extract_class_name`` finds the
# next ``{`` itself so generic declarations (``class Foo<T,...>``) and
# multi-base lists (``class Foo : Base, IFoo``) are handled uniformly.
_CLASS_NAME_RE = re.compile(
    r"\bclass\s+(\w+)",
    re.MULTILINE,
)

# The base class the candidate must extend (matched in source text).
_BASE_CLASS_NAME = "Gold2ReconstructionCandidateBase"


@dataclass(frozen=True)
class _Response:
    """The generator's response object.

    Satisfies the ``g3_builder.GenerationResponse`` Protocol (source_text /
    compiler_hash / config_hash / training_gate_passed) and additionally
    carries ``candidate_class`` + ``dll_path`` for Task 6 to thread into
    ``G3BuildResult`` (Task 6 extends the Protocol + G3BuildResult to carry
    these optional fields).

    On a compile failure ``compiler_hash`` is "" and ``training_gate_passed``
    is False (so G3Builder maps the outcome to CONSTRUCTION_FAILED via the
    empty-compiler_hash branch at g3_builder.py:285-291).
    """
    source_text: str
    compiler_hash: str
    config_hash: str
    training_gate_passed: bool
    candidate_class: str | None = None
    dll_path: str | None = None


@dataclass(frozen=True)
class _GateRequest:
    """Input to ``_run_train_gate`` (Task 7 implements the real gate).

    Carries everything the gate needs to (a) reflect the dll for the
    BuildRiskModels override, (b) run LEAN on the train period, and
    (c) read the train sharpe. ``train_data_folder`` is threaded separately
    (as a keyword arg) so the gate can pass it to ``build_run_config``.
    """
    candidate_id: str
    candidate_class: str
    dll_path: str
    window_id: str
    train_start: str  # ISO date; resolved from phase0_types.proof_windows()
    train_end: str


def _call_llm(prompt: str, **kwargs: Any) -> str:
    """Call the LLM with the prompt and return the raw content string.

    Thin wrapper around ``g3_real_llm_client.generate_reconstruction`` so
    tests can monkeypatch this single function instead of the HTTP client.
    """
    return generate_reconstruction(prompt, **kwargs)


def _strip_fences(text: str) -> str:
    """Strip markdown code fences if the LLM wrapped its output.

    Handles ```csharp ... ```, ```cs ... ```, and bare ``` ... ```.
    If no fence is present, returns the text unchanged.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    # Drop the opening fence line (optionally carrying a language tag).
    lines = s.splitlines()
    if lines:
        lines = lines[1:]
    # Drop the closing fence if present.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _looks_like_csharp(source: str) -> bool:
    """Fast pre-filter: the source must (a) mention the base class and
    (b) declare at least one class. The reflect check (Task 7) enforces the
    BuildRiskModels override at runtime; this is only a sanity gate so the
    generator does not try to compile obvious non-C# output (e.g. an LLM
    apology) and waste a dotnet build."""
    if not source or _BASE_CLASS_NAME not in source:
        return False
    return bool(_CLASS_NAME_RE.search(source))


def _extract_class_name(source: str) -> str | None:
    """Extract the candidate class name from the C# source.

    Iterates over every ``class <Name> ...`` declaration in the source and
    returns the name of the class whose base-list (the text between the
    class name and the opening ``{``) contains
    ``Gold2ReconstructionCandidateBase``. This is the candidate subclass —
    not a helper RiskManagementModel that the LLM may emit before or after
    it (the prompt allows helper subclasses in the same file, and the LLM
    may emit them in any order).

    Why not return the first class: a helper like
    ``public class HelperRiskModel : RiskManagementModel {...}`` declared
    before the candidate subclass would otherwise be picked, pinned into
    ``config_hash``'s ``algorithm_type_name``, and Task 7's reflect would
    fail to find ``BuildRiskModels`` on ``HelperRiskModel`` -> false
    CANDIDATE_REJECTED.

    Also handles generic declarations (``class Foo<T> : Base``) by reading
    the base-list regardless of the optional ``<...>`` arity.

    Returns None if no class extending ``Gold2ReconstructionCandidateBase``
    is found (the caller treats None as a non-C# / contract-violation
    response).
    """
    for m in _CLASS_NAME_RE.finditer(source):
        name = m.group(1)
        # The base-list starts at the end of the class-name match and runs
        # up to the opening ``{``. Scan that window for the base class name.
        # ``m.end()`` is just past the captured class name + one trailing
        # char (the regex's non-capturing group consumes one of ``{``, ``:``,
        # or whitespace). Find the next ``{`` from here; the text in between
        # is the base-list (possibly with generic arity, interfaces, etc.).
        brace_idx = source.find("{", m.end())
        if brace_idx == -1:
            continue
        base_list = source[m.end():brace_idx]
        if _BASE_CLASS_NAME in base_list:
            return name
    return None


def _load_train_bundle(window_id: str) -> dict:
    """Load the TRAIN-period P2 review bundle for ``window_id``.

    Reuses ``construct_p3._load_p2_review_bundle`` (construct_p3.py:122)
    so the generator's prompt is pinned to the SAME frozen evidence the
    G3 construction is built on (spec §6 line 246: generations are
    diagnostics over a SINGLE frozen bundle). Anti-p-hacking: the bundle
    is the TRAIN-period formal-review bundle; no blind-year data enters.
    """
    # Late import to avoid a circular dependency at module load time
    # (construct_p3 imports from g3_builder which imports this module's
    # Protocol only; but keeping the import local makes the dependency
    # direction explicit and test-isolated).
    from Scripts.gold2_closed_loop.construct_p3 import _load_p2_review_bundle
    return _load_p2_review_bundle(window_id)


def _train_start(window_id: str) -> str:
    """ISO date string for the window's train-period start."""
    from Scripts.gold2_closed_loop.phase0_types import proof_windows
    for w in proof_windows():
        if w.window_id == window_id:
            return w.train[0].isoformat()
    raise KeyError(window_id)


def _train_end(window_id: str) -> str:
    """ISO date string for the window's train-period end."""
    from Scripts.gold2_closed_loop.phase0_types import proof_windows
    for w in proof_windows():
        if w.window_id == window_id:
            return w.train[1].isoformat()
    raise KeyError(window_id)


def _hash_config(candidate_class: str, dll_path: str, instrument: str) -> str:
    """Canonical hash of the candidate's runtime config.

    Frozen into ``config_hash`` so a downstream reviewer cannot swap the
    candidate dll or class without breaking the frozen descriptor
    (anti-p-hacking, spec §7 line 274).
    """
    return canonical_hash({
        "algorithm_type_name": candidate_class,
        "algorithm_location": str(dll_path),
        "instrument": instrument,
    })


def _default_cand_dir(proof_root: Path | str, candidate_id: str) -> Path:
    """Compute the candidate directory from ``proof_root`` + ``candidate_id``.

    Matches ``g3_builder.G3Builder.build``'s candidate_dir convention
    (g3_builder.py:236-238: ``proof_root/candidates/<candidate_id>``).
    The generator runs FIRST as the injected ``generator`` callable
    (g3_builder.py:199), so it writes G3.cs here BEFORE ``G3Builder.build``
    re-writes the same bytes atomically (g3_builder.py:263-267). The
    re-write is idempotent (same bytes via ``atomic_write_bytes``).
    """
    return Path(proof_root) / "candidates" / candidate_id


def _run_train_gate(req: _GateRequest, *, train_data_folder: str | None) -> bool:
    """Run the candidate on the train period; pass iff (a) the subclass
    overrides BuildRiskModels without overriding Initialize (reflect), AND
    (b) train sharpe >= MIN_DSR.

    Task 5 STUB: raises ``NotImplementedError``. The Task 5 unit tests
    monkeypatch this function, so the stub is never called in tests.
    Task 7 replaces this stub with the real implementation (LEAN train run
    + pythonnet reflect; see plan Task 7 Step 3).
    """
    raise NotImplementedError(
        "_run_train_gate is a Task 5 stub; Task 7 implements the real LEAN "
        "train run + pythonnet reflect. The Task 5 tests monkeypatch this "
        "function so the stub is never called."
    )


class G3RealGenerator:
    """Injectable generator that produces a real LLM-reconstructed G3 candidate.

    Replaces ``construct_p3._StaticGen`` as the ``generator`` callable passed
    to ``G3Builder``. The generator orchestrates LLM -> strip -> write G3.cs
    -> compile -> train gate -> ``_Response`` (a ``GenerationResponse``).

    Parameters
    ----------
    proof_root:
        The experiment's proof root. The candidate directory is computed as
        ``proof_root/candidates/<candidate_id>`` (matching
        ``g3_builder.G3Builder.build``'s convention at g3_builder.py:236-238).
        Passed by ``construct_p3`` (Task 6 wires this through).
    train_data_folder:
        The LEAN data-folder for the train-period gate run. ``None`` means
        use LEAN's default. Threaded into ``_run_train_gate`` as a keyword.
    instrument:
        The instrument spec key (default "518880"). Pinned into the
        candidate's ``config_hash`` so it cannot be silently swapped.
    """

    def __init__(
        self,
        *,
        proof_root: Path | str,
        train_data_folder: str | None = None,
        instrument: str = "518880",
    ) -> None:
        self._proof_root = Path(proof_root)
        self._train_data_folder = train_data_folder
        self._instrument = instrument

    def __call__(self, request: Any) -> _Response:
        """Build one G3 candidate. Raises on LLM/compile failure (the
        ``G3Builder`` catches and maps to CONSTRUCTION_FAILED)."""
        window_id = getattr(request, "window_id")
        candidate_id = getattr(request, "candidate_id")

        # 1. Build prompt from the TRAIN-period review bundle (no blind data).
        bundle = _load_train_bundle(window_id)
        prompt = build_prompt(bundle, instrument=self._instrument)

        # 2. Call the LLM.
        raw = _call_llm(prompt)

        # 3. Strip fences + sanity-check.
        source_text = _strip_fences(raw)
        if not _looks_like_csharp(source_text):
            # The LLM did not produce a C# subclass. Return a degenerate
            # response with empty source; G3Builder maps empty source_text
            # to CONSTRUCTION_FAILED (g3_builder.py:218-223).
            return _Response(
                source_text="",
                compiler_hash="",
                config_hash="",
                training_gate_passed=False,
                candidate_class=None,
                dll_path=None,
            )

        # 4. Extract the candidate class name.
        candidate_class = _extract_class_name(source_text) or ""

        # 5. Compute candidate_dir + write G3.cs BEFORE G3Builder.build
        # re-writes it atomically (idempotent: same bytes).
        candidate_dir = _default_cand_dir(self._proof_root, candidate_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "G3.cs").write_text(source_text, encoding="utf-8")

        # 6. Compile -> dll + sha256 compiler_hash.
        compile_result = compile_candidate(
            candidate_dir / "G3.cs",
            candidate_id=candidate_id,
            candidate_class=candidate_class,
        )
        if not compile_result.ok or not compile_result.compiler_hash:
            # Compile failed: empty compiler_hash -> G3Builder maps to
            # CONSTRUCTION_FAILED (g3_builder.py:285-291).
            return _Response(
                source_text=source_text,
                compiler_hash="",
                config_hash="",
                training_gate_passed=False,
                candidate_class=candidate_class or None,
                dll_path=str(compile_result.dll_path) or None,
            )

        # 7. Run the train gate (Task 7 implements the real version;
        # Task 5 tests monkeypatch this).
        gate_req = _GateRequest(
            candidate_id=candidate_id,
            candidate_class=candidate_class,
            dll_path=str(compile_result.dll_path),
            window_id=window_id,
            train_start=_train_start(window_id),
            train_end=_train_end(window_id),
        )
        gate_passed = _run_train_gate(gate_req, train_data_folder=self._train_data_folder)

        # 8. Build the frozen config_hash + return _Response.
        config_hash = _hash_config(
            candidate_class, compile_result.dll_path, self._instrument
        )
        return _Response(
            source_text=source_text,
            compiler_hash=compile_result.compiler_hash,
            config_hash=config_hash,
            training_gate_passed=bool(gate_passed),
            candidate_class=candidate_class,
            dll_path=str(compile_result.dll_path),
        )


__all__ = [
    "G3RealGenerator",
    "MIN_DSR",
    "CompileResult",
    "_GateRequest",
    "_Response",
    "_call_llm",
    "_default_cand_dir",
    "_extract_class_name",
    "_hash_config",
    "_looks_like_csharp",
    "_load_train_bundle",
    "_run_train_gate",
    "_strip_fences",
    "_train_end",
    "_train_start",
]
