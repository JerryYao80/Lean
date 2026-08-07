# Tests/Python/FactorZoo/test_alpha101_descriptions.py
"""Validate alpha101_descriptions.yaml: 101 entries x 4 fields."""
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[3]
DESC_PATH = REPO / "Scripts" / "factor_zoo" / "alpha101_descriptions.yaml"

FAMILIES = {
    "量价反转", "VWAP反转", "量价相关", "波动率", "动量反转", "开收结构",
    "极值反转", "趋势条件", "行业中性", "量能异动", "价量协方差",
    "高低量相关", "复合时序",
}
DIRECTIONS = {"正向", "反向", "中性"}


def _load():
    return yaml.safe_load(DESC_PATH.read_text(encoding="utf-8"))


def test_descriptions_cover_all_101():
    doc = _load()
    missing = [n for n in range(1, 102) if f"alpha{n:03d}" not in doc]
    assert not missing, f"missing alphas: {missing}"


def test_each_entry_has_4_fields_nonempty():
    doc = _load()
    for aid, entry in doc.items():
        assert entry.get("intent"), f"{aid} missing intent"
        assert entry.get("scenarios"), f"{aid} missing scenarios"
        assert entry.get("direction"), f"{aid} missing direction"
        assert entry.get("family"), f"{aid} missing family"


def test_direction_in_valid_set():
    doc = _load()
    for aid, entry in doc.items():
        assert entry["direction"] in DIRECTIONS, f"{aid} bad direction {entry['direction']}"


def test_family_in_13_families():
    doc = _load()
    for aid, entry in doc.items():
        assert entry["family"] in FAMILIES, f"{aid} bad family {entry['family']}"


def test_scenarios_is_list_nonempty():
    doc = _load()
    for aid, entry in doc.items():
        s = entry.get("scenarios")
        assert isinstance(s, list) and len(s) >= 1, f"{aid} scenarios not nonempty list"


def test_no_extra_keys():
    doc = _load()
    allowed = {"intent", "scenarios", "direction", "family"}
    for aid, entry in doc.items():
        extra = set(entry.keys()) - allowed
        assert not extra, f"{aid} has extra keys {extra}"
