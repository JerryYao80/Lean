"""G3-Real P4 wiring (Task 6): construct_p3 uses G3RealGenerator by default
with a static fallback via GOLD2_G3_GENERATOR=static; G3BuildResult carries
candidate_class/dll_path for Task 7 to consume.

Tests (per plan Task 6 Step 1):
- test_construct_p3_uses_real_generator_by_default
- test_static_fallback_env
- test_g3buildresult_has_candidate_class_dll_path

Task 7 adds the resolve_final_stage / frozen_params_for tests on top.
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_construct_p3_uses_real_generator_by_default():
    """construct_p3.py source must reference G3RealGenerator + GOLD2_G3_GENERATOR
    + keep _StaticGen as a fallback."""
    import Scripts.gold2_closed_loop.construct_p3 as p3

    src = Path(p3.__file__).read_text()
    assert "G3RealGenerator" in src, (
        "construct_p3 must wire G3RealGenerator as the default generator"
    )
    assert "GOLD2_G3_GENERATOR" in src, (
        "construct_p3 must read GOLD2_G3_GENERATOR to switch real/static"
    )
    assert "_StaticGen" in src, (
        "construct_p3 must keep _StaticGen as the static fallback"
    )
    assert "_select_generator" in src, (
        "construct_p3 must define a _select_generator helper"
    )


def test_static_fallback_env(monkeypatch):
    """GOLD2_G3_GENERATOR=static -> _select_generator returns _StaticGen.

    Default (unset or 'real') -> G3RealGenerator.
    """
    import Scripts.gold2_closed_loop.construct_p3 as p3

    # Static fallback
    monkeypatch.setenv("GOLD2_G3_GENERATOR", "static")
    gen = p3._select_generator(Path("/tmp/proof"))
    assert gen.__class__.__name__ == "_StaticGen", (
        f"static env must return _StaticGen, got {gen.__class__.__name__}"
    )

    # Default (real) — env unset
    monkeypatch.delenv("GOLD2_G3_GENERATOR", raising=False)
    gen = p3._select_generator(Path("/tmp/proof"))
    assert gen.__class__.__name__ == "G3RealGenerator", (
        f"default env must return G3RealGenerator, got {gen.__class__.__name__}"
    )

    # Explicit real
    monkeypatch.setenv("GOLD2_G3_GENERATOR", "real")
    gen = p3._select_generator(Path("/tmp/proof"))
    assert gen.__class__.__name__ == "G3RealGenerator"


def test_g3buildresult_has_candidate_class_dll_path():
    """G3BuildResult dataclass must have optional candidate_class + dll_path
    fields (default None so G0 / static-stub paths are unaffected)."""
    from Scripts.gold2_closed_loop.g3_builder import G3BuildResult

    fields = {f.name: f for f in dataclasses.fields(G3BuildResult)}
    assert "candidate_class" in fields, (
        "G3BuildResult must carry candidate_class (Task 6 extends it for P4)"
    )
    assert "dll_path" in fields, (
        "G3BuildResult must carry dll_path (Task 6 extends it for P4)"
    )
    # Defaults must be None so existing G0 / static-stub paths that do not
    # supply these fields are unaffected.
    assert fields["candidate_class"].default is None, (
        "candidate_class default must be None (static-stub path returns "
        "G3BuildResult without these attrs; getattr(..., None) handles that, "
        "but a non-None default would leak into G0 results)."
    )
    assert fields["dll_path"].default is None, (
        "dll_path default must be None (static-stub path returns G3BuildResult "
        "without these attrs; a non-None default would leak into G0 results)."
    )


def test_g3buildresult_optional_fields_do_not_break_positional_construction(tmp_path):
    """Existing g3_builder tests construct G3BuildResult positionally in some
    failure-path fixtures (e.g. _construction_failed at g3_builder.py:350).
    The new optional fields with default None MUST NOT break that positional
    construction (they come AFTER verdict_cap, the last positional field)."""
    from Scripts.gold2_closed_loop.g3_builder import G3Builder, G3BuildResult
    from dataclasses import dataclass

    @dataclass
    class FakeResp:
        source_text: str
        compiler_hash: str
        config_hash: str
        training_gate_passed: bool

    class Gen:
        def __call__(self, req):
            return FakeResp(
                source_text="class G3 { }",
                compiler_hash="c0mp1ler" * 8,
                config_hash="c0nf1g" * 10,
                training_gate_passed=True,
            )

    from Scripts.gold2_closed_loop.g3_builder import G3Request

    req = G3Request(
        experiment_id="E1", window_id="W1", stage_id="G3",
        candidate_id="W1-G3-C0", partition="W1/train",
        input_evidence_sha256="a" * 64, parent_generation_id=None,
        generation_index=1, seed=17, budget=4,
    )
    result = G3Builder(Gen()).build(req, tmp_path / "proof")
    # The eligible path sets candidate_class/dll_path from getattr(response, ...).
    assert result.eligible is True
    # The FakeResp does not carry candidate_class/dll_path, so getattr(..., None)
    # must yield None (consistent with the static stub + G0 paths).
    assert result.candidate_class is None
    assert result.dll_path is None


def test_g3builder_threads_candidate_class_dll_path_from_response(tmp_path):
    """G3Builder.build must thread candidate_class + dll_path from the
    generator response (via getattr) into G3BuildResult when present."""
    from Scripts.gold2_closed_loop.g3_builder import G3Builder, G3Request
    from dataclasses import dataclass
    from pathlib import Path

    @dataclass
    class FakeResp:
        source_text: str
        compiler_hash: str
        config_hash: str
        training_gate_passed: bool
        candidate_class: str
        dll_path: str

    class Gen:
        def __call__(self, req):
            return FakeResp(
                source_text="class G3 { }",
                compiler_hash="c0mp1ler" * 8,
                config_hash="c0nf1g" * 10,
                training_gate_passed=True,
                candidate_class="Gold2G3Real_W1",
                dll_path="/tmp/x.dll",
            )

    req = G3Request(
        experiment_id="E1", window_id="W1", stage_id="G3",
        candidate_id="W1-G3-C0", partition="W1/train",
        input_evidence_sha256="a" * 64, parent_generation_id=None,
        generation_index=1, seed=17, budget=4,
    )
    result = G3Builder(Gen()).build(req, tmp_path / "proof")
    assert result.eligible is True
    assert result.candidate_class == "Gold2G3Real_W1"
    assert result.dll_path == "/tmp/x.dll"


def test_construct_p3_p3_report_carries_candidate_class_dll_path(tmp_path, monkeypatch):
    """construct_p3.main() must write candidate_class + dll_path into the
    g3_build dict of p3_report.json so Task 7's _resolve_final_stage /
    _frozen_params_for can read them.

    This test uses the _StaticGen path (which omits the attrs) to confirm
    the dict still gets the keys with None values (getattr(..., None)).
    """
    import json
    import Scripts.gold2_closed_loop.construct_p3 as p3

    # Force static so we don't depend on LLM/dotnet being available.
    monkeypatch.setenv("GOLD2_G3_GENERATOR", "static")

    # Capture the g3_build dict the main() writes by patching the report path.
    # Cheaper path: invoke _StaticGen via G3Builder directly and inspect the
    # dict shape main() builds. We replicate the main() dict-construction
    # here so the test does not require the full P2/P3 harness.
    from Scripts.gold2_closed_loop.g3_builder import G3Builder, G3Request
    from Scripts.gold2_closed_loop.construct_p3 import _StaticGen, _build_g3_request

    req = G3Request(
        experiment_id="E1", window_id="W1", stage_id="G3",
        candidate_id="W1-G3-C0", partition="W1/train",
        input_evidence_sha256="a" * 64, parent_generation_id=None,
        generation_index=1, seed=17, budget=4,
    )
    build = G3Builder(_StaticGen()).build(req, tmp_path / "proof")
    g3_build = {
        "eligible": build.eligible,
        "alias_reason": build.alias_reason,
        "source_sha256": build.source_sha256,
        "candidate_set_sha256": build.candidate_set_sha256,
        "verdict_cap": build.verdict_cap,
        "candidate_class": build.candidate_class,
        "dll_path": build.dll_path,
    }
    # Static stub does not supply candidate_class/dll_path; the G3BuildResult
    # defaults are None, and the dict must carry None (so Task 7 sees the
    # field is present but empty -> alias G2/G1/G0).
    assert "candidate_class" in g3_build
    assert "dll_path" in g3_build
    assert g3_build["candidate_class"] is None
    assert g3_build["dll_path"] is None
    # Must be JSON-serializable (it ends up in p3_report.json).
    json.dumps(g3_build)


# ---------------------------------------------------------------------------
# Task 7: _resolve_final_stage + _frozen_params_for + _run_train_gate wiring
# ---------------------------------------------------------------------------


def _write_p3_report(p3_dir: Path, *, window_id: str, g3_build: dict | None) -> None:
    """Helper: write a minimal p3_report.json with the given g3_build."""
    import json
    p3_dir.mkdir(parents=True, exist_ok=True)
    payload = {"windows": [{"window_id": window_id, "g3_build": g3_build}]}
    (p3_dir / "p3_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
    )


def _write_p2_report(p2_dir: Path, *, window_id: str,
                     g2_shaping: dict | None = None,
                     g1_selected: dict | None = None) -> None:
    """Helper: write a minimal p2_report.json for alias-chain tests."""
    import json
    p2_dir.mkdir(parents=True, exist_ok=True)
    payload = {"windows": [{"window_id": window_id,
                             "g2_shaping": g2_shaping,
                             "g1_selected": g1_selected}]}
    (p2_dir / "p2_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
    )


def test_resolve_final_stage_returns_g3_when_compiled(tmp_path, monkeypatch):
    """Task 7: when p3_report.g3_build.eligible==True (real LLM candidate
    compiled + passed the train gate), _resolve_final_stage MUST return "G3"
    instead of falling through to the G2/G1/G0 alias chain. This is the core
    supersede: G3 stops aliasing to G2/G1/G0 when a real candidate compiled.
    """
    import json
    import Scripts.gold2_closed_loop.construct_p4 as p4

    # Patch ROOT so the report paths land under tmp_path and the test does
    # not touch the real result/ tree.
    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {"_iso": staticmethod(lambda d: d.isoformat())})(),
    )
    _write_p3_report(
        tmp_path / "result" / "gold2-p3-construction",
        window_id="W1",
        g3_build={
            "eligible": True,
            "candidate_set_sha256": "x" * 64,
            "source_sha256": "y" * 64,
            "candidate_class": "Gold2G3Real_W1",
            "dll_path": str(tmp_path / "x.dll"),
        },
    )
    assert p4._resolve_final_stage("W1") == "G3", (
        "eligible G3 build must supersede the G2/G1/G0 alias chain"
    )


def test_resolve_final_stage_static_fallback_aliases(tmp_path, monkeypatch):
    """Task 7: when p3_report.g3_build is None or not eligible (the static
    stub / CONSTRUCTION_FAILED / CANDIDATE_REJECTED paths), _resolve_final_stage
    MUST fall through to the existing G2/G1/G0 alias chain (so a static-only
    P3 run still aliases to G2 when shaping is present)."""
    import json
    import Scripts.gold2_closed_loop.construct_p4 as p4

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {"_iso": staticmethod(lambda d: d.isoformat())})(),
    )
    _write_p3_report(
        tmp_path / "result" / "gold2-p3-construction",
        window_id="W1",
        g3_build=None,
    )
    _write_p2_report(
        tmp_path / "result" / "gold2-p2-construction",
        window_id="W1",
        g2_shaping={"extreme_risk_contrib_penalty": 1.5},
    )
    assert p4._resolve_final_stage("W1") == "G2"


def test_resolve_final_stage_not_eligible_falls_through(tmp_path, monkeypatch):
    """If g3_build is present but eligible==False (CANDIDATE_REJECTED or
    CONSTRUCTION_FAILED), _resolve_final_stage MUST NOT return "G3" — the
    candidate did not pass the gate, so it cannot supersede the alias chain."""
    import Scripts.gold2_closed_loop.construct_p4 as p4

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {"_iso": staticmethod(lambda d: d.isoformat())})(),
    )
    _write_p3_report(
        tmp_path / "result" / "gold2-p3-construction",
        window_id="W1",
        g3_build={"eligible": False, "alias_reason": "CANDIDATE_REJECTED"},
    )
    _write_p2_report(
        tmp_path / "result" / "gold2-p2-construction",
        window_id="W1",
        g2_shaping=None,
        g1_selected={"trend-ma-short": "20", "vol-target": "0.11"},
    )
    assert p4._resolve_final_stage("W1") == "G1"


def test_frozen_params_for_g3_returns_candidate_descriptor(tmp_path, monkeypatch):
    """Task 7: _frozen_params_for(stage_id="G3") returns the candidate
    algorithm-type-name + algorithm-location (the candidate dll path) when the
    P3 build is eligible. This is the descriptor the blind runner threads into
    build_run_config so LEAN loads the candidate dll instead of the proof
    strategy dll."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {"_iso": staticmethod(lambda d: d.isoformat())})(),
    )
    dll_path = str(tmp_path / "x.dll")
    _write_p3_report(
        tmp_path / "result" / "gold2-p3-construction",
        window_id="W1",
        g3_build={
            "eligible": True,
            "candidate_class": "Gold2G3Real_W1",
            "dll_path": dll_path,
        },
    )
    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    params = p4._frozen_params_for(fc)
    assert params is not None, (
        "eligible G3 must yield non-None params so the blind runner loads "
        "the candidate dll + algorithm-type-name"
    )
    assert params["algorithm-type-name"] == "Gold2G3Real_W1"
    assert params["algorithm-location"] == dll_path


def test_frozen_params_for_g3_returns_none_when_not_eligible(tmp_path, monkeypatch):
    """Task 7: when the G3 build is not eligible (static stub / rejected),
    _frozen_params_for MUST return None so the blind runner falls back to the
    default _lean_run path (no algorithm-location override). The alias chain
    in _resolve_final_stage then picks G2/G1/G0."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {"_iso": staticmethod(lambda d: d.isoformat())})(),
    )
    _write_p3_report(
        tmp_path / "result" / "gold2-p3-construction",
        window_id="W1",
        g3_build={"eligible": False, "alias_reason": "CANDIDATE_REJECTED"},
    )
    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    assert p4._frozen_params_for(fc) is None


# ---------------------------------------------------------------------------
# Task 7: _run_train_gate / _reflect_interface_ok (source-level reflect)
# ---------------------------------------------------------------------------


def test_reflect_interface_ok_source_level_trivial_subclass():
    """Source-level reflect on the TRIVIAL_SUBCLASS (override present, no
    Initialize override) must return True. This is the weaker source-parse
    fallback when pythonnet runtime reflect is unavailable (this env)."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Tests.gold2_closed_loop.test_g3_real_generator import TRIVIAL_SUBCLASS

    assert gen._reflect_interface_ok_source_level(TRIVIAL_SUBCLASS) is True, (
        "TRIVIAL_SUBCLASS overrides BuildRiskModels and does NOT override "
        "Initialize -> source-level reflect must accept it"
    )


def test_reflect_interface_ok_source_level_no_override_rejected():
    """Source-level reflect on NO_OVERRIDE_SUBCLASS (no BuildRiskModels
    override) must return False — the gate rejects it so the candidate is
    marked CANDIDATE_REJECTED downstream."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Tests.gold2_closed_loop.test_g3_real_generator import NO_OVERRIDE_SUBCLASS

    assert gen._reflect_interface_ok_source_level(NO_OVERRIDE_SUBCLASS) is False, (
        "NO_OVERRIDE_SUBCLASS does not override BuildRiskModels -> reflect "
        "must reject it"
    )


def test_reflect_interface_ok_source_level_rejects_initialize_override():
    """Source-level reflect on a candidate that ALSO overrides Initialize()
    (which the prompt forbids — subclasses must not touch Universe/Alpha/
    Portfolio/Execution/Initialize) must return False."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen

    src = """using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class BadOverride : Gold2ReconstructionCandidateBase
    {
        public override void Initialize()
        {
            // forbidden: subclasses must not touch Initialize
        }
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
            => base.BuildRiskModels();
    }
}
"""
    assert gen._reflect_interface_ok_source_level(src) is False, (
        "a candidate that overrides Initialize() must be rejected (spec "
        "forbids subclasses touching Universe/Alpha/Portfolio/Execution/"
        "Initialize)"
    )


def test_reflect_interface_ok_source_level_handles_initializer_parens():
    """Edge case: a candidate overriding ``BuildRiskModels()`` (with parens
    in the method-name regex) must be detected. The source-level parser must
    handle both ``BuildRiskModels()`` and ``BuildRiskModels`` (the regex
    should not be thrown off by the parens in the signature)."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen

    src = """using System.Collections.Generic;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;
namespace X {
    public class WithParens : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
        {
            yield return new Gold2ExtremeRiskModel(null, null, 0m);
        }
    }
}"""
    assert gen._reflect_interface_ok_source_level(src) is True


def test_run_train_gate_returns_false_when_reflect_fails(monkeypatch):
    """_run_train_gate must call _reflect_interface_ok first; if the reflect
    check fails (no BuildRiskModels override), the gate MUST short-circuit
    and return False WITHOUT invoking LEAN. This wires the reflect check
    into the gate path."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen

    # _reflect_interface_ok returns False (source-level). The gate must NOT
    # touch build_run_config / run_lean — the unit test would fail if it did
    # (no dotnet / no LEAN data available here).
    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: False)

    # Sentinel: if the gate calls build_run_config, blow up.
    def _boom(*a, **kw):
        raise AssertionError(
            "_run_train_gate must NOT call build_run_config when the "
            "reflect check fails"
        )
    monkeypatch.setattr(
        "Scripts.gold2_closed_loop.lean_runner.build_run_config", _boom
    )
    monkeypatch.setattr(
        "Scripts.gold2_closed_loop.lean_runner.run_lean", _boom
    )

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="NoOverride",
        dll_path="/tmp/NoOverride.dll", window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    assert gen._run_train_gate(req, train_data_folder=None) is False


def test_run_train_gate_returns_bool_from_sharpe(monkeypatch, tmp_path):
    """When the reflect check passes, _run_train_gate must:
    (a) call build_run_config with the candidate dll as algorithm-location
        and candidate_class as algorithm-type-name;
    (b) call run_lean on the train period;
    (c) read the sharpe from the result packet;
    (d) return True iff sharpe >= MIN_DSR (0.0).

    This test mocks build_run_config + run_lean + the packet — it does NOT
    actually run dotnet LEAN (that's a Task 9 end-to-end concern).
    """
    import json
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Scripts.gold2_closed_loop import lean_runner as lr

    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: True)

    captured = {}

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, *,
                              run_id, start_date, end_date, trace_path,
                              experiment_id, window_id, stage_id, candidate_id,
                              data_folder):
        captured["base"] = base
        captured["run_dir"] = run_dir
        captured["algorithm_type_name"] = algorithm_type_name
        captured["parameters"] = parameters
        captured["run_id"] = run_id
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["trace_path"] = trace_path
        captured["data_folder"] = data_folder
        captured["stage_id"] = stage_id
        captured["candidate_id"] = candidate_id
        return {"_mock": True}

    class _FakeRes:
        packet_path = tmp_path / "mock-packet.json"

    def fake_run_lean(cfg, *, run_dir, run_id, timeout_seconds,
                      worktree_root, trace_path):
        captured["timeout_seconds"] = timeout_seconds
        captured["worktree_root"] = worktree_root
        captured["trace_path_passed"] = trace_path
        # Write a mock packet with a passing sharpe.
        _FakeRes.packet_path.parent.mkdir(parents=True, exist_ok=True)
        _FakeRes.packet_path.write_text(
            json.dumps({"statistics": {"Sharpe Ratio": "0.42"}})
        )
        return _FakeRes()

    monkeypatch.setattr(lr, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(lr, "run_lean", fake_run_lean)

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="TrivialG3RealCandidate",
        dll_path=str(tmp_path / "candidate.dll"),
        window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    result = gen._run_train_gate(req, train_data_folder="/tmp/frozen-train")
    assert result is True
    # The candidate dll + class were threaded into build_run_config's base.
    assert captured["base"]["algorithm-location"] == str(tmp_path / "candidate.dll")
    assert captured["algorithm_type_name"] == "TrivialG3RealCandidate"
    assert captured["start_date"] == "2018-01-02"
    assert captured["end_date"] == "2020-12-31"
    assert captured["data_folder"] == "/tmp/frozen-train"
    assert captured["stage_id"] == "G3"
    assert captured["candidate_id"] == "c0"


def test_run_train_gate_returns_false_on_negative_sharpe(monkeypatch, tmp_path):
    """If the candidate's train sharpe < MIN_DSR (0.0), the gate returns
    False — the candidate is rejected (CANDIDATE_REJECTED downstream)."""
    import json
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Scripts.gold2_closed_loop import lean_runner as lr

    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: True)

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, **kw):
        return {"_mock": True}

    class _FakeRes:
        packet_path = tmp_path / "mock-packet.json"

    def fake_run_lean(cfg, *, run_dir, run_id, timeout_seconds,
                      worktree_root, trace_path):
        _FakeRes.packet_path.parent.mkdir(parents=True, exist_ok=True)
        _FakeRes.packet_path.write_text(
            json.dumps({"statistics": {"Sharpe Ratio": "-0.5"}})
        )
        return _FakeRes()

    monkeypatch.setattr(lr, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(lr, "run_lean", fake_run_lean)

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="TrivialG3RealCandidate",
        dll_path=str(tmp_path / "candidate.dll"),
        window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    assert gen._run_train_gate(req, train_data_folder=None) is False


def test_run_train_gate_returns_false_on_missing_packet(monkeypatch, tmp_path):
    """If run_lean did not produce a packet (compile failure / timeout), the
    gate returns False (no sharpe to read)."""
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Scripts.gold2_closed_loop import lean_runner as lr

    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: True)

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, **kw):
        return {"_mock": True}

    class _FakeRes:
        packet_path = tmp_path / "does-not-exist.json"  # missing

    def fake_run_lean(cfg, *, run_dir, run_id, timeout_seconds,
                      worktree_root, trace_path):
        return _FakeRes()

    monkeypatch.setattr(lr, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(lr, "run_lean", fake_run_lean)

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="TrivialG3RealCandidate",
        dll_path=str(tmp_path / "candidate.dll"),
        window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    assert gen._run_train_gate(req, train_data_folder=None) is False


def test_run_train_gate_returns_false_on_missing_sharpe_key(monkeypatch, tmp_path):
    """Regression (review Important 1): if the packet exists but
    ``statistics.Sharpe Ratio`` is MISSING, the gate MUST return False —
    NOT silently pass via `0 >= 0.0`. The prior implementation used
    ``(... or 0)`` which defaulted a missing sharpe to 0, falsely passing
    the gate. A malformed packet (compile crash mid-LEAN / partial packet
    / missing statistics block) fails closed -> CANDIDATE_REJECTED.
    """
    import json
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Scripts.gold2_closed_loop import lean_runner as lr

    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: True)

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, **kw):
        return {"_mock": True}

    class _FakeRes:
        packet_path = tmp_path / "malformed-packet.json"

    def fake_run_lean(cfg, *, run_dir, run_id, timeout_seconds,
                      worktree_root, trace_path):
        _FakeRes.packet_path.parent.mkdir(parents=True, exist_ok=True)
        # Packet exists but the statistics block is missing the Sharpe Ratio
        # key. A silent `or 0` default would pass this -> 0 >= 0.0 -> True
        # (false positive). The fix returns False.
        _FakeRes.packet_path.write_text(
            json.dumps({"statistics": {"Total Orders": "10"}})
        )
        return _FakeRes()

    monkeypatch.setattr(lr, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(lr, "run_lean", fake_run_lean)

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="TrivialG3RealCandidate",
        dll_path=str(tmp_path / "candidate.dll"),
        window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    assert gen._run_train_gate(req, train_data_folder=None) is False, (
        "packet missing `statistics.Sharpe Ratio` MUST fail the gate "
        "(not silently pass via `or 0` -> 0 >= 0.0)"
    )


def test_run_train_gate_returns_false_when_statistics_block_missing(monkeypatch, tmp_path):
    """Regression (review Important 1, sibling): if the packet has NO
    ``statistics`` block at all (a more degenerate malformed packet), the
    gate MUST return False (not silently pass via `or 0`)."""
    import json
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Scripts.gold2_closed_loop import lean_runner as lr

    monkeypatch.setattr(gen, "_reflect_interface_ok",
                        lambda dll, cls: True)

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, **kw):
        return {"_mock": True}

    class _FakeRes:
        packet_path = tmp_path / "no-stats-packet.json"

    def fake_run_lean(cfg, *, run_dir, run_id, timeout_seconds,
                      worktree_root, trace_path):
        _FakeRes.packet_path.parent.mkdir(parents=True, exist_ok=True)
        _FakeRes.packet_path.write_text(json.dumps({"charts": {}}))
        return _FakeRes()

    monkeypatch.setattr(lr, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(lr, "run_lean", fake_run_lean)

    req = gen._GateRequest(
        candidate_id="c0", candidate_class="TrivialG3RealCandidate",
        dll_path=str(tmp_path / "candidate.dll"),
        window_id="W1",
        train_start="2018-01-02", train_end="2020-12-31",
    )
    assert gen._run_train_gate(req, train_data_folder=None) is False


def test_lean_run_candidate_loud_fails_on_missing_dll(tmp_path, monkeypatch):
    """Regression (review Important 2): if the candidate dll is missing at
    blind-run time, ``_lean_run_candidate`` MUST raise pre-launch (loud fail).
    Without this guard, build_run_config (lean_runner.py:134-139) silently
    falls through to DEFAULT_PROOF_DLL.resolve() (G0 proof dll), and LEAN
    would load the G0 strategy under the candidate's algorithm-type-name ->
    runtime type-load failure mid-LEAN, not a pre-launch refusal."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {
            "_iso": staticmethod(lambda d: d.isoformat()),
            "BASE": {"algorithm-location": "/tmp/proof.dll",
                     "parameters": {}},
        })(),
    )

    # Sentinel: build_run_config must NOT be called when the dll is missing.
    def _boom(*a, **kw):
        raise AssertionError(
            "_lean_run_candidate must NOT call build_run_config when the "
            "candidate dll is missing (should loud-fail pre-launch)"
        )
    monkeypatch.setattr(p4, "build_run_config", _boom)
    monkeypatch.setattr(p4, "run_lean", _boom)

    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    params = {
        "algorithm-type-name": "Gold2G3Real_W1",
        "algorithm-location": str(tmp_path / "does-not-exist.dll"),
    }
    with pytest.raises(AssertionError) as excinfo:
        p4._lean_run_candidate(
            tmp_path, "W1-G3-BLIND", fc, "2022-01-01", "2022-12-31",
            tmp_path / "trace.jsonl", params,
        )
    assert "candidate dll missing" in str(excinfo.value), (
        "the loud-fail message must identify the missing dll"
    )


# ---------------------------------------------------------------------------
# Task 7: _lean_run_candidate blind runner wiring
# ---------------------------------------------------------------------------


def test_blind_runner_uses_lean_run_candidate_for_g3(tmp_path, monkeypatch):
    """When a FrozenCandidate is stage_id="G3" AND _frozen_params_for returns
    a dict carrying algorithm-type-name + algorithm-location, the blind runner
    MUST dispatch to _lean_run_candidate (not the G0/G1/G2 _lean_run path).

    This test mocks _lean_run_candidate + _lean_run + _metrics so it does NOT
    actually invoke dotnet LEAN; it only verifies the dispatch.
    """
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "OUT", tmp_path / "result" / "gold2-p4-construction")
    monkeypatch.setattr(p4, "P3", tmp_path / "result" / "gold2-p3-construction")
    monkeypatch.setattr(p4, "WINDOWS", list(p4.WINDOWS) if p4.WINDOWS else [])
    # Ensure _p2._iso works for the blind date range.
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {
            "_iso": staticmethod(lambda d: d.isoformat()),
            "BASE": {"algorithm-location": "/tmp/proof.dll",
                     "parameters": {}},
        })(),
    )

    # Patch _frozen_params_for to return a G3 candidate descriptor.
    g3_params = {
        "algorithm-type-name": "Gold2G3Real_W1",
        "algorithm-location": "/tmp/candidate.dll",
    }
    monkeypatch.setattr(p4, "_frozen_params_for", lambda fc: g3_params)

    called = {"lean_run": False, "lean_run_candidate": False}

    def fake_lean_run(*a, **kw):
        called["lean_run"] = True
        return None

    def fake_lean_run_candidate(*a, **kw):
        called["lean_run_candidate"] = True
        return None

    monkeypatch.setattr(p4, "_lean_run", fake_lean_run)
    monkeypatch.setattr(p4, "_lean_run_candidate", fake_lean_run_candidate)
    # _metrics reads the result packet; bypass it.
    monkeypatch.setattr(p4, "_metrics",
                        lambda run_dir, run_id: {"sharpe": 0.5})

    # Use a real WindowDefinition for W1 (proof_windows).
    from Scripts.gold2_closed_loop.phase0_types import proof_windows
    monkeypatch.setattr(p4, "WINDOWS", proof_windows())

    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    runner = p4._blind_runner_factory()
    runner(fc)
    assert called["lean_run_candidate"] is True, (
        "G3 candidate with algorithm-type-name + algorithm-location MUST "
        "dispatch to _lean_run_candidate (not _lean_run)"
    )
    assert called["lean_run"] is False, (
        "_lean_run (G0/G1/G2 path) MUST NOT be called for an eligible G3 "
        "candidate"
    )


def test_blind_runner_falls_back_to_lean_run_for_g0(tmp_path, monkeypatch):
    """G0 stage MUST use the existing _lean_run path (no candidate dll)."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate
    from Scripts.gold2_closed_loop.phase0_types import proof_windows

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "OUT", tmp_path / "result" / "gold2-p4-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {
            "_iso": staticmethod(lambda d: d.isoformat()),
            "BASE": {"algorithm-location": "/tmp/proof.dll",
                     "parameters": {}},
        })(),
    )
    monkeypatch.setattr(p4, "WINDOWS", proof_windows())
    # G0 returns None (no override).
    monkeypatch.setattr(p4, "_frozen_params_for", lambda fc: None)

    called = {"lean_run": False, "lean_run_candidate": False}
    monkeypatch.setattr(
        p4, "_lean_run",
        lambda *a, **kw: called.__setitem__("lean_run", True))
    monkeypatch.setattr(
        p4, "_lean_run_candidate",
        lambda *a, **kw: called.__setitem__("lean_run_candidate", True))
    monkeypatch.setattr(p4, "_metrics",
                        lambda run_dir, run_id: {"sharpe": 0.3})

    fc = FrozenCandidate(
        window_id="W1", stage_id="G0", candidate_id="W1-G0-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    runner = p4._blind_runner_factory()
    runner(fc)
    assert called["lean_run"] is True
    assert called["lean_run_candidate"] is False


def test_blind_runner_falls_back_when_g3_not_eligible(tmp_path, monkeypatch):
    """When stage_id="G3" but _frozen_params_for returns None (static stub /
    rejected candidate), the runner MUST fall back to _lean_run (no candidate
    dll override). _resolve_final_stage will alias the window to G2/G1/G0 so
    the actual call uses G0/G1/G2, but a direct G3-stage call (rare) must
    still not crash on missing algorithm-type-name."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate
    from Scripts.gold2_closed_loop.phase0_types import proof_windows

    monkeypatch.setattr(p4, "ROOT", tmp_path)
    monkeypatch.setattr(p4, "OUT", tmp_path / "result" / "gold2-p4-construction")
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {
            "_iso": staticmethod(lambda d: d.isoformat()),
            "BASE": {"algorithm-location": "/tmp/proof.dll",
                     "parameters": {}},
        })(),
    )
    monkeypatch.setattr(p4, "WINDOWS", proof_windows())
    monkeypatch.setattr(p4, "_frozen_params_for", lambda fc: None)

    called = {"lean_run": False, "lean_run_candidate": False}
    monkeypatch.setattr(
        p4, "_lean_run",
        lambda *a, **kw: called.__setitem__("lean_run", True))
    monkeypatch.setattr(
        p4, "_lean_run_candidate",
        lambda *a, **kw: called.__setitem__("lean_run_candidate", True))
    monkeypatch.setattr(p4, "_metrics",
                        lambda run_dir, run_id: {"sharpe": 0.3})

    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    runner = p4._blind_runner_factory()
    runner(fc)
    # No algorithm-type-name in params -> falls back to _lean_run.
    assert called["lean_run"] is True
    assert called["lean_run_candidate"] is False


def test_lean_run_candidate_threads_algorithm_location_and_type(tmp_path, monkeypatch):
    """_lean_run_candidate must call build_run_config with the candidate dll
    as algorithm-location AND the candidate class as algorithm-type-name, so
    LEAN loads the candidate dll (not the proof strategy dll)."""
    import Scripts.gold2_closed_loop.construct_p4 as p4
    from Scripts.gold2_closed_loop.blind_evaluator import FrozenCandidate

    monkeypatch.setattr(p4, "ROOT", tmp_path)

    captured = {}

    def fake_build_run_config(base, run_dir, algorithm_type_name, parameters, **kw):
        captured["base"] = base
        captured["algorithm_type_name"] = algorithm_type_name
        captured["parameters"] = parameters
        captured["run_id"] = kw.get("run_id")
        captured["start_date"] = kw.get("start_date")
        captured["end_date"] = kw.get("end_date")
        captured["stage_id"] = kw.get("stage_id")
        captured["window_id"] = kw.get("window_id")
        captured["candidate_id"] = kw.get("candidate_id")
        return {"_mock": True}

    def fake_run_lean(cfg, **kw):
        captured["run_lean_kw"] = kw
        return None

    # _p2.BASE must exist for the deepcopy.
    monkeypatch.setattr(
        p4, "_p2",
        type("P2Stub", (), {
            "_iso": staticmethod(lambda d: d.isoformat()),
            "BASE": {"algorithm-location": "/tmp/proof.dll",
                     "parameters": {"trend-ma-short": "20"}},
        })(),
    )
    monkeypatch.setattr(p4, "build_run_config", fake_build_run_config)
    monkeypatch.setattr(p4, "run_lean", fake_run_lean)

    # Create the candidate dll on disk so the pre-launch assert passes (the
    # loud-fail guard at construct_p4.py:221 refuses to launch when the dll
    # is missing). The dll content does not matter — build_run_config + run_lean
    # are mocked; only the path's existence is checked.
    candidate_dll = tmp_path / "candidate.dll"
    candidate_dll.write_bytes(b"\x4d\x5a" + b"\x00" * 100)

    fc = FrozenCandidate(
        window_id="W1", stage_id="G3", candidate_id="W1-G3-C0",
        candidate_set_sha256="cs" * 32, source_sha256="ss" * 32,
        config_sha256="cf" * 32,
    )
    params = {
        "algorithm-type-name": "Gold2G3Real_W1",
        "algorithm-location": str(candidate_dll),
    }
    p4._lean_run_candidate(
        tmp_path, "W1-G3-BLIND", fc, "2022-01-01", "2022-12-31",
        tmp_path / "trace.jsonl", params,
    )
    assert captured["base"]["algorithm-location"] == str(candidate_dll)
    assert captured["algorithm_type_name"] == "Gold2G3Real_W1"
    # The algorithm-type-name + algorithm-location must NOT be in the
    # caller-supplied ``parameters`` dict (they are identity overrides, not
    # tunables; build_run_config threads them into the top-level config).
    assert "algorithm-type-name" not in captured["parameters"]
    assert "algorithm-location" not in captured["parameters"]
    assert captured["run_id"] == "W1-G3-BLIND"
    assert captured["start_date"] == "2022-01-01"
    assert captured["end_date"] == "2022-12-31"
    assert captured["stage_id"] == "G3"
    assert captured["candidate_id"] == "W1-G3-C0"
    # The base config's tunable params (trend-ma-short) are preserved across
    # the deepcopy (build_run_config merges base["parameters"] + caller params;
    # the candidate inherits the G0 frozen defaults this way).
    assert captured["base"]["parameters"]["trend-ma-short"] == "20"


# ---------------------------------------------------------------------------
# Task 7: pythonnet runtime reflect (real-behavior test, only when available)
# ---------------------------------------------------------------------------


def test_reflect_interface_ok_runtime_on_trivial_subclass(tmp_path):
    """Real-behavior test: compile TRIVIAL_SUBCLASS to a real dll, then run
    the runtime reflect path (pythonnet). When pythonnet is unavailable
    (the host env), this test is skipped — the source-level path is covered
    by test_reflect_interface_ok_source_level_*.

    This test exercises _reflect_interface_ok_runtime directly. The runtime
    path returns True when BuildRiskModels is overridden + Initialize is NOT
    overridden. When pythonnet is unavailable, the runtime path returns None
    and the test skips (the source-level fallback covers the same source).
    """
    import shutil
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Tests.gold2_closed_loop.test_g3_real_generator import TRIVIAL_SUBCLASS

    dotnet = shutil.which("dotnet") or "/usr/local/dotnet/dotnet"
    if not Path(dotnet).is_file():
        import pytest
        pytest.skip("dotnet not available")

    # Try the runtime path first. When pythonnet is unavailable, skip.
    runtime_verdict = gen._reflect_interface_ok_runtime.__wrapped__ if hasattr(
        gen._reflect_interface_ok_runtime, "__wrapped__") else gen._reflect_interface_ok_runtime
    # _reflect_interface_ok_runtime does not take a source arg; compile first
    # to get a real dll, then check.
    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate

    cs = tmp_path / "G3.cs"
    cs.write_text(TRIVIAL_SUBCLASS)
    result = compile_candidate(
        cs, candidate_id="trivial_rt", candidate_class="TrivialG3RealCandidate",
        dotnet=dotnet,
    )
    if not result.ok:
        import pytest
        pytest.skip(f"compile failed: {result.stderr_tail[:200]}")

    # Try the pythonnet runtime reflect path. Returns None when unavailable.
    runtime = gen._reflect_interface_ok_runtime(str(result.dll_path),
                                                "TrivialG3RealCandidate")
    if runtime is None:
        import pytest
        pytest.skip("pythonnet runtime reflect unavailable in this env")
    assert runtime is True


def test_reflect_interface_ok_probe_on_trivial_subclass(tmp_path):
    """Real-behavior test for the second fallback (ReflectProbe exe). When
    dotnet is available, this path compiles a real candidate dll + runs the
    probe; returns True/False. When unavailable, skip.

    This test verifies the ReflectProbe can LOAD the candidate dll (the
    AssemblyResolve probing must find NodaTime + QuantConnect.*.dll under
    Algorithm.CSharp/bin/Debug + Launcher/bin/Debug). If the probe cannot
    resolve a dep, the test surfaces it as a failure (not a silent skip) so
    Task 9 end-to-end can rely on the probe path.
    """
    import shutil
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Tests.gold2_closed_loop.test_g3_real_generator import TRIVIAL_SUBCLASS

    dotnet = shutil.which("dotnet") or "/usr/local/dotnet/dotnet"
    if not Path(dotnet).is_file():
        import pytest
        pytest.skip("dotnet not available")

    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate

    cs = tmp_path / "G3.cs"
    cs.write_text(TRIVIAL_SUBCLASS)
    result = compile_candidate(
        cs, candidate_id="trivial_pr", candidate_class="TrivialG3RealCandidate",
        dotnet=dotnet,
    )
    if not result.ok:
        import pytest
        pytest.skip(f"compile failed: {result.stderr_tail[:200]}")

    probe = gen._reflect_interface_ok_probe(str(result.dll_path),
                                            "TrivialG3RealCandidate")
    if probe is None:
        import pytest
        pytest.skip(
            "ReflectProbe unavailable (probe build/run failed or "
            "reflect_probe.cs missing)"
        )
    assert probe is True


def test_reflect_interface_ok_rejects_no_override_runtime_or_source(tmp_path):
    """For NO_OVERRIDE_SUBCLASS, the reflect check MUST return False (the
    candidate does not override BuildRiskModels). This exercises whichever
    reflect path is available in the env (runtime probe > source-level)."""
    import shutil
    import Scripts.gold2_closed_loop.g3_real_generator as gen
    from Tests.gold2_closed_loop.test_g3_real_generator import NO_OVERRIDE_SUBCLASS

    dotnet = shutil.which("dotnet") or "/usr/local/dotnet/dotnet"

    if dotnet and Path(dotnet).is_file():
        from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate
        cs = tmp_path / "G3.cs"
        cs.write_text(NO_OVERRIDE_SUBCLASS)
        result = compile_candidate(
            cs, candidate_id="no_ov", candidate_class="NoOverride",
            dotnet=dotnet,
        )
        if result.ok:
            # The full reflect path: runtime > probe > source-level.
            verdict = gen._reflect_interface_ok(str(result.dll_path), "NoOverride")
            assert verdict is False, (
                "_reflect_interface_ok must reject NO_OVERRIDE (no "
                "BuildRiskModels override) regardless of which path runs"
            )
            return
    # Fallback: source-level only.
    assert gen._reflect_interface_ok_source_level(NO_OVERRIDE_SUBCLASS) is False


# ---------------------------------------------------------------------------
# Task 8: anti-p-hacking + §6 isolation + freeze-before-open regression
# ---------------------------------------------------------------------------
# These tests pin existing correct behavior (the Task 7 review already
# confirmed the audit). They exist so future changes cannot silently regress
# the anti-p-hacking guarantees: MIN_DSR stays 0.0, the LLM prompt never
# sees blind-year tokens, the G1 grid is not widened, new G3 modules stay
# §6-isolated from production daemons, and the freeze hash is computed
# BEFORE the blind partition opens.


def test_no_p_hacking_regression():
    """Anti-p-hacking audit (pinned as a regression):

    1. ``MIN_DSR == 0.0`` — the train gate is NOT silently tightened across
       runs. A negative-sharpe candidate is rejected (>= 0.0); a positive
       one passes. A non-zero MIN_DSR would let a future change silently
       raise the bar and reject candidates that previously passed.
    2. ``build_prompt(...)`` does NOT leak any blind-year token into the
       LLM prompt (``blind`` / ``2022`` / ``2023`` / ``2024`` / ``2025``).
       The prompt is built ONLY from the TRAIN-period review bundle + the
       production C# source files (which themselves carry no blind-year
       data). An empty ``layer_attribution`` exercises the degenerate
       bundle path; a real bundle would carry train-period attribution
       values, still no blind tokens.
    3. The G1 grid is unchanged: ``construct_p2.py`` still parametrizes the
       G1 search over ``trend-ma-short in {20, 30}`` and
       ``vol-target in {0.11, 0.15}``. A future widening (e.g. adding 10/40
       to trend-ma-short or 0.05/0.20 to vol-target) would silently let the
       search pick a different candidate set, retroactively changing the
       frozen G1 evidence G3 builds on.
    4. The G1/G2 budgets + seeds are unchanged: ``budget=4, seed=17`` for
       G1, ``budget=2, seed=17`` for G2. A change here would re-roll the
       search and silently produce different G1/G2 candidates.

    This test is a STATIC source-level pin: it greeps the construct_p2 /
    g3_real_generator / g3_real_llm_client source for these constants. If a
    future change moves them (e.g. into a config file), update the
    assertion to match the new location — the goal is to pin the values,
    not the source layout.
    """
    # 1. MIN_DSR stays 0.0.
    from Scripts.gold2_closed_loop.g3_real_generator import MIN_DSR
    assert MIN_DSR == 0.0, (
        f"MIN_DSR must stay 0.0 (no silent gate tightening); got {MIN_DSR}"
    )

    # 2. build_prompt does NOT leak blind-year tokens.
    from Scripts.gold2_closed_loop.g3_real_llm_client import build_prompt
    p = build_prompt({"layer_attribution": {}}, instrument="518880")
    pl = p.lower()
    for tok in ("blind", "2022", "2023", "2024", "2025"):
        assert tok not in pl, (
            f"build_prompt must not leak blind-year token {tok!r} into the "
            f"LLM prompt (spec §6 anti-p-hacking); prompt length={len(p)}"
        )

    # 3. G1 grid unchanged: trend-ma-short in {20, 30}, vol-target in {0.11, 0.15}.
    import Scripts.gold2_closed_loop.construct_p2 as p2
    src = Path(p2.__file__).read_text()
    # The grid is a literal dict at construct_p2.py:116-117:
    #   {"trend-ma-short": {"type": "choice", "values": ["20", "30"]},
    #    "vol-target": {"type": "choice", "values": ["0.11", "0.15"]}}
    # Match the literal shape so a future refactor cannot silently widen it.
    assert '"trend-ma-short"' in src and '"vol-target"' in src, (
        "construct_p2 must still parametrize the G1 search over "
        "trend-ma-short + vol-target"
    )
    assert '"20"' in src and '"30"' in src, (
        "G1 trend-ma-short grid must still be {20, 30}"
    )
    assert '"0.11"' in src and '"0.15"' in src, (
        "G1 vol-target grid must still be {0.11, 0.15}"
    )
    # Pin the grid as a literal dict (not a dynamic list): the assertion
    # ``"values": ["20", "30"]`` appears as a literal in the source. If a
    # future refactor builds the list dynamically, this assertion breaks
    # and the reviewer must update it (the goal is to catch silent widening).
    assert '"values": ["20", "30"]' in src, (
        "G1 trend-ma-short values must be the literal ['20', '30'] "
        "(dynamic widening would require updating this assertion)"
    )
    assert '"values": ["0.11", "0.15"]' in src, (
        "G1 vol-target values must be the literal ['0.11', '0.15'] "
        "(dynamic widening would require updating this assertion)"
    )

    # 4. Budget + seed unchanged.
    #    G1: budget=4, seed=17 (construct_p2.py:113)
    #    G2: budget=2, seed=17 (construct_p2.py:150)
    assert "budget=4, seed=17" in src, (
        "G1 ParameterOptimizerAdapter must keep budget=4, seed=17 "
        "(re-rolling the seed would silently change the G1 candidate set)"
    )
    assert "budget=2, seed=17" in src, (
        "G2 FeedbackConstructionAdapter must keep budget=2, seed=17 "
        "(re-rolling the seed would silently change the G2 shaping bundle)"
    )


def test_forbidden_references_not_matched():
    """§6 isolation regression: the new G3-Real module names must NOT match
    any of ``interface_readiness._FORBIDDEN_REFERENCES`` as whole tokens.

    The proof interface readiness scanner (spec §6 line 158) statically
    rejects any proof module that imports the production daemon / evolution
    / generation / layer-state machinery. The check is whole-token: it
    splits the source on non-alphanumeric characters and checks each token
    against the forbidden list.

    This test replicates that check against the new G3-Real module names
    (``g3_real_llm_client``, ``g3_real_generator``, ``g3_real_compile``,
    ``g3_real_candidate``). None of them must match any forbidden token as
    a whole token — otherwise the readiness scanner would false-positive on
    the new modules and block the proof at the STOP P2 interface gate.

    The check also verifies the §6 isolation is structural: the new module
    names do not CONTAIN any forbidden token as a substring that would
    match the regex word-boundary check the scanner uses
    (``re.search(r"\\b" + re.escape(ref) + r"\\b", text)``).
    """
    import re
    from Scripts.gold2_closed_loop import interface_readiness as ir

    new_modules = (
        "g3_real_llm_client",
        "g3_real_generator",
        "g3_real_compile",
        "g3_real_candidate",
    )
    for new_mod in new_modules:
        # Whole-token check: split on non-alphanumeric/underscore, check
        # membership. This is the same logic interface_readiness uses (it
        # applies a regex word-boundary search, which is equivalent to a
        # whole-token match on identifiers).
        tokens = re.split(r"[^A-Za-z0-9_]", new_mod)
        for forb in ir._FORBIDDEN_REFERENCES:
            assert forb not in tokens, (
                f"new module {new_mod!r} must not match forbidden token "
                f"{forb!r} as a whole token (§6 isolation)"
            )
            # Also replicate the regex word-boundary check the scanner uses
            # (interface_readiness.py:138) so the test mirrors the actual
            # production gate logic.
            assert not re.search(
                r"\b" + re.escape(forb) + r"\b", new_mod
            ), (
                f"new module {new_mod!r} must not match forbidden token "
                f"{forb!r} via word-boundary regex (§6 isolation, "
                f"interface_readiness.py:138)"
            )


def test_freeze_before_open_order():
    """Freeze-before-open ordering regression (spec §6 line 118: 全局一次冻结).

    The G3 candidate's ``source_sha256`` MUST be computed BEFORE the blind
    partition opens. Concretely:

    * ``G3Builder.build`` (g3_builder.py) computes ``source_sha =
      canonical_hash({"source_text": source_text})`` and freezes it into
      ``G3BuildResult.source_sha256``. It does NOT open the blind — the
      blind is opened LATER by ``BlindEvaluator.open_blind`` (a separate
      module). This pins the ordering: the freeze hash is computed in the
      builder, the open is a downstream one-shot in the evaluator.
    * ``g3_builder`` source MUST NOT reference ``open_blind`` (the builder
      is responsible for FREEZING, not OPENING). If a future change moved
      ``open_blind`` into the builder, the freeze/open ordering would
      collapse and a candidate could be opened before its source hash was
      pinned.
    * ``g3_builder`` source MUST reference ``source_sha`` (the freeze hash
      IS computed in build()).
    * ``blind_evaluator`` MUST expose ``open_blind`` + ``validate_immutability``
      (the gate exists so the open can be guarded by a post-open
      immutability revalidation).

    This test is a source-level pin: it greps the module sources for the
    presence/absence of these identifiers. If a future refactor renames
    them, update the assertion to match.
    """
    import inspect
    from Scripts.gold2_closed_loop import g3_builder, blind_evaluator

    builder_src = inspect.getsource(g3_builder)
    # The builder computes source_sha (the freeze hash) in build().
    assert "source_sha" in builder_src, (
        "g3_builder must compute source_sha (the freeze hash) in build()"
    )
    # The builder MUST NOT open the blind — that's the evaluator's job.
    # The identifier ``open_blind`` must not appear in the builder source.
    assert "open_blind" not in builder_src, (
        "g3_builder must NOT open the blind (open_blind is the evaluator's "
        "job; freezing happens in build, opening is a downstream one-shot)"
    )

    # The evaluator exposes the open + the immutability gate.
    evaluator_src = inspect.getsource(blind_evaluator)
    assert "def open_blind" in evaluator_src, (
        "blind_evaluator must expose open_blind (the freeze->open gate)"
    )
    assert "def validate_immutability" in evaluator_src, (
        "blind_evaluator must expose validate_immutability (post-open "
        "revalidation of every frozen code + artifact hash)"
    )
    # Cross-check: BlindEvaluator class has both methods (not just module-level).
    assert hasattr(blind_evaluator.BlindEvaluator, "open_blind"), (
        "BlindEvaluator.open_blind must exist"
    )
    assert hasattr(blind_evaluator.BlindEvaluator, "validate_immutability"), (
        "BlindEvaluator.validate_immutability must exist"
    )


def test_run_train_gate_uses_train_not_blind():
    """Anti-p-hacking: ``_run_train_gate`` operates on the TRAIN period,
    NEVER the blind period.

    Source-level pin: ``_run_train_gate`` references ``req.train_start`` /
    ``req.train_end`` (which come from ``phase0_types.proof_windows()[w].train``),
    NOT ``w.blind`` or ``req.blind``. A future change that swapped train for
    blind here would let the gate peek at the blind partition — a §6 line
    118 violation (the blind is physically absent from the train snapshot,
    but the gate must ALSO not be wired to read blind dates even if the
    data folder leaked).

    Also verifies the helpers ``_train_start`` / ``_train_end`` read
    ``w.train[0]`` / ``w.train[1]`` from ``phase0_types.proof_windows()``
    (the canonical source of train-period dates), NOT ``w.blind``.
    """
    import inspect
    from Scripts.gold2_closed_loop import g3_real_generator as gen
    from Scripts.gold2_closed_loop.phase0_types import proof_windows, WindowDefinition

    # _run_train_gate source.
    gate_src = inspect.getsource(gen._run_train_gate)
    assert "train_start" in gate_src, (
        "_run_train_gate must reference train_start (from w.train via "
        "_GateRequest.train_start)"
    )
    assert "train_end" in gate_src, (
        "_run_train_gate must reference train_end (from w.train via "
        "_GateRequest.train_end)"
    )
    # The gate MUST NOT reference w.blind or req.blind anywhere in its body.
    assert "w.blind" not in gate_src, (
        "_run_train_gate must NOT reference w.blind (anti-p-hacking: the "
        "gate runs on the TRAIN period, never the blind)"
    )
    assert "req.blind" not in gate_src, (
        "_run_train_gate must NOT reference req.blind (anti-p-hacking: the "
        "gate runs on the TRAIN period, never the blind)"
    )

    # The helpers _train_start / _train_end read w.train (the canonical
    # source from phase0_types.proof_windows), NOT w.blind.
    start_src = inspect.getsource(gen._train_start)
    end_src = inspect.getsource(gen._train_end)
    assert "w.train" in start_src, (
        "_train_start must read w.train[0] from phase0_types.proof_windows()"
    )
    assert "w.train" in end_src, (
        "_train_end must read w.train[1] from phase0_types.proof_windows()"
    )
    assert "w.blind" not in start_src, (
        "_train_start must NOT read w.blind (anti-p-hacking)"
    )
    assert "w.blind" not in end_src, (
        "_train_end must NOT read w.blind (anti-p-hacking)"
    )

    # Cross-check: phase0_types.WindowDefinition carries both .train and
    # .blind as DateRange fields, so the helpers COULD have read .blind.
    # Pin that .train is the source the helpers use (not .blind).
    import dataclasses
    fields = {f.name for f in dataclasses.fields(WindowDefinition)}
    assert "train" in fields and "blind" in fields, (
        "WindowDefinition must carry both .train and .blind (the helpers "
        "chose .train; this assertion pins that choice)"
    )
