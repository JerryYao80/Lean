# Tests/Python/FactorZoo/test_alpha101_indclass_levels.py
"""Assert INDCLASS_LEVELS matches the actual indneutralize calls in each formula."""
import sys, inspect, ast
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import formulas as F  # noqa: E402


def _ind_calls(func) -> list[str]:
    """Extract the level string from each top-level indneutralize(x, _ind(p, "level")) call.

    A nested indneutralize (i.e. one whose own args contain another indneutralize)
    is *not* double-counted: alpha_100 wraps `indneutralize(indneutralize(inner, "sub"), "sub")`,
    which the spec table records as 2 occurrences, not 3. We therefore record only
    indneutralize calls that are not themselves nested inside another indneutralize.
    """
    src = inspect.getsource(func)
    tree = ast.parse(src)
    levels = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and getattr(child.func, "id", None) == "indneutralize":
                # second arg is _ind(p, "level") — a Call whose last arg is a Constant str
                if len(child.args) >= 2 and isinstance(child.args[1], ast.Call):
                    inner = child.args[1]
                    if inner.args and isinstance(inner.args[-1], ast.Constant):
                        levels.append(inner.args[-1].value)
                # Do not descend into this indneutralize's subtree; nested
                # indneutralize calls are attributed to this outer occurrence.
                continue
            visit(child)

    visit(tree)
    return levels


@pytest.mark.parametrize("n", sorted(F.INDCLASS_LEVELS))
def test_levels_match(n):
    fn = getattr(F, f"alpha_{n:03d}")
    actual = _ind_calls(fn)
    expected = F.INDCLASS_LEVELS[n]
    assert actual == expected, f"alpha_{n:03d}: AST found {actual}, table says {expected}"


def test_no_unlisted_indneutralize():
    for i in range(1, 102):
        fn = getattr(F, f"alpha_{i:03d}")
        if _ind_calls(fn):
            assert i in F.INDCLASS_LEVELS, f"alpha_{i:03d} calls indneutralize but is not in INDCLASS_LEVELS"
