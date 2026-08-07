import json
import unittest
from pathlib import Path


class GrafanaAssetsTests(unittest.TestCase):
    def dashboard_root(self) -> Path:
        return Path(__file__).resolve().parents[3] / "monitoring" / "grafana" / "dashboards" / "lean"

    def load_dashboard(self, name: str) -> dict:
        dashboard_path = self.dashboard_root() / name
        self.assertTrue(dashboard_path.exists(), f"Missing dashboard: {dashboard_path}")
        return json.loads(dashboard_path.read_text(encoding="utf-8"))

    def assert_uses_project_influx_datasource(self, dashboard: dict):
        encoded = json.dumps(dashboard, ensure_ascii=False)
        self.assertIn("lean-influxdb", encoded)
        self.assertNotIn("InfluxDB-Quant", encoded)

    def test_soloquant_dashboard_references_expected_measurements_without_secrets(self):
        dashboard = self.load_dashboard("soloquant-research-overview.json")
        encoded = json.dumps(dashboard, ensure_ascii=False)

        self.assertEqual(dashboard["uid"], "soloquant-research-overview")
        self.assertIn("soloquant_research_artifact", encoded)
        self.assertIn("soloquant_finance_event_node", encoded)
        self.assertIn("soloquant_finance_event_edge", encoded)
        self.assertIn("soloquant_strategy_result", encoded)
        self.assertIn("Strategy Backtest vs Live Paper Returns", encoded)
        self.assertIn("strategy_id", encoded)
        self.assertIn("/d/lean-backtest-overview/backtest-overview?var-algorithm_id=${strategy_id}", encoded)
        self.assertIn("/d/lean-live-trading/live-paper-trading?var-algorithm_id=${strategy_id}", encoded)
        self.assertIn("Finance Events by Source Type", encoded)
        self.assertIn("Finance Events by Risk Level", encoded)
        self.assertIn("Finance Entities by Type", encoded)
        self.assertIn("Finance Themes", encoded)
        self.assertIn("source_type", encoded)
        self.assertIn("risk_level", encoded)
        self.assertIn("entity_type", encoded)
        self.assertIn("theme", encoded)
        self.assertNotIn("admin-token", encoded)
        self.assertNotIn("sk-", encoded)
        self.assertNotIn("glsa_", encoded)

    def test_backtest_overview_dashboard_is_projectized_from_lean_grafana(self):
        dashboard = self.load_dashboard("backtest-overview.json")
        encoded = json.dumps(dashboard, ensure_ascii=False)
        titles = {panel["title"] for panel in dashboard["panels"]}

        self.assertEqual(dashboard["uid"], "lean-backtest-overview")
        self.assertEqual(dashboard["title"], "Backtest Overview")
        self.assertTrue({"Equity Curve", "Drawdown", "Orders", "Statistics", "Final Equity", "Max Drawdown", "Total Orders", "Net Profit"}.issubset(titles))
        self.assertIn("algorithm_id", encoded)
        self.assertIn("lean_chart", encoded)
        self.assertIn("lean_metric", encoded)
        self.assertIn("lean_order", encoded)
        self.assertIn("backtesting", encoded)
        self.assert_uses_project_influx_datasource(dashboard)

    def test_live_paper_trading_dashboard_is_projectized_from_lean_grafana(self):
        dashboard = self.load_dashboard("live-trading.json")
        encoded = json.dumps(dashboard, ensure_ascii=False)
        titles = {panel["title"] for panel in dashboard["panels"]}

        self.assertEqual(dashboard["uid"], "lean-live-trading")
        self.assertEqual(dashboard["title"], "Live Paper Trading")
        self.assertTrue({"Live Equity", "Current Equity", "Current Positions", "Order Flow", "Runtime Statistics", "Cash Balance", "Position Value", "Unrealized P&L"}.issubset(titles))
        self.assertIn("algorithm_id", encoded)
        self.assertIn("lean_holding", encoded)
        self.assertIn("lean_metric", encoded)
        self.assertIn("live", encoded)
        self.assert_uses_project_influx_datasource(dashboard)

    def test_grafana_install_script_does_not_default_to_inline_influxdb_token(self):
        script_path = Path(__file__).resolve().parents[3] / "monitoring" / "grafana" / "install_assets.sh"
        text = script_path.read_text(encoding="utf-8")

        self.assertNotIn("admin-token", text)
        self.assertIn("INFLUXDB_TOKEN", text)
        self.assertIn("soloquant-research-overview.json", text)
        self.assertIn("backtest-overview.json", text)
        self.assertIn("live-trading.json", text)
        self.assertIn("GRAFANA_DASHBOARD_PATH", text)
        self.assertIn("/var/lib/grafana/dashboards", text)
        self.assertIn("dashboard.yml", text)

    def test_strategy_pipeline_dashboard_references_expected_measurements_without_secrets(self):
        dashboard = self.load_dashboard("soloquant-strategy-pipeline.json")
        encoded = json.dumps(dashboard, ensure_ascii=False)

        self.assertEqual(dashboard["uid"], "soloquant-strategy-pipeline")
        self.assertEqual(dashboard["title"], "策略管道与进度")
        self.assertIn("soloquant_strategy_progress", encoded)
        self.assertIn("soloquant_pipeline_funnel", encoded)
        self.assertIn("soloquant_strategy_result", encoded)
        self.assertIn("pipeline_stage", encoded)
        self.assertIn("crawled_count", encoded)
        self.assertIn("summarized_count", encoded)
        self.assertIn("reproduced_count", encoded)
        self.assertIn("backtested_count", encoded)
        self.assertIn("live_paper_count", encoded)
        self.assertIn("serving_count", encoded)
        self.assertIn("retired_count", encoded)
        self.assertIn("strategy_id", encoded)
        self.assertIn("best_score", encoded)
        self.assertIn("live_score", encoded)
        self.assertIn("total_return", encoded)
        self.assertIn("decay_ratio", encoded)
        self.assertIn("has_code", encoded)
        self.assertIn("has_backtest", encoded)
        self.assertIn("has_live_paper", encoded)
        self.assertIn("filterable", encoded)
        self.assertIn("strategy-lifecycle", encoded)
        self.assertIn("soloquant-research-overview", encoded)
        self.assertNotIn("admin-token", encoded)
        self.assertNotIn("sk-", encoded)
        self.assertNotIn("glsa_", encoded)


if __name__ == "__main__":
    unittest.main()
