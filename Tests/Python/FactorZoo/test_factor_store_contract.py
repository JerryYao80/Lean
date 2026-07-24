"""Guard: FactorStore (Phase 2) lives in QuantConnect.Factors.Store and does not
break the existing crowding C# contract (source-substring test in test_crowding_factor.py).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))


def test_factor_store_namespace_exists():
    store_dir = REPO / "Common" / "Factors" / "Store"
    assert store_dir.is_dir(), f"expected {store_dir} to exist (Phase 2)"
    assert (store_dir / "FactorStore.cs").exists()
    assert (store_dir / "IFactorAdapter.cs").exists()


def test_existing_crowing_contract_unchanged():
    alpha = REPO / "Algorithm.CSharp" / "Models" / "Alpha" / "CrowdingFactorZooAlphaModel.cs"
    if not alpha.exists():
        return
    src = alpha.read_text(encoding="utf-8")
    assert "FactorRegistry.Initialize" in src
    assert 'FactorRegistry.Get("crowding")' in src
    assert "InjectValue" in src
    assert "Py.GIL" in src


def test_factor_store_does_not_modify_factor_registry_core():
    reg = REPO / "Common" / "Factors" / "Core" / "FactorRegistry.cs"
    src = reg.read_text(encoding="utf-8")
    assert "CrowdingFactor" in src, "crowding registration must remain in FactorRegistry.Initialize"
