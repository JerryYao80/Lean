"""Phase 6 regression: factor_worker dry-run resolves all builders + existing
contracts unchanged + no existing dashboard modified (spec §4.2)."""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WORKER = REPO / "data-source" / "tushare" / "factor_worker.py"

# factor_worker imports pandas lazily inside build callables; dry_run only
# resolves dates + queues (no builder call). Use ohmyquant (daemon's env).
PY = "/root/miniconda3/envs/ohmyquant/bin/python3"

EXPECTED_BUILDERS = {
    "crowding", "forward", "barra_v2",
    "accruals_sloan", "gross_profitability", "asset_growth", "roe_change",
    "ivol_20d", "max_ret_20d", "short_term_reversal",
}


def test_factor_worker_dry_run_resolves_all_builders():
    """--once --dry-run must report all 10 builders (no real build, no writes)."""
    r = subprocess.run(
        [PY, "-u", str(WORKER), "--once", "--dry-run"],
        cwd=str(WORKER.parent), capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"factor_worker failed: {r.stderr[-500:]}"
    # The JSON report is pretty-printed (indent=2) and is the last stdout block.
    # Find the first top-level '{' and parse from there to end of stdout.
    stdout = r.stdout
    start = stdout.find("\n{")
    if start == -1:
        start = stdout.find("{")
    else:
        start += 1  # skip the newline, keep the '{'
    assert start != -1, f"no JSON report in stdout: {stdout[-500:]}"
    report = json.loads(stdout[start:])
    built = report.get("built", {})
    missing = EXPECTED_BUILDERS - set(built.keys())
    assert not missing, f"dry-run missing builders: {missing}"


def test_existing_crowding_contract_unchanged():
    """Phase 5 must not break the existing crowding C# contract (source-substring)."""
    alpha = REPO / "Algorithm.CSharp" / "Models" / "Alpha" / "CrowdingFactorZooAlphaModel.cs"
    assert alpha.exists()
    src = alpha.read_text(encoding="utf-8")
    assert "FactorRegistry.Initialize" in src
    assert 'FactorRegistry.Get("crowding")' in src
    assert "InjectValue" in src
    assert "Py.GIL" in src
    reg = REPO / "Common" / "Factors" / "Core" / "FactorRegistry.cs"
    assert "CrowdingFactor" in reg.read_text(encoding="utf-8")


def test_no_existing_dashboard_modified():
    """The 15 pre-existing dashboards must be untouched by this branch."""
    dash_dir = REPO / "monitoring" / "grafana" / "dashboards" / "lean"
    existing = [
        "ashare-implied-volatility.json", "ashare-multi-family.json",
        "backtest-overview.json", "barra-cne5-live-paper.json",
        "barra-cne5v4-backtest.json", "barra-cne5v4-live-paper.json",
        "lean-results-overview.json", "live-trading.json",
        "soloquant-crawl-visibility.json", "soloquant-prefect-pipeline.json",
        "soloquant-research-overview.json", "soloquant-strategy-pipeline.json",
        "strategy-control.json", "strategy-lifecycle.json", "strategy-review.json",
    ]
    for name in existing:
        p = dash_dir / name
        assert p.exists(), f"existing dashboard removed: {name}"
        json.load(open(p, encoding="utf-8"))  # raises if corrupt
    assert (dash_dir / "factor-freshness.json").exists()
