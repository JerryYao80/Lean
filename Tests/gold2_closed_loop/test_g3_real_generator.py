"""G3-Real compile harness: dotnet build candidate G3.cs -> dll + compiler_hash.

Task 4 covers the compile-harness layer (g3_real_compile.compile_candidate +
g3_real_candidate.csproj.tmpl). Task 5 will append generator tests on top of
this file (LLM -> compile -> gate -> GenerationResponse).
"""
import shutil
import sys
from pathlib import Path

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
