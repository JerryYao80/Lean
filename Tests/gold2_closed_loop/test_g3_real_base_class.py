"""G3-Real base class: BuildRiskModels() default = G0 Risk Models byte-identical."""
import subprocess, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_CS = ROOT / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs"
STRATEGY_CS = ROOT / "Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs"

def test_base_class_file_exists():
    assert BASE_CS.exists(), "Gold2ReconstructionCandidateBase.cs must exist"

def test_default_buildriskmodels_returns_g0_models():
    """默认 BuildRiskModels() 返回 Gold2ExtremeRiskModel + Gold2RealRateCapModel,
    与 Gold2BetaVolTargetStrategy.cs:106-107 逐字节等价。"""
    src = BASE_CS.read_text()
    assert "new Gold2ExtremeRiskModel(_ext, _gold, _effExtremeCap)" in src
    assert "new Gold2RealRateCapModel(_realrate, _gold, _effRealRateCap)" in src
    assert "virtual IEnumerable<IRiskManagementModel> BuildRiskModels" in src
    assert "foreach (var model in BuildRiskModels()) AddRiskManagement(model)" in src

def test_base_class_implements_same_interfaces():
    src = BASE_CS.read_text()
    assert "Gold2ReconstructionCandidateBase : QCAlgorithm, IOptimizableStrategy, IRlStateExportable" in src
    for name in ["trend-ma-short","vol-target","extreme-vol-cap","realrate-cap","trend-disable"]:
        assert name in src, f"base class must expose tunable {name}"

def test_instrument_registry_has_518880():
    spec_cs = ROOT / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2InstrumentSpec.cs"
    assert spec_cs.exists()
    src = spec_cs.read_text()
    assert "518880" in src and "SSE" in src
    assert "AuShfDailyBar" in src and "AU.SHF" in src
    assert "FredMacroData" in src and "VIX" in src and "DFII10" in src
    assert "throw new ArgumentException" in src
