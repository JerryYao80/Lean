"""DSR (Deflated Sharpe Ratio) reward. Spec §5.3. López de Prado (2014).

The ``Sharpe Ratio`` carried in LEAN stats is *annualized*. The DSR/PSR
standard-error formula (López de Prado 2014) operates on the *per-period*
Sharpe, so we de-annualize by ``sharpe / sqrt(periods_per_year)`` before
applying the closed-form SE. ``TradingDays`` is the sample length T and
``periods_per_year`` defaults to 252 (daily, A-share convention).

---
auto-update5.md 修复: DSR 不再作为每-trial 的实时 reward (非平稳).
- ``compute_stationary_reward`` 返回平稳的原始 Sharpe 作为优化目标
  (贝叶斯优化代理模型要求目标函数是参数的稳定函数).
- ``compute_dsr_gate`` 在整轮搜索结束后, 用总 trial 数 N 对 champion
  trial 做一次性事后多重检验校正 (Bailey & López de Prado 原始用法).
- ``compute_reward`` 保留向后兼容 (仍能算 DSR), 但不再被 bayesian_optimizer
  当作 objective return 值.
"""
import math
from scipy.stats import norm

_DEFAULT_PERIODS_PER_YEAR = 252


def _to_float(val, default=0.0):
    """LEAN statistics 常带 '%' 或 ',' 后缀 (如 '2.900%', '1,234.5'), 转为 float."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return default


def _deannualize(sharpe, periods_per_year=_DEFAULT_PERIODS_PER_YEAR):
    return sharpe / math.sqrt(periods_per_year)


def compute_stationary_reward(stats: dict, reward_config=None):
    """平稳优化目标: 原始 Sharpe (非 annualized 调整, 直接用 LEAN 输出).

    贝叶斯优化的 GP+EI 要求目标函数 f(θ) 仅依赖参数 θ, 不依赖 trial 计数.
    原始 Sharpe 满足此条件. 返回 dict 含 sharpe + 辅助指标, 供 sampler 用.
    """
    sharpe = _to_float(stats.get("Sharpe Ratio", 0))
    n_orders = int(_to_float(stats.get("Total Orders", 1), 1))
    if n_orders == 0:
        return {"objective": float("-inf"), "sharpe": 0.0, "sortino": 0.0,
                "calmar": 0.0, "no_trades": True}
    sortino = _to_float(stats.get("Sortino Ratio", 0))
    # Drawdown from LEAN is a percentage string like "2.900%" → 0.029 as a fraction
    dd_pct = _to_float(stats.get("Drawdown", 0.01), 0.01)
    dd_fraction = dd_pct / 100.0 if dd_pct > 1 else dd_pct
    calmar = sharpe / max(dd_fraction, 0.01)
    # objective = 原始 Sharpe (平稳, 仅依赖 θ)
    return {"objective": sharpe, "sharpe": sharpe, "sortino": sortino,
            "calmar": calmar, "no_trades": False}


def compute_dsr_gate(stats: dict, n_trials_total: int, baseline_sharpe: float,
                     reward_config=None, gate_threshold: float = 0.0):
    """事后 DSR 门禁: 整轮搜索结束后对 champion trial 做一次性多重检验校正.

    Bailey & López de Prado 原始用法: 用总 trial 数 N deflate champion Sharpe,
    返回 DSR + 是否通过门禁 (DSR > threshold 才允许上线).
    """
    sharpe = _to_float(stats.get("Sharpe Ratio", 0))
    n_orders = int(_to_float(stats.get("Total Orders", 1), 1))
    if n_orders == 0:
        return {"dsr": float("-inf"), "passed": False, "reason": "no trades"}

    T = int(_to_float(stats.get("TradingDays", 252 * 4), 252 * 4))
    skew = _to_float(stats.get("ReturnSkew", 0))
    kurt = _to_float(stats.get("ReturnKurt", 3))

    if reward_config is not None:
        periods_per_year = reward_config.get("periods_per_year", _DEFAULT_PERIODS_PER_YEAR)
    else:
        periods_per_year = _DEFAULT_PERIODS_PER_YEAR

    sr = _deannualize(sharpe, periods_per_year)
    baseline_sr = _deannualize(baseline_sharpe, periods_per_year)

    se_sharpe = math.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr**2) / max(T - 1, 1))

    # 事后校正: 用总 trial 数 N (固定值, 不随当前 trial 变化)
    if n_trials_total <= 1:
        expected_max = baseline_sr
    else:
        expected_max = baseline_sr + se_sharpe * norm.ppf(1 - 1 / n_trials_total)

    dsr = norm.cdf((sr - expected_max) / se_sharpe) if se_sharpe > 0 else 0
    passed = dsr > gate_threshold
    return {"dsr": dsr, "passed": passed, "n_trials_used_for_deflation": n_trials_total,
            "expected_max": expected_max, "se_sharpe": se_sharpe}


def compute_reward(stats: dict, n_trials: int, baseline_sharpe: float, reward_config=None):
    """[已弃用作为 objective] 仍保留向后兼容, 但 bayesian_optimizer 不再用其返回值当 reward.

    auto-update5.md: 此函数的 DSR 依赖运行时 n_trials, 非平稳, 违反贝叶斯优化假设.
    新代码用 compute_stationary_reward (优化目标) + compute_dsr_gate (事后门禁).
    """
    sharpe = _to_float(stats.get("Sharpe Ratio", 0))
    n_orders = int(_to_float(stats.get("Total Orders", 1), 1))
    if n_orders == 0:
        return {"dsr": float("-inf"), "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0, "no_trades": True}

    T = int(_to_float(stats.get("TradingDays", 252 * 4), 252 * 4))
    skew = _to_float(stats.get("ReturnSkew", 0))
    kurt = _to_float(stats.get("ReturnKurt", 3))

    if reward_config is not None:
        periods_per_year = reward_config.get("periods_per_year", _DEFAULT_PERIODS_PER_YEAR)
    else:
        periods_per_year = _DEFAULT_PERIODS_PER_YEAR

    sr = _deannualize(sharpe, periods_per_year)
    baseline_sr = _deannualize(baseline_sharpe, periods_per_year)

    se_sharpe = math.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr**2) / max(T - 1, 1))

    if n_trials <= 1:
        expected_max = baseline_sr
    else:
        expected_max = baseline_sr + se_sharpe * norm.ppf(1 - 1 / n_trials)

    dsr = norm.cdf((sr - expected_max) / se_sharpe) if se_sharpe > 0 else 0
    sortino = _to_float(stats.get("Sortino Ratio", 0))
    dd_pct = _to_float(stats.get("Drawdown", 0.01), 0.01)
    dd_fraction = dd_pct / 100.0 if dd_pct > 1 else dd_pct
    calmar = sharpe / max(dd_fraction, 0.01)
    return {"dsr": dsr, "sharpe": sharpe, "sortino": sortino, "calmar": calmar, "no_trades": False}

