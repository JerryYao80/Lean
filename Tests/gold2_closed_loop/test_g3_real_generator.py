"""G3-Real compile harness + generator: dotnet build candidate G3.cs -> dll +
compiler_hash; G3RealGenerator (LLM -> compile -> train gate -> GenerationResponse)
+ interface check.

Task 4 covers the compile-harness layer (g3_real_compile.compile_candidate +
g3_real_candidate.csproj.tmpl). Task 5 appends generator tests on top of this
file: the generator orchestrates LLM -> strip -> write G3.cs -> compile ->
_run_train_gate -> _Response (a GenerationResponse). LLM/compile/gate are
mocked; no real network/dotnet/LEAN in Task 5 unit tests.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# A trivially-correct subclass: overrides BuildRiskModels() => base.BuildRiskModels()
# (byte-identical to G0). Used to exercise the .csproj + dotnet build path without
# requiring the LLM or a regime-asymmetric reconstruction.
TRIVIAL_SUBCLASS = '''using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class TrivialG3RealCandidate : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
            => base.BuildRiskModels();
    }
}
'''

# A subclass that does NOT override BuildRiskModels() — interface violation.
# The train gate's reflect check must reject it (training_gate_passed=False).
NO_OVERRIDE_SUBCLASS = '''using System.Collections.Generic;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class NoOverride : Gold2ReconstructionCandidateBase { }
}'''


def _resolve_dotnet() -> str:
    """Return a usable dotnet executable path.

    Prefer an explicit /usr/local/dotnet/dotnet install (the documented location
    in this environment), then fall back to PATH lookup. Returns "" if neither
    resolves.
    """
    candidate = Path("/usr/local/dotnet/dotnet")
    if candidate.exists() and candidate.is_file():
        return str(candidate)
    on_path = shutil.which("dotnet")
    return on_path or ""


def test_compile_trivial_subclass_succeeds(tmp_path):
    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate
    cs = tmp_path / "G3.cs"
    cs.write_text(TRIVIAL_SUBCLASS)
    dotnet = _resolve_dotnet()
    if not dotnet:
        import pytest
        pytest.skip("dotnet not available on PATH or /usr/local/dotnet/dotnet")
    result = compile_candidate(
        cs,
        candidate_id="trivial",
        candidate_class="TrivialG3RealCandidate",
        dotnet=dotnet,
    )
    assert result.ok, f"compile failed: {result.stderr_tail}"
    assert result.dll_path.exists()
    assert result.compiler_hash and len(result.compiler_hash) == 64
    # hex sha256 only
    assert all(c in "0123456789abcdef" for c in result.compiler_hash)
    assert result.candidate_class == "TrivialG3RealCandidate"


def test_compile_broken_source_fails(tmp_path):
    from Scripts.gold2_closed_loop.g3_real_compile import compile_candidate
    cs = tmp_path / "G3.cs"
    cs.write_text("this is not C#")
    dotnet = _resolve_dotnet()
    if not dotnet:
        import pytest
        pytest.skip("dotnet not available on PATH or /usr/local/dotnet/dotnet")
    result = compile_candidate(
        cs,
        candidate_id="broken",
        candidate_class="X",
        dotnet=dotnet,
    )
    assert not result.ok
    assert not result.dll_path.exists()
    assert result.compiler_hash == ""
    assert result.candidate_class == "X"
    # stderr_tail should carry useful compiler output (not empty)
    assert result.stderr_tail, "expected non-empty stderr_tail on compile failure"


# ---------------------------------------------------------------------------
# Task 5: G3RealGenerator (LLM -> compile -> train gate -> GenerationResponse)
# ---------------------------------------------------------------------------


def _fake_request(monkeypatch, tmp_path, *, candidate_id, candidate_class):
    """Build a G3Request-shaped object the generator can consume.

    The generator only reads request.candidate_id, request.window_id, and
    request.input_evidence_sha256 (for _load_train_bundle via window_id), so
    a light fake with sensible defaults is enough. Returns the request and
    the proof_root (under tmp_path).
    """
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class _Req:
        experiment_id: str = "E1"
        window_id: str = "W1"
        stage_id: str = "G3"
        candidate_id: str = ""
        partition: str = "W1/train"
        input_evidence_sha256: str = "a" * 64
        parent_generation_id: str | None = None
        generation_index: int = 1
        seed: int = 17
        budget: int = 4
        g2_candidate_set_sha256: str | None = None

    return _Req(candidate_id=candidate_id)


def test_generator_returns_generation_response_shape(monkeypatch, tmp_path):
    """G3RealGenerator.__call__ returns a GenerationResponse with the four
    Protocol fields (source_text, compiler_hash, config_hash,
    training_gate_passed) PLUS the candidate_class / dll_path extras Task 6
    threads into G3BuildResult. LLM, compile, and _run_train_gate are mocked.
    """
    from Scripts.gold2_closed_loop import g3_real_generator as gen

    monkeypatch.setattr(gen, "_call_llm", lambda prompt, **kw: TRIVIAL_SUBCLASS)

    def fake_compile(g3_cs, *, candidate_id, candidate_class, **kw):
        dll = g3_cs.parent / "bin" / f"G3Real_{candidate_id}.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"\x4d\x5a" + b"\x00" * 100)
        return gen.CompileResult(
            ok=True, dll_path=dll, compiler_hash="a" * 64,
            candidate_class=candidate_class, stderr_tail="",
        )

    monkeypatch.setattr(gen, "compile_candidate", fake_compile)
    monkeypatch.setattr(gen, "_run_train_gate", lambda *a, **kw: True)

    g = gen.G3RealGenerator(proof_root=tmp_path, train_data_folder=None)
    req = _fake_request(monkeypatch, tmp_path,
                        candidate_id="c0",
                        candidate_class="TrivialG3RealCandidate")
    resp = g(req)

    # _strip_fences normalizes trailing whitespace, so compare stripped forms.
    assert resp.source_text.strip() == TRIVIAL_SUBCLASS.strip()
    assert resp.compiler_hash == "a" * 64
    assert isinstance(resp.config_hash, str) and len(resp.config_hash) == 64
    assert resp.training_gate_passed is True
    # Task 6 will thread these into G3BuildResult; Task 5 carries them.
    assert resp.candidate_class == "TrivialG3RealCandidate"
    assert str(resp.dll_path).endswith(f"G3Real_c0.dll")

    # The generator must have written G3.cs into the candidate_dir computed
    # from proof_root + candidate_id (matching g3_builder's convention).
    expected_cs = tmp_path / "candidates" / "c0" / "G3.cs"
    assert expected_cs.exists(), (
        f"generator must write G3.cs at {expected_cs}; got "
        f"{tmp_path / 'candidates'} listing: {list((tmp_path / 'candidates').rglob('*'))}"
    )
    assert expected_cs.read_text().strip() == TRIVIAL_SUBCLASS.strip()


def test_extract_class_name_picks_candidate_subclass_not_first_class():
    """_extract_class_name must return the class extending
    Gold2ReconstructionCandidateBase, NOT the first class in the source.

    The prompt allows helper RiskManagementModel subclasses in the same file,
    and the LLM may emit them in any order. If a helper (e.g.
    ``public class HelperRiskModel : RiskManagementModel``) is declared BEFORE
    the candidate subclass, the naive first-class regex would pick the helper
    and pin it into ``config_hash``'s ``algorithm_type_name``. Task 7's
    reflect would then fail to find ``BuildRiskModels`` on the helper ->
    false CANDIDATE_REJECTED. This test pins the fix.

    Also covers the generic-declaration edge case (``class Foo<T> : Base``).
    """
    from Scripts.gold2_closed_loop.g3_real_generator import _extract_class_name

    # Helper RiskManagementModel BEFORE the candidate subclass (the bug).
    src_helper_first = """
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2.Reconstruction
{
    public class HelperRiskModel : RiskManagementModel
    {
        public override IEnumerable<IRiskManagementModel> ManageRisk(
            QCAlgorithmFramework algorithm)
        {
            return base.ManageRisk(algorithm);
        }
    }

    public class Gold2G3Real_W1 : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
        {
            yield return new HelperRiskModel();
        }
    }
}
"""
    assert _extract_class_name(src_helper_first) == "Gold2G3Real_W1", (
        "_extract_class_name must return the Gold2ReconstructionCandidateBase "
        "subclass, not the first-declared helper RiskManagementModel"
    )

    # Candidate BEFORE the helper (original happy path; must still work).
    src_candidate_first = """
namespace X {
    public class TrivialG3RealCandidate : Gold2ReconstructionCandidateBase
    {
        protected override IEnumerable<IRiskManagementModel> BuildRiskModels()
            => base.BuildRiskModels();
    }
    public class HelperRiskModel : RiskManagementModel { }
}
"""
    assert _extract_class_name(src_candidate_first) == "TrivialG3RealCandidate"

    # Generic declaration: ``class Foo<T> : Gold2ReconstructionCandidateBase``.
    src_generic = (
        "namespace X { public class Foo<T> : "
        "Gold2ReconstructionCandidateBase { } }"
    )
    assert _extract_class_name(src_generic) == "Foo", (
        "_extract_class_name must handle generic declarations "
        "(class Foo<T> : Base)"
    )

    # No candidate subclass present -> None (caller treats as
    # contract-violation / non-C# response).
    src_no_candidate = (
        "namespace X { public class Helper : RiskManagementModel { } }"
    )
    assert _extract_class_name(src_no_candidate) is None

    # No class at all (e.g. LLM apology) -> None.
    assert _extract_class_name("sorry, I cannot help") is None


def test_interface_violation_no_buildriskmodels_override_rejected(monkeypatch, tmp_path):
    """A subclass that does NOT override BuildRiskModels() must fail the train
    gate (reflect check rejects), so training_gate_passed=False downstream
    maps to CANDIDATE_REJECTED."""
    from Scripts.gold2_closed_loop import g3_real_generator as gen

    monkeypatch.setattr(gen, "_call_llm", lambda prompt, **kw: NO_OVERRIDE_SUBCLASS)

    def fake_compile(g3_cs, *, candidate_id, candidate_class, **kw):
        dll = g3_cs.parent / "bin" / f"G3Real_{candidate_id}.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"\x4d\x5a" + b"\x00" * 100)
        return gen.CompileResult(
            ok=True, dll_path=dll, compiler_hash="b" * 64,
            candidate_class=candidate_class, stderr_tail="",
        )

    monkeypatch.setattr(gen, "compile_candidate", fake_compile)
    # Gate returns False: the candidate compiled but the reflect check found
    # no BuildRiskModels override -> CANDIDATE_REJECTED downstream.
    monkeypatch.setattr(gen, "_run_train_gate", lambda *a, **kw: False)

    g = gen.G3RealGenerator(proof_root=tmp_path, train_data_folder=None)
    req = _fake_request(monkeypatch, tmp_path,
                        candidate_id="c1",
                        candidate_class="NoOverride")
    resp = g(req)

    assert resp.training_gate_passed is False
    assert resp.candidate_class == "NoOverride"
    assert resp.compiler_hash == "b" * 64
    # G3.cs still written (so the frozen source is hash-pinned per spec §7).
    expected_cs = tmp_path / "candidates" / "c1" / "G3.cs"
    assert expected_cs.exists()
    assert expected_cs.read_text().strip() == NO_OVERRIDE_SUBCLASS.strip()
