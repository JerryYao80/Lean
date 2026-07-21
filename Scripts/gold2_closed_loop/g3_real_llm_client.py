"""G3-Real LLM client: glm-5.2 via mydamoxing.cn/v1 (OpenAI-compatible).

PROOF-ONLY, independent. Does NOT depend on the production
soloquant_orchestrator / evolution_scheduler / layer_state / inspiration
modules (spec §6 isolation; interface_readiness._FORBIDDEN_REFERENCES
whole-token not matched). Mirrors the HTTP shape of
soloquant_orchestrator.summarize_with_llm without depending on it.

Anti-p-hacking: build_prompt consumes ONLY the TRAIN-period formal-review
bundle (construct_p2 runs G0 on w.train; feedback_construction uses
{wid}/train + {wid}/review only). No blind-year data enters the prompt.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

DEFAULT_BASE_URL = "https://mydamoxing.cn"
DEFAULT_MODEL = "glm-5.2"
# Token read from env GOLD2_G3_LLM_TOKEN; the default matches the
# user-provided proof token. Never logged.
DEFAULT_TOKEN = os.environ.get(
    "GOLD2_G3_LLM_TOKEN",
    "sk-Lqf1cFD3252DX3Jt4lXWa3C2lCD3U5bwSdFoUwUIJZWD6Dpw",
)

# Diagnosis derived from TRAIN-period review only. Speaks of regimes
# abstractly (bull-market / drawdown) — NOT specific OOS windows,
# so no out-of-sample data leaks into the prompt.
_DIAGNOSIS = """\
DIAGNOSIS (from TRAIN-period review bundle, NOT out-of-sample data):
G2 shaping caps the realrate layer in RISING_FAST regime unconditionally
(Gold2RealRateCapModel.cs: capFactor = regime==RISING_FAST ? effRealRateCap : 1.0).
This cut gains in bull-market windows AND gave no extra protection in drawdowns.
REQUIRED reconstruction: regime-ASYMMETRIC. When regime==RISING_FAST AND the trend
factor direction is UP, do NOT cap (capFactor=1.0, let winners run). Only tighten
on drawdown > threshold OR extreme_triggered. You MAY relax caps (not only add).
"""


def build_prompt(review_bundle: dict, *, instrument: str = "518880") -> str:
    """Build the LLM prompt from TRAIN-period bundle + source + tunable space.

    Anti-p-hacking: review_bundle is the TRAIN-period formal-review bundle
    (construct_p2 runs G0 on w.train; feedback_construction uses {wid}/train +
    {wid}/review only). No blind-year data enters this function.
    """
    attr = review_bundle.get("layer_attribution", {})
    src_root = Path(__file__).resolve().parents[2]
    strategy_src = (src_root / "Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs").read_text()
    ext_src = (src_root / "Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs").read_text()
    rr_src = (src_root / "Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs").read_text()
    base_src = (src_root / "Algorithm.CSharp/Models/Gold2/Reconstruction/Gold2ReconstructionCandidateBase.cs").read_text()
    return f"""You are reconstructing the Risk layer of a LEAN C# gold strategy.
Instrument: {instrument}

TRAIN-period review bundle layer_attribution (pnl_pct_of_total):
{attr}

{_DIAGNOSIS}

Tunable parameter names (G1 grid, do NOT widen): trend-ma-short, vol-target,
trend-ma-long, ewma-lambda, vol-warmup, smooth-alpha, rebalance-threshold,
extreme-vol-cap, realrate-cap, trend-floor, trend-disable.
Shaping penalties (G2-derived, default 1.0 = neutral): extreme_risk_contrib_penalty,
realrate_cap_contrib_penalty.

Existing strategy source:
{strategy_src}

Existing Risk Models:
{ext_src}
{rr_src}

Base class you MUST extend (override ONLY BuildRiskModels, do NOT touch
Universe/Alpha/Portfolio/Execution/Initialize):
{base_src}

OUTPUT: a single C# file. Define a subclass of Gold2ReconstructionCandidateBase
that overrides ONLY `protected override IEnumerable<IRiskManagementModel> BuildRiskModels()`.
You may define helper RiskManagementModel subclasses in the same file. The class
must compile against the Algorithm.CSharp project. Output ONLY the C# code,
no markdown fences, no explanation.
"""


def generate_reconstruction(prompt: str, *, base_url: str = DEFAULT_BASE_URL,
                            auth_token: str = DEFAULT_TOKEN, model: str = DEFAULT_MODEL,
                            timeout: int = 180) -> str:
    """POST to {base_url}/v1/chat/completions (OpenAI-compatible). Returns content string."""
    resp = requests.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.2},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
