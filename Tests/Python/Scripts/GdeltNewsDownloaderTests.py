import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.error import HTTPError


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "gdelt_news_downloader.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("gdelt_news_downloader", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GdeltNewsDownloaderTests(unittest.TestCase):
    def test_aggregate_articles_groups_by_seen_date_and_tone(self):
        module = load_module()
        articles = [
            {"seendate": "20240101030000", "tone": "2.5", "domain": "a.example"},
            {"seendate": "20240101100000", "tone": "-4.0", "domain": "b.example"},
            {"seendate": "20240102120000", "tone": "0", "domain": "a.example"},
            {"seendate": "20240102130000", "tone": "", "domain": ""},
        ]

        rows = module.aggregate_articles(
            articles,
            query="test query",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
        )

        self.assertEqual(rows[0]["trade_date"], "20240101")
        self.assertEqual(rows[0]["article_count"], 2)
        self.assertEqual(rows[0]["mean_tone"], -0.75)
        self.assertEqual(rows[0]["positive_count"], 1)
        self.assertEqual(rows[0]["negative_count"], 1)
        self.assertEqual(rows[0]["neutral_count"], 0)
        self.assertEqual(rows[0]["source_count"], 2)
        self.assertEqual(rows[0]["top_domain"], "a.example")
        self.assertEqual(rows[1]["article_count"], 2)
        self.assertEqual(rows[1]["mean_tone"], 0.0)
        self.assertEqual(rows[1]["neutral_count"], 2)

    def test_export_symbol_writes_market_daily_csv(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "trade_date": "20240101",
                    "query": "bank",
                    "article_count": 1,
                    "mean_tone": -1.5,
                    "positive_count": 0,
                    "negative_count": 1,
                    "neutral_count": 0,
                    "source_count": 1,
                    "top_domain": "news.example",
                }
            ]

            path = module.write_symbol_csv(root, "600000.SH", rows)

            self.assertEqual(path, root / "sse" / "daily" / "600000.csv")
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                [
                    "trade_date,query,article_count,mean_tone,positive_count,negative_count,neutral_count,source_count,top_domain",
                    "20240101,bank,1,-1.500000,0,1,0,1,news.example",
                ],
            )

    def test_build_doc_api_url_uses_public_project_endpoint(self):
        module = load_module()

        url = module.build_doc_api_url(
            query="China bank",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            max_records=100,
        )

        self.assertTrue(url.startswith("https://api.gdeltproject.org/api/v2/doc/doc?"))
        self.assertIn("mode=artlist", url)
        self.assertIn("format=json", url)
        self.assertIn("query=China+bank", url)
        self.assertIn("startdatetime=20240101000000", url)
        self.assertIn("enddatetime=20240102235959", url)
        self.assertIn("maxrecords=100", url)

    def test_extract_article_samples_normalizes_downloaded_events(self):
        module = load_module()
        articles = [
            {
                "seendate": "20240101123000",
                "title": "Market opens higher",
                "domain": "news.example",
                "url": "https://news.example/story",
                "tone": "1.25",
                "language": "English",
                "sourcecountry": "China",
            },
            {
                "seendate": "20240102123000",
                "title": "Second story",
                "domain": "other.example",
                "url": "https://other.example/story",
                "tone": "-2.5",
            },
        ]

        samples = module.extract_article_samples(articles, limit=1)

        self.assertEqual(
            samples,
            [
                {
                    "seen_date": "20240101",
                    "title": "Market opens higher",
                    "domain": "news.example",
                    "url": "https://news.example/story",
                    "tone": 1.25,
                    "language": "English",
                    "source_country": "China",
                }
            ],
        )

    def test_download_symbol_report_includes_event_samples(self):
        module = load_module()
        original_fetch_articles = module.fetch_articles
        try:
            module.fetch_articles = lambda *args, **kwargs: [
                {
                    "seendate": "20240101123000",
                    "title": "Downloaded GDELT article",
                    "domain": "news.example",
                    "url": "https://news.example/story",
                    "tone": "3.0",
                }
            ]
            with tempfile.TemporaryDirectory() as temp_dir:
                result = module.download_symbol(
                    "600000.SH",
                    "Shanghai Pudong Development Bank",
                    Path(temp_dir),
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    max_records=10,
                    sleep_seconds=0,
                    sample_limit=5,
                )
        finally:
            module.fetch_articles = original_fetch_articles

        self.assertEqual(result["article_count"], 1)
        self.assertEqual(result["sample_articles"][0]["title"], "Downloaded GDELT article")
        self.assertEqual(result["sample_articles"][0]["seen_date"], "20240101")

    def test_build_financial_intelligence_queries_expands_base_query(self):
        module = load_module()

        queries = module.build_financial_intelligence_queries("China stock market")

        self.assertGreaterEqual(len(queries), 6)
        self.assertEqual(queries[0]["scope"], "core")
        self.assertEqual(queries[0]["query"], "China stock market")
        self.assertTrue(any(query["scope"] == "monetary_policy" for query in queries))
        self.assertTrue(any("interest rate" in query["query"] for query in queries))
        self.assertEqual(len({query["query"] for query in queries}), len(queries))

    def test_extract_event_lifecycles_tracks_topic_dates_and_importance(self):
        module = load_module()
        articles = [
            {
                "seendate": "20240101080000",
                "title": "Central bank cuts interest rates to support market",
                "domain": "a.example",
                "url": "https://a.example/1",
                "tone": "-2.0",
            },
            {
                "seendate": "20240102110000",
                "title": "Central bank cuts interest rates again",
                "domain": "b.example",
                "url": "https://b.example/2",
                "tone": "-1.0",
            },
            {
                "seendate": "20240102120000",
                "title": "Copper futures rally on supply concerns",
                "domain": "c.example",
                "url": "https://c.example/3",
                "tone": "2.0",
            },
        ]

        lifecycles = module.extract_event_lifecycles(
            articles,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            limit=5,
        )

        self.assertEqual(lifecycles[0]["event_key"], "central bank cuts interest")
        self.assertEqual(lifecycles[0]["first_seen"], "20240101")
        self.assertEqual(lifecycles[0]["last_seen"], "20240102")
        self.assertEqual(lifecycles[0]["duration_days"], 2)
        self.assertEqual(lifecycles[0]["article_count"], 2)
        self.assertEqual(lifecycles[0]["source_count"], 2)
        self.assertEqual(lifecycles[0]["peak_date"], "20240101")
        self.assertEqual(lifecycles[0]["status"], "active")

    def test_download_symbol_can_fetch_expanded_financial_intelligence(self):
        module = load_module()
        calls = []
        original_fetch_articles = module.fetch_articles
        try:
            def fake_fetch(query, *args, **kwargs):
                calls.append(query)
                return [
                    {
                        "seendate": "20240101123000",
                        "title": f"{query} article",
                        "domain": "news.example",
                        "url": f"https://news.example/{len(calls)}",
                        "tone": "1.0",
                    }
                ]

            module.fetch_articles = fake_fetch
            with tempfile.TemporaryDirectory() as temp_dir:
                result = module.download_symbol(
                    "600000.SH",
                    "Shanghai Pudong Development Bank",
                    Path(temp_dir),
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    max_records=10,
                    sleep_seconds=0,
                    sample_limit=5,
                    expand_financial_intelligence=True,
                    lifecycle_limit=5,
                )
        finally:
            module.fetch_articles = original_fetch_articles

        self.assertGreater(len(calls), 1)
        self.assertEqual(result["article_count"], len(calls))
        self.assertIn("intelligence_queries", result)
        self.assertIn("event_lifecycles", result)
        self.assertGreaterEqual(len(result["event_lifecycles"]), 1)

    def test_download_symbol_records_query_errors_and_keeps_partial_results(self):
        module = load_module()
        original_fetch_articles = module.fetch_articles
        try:
            def fake_fetch(query, *args, **kwargs):
                if "interest rate" in query:
                    raise HTTPError(query, 429, "Too Many Requests", hdrs=None, fp=None)
                return [
                    {
                        "seendate": "20240101123000",
                        "title": "Core downloaded article",
                        "domain": "news.example",
                        "url": "https://news.example/core",
                        "tone": "1.0",
                    }
                ]

            module.fetch_articles = fake_fetch
            with tempfile.TemporaryDirectory() as temp_dir:
                result = module.download_symbol(
                    "600000.SH",
                    "Shanghai Pudong Development Bank",
                    Path(temp_dir),
                    date(2024, 1, 1),
                    date(2024, 1, 1),
                    max_records=10,
                    sleep_seconds=0,
                    sample_limit=5,
                    expand_financial_intelligence=True,
                    lifecycle_limit=5,
                )
        finally:
            module.fetch_articles = original_fetch_articles

        self.assertGreater(result["article_count"], 0)
        self.assertEqual(result["query_errors"][0]["scope"], "monetary_policy")
        self.assertEqual(result["query_errors"][0]["error_type"], "HTTPError")


if __name__ == "__main__":
    unittest.main()
