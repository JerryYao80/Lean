"""Tests for build_catalog (Phase 4, spec §3.2). Hermetic."""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts" / "factor_zoo"))
import build_catalog as bc  # noqa: E402


def _freshness(tmp_path, entries: dict):
    p = tmp_path / "freshness.json"
    import json
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


def test_catalog_has_required_top_level_keys(tmp_path):
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=None, out_path=out)
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "version" in doc
    assert doc["universe"] == ["CSI300", "CSI500"]
    assert "factors" in doc


def test_catalog_contains_crowding_barra_beta_hv_20d(tmp_path):
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=None, out_path=out)
    ids = {f["id"] for f in yaml.safe_load(out.read_text(encoding="utf-8"))["factors"]}
    assert "crowding" in ids
    assert "barra_beta" in ids
    assert "hv_20d" in ids


def test_crowding_status_last_date_from_freshness(tmp_path):
    fr = _freshness(tmp_path, {"crowding": {"last_date": "20260723", "status": "fresh"}})
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=fr, out_path=out)
    crowding = next(f for f in yaml.safe_load(out.read_text(encoding="utf-8"))["factors"]
                    if f["id"] == "crowding")
    assert crowding["last_date"] == "20260723"
    assert crowding["status"] == "fresh"


def test_missing_freshness_yields_unknown_status_null_date(tmp_path):
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=None, out_path=out)
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    crowding = next(f for f in doc["factors"] if f["id"] == "crowding")
    assert crowding["status"] == "unknown"
    assert crowding["last_date"] is None


def test_every_factor_has_nonempty_selection_hint(tmp_path):
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=None, out_path=out)
    factors = yaml.safe_load(out.read_text(encoding="utf-8"))["factors"]
    for f in factors:
        assert f.get("selection_hint"), f"factor {f['id']} missing selection_hint"


def test_catalog_has_51_factors(tmp_path):
    out = tmp_path / "factor-catalog.yaml"
    bc.build_catalog(freshness_path=None, out_path=out)
    factors = yaml.safe_load(out.read_text(encoding="utf-8"))["factors"]
    # 36 FactorRegistry factors (Volatility 5 + Trend 3 + Value 3 + Quality 3 + Sentiment 3
    # + Liquidity 2 + Chip 5 + Forward 12) + 15 Barra = 51.
    assert len(factors) == 51, f"expected 51 factors, got {len(factors)}"
