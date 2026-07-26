# data-source/tushare/alpha101/lookback.py
"""Static lookback walker: compute each formula's required panel window.

Window-adding operators (their `d` arg adds to the lookback):
  delay, delta, correlation, covariance, ts_min, ts_max, ts_argmax, ts_argmin,
  ts_rank, sum, product, stddev, decay_linear, min, max.
Non-adding: rank, scale, signedpower, abs, log, sign, indneutralize, np.where, arithmetic.
Nested -> sum of windows along each path; take the max across paths.
"""
from __future__ import annotations
import ast, inspect, math

WINDOW_OPS = {
    "delay", "delta", "correlation", "covariance", "ts_min", "ts_max",
    "ts_argmax", "ts_argmin", "ts_rank", "sum", "ts_sum", "product",
    "ts_product", "stddev", "decay_linear", "min", "max",
}


def _const(node) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _walk(node) -> float:
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None)
        if name in WINDOW_OPS:
            d = None
            if name in ("correlation", "covariance"):
                if len(node.args) >= 3:
                    d = _const(node.args[2])
            elif name in ("delay", "delta"):
                if len(node.args) >= 2:
                    d = _const(node.args[1])
            else:
                if len(node.args) >= 2:
                    d = _const(node.args[1])
            own = math.floor(d) if d is not None else 0
            child_max = max((_walk(a) for a in node.args), default=0)
            return own + child_max
        return max((_walk(a) for a in node.args), default=0)
    if isinstance(node, ast.BinOp):
        return max(_walk(node.left), _walk(node.right))
    if isinstance(node, ast.UnaryOp):
        return _walk(node.operand)
    if isinstance(node, ast.BoolOp):
        return max((_walk(v) for v in node.values), default=0)
    if isinstance(node, ast.Compare):
        vals = [node.left] + node.comparators
        return max((_walk(v) for v in vals), default=0)
    if isinstance(node, ast.IfExp):
        return max(_walk(node.test), _walk(node.body), _walk(node.orelse))
    if isinstance(node, ast.Subscript):
        return _walk(node.value)
    return 0


def required_lookback(func) -> int:
    src = inspect.getsource(func).lstrip()
    tree = ast.parse(src)
    ret_max = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            ret_max = max(ret_max, _walk(node.value))
    return int(ret_max) + 2  # +2 for pct_change / warm-up margin


MAX_LOOKBACK = 270  # alpha_19/39 need 250, alpha_32 needs 230, +margin
