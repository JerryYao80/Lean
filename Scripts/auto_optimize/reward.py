"""DSR (Deflated Sharpe Ratio) reward. Spec §5.3. López de Prado (2014).

The ``Sharpe Ratio`` carried in LEAN stats is *annualized*. The DSR/PSR
standard-error formula (López de Prado 2014) operates on the *per-period*
Sharpe, so we de-annualize by ``sharpe / sqrt(periods_per_year)`` before
applying the closed-form SE. ``TradingDays`` is the sample length T and
``periods_per_year`` defaults to 252 (daily, A-share convention).
"""
import math
from scipy.stats import norm

_DEFAULT_PERIODS_PER_YEAR = 252


def compute_reward(stats: dict, n_trials: int, baseline_sharpe: float, reward_config=None):
    sharpe = stats.get("Sharpe Ratio", 0) or 0
    n_orders = stats.get("Total Orders", 1)
    if n_orders == 0:
        return {"dsr": float("-inf"), "sharpe": 0, "sortino": 0, "calmar": 0, "no_trades": True}

    T = stats.get("TradingDays", 252 * 4)
    skew = stats.get("ReturnSkew", 0)
    kurt = stats.get("ReturnKurt", 3)

    if reward_config is not None:
        periods_per_year = reward_config.get("periods_per_year", _DEFAULT_PERIODS_PER_YEAR)
    else:
        periods_per_year = _DEFAULT_PERIODS_PER_YEAR

    # De-annualize Sharpe to per-period for the SE formula (López de Prado 2014).
    sr = sharpe / math.sqrt(periods_per_year)
    baseline_sr = baseline_sharpe / math.sqrt(periods_per_year)

    se_sharpe = math.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr**2) / max(T - 1, 1))

    if n_trials <= 1:
        expected_max = baseline_sr
    else:
        expected_max = baseline_sr + se_sharpe * norm.ppf(1 - 1 / n_trials)

    dsr = norm.cdf((sr - expected_max) / se_sharpe) if se_sharpe > 0 else 0
    sortino = stats.get("Sortino Ratio", 0) or 0
    dd = stats.get("Drawdown", 0.01) or 0.01
    calmar = sharpe / max(dd, 0.01)
    return {"dsr": dsr, "sharpe": sharpe, "sortino": sortino, "calmar": calmar, "no_trades": False}
