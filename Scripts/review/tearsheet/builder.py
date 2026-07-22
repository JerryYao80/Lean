"""HTML tearsheet from review.json alone. Spec §4.1. Jinja2 + inline, no CDN/plotly."""
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TPL_DIR = Path(__file__).parent / "templates"


def build_html(review_doc: dict, self_check: bool = False) -> str:
    """Render self-contained HTML from a review.json dict.

    self_check=True enforces the spec §2.2 invariant (sum(layer pnl_abs) ==
    total_closed_trade_pnl) before rendering — fail-fast.
    """
    if self_check:
        total = Decimal(str(review_doc["run_meta"]["total_closed_trade_pnl"]))
        layer_sum = sum(Decimal(str(v["pnl_abs"])) for v in review_doc["layer_attribution"].values())
        assert abs(layer_sum - total) < Decimal("0.01"), \
            f"sum invariant violated: sum={layer_sum} total={total}"
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), autoescape=False)
    tpl = env.get_template("gold2.html.j2")
    return tpl.render(**review_doc)
