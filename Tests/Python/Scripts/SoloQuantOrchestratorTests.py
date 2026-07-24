import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "soloquant_orchestrator.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("soloquant_orchestrator", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SoloQuantOrchestratorTests(unittest.TestCase):
    def test_default_config_uses_environment_secret_names_and_no_strategy_binding(self):
        module = load_module()

        config = module.default_config()
        encoded = json.dumps(config, ensure_ascii=False)

        self.assertEqual(config["llm"]["api-key-env-var"], "LLM_API_KEY")
        self.assertEqual(config["influxdb"]["token-env-var"], "INFLUXDB_TOKEN")
        self.assertEqual(config["grafana"]["token-env-var"], "GRAFANA_TOKEN")
        self.assertNotIn("sk-", encoded)
        self.assertNotIn("glsa_", encoded)
        self.assertNotIn("admin-token", encoded)
        self.assertNotIn("algorithm-type-name", config["lean"])
        self.assertIn("AShareLlmQuantLeanAlgorithm", config["strategy-policy"]["avoid-algorithms"])

    def test_default_workflow_root_stays_inside_repo_results(self):
        module = load_module()

        config = module.load_config()
        repo_root = Path(__file__).resolve().parents[3]
        expected_root = repo_root / "Results" / "soloquant"

        self.assertEqual(Path(config["workflow-root"]), expected_root)
        self.assertEqual(Path(config["artifact-root"]), expected_root / "artifacts")
        self.assertEqual(Path(config["strategy-root"]), expected_root / "strategies")
        self.assertTrue(str(config["workflow-root"]).startswith(str(repo_root)))
        self.assertNotIn("/home/project/Results", config["workflow-root"])

    def test_project_config_workflow_root_stays_inside_repo_results(self):
        module = load_module()
        repo_root = Path(__file__).resolve().parents[3]
        config = module.load_config(repo_root / "Launcher" / "config" / "config-soloquant.json")
        expected_root = repo_root / "Results" / "soloquant"

        self.assertEqual(Path(config["workflow-root"]), expected_root)
        self.assertEqual(Path(config["artifact-root"]), expected_root / "artifacts")
        self.assertEqual(Path(config["strategy-root"]), expected_root / "strategies")
        self.assertEqual(Path(config["lean"]["launcher-dll"]), repo_root / "Launcher" / "bin" / "Debug" / "QuantConnect.Lean.Launcher.dll")

    def test_load_config_rejects_inline_service_secrets(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config-soloquant.json"
            config_path.write_text(
                json.dumps(
                    {
                        "glm": {
                            "base-url": "https://api.deepseek.com",
                            "api-key": "sk-inline-secret",
                            "api-key-env-var": "LLM_API_KEY",
                        },
                        "influxdb": {
                            "url": "http://localhost:8086",
                            "token": "admin-token-inline",
                            "token-env-var": "INFLUXDB_TOKEN",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "glm.api-key"):
                module.load_config(config_path)

    def test_build_lean_launcher_command_uses_required_dotnet_dll_config_shape(self):
        module = load_module()
        config_path = Path("/tmp/generated-strategy-backtest.json")

        command, cwd = module.build_lean_launcher_command(config_path)

        self.assertEqual(command[0], "/usr/local/dotnet/dotnet")
        self.assertTrue(command[1].endswith("Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"))
        self.assertEqual(command[2:], ["--config", str(config_path)])
        self.assertTrue(str(cwd).endswith("Launcher/bin/Debug"))

    def test_resolve_data_requirements_logs_missing_fields_and_builds_placeholders(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping_path = root / "tushare_field_mapping.json"
            missing_log = root / "missing-data.jsonl"
            placeholder_dir = root / "placeholders"
            mapping_path.write_text(
                json.dumps(
                    {
                        "datasets": {
                            "daily": {"fields": {"close": {}, "pct_chg": {}}},
                            "moneyflow": {"fields": {"net_mf_amount": {}}},
                        }
                    }
                ),
                encoding="utf-8",
            )

            resolution = module.resolve_data_requirements(
                [
                    {"name": "close", "dataset": "daily"},
                    {"name": "news_sentiment", "dataset": "external_news"},
                ],
                mapping_path,
                missing_log,
            )
            placeholder_report = module.materialize_missing_data_placeholders(
                "news-momentum",
                resolution["missing"],
                placeholder_dir,
                seed=7,
            )

            self.assertEqual(resolution["available"][0]["field"], "close")
            self.assertEqual(resolution["missing"][0]["field"], "news_sentiment")
            self.assertTrue(missing_log.exists())
            self.assertIn("news_sentiment", missing_log.read_text(encoding="utf-8"))
            self.assertEqual(placeholder_report["placeholder_count"], 1)
            self.assertTrue(Path(placeholder_report["files"][0]).exists())

    def test_resolve_data_requirements_supplements_missing_fields_from_external_crawl(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping_path = root / "tushare_field_mapping.json"
            missing_log = root / "missing-data.jsonl"
            cache_root = root / "field-cache"
            mapping_path.write_text(json.dumps({"datasets": {"daily": {"fields": {"close": {}}}}}), encoding="utf-8")
            search_calls = []
            crawl_calls = []

            def fake_search(query):
                search_calls.append(query)
                return [{"title": "news sentiment source", "url": "https://example.test/sentiment"}]

            def fake_crawl(result):
                crawl_calls.append(result)
                return {"content": "A-share news sentiment value: 0.62"}

            resolution = module.resolve_data_requirements(
                [{"name": "news_sentiment", "dataset": "external_news"}],
                mapping_path,
                missing_log,
                supplement_root=cache_root,
                search_client=fake_search,
                crawl_client=fake_crawl,
            )

            self.assertEqual(resolution["missing"], [])
            self.assertEqual(resolution["supplemented"][0]["field"], "news_sentiment")
            self.assertTrue(Path(resolution["supplemented"][0]["file"]).exists())
            self.assertIn("news_sentiment external_news data source", search_calls[0]["query"])
            self.assertEqual(crawl_calls[0]["url"], "https://example.test/sentiment")

    def test_optimize_strategy_runs_baseline_plus_two_versions_and_selects_best(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            commands = []
            for index, score in enumerate([0.10, 0.25, 0.18]):
                config_path = root / f"variant-{index}.json"
                summary_path = root / f"summary-{index}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "algorithm-type-name": f"SoloQuantGeneratedStrategyV{index}",
                            "parameters": {
                                "summary-file": str(summary_path),
                                "soloquant-score-metric": "score",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                summary_path.write_text(json.dumps({"score": score}), encoding="utf-8")

            variants = [
                {"version": "baseline", "backtest-config": str(root / "variant-0.json")},
                {"version": "v1", "backtest-config": str(root / "variant-1.json")},
                {"version": "v2", "backtest-config": str(root / "variant-2.json")},
            ]

            def fake_run(command, cwd):
                commands.append(command)
                return 0

            result = module.optimize_strategy_versions(variants, runner=fake_run)

            self.assertEqual([item["version"] for item in result["evaluated_versions"]], ["baseline", "v1", "v2"])
            self.assertEqual(result["best_version"], "v1")
            self.assertEqual(len(commands), 3)
            self.assertTrue(all(command[0] == "/usr/local/dotnet/dotnet" for command in commands))

    def test_optimize_strategy_scores_from_portfolio_snapshot_when_summary_missing(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            variants = []
            for index, total_return in enumerate([0.01, 0.07, 0.03]):
                config_path = root / f"variant-{index}.json"
                snapshot_path = root / f"portfolio-{index}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "parameters": {
                                "summary-file": str(root / f"missing-summary-{index}.json"),
                                "portfolio-snapshot-file": str(snapshot_path),
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                snapshot_path.write_text(json.dumps({"total_return": total_return}), encoding="utf-8")
                variants.append({"version": f"v{index}", "backtest-config": str(config_path)})

            result = module.optimize_strategy_versions(variants, runner=lambda command, cwd: 0)

            self.assertEqual(result["best_version"], "v1")
            self.assertEqual(result["best_score"], 0.07)
            self.assertEqual(result["evaluated_versions"][1]["summary"]["score"], 0.07)

    def test_load_lean_summary_reads_bom_prefixed_portfolio_snapshot(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config-backtest.json"
            snapshot_path = root / "portfolio.json"
            config_path.write_text(
                json.dumps(
                    {
                        "parameters": {
                            "summary-file": str(root / "missing-summary.json"),
                            "portfolio-snapshot-file": str(snapshot_path),
                        },
                    }
                ),
                encoding="utf-8",
            )
            snapshot_path.write_text('\ufeff{"total_return": 0.0872061201}', encoding="utf-8")

            summary = module._load_lean_summary_from_config(config_path)

            self.assertEqual(summary["total_return"], 0.0872061201)
            self.assertEqual(summary["score"], 0.0872061201)

    def test_validate_strategy_manifest_rejects_ashare_llm_quant_algorithm(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.validate_strategy_manifest(
                {
                    "strategy-id": "bad-reuse",
                    "lean-configs": {
                        "backtest": {
                            "algorithm-type-name": "AShareLlmQuantLeanAlgorithm",
                        }
                    },
                }
            )

    def test_build_research_query_plan_covers_strategy_and_intelligence_sources(self):
        module = load_module()

        plan = module.build_research_query_plan(["momentum crash", "A股资金流"])
        queries = [item["query"] for item in plan["queries"]]
        sources = [item.get("source") for item in plan["queries"]]

        self.assertGreaterEqual(len(queries), 10)
        # Sources are metadata, not in query text; site: restrictions replace source name prefix
        self.assertTrue(any("site:arxiv.org" in query for query in queries), "arXiv site restriction")
        self.assertTrue(any("site:papers.ssrn.com" in query for query in queries), "SSRN site restriction")
        self.assertTrue(any("site:quantpedia.com" in query for query in queries), "Quantpedia site restriction")
        self.assertTrue(any("site:quantconnect.com" in query for query in queries), "QuantConnect site restriction")
        # Sources should be in metadata
        self.assertIn("arXiv q-fin", sources)
        self.assertIn("SSRN", sources)
        self.assertTrue(any(item["category"] == "finance_intelligence" for item in plan["queries"]))
        self.assertTrue(any("site:pbc.gov.cn" in query for query in queries))
        self.assertTrue(any("site:sse.com.cn" in query for query in queries))
        self.assertTrue(any("site:federalreserve.gov" in query for query in queries))
        self.assertTrue(any("site:ecb.europa.eu" in query for query in queries))
        self.assertTrue(any("site:boj.or.jp" in query for query in queries))
        self.assertTrue(any("site:cmegroup.com" in query for query in queries))
        self.assertTrue(any("article/detail" in query for query in queries))
        finance_queries = [item for item in plan["queries"] if item["category"] == "finance_intelligence"]
        direct_urls = [url for item in finance_queries for url in item.get("direct_urls", [])]
        self.assertTrue(any("pbc.gov.cn" in url for url in direct_urls))
        self.assertTrue(any("sse.com.cn" in url for url in direct_urls))
        self.assertTrue(any("paper.cnstock.com" in url for url in direct_urls))
        self.assertTrue(any("federalreserve.gov" in url for url in direct_urls))
        self.assertTrue(any("ecb.europa.eu" in url for url in direct_urls))
        self.assertTrue(any("boj.or.jp" in url for url in direct_urls))
        self.assertTrue(any("cmegroup.com" in url for url in direct_urls))

    def test_finance_intelligence_quality_rejects_media_homepage_shell(self):
        module = load_module()

        result = module.evaluate_finance_intelligence_quality(
            {
                "category": "finance_intelligence",
                "source": "权威财经媒体",
                "title": "证券时报官方网站-中国资本市场信息披露平台",
                "url": "https://www.stcn.com/",
                "content": (
                    "首页 快讯 新闻 视频 投资 数据 信披 专题 服务 公众号 客户端 登录 "
                    "51只十倍基回报创纪录 千余只主动权益基金创历史新高"
                ),
            },
            run_date="20260510",
        )

        self.assertFalse(result["accepted"])
        self.assertIn("landing_or_listing_page", result["reject_reasons"])

    def test_finance_intelligence_quality_accepts_recent_authoritative_market_impact_item(self):
        module = load_module()

        result = module.evaluate_finance_intelligence_quality(
            {
                "category": "finance_intelligence",
                "source": "央行与监管机构",
                "title": "人民银行开展公开市场逆回购操作 维护流动性合理充裕",
                "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/20260510/index.html",
                "content": (
                    "2026年5月10日，人民银行开展公开市场逆回购操作，实现净投放，维护银行体系流动性合理充裕。"
                    "资金面边际宽松可能带动短端利率下行，影响债券久期、高股息红利和成长风格估值。"
                ),
            },
            run_date="20260510",
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["authority_type"], "central_bank")
        self.assertIn("公开市场", result["impact_terms"])
        self.assertGreaterEqual(result["score"], 6)

    def test_build_llm_screening_payload_encodes_value_criteria_without_secret(self):
        module = load_module()

        payload = module.build_llm_screening_payload(
            [
                {
                    "title": "Paper",
                    "url": "https://example.test/paper",
                    "content": "complete alpha idea with implementation and backtest",
                }
            ],
            model="glm-5.1",
        )
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["model"], "glm-5.1")
        self.assertIn("可理解的量化思路", encoded)
        self.assertIn("实现步骤", encoded)
        self.assertIn("回测或实盘效果", encoded)
        self.assertNotIn("apiKey", encoded)
        self.assertNotIn("sk-", encoded)

    def test_parse_llm_screening_decisions_keeps_only_valuable_items(self):
        module = load_module()

        decisions = module.parse_llm_screening_decisions(
            {
                "valuable_items": [
                    {
                        "title": "Useful strategy",
                        "url": "https://example.test/useful",
                        "is_valuable": True,
                        "required_fields": [{"dataset": "daily", "field": "close"}],
                    },
                    {
                        "title": "Market color",
                        "url": "https://example.test/color",
                        "is_valuable": False,
                    },
                ]
            }
        )

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["title"], "Useful strategy")
        self.assertEqual(decisions[0]["required_fields"][0]["field"], "close")

    def test_build_reproduction_summary_payload_reads_core_idea_and_repro_schema_without_secret(self):
        module = load_module()

        paper_content = "Rank stocks by 12 month momentum, skip the latest month, rebalance monthly. Backtest Sharpe 1.1."
        payload = module.build_reproduction_summary_payload(
            {
                "title": "Cross-sectional Momentum",
                "url": "https://example.test/paper",
                "source": "arXiv q-fin",
                "content": paper_content,
            },
            model="glm-5.1",
        )
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["model"], "glm-5.1")
        self.assertNotIn("max_tokens", payload)
        self.assertIn("核心量化思路", encoded)
        self.assertIn("复现准备", encoded)
        self.assertIn("LEAN", encoded)
        self.assertIn(paper_content, encoded)
        self.assertNotIn("sk-", encoded)
        self.assertNotIn("apiKey", encoded)

    def test_reproduction_summary_payload_does_not_truncate_content_by_default(self):
        module = load_module()
        long_content = "alpha-signal-" * 2000

        payload = module.build_reproduction_summary_payload({"content": long_content})
        user_content = payload["messages"][1]["content"]

        self.assertIn(long_content, user_content)

    def test_reproduction_summary_payload_supports_explicit_content_limit(self):
        module = load_module()
        long_content = "alpha-signal-" * 2000

        payload = module.build_reproduction_summary_payload({"content": long_content}, max_content_chars=100, max_tokens=256)
        user_content = payload["messages"][1]["content"]

        self.assertIn(long_content[:100], user_content)
        self.assertNotIn(long_content[:120], user_content)
        self.assertEqual(payload["max_tokens"], 256)

    def test_resolve_arxiv_pdf_url_converts_abs_page_to_pdf(self):
        module = load_module()

        self.assertEqual(
            module.resolve_pdf_url({"url": "https://arxiv.org/abs/2409.06289"}),
            "https://arxiv.org/pdf/2409.06289",
        )
        self.assertEqual(
            module.resolve_pdf_url({"url": "https://arxiv.org/pdf/2409.06289"}),
            "https://arxiv.org/pdf/2409.06289",
        )
        self.assertIsNone(module.resolve_pdf_url({"url": "https://example.test/paper"}))

    def test_prepare_reproduction_summaries_prefers_arxiv_pdf_text_over_page_shell(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "arxiv.json").write_text(
                json.dumps(
                    {
                        "category": "strategy",
                        "source": "arXiv q-fin",
                        "title": "Automate Strategy Finding",
                        "url": "https://arxiv.org/abs/2409.06289",
                        "content": "Skip to main content arXiv page shell with abstract only",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_llm(payload):
                calls.append(payload)
                return {
                    "core_idea": "full paper seed alpha factory strategy",
                    "reproduction_steps": ["read full PDF", "rank stocks by composite alpha"],
                }

            with mock.patch.object(
                module,
                "download_and_extract_pdf_text",
                return_value={
                    "content": "Full PDF text: top-k/drop-n portfolio, k=13, n=5, daily rebalance.",
                    "content_source": "pdf",
                    "pdf_url": "https://arxiv.org/pdf/2409.06289",
                    "pdf_file": str(artifact_root / "pdf" / "strategy" / "paper.pdf"),
                    "text_file": str(artifact_root / "pdf" / "strategy" / "paper.txt"),
                },
            ) as extractor:
                report = module.prepare_reproduction_summaries(
                    artifact_root=artifact_root,
                    run_date="20260510",
                    llm_summary_client=fake_llm,
                    model="glm-5.1",
                )

            self.assertEqual(report["written_count"], 1)
            extractor.assert_called_once()
            encoded_payload = json.dumps(calls[0], ensure_ascii=False)
            self.assertIn("Full PDF text", encoded_payload)
            self.assertNotIn("Skip to main content", encoded_payload)
            summary = json.loads(Path(report["files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(summary["content_source"], "pdf")
            self.assertEqual(summary["pdf_url"], "https://arxiv.org/pdf/2409.06289")
            self.assertTrue(summary["pdf_file"].endswith("paper.pdf"))

    def test_prepare_reproduction_summaries_falls_back_to_local_pdf_summary_when_llm_times_out(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "arxiv.json").write_text(
                json.dumps(
                    {
                        "category": "strategy",
                        "source": "arXiv q-fin",
                        "title": "Automate Strategy Finding with LLM in Quant Investment",
                        "url": "https://arxiv.org/abs/2409.06289",
                        "content": "arXiv page shell",
                    }
                ),
                encoding="utf-8",
            )
            pdf_text = (
                "3 Methodology Confidence Score Agent Risk Preference Agent Weight Optimization. "
                "A.3 Generate Seed Alpha factory (CLOSE - DELAY(CLOSE, 14)) "
                "(BOLL_UP - BOLL_DOWN) / SMA(CLOSE, 20) (EPS / DELAY(EPS, 1) - 1). "
                "A.8 Portfolio Construction Methods top-k/drop-n k = 13 and n = 5 equal-weighting daily."
            )

            with mock.patch.object(
                module,
                "download_and_extract_pdf_text",
                return_value={"content": pdf_text, "content_source": "pdf", "pdf_url": "https://arxiv.org/pdf/2409.06289"},
            ):
                report = module.prepare_reproduction_summaries(
                    artifact_root=artifact_root,
                    run_date="20260510",
                    llm_summary_client=mock.Mock(side_effect=TimeoutError("read timed out")),
                )

            self.assertEqual(report["written_count"], 1)
            summary = json.loads(Path(report["files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(summary["summary"]["summary_source"], "local_pdf_fallback")
            self.assertIn("read timed out", summary["summary"]["llm_error"])
            self.assertTrue(any(step for step in summary["summary"]["reproduction_steps"] if "top-k/drop-n" in step))

    def test_local_pdf_summary_extracts_key_alpha_formulas_from_noisy_pdf_text(self):
        module = load_module()
        pdf_text = (
            "Selected Alpha (CLOSE - DELAY(CLOSE, 14)) "
            "(RSI - DELAY(RSI, 14)) (CLOSE - DELAY(SMA(CLOSE, 14), 7)) "
            "(BOLL_UP - BOLL_DOWN) / SMA(CLOSE, 20) STD(CLOSE, 10) / STD(CLOSE, 50) "
            "VOLUME / MARKET_CAP VOLUME * CLOSE (EPS / DELAY(EPS, 1) - 1) "
            "Portfolio Construction Methods top-k/drop-n k = 13 and n = 5"
        )

        summary = module.build_local_reproduction_summary({"title": "paper"}, pdf_text)
        formulas = [item["formula"] for item in summary["signals"]]

        self.assertIn("CLOSE - DELAY(CLOSE, 14)", formulas)
        self.assertIn("RSI - DELAY(RSI, 14)", formulas)
        self.assertIn("CLOSE - DELAY(SMA(CLOSE, 14), 7)", formulas)
        self.assertIn("(BOLL_UP - BOLL_DOWN) / SMA(CLOSE, 20)", formulas)
        self.assertIn("STD(CLOSE, 10) / STD(CLOSE, 50)", formulas)
        self.assertIn("EPS / DELAY(EPS, 1) - 1", formulas)

    def test_select_reproduction_content_keeps_pdf_method_and_appendix_sections(self):
        module = load_module()
        pdf_text = "\n".join(
            [
                "Abstract useful overview",
                "1 Introduction",
                "intro text",
                "2   Problem Formulation",
                "problem formulation with IC definition",
                "3     Methodology",
                "methodology with confidence and risk agents",
                "4       Experiment",
                "experiment dataset and selected alpha table",
                "References",
                "long references that should not dominate",
                "A.3   Generate Seed Alpha factory",
                "factor formulas and categories",
                "A.5    Category-Based Alpha Selection",
                "category algorithm",
                "A.6    Dynamic Alpha Strategy Construction",
                "overall framework algorithm",
                "A.8   Portfolio Construction Methods",
                "top-k/drop-n portfolio construction",
            ]
        )

        selected = module.select_reproduction_content({"content": pdf_text, "content_source": "pdf"})

        self.assertIn("Problem Formulation", selected)
        self.assertIn("Methodology", selected)
        self.assertIn("Generate Seed Alpha factory", selected)
        self.assertIn("Portfolio Construction Methods", selected)
        self.assertNotIn("long references that should not dominate", selected)

    def test_convert_pdf_text_to_markdown_adds_headings_tables_and_quality_metrics(self):
        module = load_module()
        pdf_text = "\n".join(
            [
                "2   Problem Formulation",
                "IC = sigma(u, v)",
                "3     Methodology",
                "Confidence Score Agent",
                "Table 3: SSE50 2023 test combination of 12 Alphas",
                "# Alpha Weight IC(SSE50)",
                "1 (CLOSE - DELAY(CLOSE, 14)) -0.1459 0.0209",
                "A.8   Portfolio Construction Methods",
                "top-k/drop-n k = 13 and n = 5",
            ]
        )

        result = module.convert_pdf_text_to_markdown(pdf_text)

        self.assertGreaterEqual(result["quality"]["score"], 5)
        self.assertIn("## 2 Problem Formulation", result["markdown"])
        self.assertIn("## 3 Methodology", result["markdown"])
        self.assertIn("| # | Alpha | Weight | IC(SSE50) |", result["markdown"])
        self.assertIn("(CLOSE - DELAY(CLOSE, 14))", result["markdown"])

    def test_select_reproduction_content_prefers_high_quality_pdf_markdown(self):
        module = load_module()

        selected = module.select_reproduction_content(
            {
                "content": "2   Problem Formulation\nTXT fallback only",
                "markdown": "## 2 Problem Formulation\nMarkdown with formula table\n## A.8 Portfolio Construction Methods\ntop-k/drop-n",
                "content_source": "pdf",
                "markdown_quality": {"score": 6},
            }
        )

        self.assertIn("Markdown with formula table", selected)
        self.assertNotIn("TXT fallback only", selected)

    def test_select_reproduction_content_rejects_low_quality_pdf_markdown(self):
        module = load_module()

        selected = module.select_reproduction_content(
            {
                "content": "2   Problem Formulation\nTXT fallback with usable sections",
                "markdown": "tiny markdown",
                "content_source": "pdf",
                "markdown_quality": {"score": 1},
            }
        )

        self.assertIn("TXT fallback with usable sections", selected)
        self.assertNotIn("tiny markdown", selected)

    def test_is_listing_content_detects_arxiv_index_pages(self):
        module = load_module()
        listing = "Showing 1-25 of 150 results\nSort by: relevance\nresults per page: 25\nNext Page\nFilter by year"
        self.assertTrue(module.is_listing_content(listing))
        paper = "2   Problem Formulation\nWe propose a momentum factor strategy\n3   Methodology\nThe alpha signal is constructed as"
        self.assertFalse(module.is_listing_content(paper))

    def test_select_reproduction_content_rejects_listing_pages(self):
        module = load_module()
        listing_content = "Showing 1-25 of 150 results\nSort by: relevance\nresults per page: 25\nNext Page\nFilter by year"
        selected = module.select_reproduction_content({"content": listing_content, "content_source": "crawl"})
        self.assertEqual(selected, "")

    def test_select_reproduction_content_accepts_real_paper_content(self):
        module = load_module()
        paper_content = "2   Problem Formulation\nWe propose a cross-sectional momentum strategy\n3   Methodology\nThe alpha signal uses lookback period"
        selected = module.select_reproduction_content({"content": paper_content, "content_source": "crawl"})
        self.assertIn("Problem Formulation", selected)

    def test_prepare_reproduction_summaries_reads_crawled_papers_and_persists_summary(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            paper_path = crawled_dir / "paper.json"
            paper_path.write_text(
                json.dumps(
                    {
                        "category": "strategy",
                        "source": "arXiv q-fin",
                        "title": "Momentum Paper",
                        "url": "https://example.test/momentum",
                        "content": "A complete momentum strategy with ranking, rebalance rules, and backtest results.",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_llm(payload):
                calls.append(payload)
                return {
                    "core_idea": "rank stocks by momentum",
                    "tradable_universe": "A-share liquid stocks",
                    "signals": [{"name": "momentum_12_1", "formula": "return_12m_ex_last_month"}],
                    "data_requirements": [{"dataset": "daily", "field": "close"}],
                    "reproduction_steps": ["load daily close", "rank universe", "rebalance monthly"],
                    "backtest_plan": {"start_date": "2020-01-01", "benchmark": "CSI300"},
                    "implementation_notes": ["map close to Tushare daily"],
                    "open_questions": ["transaction cost assumption"],
                }

            report = module.prepare_reproduction_summaries(
                artifact_root=artifact_root,
                run_date="20260510",
                llm_summary_client=fake_llm,
                model="glm-5.1",
            )

            self.assertEqual(report["written_count"], 1)
            self.assertEqual(len(calls), 1)
            self.assertIn("复现准备", json.dumps(calls[0], ensure_ascii=False))
            summary_path = Path(report["files"][0])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["title"], "Momentum Paper")
            self.assertEqual(summary["summary"]["core_idea"], "rank stocks by momentum")
            self.assertEqual(summary["summary"]["data_requirements"][0]["field"], "close")
            self.assertTrue((artifact_root / "reproduction" / "strategy" / "index.json").exists())

    def test_build_finance_intelligence_payload_extracts_key_points_without_truncating(self):
        module = load_module()
        long_content = "央行公开市场操作影响流动性。" * 1500

        payload = module.build_finance_intelligence_analysis_payload(
            {
                "title": "公开市场业务交易公告",
                "url": "https://example.test/pbc",
                "source": "人民银行",
                "content": long_content,
            },
            model="glm-5.1",
        )
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["model"], "glm-5.1")
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertNotIn("response_format", payload)
        self.assertIn("提取要点", encoded)
        self.assertIn("影响分析", encoded)
        self.assertIn(long_content, encoded)
        self.assertNotIn("sk-", encoded)

    def test_prepare_finance_intelligence_analyses_reads_crawled_items_and_persists_analysis(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "finance_intelligence"
            crawled_dir.mkdir(parents=True)
            item_path = crawled_dir / "pbc.json"
            item_path.write_text(
                json.dumps(
                    {
                        "category": "finance_intelligence",
                        "source": "人民银行",
                        "title": "公开市场业务交易公告",
                        "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/20260510/index.html",
                        "content": (
                            "2026年5月10日，"
                            "人民银行今日开展公开市场逆回购操作并实现净投放，维护银行体系流动性合理充裕。"
                            "资金面边际宽松可能带动短端利率下行，对债券久期资产形成支撑，同时改善权益市场风险偏好。"
                            "后续需要继续观察DR007、同业存单利率和公开市场到期量。"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_llm(payload):
                calls.append(payload)
                return {
                    "key_points": ["央行净投放流动性"],
                    "impact_analysis": "短端利率可能下行",
                    "risk_level": "medium",
                    "event_type": "monetary_policy",
                    "signal_direction": "bullish",
                    "affected_sectors": ["债券"],
                }

            report = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root,
                run_date="20260510",
                llm_analysis_client=fake_llm,
                model="glm-5.1",
            )

            self.assertEqual(report["written_count"], 1)
            self.assertEqual(len(calls), 1)
            self.assertIn("影响分析", json.dumps(calls[0], ensure_ascii=False))
            analysis_path = Path(report["files"][0])
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            self.assertEqual(analysis["title"], "公开市场业务交易公告")
            self.assertEqual(analysis["analysis"]["key_points"][0], "央行净投放流动性")
            self.assertEqual(analysis["event_type"], "monetary_policy")
            index = json.loads((artifact_root / "intelligence-analysis" / "finance_intelligence" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["items"][0]["event_type"], "monetary_policy")
            self.assertTrue((artifact_root / "intelligence-analysis" / "finance_intelligence" / "index.json").exists())

    def test_prepare_finance_intelligence_analyses_skips_low_quality_content(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "finance_intelligence"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "bad.json").write_text(
                json.dumps(
                    {
                        "category": "finance_intelligence",
                        "source": "人民银行",
                        "title": "empty",
                        "url": "https://example.test/bad",
                        "content": "Nessuna informazione disponibile per questa pagina.",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            report = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root,
                run_date="20260510",
                llm_analysis_client=lambda payload: calls.append(payload) or {},
                model="glm-5.1",
            )

            self.assertEqual(report["written_count"], 0)
            self.assertEqual(report["skipped_count"], 1)
            self.assertEqual(calls, [])

    def test_prepare_finance_intelligence_analyses_skips_legacy_rejected_quality_items(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "finance_intelligence"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "homepage.json").write_text(
                json.dumps(
                    {
                        "category": "finance_intelligence",
                        "source": "权威财经媒体",
                        "title": "证券时报官方网站-中国资本市场信息披露平台",
                        "url": "https://www.stcn.com/",
                        "content": "首页 快讯 新闻 视频 投资 数据 信披 专题 服务 公众号 客户端 登录" * 2000,
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            report = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root,
                run_date="20260510",
                llm_analysis_client=lambda payload: calls.append(payload) or {},
                model="glm-5.1",
            )

            self.assertEqual(report["written_count"], 0)
            self.assertEqual(report["skipped_count"], 1)
            self.assertEqual(calls, [])

    def test_persist_crawled_items_deduplicates_across_ticks(self):
        """Same URL written in tick 1 must be skipped in tick 2 (cross-tick dedup)."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            item = {
                "category": "finance_intelligence",
                "source": "人民银行",
                "title": "公开市场操作公告",
                "url": "https://www.pbc.gov.cn/test/20260510/index.html",
                "content": "央行今日开展逆回购操作，净投放流动性500亿元。" * 50,
                "quality": {"accepted": True, "reject_reasons": []},
            }
            # Tick 1
            r1 = module.persist_crawled_items([item], artifact_root, run_date="20260510")
            # Tick 2 — same URL, slightly different content (simulates re-crawl)
            item2 = {**item, "content": item["content"] + " 补充信息"}
            r2 = module.persist_crawled_items([item2], artifact_root, run_date="20260510")

            self.assertEqual(r1["written_count"], 1)
            self.assertEqual(r2["written_count"], 0)
            self.assertEqual(r2["duplicate_count"], 1)

    def test_prepare_finance_intelligence_analyses_skips_already_analyzed_urls(self):
        """URL analyzed in tick 1 must not be re-analyzed in tick 2."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "finance_intelligence"
            crawled_dir.mkdir(parents=True)
            good_item = {
                "category": "finance_intelligence",
                "source": "人民银行",
                "title": "公开市场操作",
                "url": "https://www.pbc.gov.cn/test/20260510/index.html",
                "content": "央行今日开展逆回购操作，净投放流动性500亿元，维护银行体系流动性合理充裕。" * 20,
            }
            (crawled_dir / "pbc.json").write_text(json.dumps(good_item), encoding="utf-8")
            calls = []

            def fake_llm(payload):
                calls.append(payload)
                return {"key_points": ["净投放500亿"], "impact_analysis": "流动性宽松", "risk_level": "low"}

            # Tick 1 — should analyze
            r1 = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root, run_date="20260510", llm_analysis_client=fake_llm
            )
            # Tick 2 — same crawled file, same URL — should skip
            r2 = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root, run_date="20260510", llm_analysis_client=fake_llm
            )

            self.assertEqual(r1["written_count"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(r2["written_count"], 0)
            self.assertEqual(r2["skipped_count"], 1)
            self.assertEqual(len(calls), 1)  # GLM not called again

    def test_prepare_finance_intelligence_analyses_stores_event_type_in_filename_and_index(self):
        """Analysis filename must contain event_type; index must have event_type per item."""
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "finance_intelligence"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "pbc.json").write_text(
                json.dumps({
                    "category": "finance_intelligence",
                    "source": "人民银行",
                    "title": "央行降息公告",
                    "url": "https://www.pbc.gov.cn/test/rate/index.html",
                    "content": "2026年5月10日，人民银行宣布降息50bp，LPR同步下调，流动性宽松，风险偏好改善。" * 20,
                }),
                encoding="utf-8",
            )

            def fake_llm(payload):
                return {
                    "key_points": ["降息50bp"],
                    "impact_analysis": "流动性宽松",
                    "risk_level": "medium",
                    "event_type": "monetary_policy",
                    "signal_direction": "bullish",
                    "affected_sectors": ["银行", "地产"],
                }

            report = module.prepare_finance_intelligence_analyses(
                artifact_root=artifact_root, run_date="20260510", llm_analysis_client=fake_llm
            )

            self.assertEqual(report["written_count"], 1)
            # Filename must contain event_type
            fname = Path(report["files"][0]).name
            self.assertIn("monetary_policy", fname)
            # Index must have event_type
            index = json.loads(
                (artifact_root / "intelligence-analysis" / "finance_intelligence" / "index.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(index["items"][0]["event_type"], "monetary_policy")
            # Analysis file must have event_type
            analysis = json.loads(Path(report["files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(analysis["event_type"], "monetary_policy")

    def test_build_finance_event_graph_creates_events_entities_and_relationships(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            analysis_dir = artifact_root / "intelligence-analysis" / "finance_intelligence"
            analysis_dir.mkdir(parents=True)
            rows = [
                {
                    "title": "央行净投放流动性",
                    "url": "https://example.test/pbc",
                    "source": "人民银行",
                    "source_file": "/tmp/pbc.json",
                    "analysis": {
                        "key_points": ["人民银行逆回购净投放，DR007可能下行"],
                        "impact_analysis": "流动性宽松支撑债券久期资产，改善权益风险偏好。",
                        "risk_level": "medium",
                    },
                },
                {
                    "title": "交易所提示债券波动风险",
                    "url": "https://example.test/exchange",
                    "source": "交易所公告",
                    "source_file": "/tmp/exchange.json",
                    "analysis": {
                        "key_points": ["债券久期交易拥挤，交易所提示波动风险"],
                        "impact_analysis": "债券资产可能波动加大，权益高股息风格仍受利率影响。",
                        "risk_level": "high",
                    },
                },
            ]
            for index, row in enumerate(rows, start=1):
                (analysis_dir / f"item-{index}.json").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

            report = module.build_finance_event_graph(artifact_root, run_date="20260510")
            graph_path = Path(report["graph_file"])
            graph = json.loads(graph_path.read_text(encoding="utf-8"))

            node_types = {node["type"] for node in graph["nodes"]}
            edge_types = {edge["type"] for edge in graph["edges"]}
            event_nodes = [node for node in graph["nodes"] if node["type"] == "event"]

            self.assertEqual(report["event_count"], 2)
            self.assertIn("event", node_types)
            self.assertIn("entity", node_types)
            self.assertIn("theme", node_types)
            self.assertIn("source", node_types)
            self.assertIn("SOURCE_REPORTED_EVENT", edge_types)
            self.assertIn("EVENT_IMPACTS_ENTITY", edge_types)
            self.assertIn("EVENT_HAS_THEME", edge_types)
            self.assertIn("EVENT_RELATED_TO_EVENT", edge_types)
            self.assertTrue(any(node["properties"]["risk_level"] == "high" for node in event_nodes))
            self.assertTrue(any(node["name"] == "债券" for node in graph["nodes"]))
            self.assertTrue((artifact_root / "event-graph" / "finance_intelligence" / "graph.json").exists())

    def test_build_finance_event_graph_dedupes_repeated_analysis_for_same_url(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            analysis_dir = artifact_root / "intelligence-analysis" / "finance_intelligence"
            analysis_dir.mkdir(parents=True)
            base = {
                "title": "股票交易风险提示公告",
                "url": "https://paper.cnstock.com/html/2026-05/08/content_2213626.htm",
                "source": "权威财经媒体",
                "quality": {"accepted": True},
                "analysis": {
                    "key_points": ["退市风险警示"],
                    "impact_analysis": "退市风险警示影响风险偏好。",
                    "risk_level": "high",
                },
            }
            for index in range(2):
                payload = {**base, "source_file": f"/tmp/source-{index}.json"}
                (analysis_dir / f"duplicate-{index}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            report = module.build_finance_event_graph(artifact_root, run_date="20260510")
            graph = json.loads(Path(report["graph_file"]).read_text(encoding="utf-8"))
            events = [node for node in graph["nodes"] if node["type"] == "event"]

            self.assertEqual(report["event_count"], 1)
            self.assertEqual(len(events), 1)

    def test_build_finance_event_graph_keeps_latest_duplicate_analysis(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            analysis_dir = artifact_root / "intelligence-analysis" / "finance_intelligence"
            analysis_dir.mkdir(parents=True)
            base = {
                "title": "股票交易风险提示公告",
                "url": "https://paper.cnstock.com/html/2026-05/08/content_2213626.htm",
                "source": "权威财经媒体",
                "quality": {"accepted": True, "authority_type": "official_media"},
                "analysis": {
                    "key_points": ["退市风险警示"],
                    "risk_level": "high",
                },
            }
            older = {**base, "analyzed_at_utc": "2026-05-10T00:00:00+00:00", "analysis": {**base["analysis"], "impact_analysis": "旧分析"}}
            newer = {**base, "analyzed_at_utc": "2026-05-10T01:00:00+00:00", "analysis": {**base["analysis"], "impact_analysis": "新分析"}}
            (analysis_dir / "aaa-older.json").write_text(json.dumps(older, ensure_ascii=False), encoding="utf-8")
            (analysis_dir / "zzz-newer.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")

            report = module.build_finance_event_graph(artifact_root, run_date="20260510")
            graph = json.loads(Path(report["graph_file"]).read_text(encoding="utf-8"))
            events = [node for node in graph["nodes"] if node["type"] == "event"]

            self.assertEqual(report["event_count"], 1)
            self.assertEqual(events[0]["properties"]["impact_analysis"], "新分析")

    def test_parse_llm_json_response_marks_length_limited_reasoning_only_response_incomplete(self):
        module = load_module()

        parsed = module.parse_llm_json_response(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "core idea text but no final json",
                        },
                    }
                ],
                "usage": {"completion_tokens": 1024},
            }
        )

        self.assertFalse(parsed["is_complete"])
        self.assertEqual(parsed["finish_reason"], "length")
        self.assertIn("core idea text", parsed["raw_reasoning"])

    def test_parse_llm_json_response_extracts_markdown_json_block(self):
        module = load_module()

        parsed = module.parse_llm_json_response(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "```json\n{\"key_points\":[\"a\"],\"risk_level\":\"medium\"}\n```",
                        },
                    }
                ]
            }
        )

        self.assertTrue(parsed["is_complete"])
        self.assertEqual(parsed["key_points"], ["a"])
        self.assertEqual(parsed["risk_level"], "medium")

    def test_parse_llm_json_response_extracts_json_from_reasoning_content(self):
        module = load_module()

        parsed = module.parse_llm_json_response(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "",
                            "reasoning_content": "I will generate the strategy. ```json\n{\"algorithm_name\": \"TestStrategy\", \"code\": \"using System;\"}\n```",
                        },
                    }
                ]
            }
        )

        self.assertTrue(parsed["is_complete"])
        self.assertEqual(parsed["algorithm_name"], "TestStrategy")
        self.assertEqual(parsed["code"], "using System;")

    def test_create_http_client_from_config_falls_back_to_api_key_default(self):
        module = load_module()
        import os

        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("MY_TEST_API_KEY", None)

        client = module.create_http_client_from_config(
            {
                "glm": {
                    "base-url": "https://test.example.com/v1",
                    "api-key-env-var": "MY_TEST_API_KEY",
                    "api-key-default": "sk-default-test-key-123",
                    "timeout-seconds": 60,
                }
            },
            post_json=lambda *a, **k: {},
        )
        self.assertEqual(client.llm_api_key, "sk-default-test-key-123")

    def test_materialize_strategy_package_writes_manifest_and_distinct_lean_configs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = module.materialize_strategy_package(
                strategy_spec={
                    "strategy-id": "news-momentum",
                    "algorithm-type-name": "SoloQuantNewsMomentumAlgorithm",
                    "description": "uses crawled news and Tushare data",
                    "parameters": {
                        "lookback-days": "20",
                        "top-n": "15",
                    },
                },
                strategy_root=root,
                start_date="2024-01-01",
                end_date="2024-12-31",
            )

            manifest = json.loads(Path(package["manifest-file"]).read_text(encoding="utf-8"))
            backtest_config = json.loads(Path(package["backtest-config"]).read_text(encoding="utf-8"))
            live_config = json.loads(Path(package["live-paper-config"]).read_text(encoding="utf-8"))
            encoded = json.dumps(package, ensure_ascii=False) + json.dumps(backtest_config, ensure_ascii=False) + json.dumps(live_config, ensure_ascii=False)

            self.assertEqual(manifest["strategy-id"], "news-momentum")
            self.assertEqual(backtest_config["algorithm-type-name"], "SoloQuantNewsMomentumAlgorithm")
            self.assertEqual(backtest_config["environment"], "backtesting")
            self.assertEqual(live_config["environment"], "live-paper")
            self.assertTrue(live_config["live-mode"])
            self.assertEqual(backtest_config["influxdb-token-env-var"], "INFLUXDB_TOKEN")
            self.assertNotIn("AShareLlmQuantLeanAlgorithm", encoded)

    def test_build_strategy_implementation_payload_requests_bounded_lean_code_without_secret(self):
        module = load_module()

        payload = module.build_strategy_implementation_payload(
            {
                "title": "Momentum",
                "summary": {
                    "core_idea": "rank A-share stocks by momentum",
                    "signals": [{"name": "momentum", "formula": "return_60d"}],
                    "data_requirements": [{"dataset": "daily", "field": "close"}],
                },
            }
        )
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["model"], "glm-5.1")
        self.assertIn("SoloQuantGenerated", encoded)
        self.assertIn("QuantConnect.Algorithm.CSharp", encoded)
        self.assertIn("只输出JSON", encoded)
        self.assertNotIn("sk-", encoded)
        self.assertNotIn("apiKey", encoded)

    def test_generate_strategy_implementation_package_persists_validated_llm_code(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({"title": "Momentum", "summary": {"core_idea": "rank by returns"}}),
                encoding="utf-8",
            )

            def fake_llm(payload):
                return {
                    "class_name": "SoloQuantGeneratedMomentumAlgorithm",
                    "description": "generated momentum implementation",
                    "code": (
                        "using QuantConnect.Algorithm;\n"
                        "namespace QuantConnect.Algorithm.CSharp\n"
                        "{\n"
                        "    public class SoloQuantGeneratedMomentumAlgorithm : QCAlgorithm\n"
                        "    {\n"
                        "        public override void Initialize() { SetStartDate(2024, 1, 1); }\n"
                        "    }\n"
                        "}\n"
                    ),
                    "parameters": {"lookback-period": "60"},
                    "risk_controls": ["max positions"],
                }

            algorithm_root = root / "Algorithm.CSharp" / "SoloQuantGenerated"
            (root / "Algorithm.CSharp").mkdir()

            def fake_compile_validate(code, class_name, **kwargs):
                return {"success": True, "code": code, "attempts": 1, "errors": []}

            with mock.patch.object(module, "repo_root", return_value=root), \
                 mock.patch.object(module, "compile_validate_generated_code", side_effect=fake_compile_validate):
                package = module.generate_strategy_implementation_package(
                    summary_path,
                    generated_root=root / "generated-code",
                    algorithm_root=algorithm_root,
                    llm_implementation_client=fake_llm,
                )
            manifest = json.loads(Path(package["manifest-file"]).read_text(encoding="utf-8"))
            code_text = Path(package["code-file"]).read_text(encoding="utf-8")

            self.assertEqual(package["class-name"], "SoloQuantGeneratedMomentumAlgorithm")
            self.assertTrue(Path(package["code-file"]).exists())
            self.assertTrue(str(Path(package["code-file"])).startswith(str(algorithm_root)))
            self.assertEqual(manifest["class_name"], "SoloQuantGeneratedMomentumAlgorithm")
            self.assertEqual(manifest["algorithm_language"], "CSharp")
            self.assertTrue(manifest["algorithm_file"].endswith("SoloQuantGeneratedMomentumAlgorithm.cs"))
            self.assertIn("namespace QuantConnect.Algorithm.CSharp", code_text)
            self.assertNotIn("sk-", json.dumps(manifest, ensure_ascii=False) + code_text)

    def test_generate_strategy_implementation_package_rejects_forbidden_or_secret_code(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps({"title": "Bad", "summary": {}}), encoding="utf-8")

            def forbidden_llm(payload):
                return {"class_name": "AShareLlmQuantLeanAlgorithm", "code": "public class AShareLlmQuantLeanAlgorithm {}"}

            (root / "Algorithm.CSharp").mkdir()
            with self.assertRaisesRegex(ValueError, "forbidden"), mock.patch.object(module, "repo_root", return_value=root):
                module.generate_strategy_implementation_package(
                    summary_path,
                    generated_root=root / "generated-code",
                    algorithm_root=root / "Algorithm.CSharp" / "SoloQuantGenerated",
                    llm_implementation_client=forbidden_llm,
                )

            def secret_llm(payload):
                return {
                    "class_name": "SoloQuantGeneratedBadAlgorithm",
                    "code": "namespace QuantConnect.Algorithm.CSharp { public class SoloQuantGeneratedBadAlgorithm { string k = \"sk-secret\"; } }",
                }

            with self.assertRaisesRegex(ValueError, "Inline secret"), mock.patch.object(module, "repo_root", return_value=root):
                module.generate_strategy_implementation_package(
                    summary_path,
                    generated_root=root / "generated-code",
                    algorithm_root=root / "Algorithm.CSharp" / "SoloQuantGenerated",
                    llm_implementation_client=secret_llm,
                )

    def test_generated_strategy_algorithm_root_must_be_inside_lean_algorithm_tree(self):
        module = load_module()

        csharp_root = module.resolve_generated_algorithm_root("CSharp")
        python_root = module.resolve_generated_algorithm_root("Python")

        self.assertTrue(str(csharp_root).endswith("Algorithm.CSharp/SoloQuantGenerated"))
        self.assertTrue(str(python_root).endswith("Algorithm.Python/SoloQuantGenerated"))
        with self.assertRaisesRegex(ValueError, "LEAN algorithm directory"):
            module.resolve_generated_algorithm_root("CSharp", Path("/tmp/not-lean"))

    def test_generate_strategy_implementations_scans_reproduction_summaries(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_dir = root / "artifacts" / "reproduction" / "strategy"
            summary_dir.mkdir(parents=True)
            (summary_dir / "index.json").write_text(json.dumps({"items": []}), encoding="utf-8")
            (summary_dir / "momentum.json").write_text(json.dumps({"title": "Momentum", "summary": {}}), encoding="utf-8")

            def fake_llm(payload):
                return {
                    "class_name": "SoloQuantGeneratedMomentumAlgorithm",
                    "code": (
                        "namespace QuantConnect.Algorithm.CSharp\n"
                        "{ public class SoloQuantGeneratedMomentumAlgorithm : QCAlgorithm { } }"
                    ),
                }

            (root / "Algorithm.CSharp").mkdir()

            def fake_compile_validate(code, class_name, **kwargs):
                return {"success": True, "code": code, "attempts": 1, "errors": []}

            with mock.patch.object(module, "repo_root", return_value=root), \
                 mock.patch.object(module, "compile_validate_generated_code", side_effect=fake_compile_validate):
                report = module.generate_strategy_implementations(
                    root / "artifacts",
                    generated_root=root / "generated-code",
                    algorithm_root=root / "Algorithm.CSharp" / "SoloQuantGenerated",
                    llm_implementation_client=fake_llm,
                    run_date="20260510",
                )

            self.assertEqual(report["reproduced_count"], 1)
            self.assertTrue(Path(report["packages"][0]["code-file"]).exists())

    def test_materialize_generated_strategy_implementations_writes_three_real_generated_configs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_dir = root / "Algorithm.CSharp" / "SoloQuantGenerated" / "momentum"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "strategy_id": "momentum",
                        "class_name": "SoloQuantGeneratedMomentumAlgorithm",
                        "description": "generated strategy",
                        "parameters": {"lookback-period": "40"},
                        "source_summary": str(root / "summary.json"),
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(module, "repo_root", return_value=root):
                report = module.materialize_generated_strategy_implementations(
                    algorithm_root=manifest_dir.parent,
                    strategy_root=root / "strategies",
                    start_date="2024-01-01",
                    end_date="2024-12-31",
                    universe=["000001.SZ", "600000.SH"],
                )

            self.assertEqual(report["materialized_count"], 1)
            manifest = json.loads(Path(report["packages"][0]["manifest-file"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["template"]["algorithm-type-name"], "SoloQuantGeneratedMomentumAlgorithm")
            self.assertEqual(len(manifest["variants"]), 3)
            first_config = json.loads(Path(manifest["variants"][0]["backtest-config"]).read_text(encoding="utf-8"))
            self.assertEqual(first_config["algorithm-type-name"], "SoloQuantGeneratedMomentumAlgorithm")
            self.assertEqual(first_config["parameters"]["soloquant-generated-manifest"], str((manifest_dir / "manifest.json").resolve()))

    # ── 变更一：Path B 扩展测试 ──────────────────────────────────────────────

    def test_build_strategy_implementation_payload_supports_python_language(self):
        module = load_module()
        summary = {"title": "Momentum", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "rank by momentum"}}
        payload = module.build_strategy_implementation_payload(summary, language="Python")
        content = json.dumps(payload, ensure_ascii=False)
        self.assertIn("Python", content)
        self.assertIn("Initialize", content)
        self.assertIn("OnData", content)
        self.assertNotIn("namespace QuantConnect.Algorithm.CSharp", content)

    def test_build_strategy_implementation_payload_includes_lean_template_constraints(self):
        module = load_module()
        summary = {"title": "Factor", "url": "https://x.test", "source": "SSRN", "summary": {"core_idea": "multi-factor"}}
        for lang in ("CSharp", "Python"):
            payload = module.build_strategy_implementation_payload(summary, language=lang)
            content = json.dumps(payload, ensure_ascii=False)
            # Must require LEAN standard modules
            self.assertIn("Universe", content, f"language={lang}")
            self.assertIn("Risk", content, f"language={lang}")
            self.assertIn("Portfolio", content, f"language={lang}")
            # Must mention A-share constraints
            self.assertIn("T+1", content, f"language={lang}")

    def test_build_strategy_implementation_payload_adds_ashare_conversion_for_non_ashare_sources(self):
        module = load_module()
        # US equity strategy should trigger A-share conversion instructions
        us_summary = {"title": "S&P 500 Momentum Factor Strategy", "url": "https://arxiv.org/abs/2002.04304", "source": "arXiv", "summary": {"core_idea": "cross-sectional momentum in US equity"}}
        payload = module.build_strategy_implementation_payload(us_summary, language="CSharp")
        content = json.dumps(payload, ensure_ascii=False)
        self.assertIn("沪深300", content, "Should include A-share index replacement for US market strategy")
        self.assertIn("Market.CHINA", content, "Should specify Market.CHINA for A-share conversion")
        self.assertIn("A股交易时间", content, "Should include A-share trading hours")

        # A-share strategy should NOT trigger conversion instructions
        cn_summary = {"title": "A股多因子选股策略", "url": "https://ricequant.com/strategy", "source": "ricequant", "summary": {"core_idea": "A股动量因子"}}
        cn_payload = module.build_strategy_implementation_payload(cn_summary, language="CSharp")
        cn_content = json.dumps(cn_payload, ensure_ascii=False)
        self.assertNotIn("沪深300", cn_content, "A-share source should not trigger conversion instructions")
        self.assertNotIn("Market.CHINA", cn_content, "A-share source should not need Market.CHINA conversion")

    def test_generate_strategy_implementation_package_writes_python_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({"title": "PyMomentum", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "momentum"}}),
                encoding="utf-8",
            )
            py_code = (
                "from AlgorithmImports import *\n"
                "class SoloQuantGeneratedPyMomentumAlgorithm(QCAlgorithm):\n"
                "    def Initialize(self):\n"
                "        self.SetStartDate(2020, 1, 1)\n"
                "    def OnData(self, slice):\n"
                "        pass\n"
            )

            def fake_llm(payload):
                return {"class_name": "SoloQuantGeneratedPyMomentumAlgorithm", "code": py_code, "parameters": {}, "risk_controls": [], "data_requirements": []}

            algo_root = root / "Algorithm.Python" / "SoloQuantGenerated"
            result = module.generate_strategy_implementation_package(
                summary_path,
                generated_root=root / "generated",
                algorithm_root=algo_root,
                llm_implementation_client=fake_llm,
                language="Python",
            )
            # Python compile_validate may rename to .py.broken if ast.parse fails
            # in the test environment (no LEAN imports), so check either .py or .py.broken
            code_file = Path(result["code-file"])
            broken_file = code_file.with_suffix(".py.broken")
            self.assertTrue(code_file.exists() or broken_file.exists(),
                            f"Expected .py or .py.broken file, neither found at {code_file}")
            manifest = json.loads(Path(result["manifest-file"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["algorithm_language"], "Python")

    def test_iter_generated_strategy_manifest_files_scans_both_csharp_and_python(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cs_dir = root / "Algorithm.CSharp" / "SoloQuantGenerated" / "strat-a"
            py_dir = root / "Algorithm.Python" / "SoloQuantGenerated" / "strat-b"
            cs_dir.mkdir(parents=True)
            py_dir.mkdir(parents=True)
            (cs_dir / "manifest.json").write_text(json.dumps({"strategy_id": "strat-a", "class_name": "SoloQuantGeneratedAAlgorithm", "algorithm_language": "CSharp"}), encoding="utf-8")
            (py_dir / "manifest.json").write_text(json.dumps({"strategy_id": "strat-b", "class_name": "SoloQuantGeneratedBAlgorithm", "algorithm_language": "Python"}), encoding="utf-8")

            paths = list(module.iter_generated_strategy_manifest_files(
                csharp_root=root / "Algorithm.CSharp" / "SoloQuantGenerated",
                python_root=root / "Algorithm.Python" / "SoloQuantGenerated",
            ))
            self.assertEqual(len(paths), 2)

    def test_materialize_generated_strategy_sets_python_language_in_lean_config(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_dir = root / "Algorithm.Python" / "SoloQuantGenerated" / "py-strat"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps({
                    "strategy_id": "py-strat",
                    "class_name": "SoloQuantGeneratedPyStratAlgorithm",
                    "algorithm_language": "Python",
                    "algorithm_file": str(manifest_dir / "SoloQuantGeneratedPyStratAlgorithm.py"),
                    "parameters": {},
                }),
                encoding="utf-8",
            )
            result = module.materialize_generated_strategy_implementation(
                manifest_dir / "manifest.json",
                strategy_root=root / "strategies",
            )
            manifest = json.loads(Path(result["manifest-file"]).read_text(encoding="utf-8"))
            first_config = json.loads(Path(manifest["variants"][0]["backtest-config"]).read_text(encoding="utf-8"))
            self.assertEqual(first_config["algorithm-language"], "Python")
            self.assertIn("Algorithm.Python", first_config["algorithm-location"])

    def test_base_lean_config_defaults_to_csharp(self):
        module = load_module()
        config = module._base_lean_config("TestAlgorithm", "backtesting", "2020-01-01", "2025-12-31", {})
        self.assertEqual(config["algorithm-language"], "CSharp")
        self.assertIn("Algorithm.CSharp", config["algorithm-location"])
        self.assertIn("QuantConnect.Algorithm.CSharp.dll", config["algorithm-location"])

    def test_base_lean_config_python_sets_language_and_location(self):
        module = load_module()
        config = module._base_lean_config("TestAlgorithm", "backtesting", "2020-01-01", "2025-12-31", {}, language="Python")
        self.assertEqual(config["algorithm-language"], "Python")
        self.assertIn("Algorithm.Python", config["algorithm-location"])
        self.assertTrue(config["algorithm-location"].endswith("TestAlgorithm.py"))

    def test_base_lean_config_python_live_paper(self):
        module = load_module()
        config = module._base_lean_config("TestAlgorithm", "live-paper", "2020-01-01", "2025-12-31", {}, language="Python")
        self.assertEqual(config["algorithm-language"], "Python")
        self.assertTrue(config.get("live-mode"))

    def test_generate_strategy_implementations_forwards_language_to_package(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repro_dir = root / "reproduction" / "strategy"
            repro_dir.mkdir(parents=True)
            (repro_dir / "momentum.json").write_text(
                json.dumps({"title": "PyTest", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "momentum"}}),
                encoding="utf-8",
            )
            py_code = (
                "class SoloQuantGeneratedPyTestAlgorithm(QCAlgorithm):\n"
                "    def Initialize(self):\n"
                "        self.SetStartDate(2020, 1, 1)\n"
                "    def OnData(self, data):\n"
                "        pass\n"
            )

            def fake_llm(payload):
                return {"class_name": "SoloQuantGeneratedPyTestAlgorithm", "code": py_code, "parameters": {}, "risk_controls": [], "data_requirements": []}

            algo_root = root / "Algorithm.Python" / "SoloQuantGenerated"
            result = module.generate_strategy_implementations(
                artifact_root=root,
                generated_root=root / "generated",
                algorithm_root=algo_root,
                llm_implementation_client=fake_llm,
                language="Python",
            )
            self.assertEqual(result["reproduced_count"], 1)
            manifest = json.loads(Path(result["packages"][0]["manifest-file"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["algorithm_language"], "Python")

    def test_llm_config_reads_llm_key_with_glm_fallback(self):
        module = load_module()
        # Config with 'llm' key should be preferred over 'glm'
        config_llm = {"llm": {"base-url": "https://llm.test/v1", "model": "deepseek-v3", "api-key-env-var": "LLM_KEY", "timeout-seconds": 60}}
        config_glm = {"glm": {"base-url": "https://glm.test/v1", "model": "glm-5.1", "api-key-env-var": "LLM_API_KEY", "timeout-seconds": 60}}
        config_both = {**config_glm, **config_llm}

        calls = []
        def fake_post(url, payload, headers, timeout):
            calls.append({"url": url, "headers": headers})
            return {"choices": [{"message": {"content": "{}"}}]}

        with mock.patch.dict(module.os.environ, {"LLM_KEY": "llm-key-val", "LLM_API_KEY": "glm-key-val"}, clear=False):
            client_llm = module.create_http_client_from_config(config_llm, post_json=fake_post)
            client_glm = module.create_http_client_from_config(config_glm, post_json=fake_post)
            client_both = module.create_http_client_from_config(config_both, post_json=fake_post)

        self.assertEqual(client_llm.llm_base_url, "https://llm.test/v1")
        self.assertEqual(client_glm.llm_base_url, "https://glm.test/v1")
        self.assertEqual(client_both.llm_base_url, "https://llm.test/v1")

    def test_update_strategy_registry_persists_best_version_without_duplicates(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            result = {
                "best_version": "v2",
                "best_score": 0.31,
                "evaluated_versions": [
                    {"version": "baseline", "score": 0.1},
                    {"version": "v1", "score": 0.2},
                    {"version": "v2", "score": 0.31},
                ],
            }

            module.update_strategy_registry(registry_path, "news-momentum", result, live_config_path="/tmp/live.json")
            module.update_strategy_registry(registry_path, "news-momentum", result, live_config_path="/tmp/live.json")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))

            self.assertEqual(len(registry["strategies"]), 1)
            self.assertEqual(registry["strategies"][0]["strategy_id"], "news-momentum")
            self.assertEqual(registry["strategies"][0]["best_version"], "v2")
            self.assertEqual(registry["strategies"][0]["live-paper-config"], "/tmp/live.json")

    def test_optimize_strategy_packages_requires_three_versions_and_updates_registry(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            strategy_root = root / "strategies"
            package_root = strategy_root / "news-momentum"
            package_root.mkdir(parents=True)
            registry_path = root / "registry.json"
            commands = []

            variants = []
            for index, score in enumerate([0.1, 0.3, 0.2]):
                config_path = package_root / f"config-v{index}.json"
                summary_path = package_root / f"summary-v{index}.json"
                live_path = package_root / f"live-v{index}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "parameters": {
                                "summary-file": str(summary_path),
                                "soloquant-score-metric": "score",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                summary_path.write_text(json.dumps({"score": score}), encoding="utf-8")
                live_path.write_text(json.dumps({"environment": "live-paper"}), encoding="utf-8")
                variants.append(
                    {
                        "version": f"v{index}",
                        "backtest-config": str(config_path),
                        "live-paper-config": str(live_path),
                    }
                )

            (package_root / "manifest.json").write_text(
                json.dumps({"strategy-id": "news-momentum", "variants": variants}),
                encoding="utf-8",
            )

            report = module.optimize_strategy_packages(
                {
                    "strategy-root": str(strategy_root),
                    "registry-file": str(registry_path),
                    "strategy-policy": {"min-versions": 3, "best-metric": "score"},
                },
                runner=lambda command, cwd: commands.append(command) or 0,
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))

            self.assertEqual(report["optimized_count"], 1)
            self.assertEqual(report["results"][0]["best_version"], "v1")
            self.assertEqual(len(commands), 3)
            self.assertEqual(registry["strategies"][0]["best_version"], "v1")
            self.assertTrue(registry["strategies"][0]["live-paper-config"].endswith("live-v1.json"))

    def test_optimize_strategy_packages_rejects_incomplete_variant_sets(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_root = Path(temp_dir) / "strategies"
            package_root = strategy_root / "incomplete"
            package_root.mkdir(parents=True)
            (package_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "strategy-id": "incomplete",
                        "variants": [
                            {"version": "baseline", "backtest-config": str(package_root / "config.json")},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "requires at least 3 variants"):
                module.optimize_strategy_packages(
                    {
                        "strategy-root": str(strategy_root),
                        "registry-file": str(Path(temp_dir) / "registry.json"),
                        "strategy-policy": {"min-versions": 3},
                    },
                    runner=lambda command, cwd: 0,
                )

    def test_http_client_builds_searxng_crawl4ai_and_llm_requests(self):
        module = load_module()
        post_calls = []
        get_calls = []

        def fake_post(url, payload, headers, timeout):
            post_calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
            if "crawl" in url:
                return {"success": True, "results": [{"url": payload["urls"][0], "markdown": "content"}]}
            return {"valuable_items": [{"title": "Alpha", "url": "https://example.test/alpha", "is_valuable": True}]}

        def fake_get(url, params, headers, timeout):
            get_calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
            return {"results": [{"title": "Alpha", "url": "https://example.test/alpha"}]}

        client = module.SoloQuantHttpClient(
            searxng_url="http://localhost:11236",
            crawl4ai_url="http://localhost:11235",
            llm_base_url="https://api.deepseek.com",
            llm_api_key="secret-key",
            post_json=fake_post,
            get_json=fake_get,
        )

        results = client.search({"query": "q", "category": "strategy"})
        page = client.crawl({"url": results[0]["url"]})
        screened = client.screen_with_llm(module.build_llm_screening_payload([{"title": "Alpha", "content": page["markdown"]}]))

        self.assertEqual(results[0]["title"], "Alpha")
        self.assertEqual(page["content"], "content")
        self.assertEqual(screened["valuable_items"][0]["title"], "Alpha")
        self.assertTrue(get_calls[0]["url"].endswith("/search"))
        self.assertEqual(get_calls[0]["params"]["format"], "json")
        self.assertTrue(post_calls[0]["url"].endswith("/crawl"))
        self.assertEqual(post_calls[0]["payload"]["urls"], ["https://example.test/alpha"])
        self.assertTrue(post_calls[1]["url"].endswith("/chat/completions"))
        self.assertEqual(post_calls[1]["headers"]["Authorization"], "Bearer secret-key")

    def test_build_job_specs_reuses_tushare_downloader_and_soloquant_commands(self):
        module = load_module()
        config = module.load_config(overrides={"workflow-root": "/tmp/soloquant"})

        specs = module.build_job_specs(config)
        by_name = {spec["name"]: spec for spec in specs}

        self.assertIn("tushare-incremental-scheduler", by_name)
        self.assertIn("soloquant-crawl-strategy", by_name)
        self.assertIn("soloquant-full-auto-pipeline", by_name)
        self.assertIn("soloquant-live-paper", by_name)
        self.assertIn("soloquant-prepare-reproduction", by_name)
        self.assertIn("soloquant-generate-strategy-implementations", by_name)
        self.assertNotIn("soloquant-materialize-reproduction", by_name)  # Path A removed
        self.assertIn("soloquant-analyze-finance-intelligence", by_name)
        self.assertIn("soloquant-build-finance-event-graph", by_name)
        self.assertIn("soloquant-export-research-influx", by_name)
        self.assertIn("soloquant-export-finance-event-graph-influx", by_name)
        self.assertIn("soloquant-export-strategy-results-influx", by_name)
        self.assertIn("soloquant-export-price-ohlc", by_name)
        self.assertTrue(by_name["tushare-incremental-scheduler"]["command"][1].endswith("incremental_scheduler.py"))
        self.assertTrue(by_name["soloquant-crawl-strategy"]["command"][1].endswith("soloquant_crawl_scheduler.py"))
        self.assertTrue(by_name["soloquant-full-auto-pipeline"]["command"][1].endswith("soloquant_pipeline_runner.py"))
        self.assertIn("--daemon", by_name["soloquant-full-auto-pipeline"]["command"])
        self.assertIn("--poll-seconds", by_name["soloquant-full-auto-pipeline"]["command"])
        self.assertTrue(by_name["soloquant-full-auto-pipeline"]["long-running"])
        self.assertIn("--once", by_name["soloquant-crawl-strategy"]["command"])
        self.assertIn("--force", by_name["soloquant-crawl-strategy"]["command"])
        self.assertIn("strategy", by_name["soloquant-crawl-strategy"]["command"])
        self.assertIn("finance_intelligence", by_name["soloquant-crawl-news"]["command"])
        self.assertIn("--prepare-reproduction", by_name["soloquant-prepare-reproduction"]["command"])
        self.assertIn("--generate-strategy-implementations", by_name["soloquant-generate-strategy-implementations"]["command"])
        self.assertIn("--analyze-finance-intelligence", by_name["soloquant-analyze-finance-intelligence"]["command"])
        self.assertIn("--build-finance-event-graph", by_name["soloquant-build-finance-event-graph"]["command"])
        self.assertIn("--optimize-strategies", by_name["soloquant-optimize"]["command"])
        self.assertIn("--export-research-influx", by_name["soloquant-export-research-influx"]["command"])
        self.assertIn("--export-finance-event-graph-influx", by_name["soloquant-export-finance-event-graph-influx"]["command"])
        self.assertIn("--export-strategy-results-influx", by_name["soloquant-export-strategy-results-influx"]["command"])
        self.assertTrue(by_name["soloquant-export-price-ohlc"]["command"][1].endswith("export_price_ohlc_to_influx.py"))
        self.assertIn("--run-live-paper", by_name["soloquant-live-paper"]["command"])
        self.assertIn("*/5 * * * 1-5", by_name["soloquant-live-paper"]["cron"])
        self.assertTrue(by_name["tushare-incremental-scheduler"]["long-running"])
        self.assertTrue(by_name["soloquant-live-paper"]["long-running"])

    def test_persist_crawled_items_writes_category_date_and_deduped_index(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {
                    "category": "strategy",
                    "source": "arXiv q-fin",
                    "title": "Alpha",
                    "url": "https://example.test/alpha",
                    "content": "A" * 600,
                },
                {
                    "category": "strategy",
                    "source": "arXiv q-fin",
                    "title": "Alpha duplicate",
                    "url": "https://example.test/alpha",
                    "content": "B" * 600,
                },
                {
                    "category": "finance_intelligence",
                    "source": "交易所公告",
                    "title": "上交所提示债券交易波动风险",
                    "url": "https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml",
                    "content": (
                        "2026年5月10日，上海证券交易所发布通知，提示债券交易波动风险，要求会员加强风险管理。"
                        "该公告可能影响债券久期、低波红利和流动性偏好。"
                    ),
                },
            ]

            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")

            self.assertEqual(report["written_count"], 2)
            self.assertEqual(report["duplicate_count"], 1)
            self.assertEqual(len(report["files"]), 2)
            self.assertTrue((artifact_root / "crawled" / "strategy" / "index.json").exists())
            self.assertTrue((artifact_root / "crawled" / "finance_intelligence" / "index.json").exists())
            first_payload = json.loads(Path(report["files"][0]).read_text(encoding="utf-8"))
            self.assertIn("content_hash", first_payload)
            self.assertNotIn("secret-key", json.dumps(first_payload))

    def test_persist_crawled_items_rejects_low_quality_finance_intelligence(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {
                    "category": "finance_intelligence",
                    "source": "权威财经媒体",
                    "title": "证券时报官方网站-中国资本市场信息披露平台",
                    "url": "https://www.stcn.com/",
                    "content": "首页 快讯 新闻 视频 投资 数据 信披 专题 服务 公众号 客户端 登录",
                },
                {
                    "category": "finance_intelligence",
                    "source": "交易所公告",
                    "title": "上交所提示债券交易波动风险",
                    "url": "https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml",
                    "content": (
                        "2026年5月10日，上海证券交易所发布通知，提示债券交易波动风险，要求会员加强风险管理。"
                        "该公告可能影响债券久期、低波红利和流动性偏好。"
                    ),
                },
            ]

            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")

            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["rejected_count"], 1)
            payload = json.loads(Path(report["files"][0]).read_text(encoding="utf-8"))
            self.assertEqual(payload["title"], "上交所提示债券交易波动风险")
            self.assertTrue(payload["quality"]["accepted"])

    def test_persist_screened_items_writes_decisions_and_summary_by_category(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            decisions = [
                {
                    "title": "Useful strategy",
                    "url": "https://example.test/useful",
                    "category": "strategy",
                    "required_fields": [{"dataset": "daily", "field": "close"}],
                },
                {
                    "title": "Useful news",
                    "url": "https://example.test/news",
                    "category": "finance_intelligence",
                },
            ]

            report = module.persist_screened_items(decisions, artifact_root, run_date="20260510")

            self.assertEqual(report["written_count"], 2)
            self.assertTrue((artifact_root / "screened" / "strategy" / "valuable-items.json").exists())
            self.assertTrue((artifact_root / "screened" / "finance_intelligence" / "valuable-items.json").exists())
            strategy_payload = json.loads((artifact_root / "screened" / "strategy" / "valuable-items.json").read_text(encoding="utf-8"))
            self.assertEqual(strategy_payload["items"][0]["title"], "Useful strategy")

    def test_run_crawl_pipeline_searches_crawls_screens_and_writes_artifacts(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            config = module.load_config(
                overrides={
                    "artifact-root": str(Path(temp_dir) / "artifacts"),
                    "missing-data-log": str(Path(temp_dir) / "missing-data.jsonl"),
                    "data": {
                        "field-mapping-path": str(Path(temp_dir) / "mapping.json"),
                        "tushare-data-path": str(Path(temp_dir) / "tushare"),
                        "field-cache-root": str(Path(temp_dir) / "field-cache"),
                    },
                }
            )
            Path(config["data"]["field-mapping-path"]).write_text(
                json.dumps({"datasets": {"daily": {"fields": {"close": {}}}}}),
                encoding="utf-8",
            )

            def fake_search(query):
                return [{"title": f"{query.get('source', 'generic')} result", "url": f"https://arxiv.org/abs/2501.{abs(hash(query.get('source', 'x')))%10000:04d}"}]

            def fake_crawl(result):
                return {"content": f"crawled {result['title']} - " + "x" * 600}

            def fake_screen(payload):
                return {
                    "valuable_items": [
                        {
                            "title": "Useful",
                            "url": "https://arxiv.org/abs/2501.99999",
                            "category": "strategy",
                            "is_valuable": True,
                            "required_fields": [
                                {"dataset": "daily", "field": "close"},
                                {"dataset": "external_news", "field": "sentiment"},
                            ],
                        }
                    ]
                }

            report = module.run_crawl_pipeline(
                config,
                keywords=["资金流"],
                categories=["strategy"],
                search_client=fake_search,
                crawl_client=fake_crawl,
                llm_screen_client=fake_screen,
                run_date="20260510",
                max_queries=2,
                max_results_per_query=1,
            )

            self.assertGreaterEqual(report["crawled"]["written_count"], 1)
            self.assertEqual(report["screened"]["written_count"], 1)
            self.assertEqual(report["data_requirements"]["available"][0]["field"], "close")
            self.assertEqual(report["data_requirements"]["supplemented"][0]["field"], "sentiment")
            self.assertEqual(report["data_requirements"]["missing"], [])
            self.assertFalse(Path(config["missing-data-log"]).exists())

    def test_run_search_and_crawl_skips_failed_pages_without_failing_batch(self):
        module = load_module()

        def fake_search(query):
            return [
                {"title": "blocked", "url": "https://example.test/blocked", "content": "search fallback"},
                {"title": "good", "url": "https://example.test/good"},
            ]

        def fake_crawl(result):
            if "blocked" in result["url"]:
                raise RuntimeError("anti-bot")
            return {"content": "full page"}

        items = module.run_search_and_crawl(
            {"queries": [{"category": "strategy", "source": "test", "query": "alpha"}]},
            search_client=fake_search,
            crawl_client=fake_crawl,
        )

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["crawl_error"], "anti-bot")
        self.assertEqual(items[0]["content"], "search fallback")
        self.assertEqual(items[1]["content"], "full page")

    def test_run_search_and_crawl_preserves_query_category_over_search_result_category(self):
        module = load_module()

        items = module.run_search_and_crawl(
            {"queries": [{"category": "finance_intelligence", "source": "test", "query": "macro"}]},
            search_client=lambda query: [{"title": "news", "url": "https://example.test/news", "category": "general"}],
            crawl_client=lambda result: {"content": "market risk"},
            max_results_per_query=1,
        )

        self.assertEqual(items[0]["category"], "finance_intelligence")

    def test_run_search_and_crawl_prefilters_finance_intelligence_homepages(self):
        module = load_module()
        crawled_urls = []

        def fake_crawl(result):
            crawled_urls.append(result["url"])
            return {
                "content": (
                    "2026年5月10日，上海证券交易所发布通知，提示债券交易波动风险，要求会员加强风险管理。"
                    "该公告可能影响债券久期、低波红利和流动性偏好。"
                )
            }

        items = module.run_search_and_crawl(
            {"queries": [{"category": "finance_intelligence", "source": "交易所公告", "query": "risk"}]},
            search_client=lambda query: [
                {"title": "上海证券交易所: 首页", "url": "https://www.sse.com.cn/"},
                {"title": "上交所提示债券交易波动风险", "url": "https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml"},
            ],
            crawl_client=fake_crawl,
            max_results_per_query=2,
        )

        self.assertEqual(crawled_urls, ["https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml"])
        self.assertEqual(len(items), 1)

    def test_run_search_and_crawl_prefers_finance_direct_urls_before_search_results(self):
        module = load_module()
        crawled_urls = []

        def fake_crawl(result):
            crawled_urls.append(result["url"])
            return {
                "content": (
                    "2026年5月10日，人民银行发布公开市场业务交易公告，逆回购操作维护流动性合理充裕。"
                    "该信息影响利率、债券久期和市场风险偏好。"
                )
            }

        items = module.run_search_and_crawl(
            {
                "queries": [
                    {
                        "category": "finance_intelligence",
                        "source": "央行与监管机构",
                        "query": "公开市场业务交易公告",
                        "direct_urls": ["https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"],
                    }
                ]
            },
            search_client=lambda query: [{"title": "上海证券交易所: 首页", "url": "https://www.sse.com.cn/"}],
            crawl_client=fake_crawl,
            max_results_per_query=1,
        )

        self.assertEqual(crawled_urls, ["https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html"])
        self.assertEqual(items[0]["source_url_mode"], "direct")

    def test_run_search_and_crawl_expands_finance_direct_source_links(self):
        module = load_module()
        crawled_urls = []

        def fake_crawl(result):
            crawled_urls.append(result["url"])
            if result["url"].endswith("/general/"):
                return {
                    "content": (
                        "[关于债券交易风险提示的通知]"
                        "(https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml)"
                    )
                }
            return {
                "content": (
                    "2026年5月10日，上海证券交易所发布债券交易风险提示通知。"
                    "该通知影响利率、债券久期和风险偏好。"
                )
            }

        items = module.run_search_and_crawl(
            {
                "queries": [
                    {
                        "category": "finance_intelligence",
                        "source": "交易所公告",
                        "query": "交易风险提示",
                        "direct_urls": ["https://www.sse.com.cn/disclosure/notice/general/"],
                    }
                ]
            },
            search_client=lambda query: [],
            crawl_client=fake_crawl,
            max_results_per_query=3,
        )

        self.assertEqual(
            crawled_urls,
            [
                "https://www.sse.com.cn/disclosure/notice/general/",
                "https://www.sse.com.cn/disclosure/notice/general/c/20260510/123456.shtml",
            ],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_url_mode"], "direct_child")

    def test_run_crawl_pipeline_supplements_missing_required_fields_before_placeholder_fallback(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping_path = root / "mapping.json"
            mapping_path.write_text(json.dumps({"datasets": {"daily": {"fields": {"close": {}}}}}), encoding="utf-8")
            config = module.load_config(
                overrides={
                    "workflow-root": str(root),
                    "artifact-root": str(root / "artifacts"),
                    "missing-data-log": str(root / "missing-data.jsonl"),
                    "data": {
                        "field-mapping-path": str(mapping_path),
                        "field-cache-root": str(root / "field-cache"),
                        "tushare-data-path": str(root / "tushare_data"),
                    },
                }
            )

            def fake_search(query):
                if query.get("category") == "data_supplement":
                    return [{"title": "sentiment", "url": "https://example.test/sentiment"}]
                return [{"title": "strategy", "url": "https://example.test/strategy"}]

            def fake_crawl(result):
                if result["url"].endswith("/sentiment"):
                    return {"content": "sentiment proxy value 0.5"}
                return {"content": "strategy idea"}

            def fake_llm(payload):
                return {
                    "valuable_items": [
                        {
                            "title": "strategy",
                            "url": "https://example.test/strategy",
                            "category": "strategy",
                            "required_fields": [{"field": "news_sentiment", "dataset": "external_news"}],
                        }
                    ]
                }

            report = module.run_crawl_pipeline(
                config,
                keywords=["momentum"],
                categories=["strategy"],
                search_client=fake_search,
                crawl_client=fake_crawl,
                llm_screen_client=fake_llm,
                run_date="20260510",
                max_queries=1,
                max_results_per_query=1,
            )

            self.assertEqual(report["data_requirements"]["missing"], [])
            self.assertEqual(report["data_requirements"]["supplemented"][0]["field"], "news_sentiment")

    def test_create_http_client_from_config_reads_llm_key_from_environment_only(self):
        module = load_module()
        config = module.load_config()

        with mock.patch.dict(module.os.environ, {"LLM_API_KEY": "env-secret"}, clear=False):
            client = module.create_http_client_from_config(config, post_json=lambda url, payload, headers, timeout: {})

        self.assertEqual(client.llm_api_key, "env-secret")
        self.assertNotIn("env-secret", json.dumps(config, ensure_ascii=False))

    def test_run_best_live_paper_strategy_uses_registry_live_config_and_launcher_shape(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_config = root / "config-live-paper.json"
            live_config.write_text(json.dumps({"environment": "live-paper"}), encoding="utf-8")
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {
                                "strategy_id": "alpha-a",
                                "best_version": "v2",
                                "best_score": 0.42,
                                "live-paper-config": str(live_config),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            commands = []

            def fake_runner(command, cwd):
                commands.append((command, cwd))
                return 0

            result = module.run_best_live_paper_strategy(registry_path, runner=fake_runner)

            self.assertEqual(result["strategy_id"], "alpha-a")
            self.assertEqual(result["best_version"], "v2")
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(commands[0][0][0], "/usr/local/dotnet/dotnet")
            self.assertTrue(commands[0][0][1].endswith("Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"))
            self.assertEqual(commands[0][0][2:], ["--config", str(live_config.resolve())])

    def test_research_artifacts_export_to_influx_line_protocol_without_secrets(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            screened_path = artifact_root / "screened" / "strategy" / "valuable-items.json"
            screened_path.parent.mkdir(parents=True)
            screened_path.write_text(
                json.dumps(
                    {
                        "category": "strategy",
                        "run_date": "20260510",
                        "items": [
                            {
                                "title": "Useful strategy",
                                "url": "https://example.test/alpha",
                                "source": "arXiv q-fin",
                                "strategy_idea": "momentum after analyst revisions",
                                "implementation_steps": ["rank symbols", "rebalance daily"],
                                "evidence": "backtest sharpe 1.2",
                                "required_fields": [{"dataset": "daily", "field": "close"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            lines = module.collect_research_artifact_influx_lines(artifact_root, run_date="20260510")

            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("soloquant_research_artifact,category=strategy"))
            self.assertIn('title="Useful strategy"', lines[0])
            self.assertIn('required_field_count=1', lines[0])
            self.assertNotIn("sk-", lines[0])
            self.assertNotIn("glsa_", lines[0])
            self.assertNotIn("admin-token", lines[0])

    def test_finance_event_graph_export_to_influx_line_protocol_preserves_nodes_and_edges(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            graph_path = artifact_root / "event-graph" / "finance_intelligence" / "graph.json"
            graph_path.parent.mkdir(parents=True)
            graph_path.write_text(
                json.dumps(
                    {
                        "graph_id": "finance_intelligence:20260510",
                        "run_date": "20260510",
                        "nodes": [
                            {
                                "id": "event:policy-liquidity",
                                "type": "event",
                                "name": "央行净投放流动性",
                                "properties": {
                                    "risk_level": "medium",
                                    "impact_analysis": "短端利率可能下行",
                                    "key_points": ["人民银行逆回购净投放"],
                                    "source_type": "central_bank",
                                },
                            },
                            {
                                "id": "entity:bond",
                                "type": "entity",
                                "name": "债券",
                                "properties": {"entity_type": "asset"},
                            },
                        ],
                        "edges": [
                            {
                                "id": "edge-1",
                                "source": "event:policy-liquidity",
                                "target": "entity:bond",
                                "type": "EVENT_IMPACTS_ENTITY",
                                "properties": {"relationship_strength": 2},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            lines = module.collect_finance_event_graph_influx_lines(artifact_root, run_date="20260510")

            self.assertEqual(len(lines), 3)
            self.assertTrue(any(line.startswith("soloquant_finance_event_node,") for line in lines))
            self.assertTrue(any(line.startswith("soloquant_finance_event_edge,") for line in lines))
            encoded = "\n".join(lines)
            self.assertIn("node_type=event", encoded)
            self.assertIn("risk_level=medium", encoded)
            self.assertIn("source_type=central_bank", encoded)
            self.assertIn("entity_type=asset", encoded)
            self.assertIn("edge_type=EVENT_IMPACTS_ENTITY", encoded)
            self.assertIn('name="央行净投放流动性"', encoded)
            self.assertIn('risk_level="medium"', encoded)
            self.assertIn("key_point_count=1", encoded)
            self.assertNotIn("sk-", encoded)
            self.assertNotIn("glsa_", encoded)
            self.assertNotIn("admin-token", encoded)

    def test_export_finance_event_graph_to_influx_uses_env_token_and_supports_dry_run(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            graph_path = artifact_root / "event-graph" / "finance_intelligence" / "graph.json"
            graph_path.parent.mkdir(parents=True)
            graph_path.write_text(
                json.dumps(
                    {
                        "graph_id": "finance_intelligence:20260510",
                        "run_date": "20260510",
                        "nodes": [{"id": "event:1", "type": "event", "name": "event", "properties": {}}],
                        "edges": [{"id": "edge:1", "source": "event:1", "target": "entity:1", "type": "EVENT_IMPACTS_ENTITY", "properties": {}}],
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "artifact-root": str(artifact_root),
                "influxdb": {
                    "url": "http://localhost:8086",
                    "org": "lean",
                    "bucket": "quant",
                    "token-env-var": "INFLUXDB_TOKEN",
                },
            }
            calls = []

            def fake_writer(lines, influx_url, org, bucket, token):
                calls.append({"lines": list(lines), "url": influx_url, "org": org, "bucket": bucket, "token": token})
                return len(calls[0]["lines"])

            dry_report = module.export_finance_event_graph_to_influx(config, run_date="20260510", dry_run=True, writer=fake_writer)
            with mock.patch.dict(module.os.environ, {"INFLUXDB_TOKEN": "env-token"}, clear=False):
                report = module.export_finance_event_graph_to_influx(config, run_date="20260510", writer=fake_writer)

            self.assertEqual(dry_report["lines"], 2)
            self.assertEqual(dry_report["written"], 0)
            self.assertEqual(report["written"], 2)
            self.assertEqual(calls[0]["token"], "env-token")

    def test_strategy_results_export_to_influx_line_protocol_covers_backtest_and_live_paper(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            live_config = root / "config-live-paper.json"
            live_snapshot = root / "live-portfolio.json"
            live_config.write_text(
                json.dumps({"parameters": {"portfolio-snapshot-file": str(live_snapshot)}}),
                encoding="utf-8",
            )
            live_snapshot.write_text(json.dumps({"total_return": 0.14, "total_value": 1140000}), encoding="utf-8")
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {
                                "strategy_id": "alpha-a",
                                "best_version": "v2",
                                "best_score": 0.14,
                                "live-paper-config": str(live_config),
                                "evaluated_versions": [
                                    {
                                        "version": "baseline",
                                        "score": 0.10,
                                        "summary": {
                                            "total_return": 0.10,
                                            "total_value": 1100000,
                                            "total_pnl": 100000,
                                            "cash": 900000,
                                            "market_value": 200000,
                                            "positions": [{"symbol": "000001.SZ"}],
                                        },
                                    },
                                    {
                                        "version": "v2",
                                        "score": 0.14,
                                        "summary": {"total_return": 0.14, "positions": []},
                                    },
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            lines = module.collect_strategy_result_influx_lines(registry_path, include_live_paper=True)

            encoded = "\n".join(lines)
            self.assertEqual(len(lines), 3)
            self.assertTrue(lines[0].startswith("soloquant_strategy_result,"))
            self.assertIn("strategy_id=alpha-a", encoded)
            self.assertIn("stage=backtest", encoded)
            self.assertIn("stage=live-paper", encoded)
            self.assertIn("is_best=true", encoded)
            self.assertIn("total_return=0.14", encoded)
            self.assertIn("position_count=1", encoded)
            self.assertNotIn("admin-token", encoded)

    def test_export_strategy_results_to_influx_uses_env_token_and_supports_dry_run(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {
                                "strategy_id": "alpha-a",
                                "best_version": "baseline",
                                "best_score": 0.1,
                                "evaluated_versions": [
                                    {"version": "baseline", "score": 0.1, "summary": {"total_return": 0.1}},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "registry-file": str(registry_path),
                "influxdb": {
                    "url": "http://localhost:8086",
                    "org": "lean",
                    "bucket": "quant",
                    "token-env-var": "INFLUXDB_TOKEN",
                },
            }
            calls = []

            def fake_writer(lines, influx_url, org, bucket, token):
                calls.append({"lines": list(lines), "url": influx_url, "org": org, "bucket": bucket, "token": token})
                return len(calls[0]["lines"])

            dry_report = module.export_strategy_results_to_influx(config, dry_run=True, writer=fake_writer)
            with mock.patch.dict(module.os.environ, {"INFLUXDB_TOKEN": "env-token"}, clear=False):
                report = module.export_strategy_results_to_influx(config, writer=fake_writer)

            self.assertEqual(dry_report["lines"], 1)
            self.assertEqual(dry_report["written"], 0)
            self.assertEqual(report["written"], 1)
            self.assertEqual(calls[0]["token"], "env-token")


    # ── 变更三：修复琐碎问题 ──────────────────────────────────────────────

    def test_validate_generated_strategy_code_rejects_file_read_terms_csharp(self):
        module = load_module()
        base_code = (
            "namespace QuantConnect.Algorithm.CSharp {\n"
            "    public class SoloQuantGeneratedTestAlgorithm : QCAlgorithm {}\n"
            "}"
        )
        for term in ("File.ReadAllText", "StreamReader", "File.Open", "File.ReadAllLines", "File.ReadAllBytes"):
            with self.assertRaises(ValueError, msg=f"should reject {term}"):
                module.validate_generated_strategy_code(
                    "SoloQuantGeneratedTestAlgorithm",
                    base_code + f"\n// {term}",
                )

    def test_validate_generated_strategy_code_rejects_file_read_terms_python(self):
        module = load_module()
        base_code = (
            "class SoloQuantGeneratedTestAlgorithm(QCAlgorithm):\n"
            "    def Initialize(self): pass\n"
            "    def OnData(self, data): pass\n"
        )
        # Each term is injected as it would appear in real code (not as a comment)
        for term, injection in [
            ("import requests", "import requests\n"),
            ("requests.", "x = requests.get('http://x')\n"),
            ("import subprocess", "import subprocess\n"),
            ("subprocess.", "subprocess.run(['ls'])\n"),
            ("urllib.", "urllib.request.urlopen('http://x')\n"),
            ("open(", "f = open('data.csv')\n"),
            ("pd.read_csv(", "df = pd.read_csv('data.csv')\n"),
            ("pd.read_parquet(", "df = pd.read_parquet('data.parquet')\n"),
        ]:
            with self.assertRaises(ValueError, msg=f"should reject {term}"):
                module.validate_generated_strategy_code(
                    "SoloQuantGeneratedTestAlgorithm",
                    base_code + injection,
                    language="Python",
                )

    def test_score_variant_prefers_sharpe_ratio(self):
        module = load_module()

        sharpe_summary = {"sharpe_ratio": 1.5, "total_return": 0.3, "max_drawdown": 0.1}
        calmar_summary = {"calmar_ratio": 2.0, "total_return": 0.3, "max_drawdown": 0.1}
        fallback_summary = {"total_return": 0.3, "max_drawdown": 0.1}
        empty_summary = {}

        self.assertAlmostEqual(module._score_variant(sharpe_summary), 1.5)
        self.assertAlmostEqual(module._score_variant(calmar_summary), 2.0)
        self.assertAlmostEqual(module._score_variant(fallback_summary), 0.2)
        self.assertEqual(module._score_variant(empty_summary), float("-inf"))

    def test_update_strategy_lifecycle_keeps_candidate_when_no_live_data(self):
        import importlib.util as _ilu
        import sys as _sys
        _pr_path = Path(__file__).resolve().parents[3] / "Scripts" / "soloquant_pipeline_runner.py"
        _spec = _ilu.spec_from_file_location("soloquant_pipeline_runner_tmp", _pr_path)
        _pr = _ilu.module_from_spec(_spec)
        _sys.modules[_spec.name] = _pr
        _spec.loader.exec_module(_pr)

        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            registry_path.write_text(
                json.dumps({
                    "strategies": [
                        {"strategy_id": "no-live", "best_score": 0.5},
                    ]
                }),
                encoding="utf-8",
            )
            report = _pr.update_strategy_lifecycle(
                registry_path,
                serving_score_threshold=0.0,
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(report["serving_count"], 0)
            self.assertEqual(registry["strategies"][0]["status"], "candidate")

    def test_update_strategy_registry_is_thread_safe(self):
        import threading
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "registry.json"
            errors = []

            def write_strategy(i):
                try:
                    module.update_strategy_registry(
                        registry_path,
                        strategy_id=f"strategy-{i}",
                        optimization_result={"best_version": "baseline", "best_score": 0.1 * i, "evaluated_versions": []},
                        live_config_path=f"/tmp/live-{i}.json",
                    )
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=write_strategy, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            registry = module.load_json_payload(registry_path)
            self.assertEqual(len(registry["strategies"]), 10)

    # ── 变更二：财经情报事件分类与策略信号闭环 ──────────────────────────────

    def test_classify_finance_event_type_returns_correct_category(self):
        module = load_module()
        self.assertEqual(module.classify_finance_event_type("央行降息50bp，流动性宽松"), "monetary_policy")
        self.assertEqual(module.classify_finance_event_type("FOMC会议决定维持利率不变"), "monetary_policy")
        self.assertEqual(module.classify_finance_event_type("证监会发布新规，加强退市监管"), "regulatory")
        self.assertEqual(module.classify_finance_event_type("CPI同比上涨2.1%，通胀压力温和"), "macro_data")
        self.assertEqual(module.classify_finance_event_type("美国对华加征关税，贸易战升级"), "geopolitical")
        self.assertEqual(module.classify_finance_event_type("某公司发布业绩预告，净利润增长30%"), "corporate")
        self.assertEqual(module.classify_finance_event_type("市场风险偏好下降，VIX指数上升"), "sentiment")
        self.assertEqual(module.classify_finance_event_type("交易所调整涨跌停规则"), "market_structure")
        # Unknown text should return "unknown"
        self.assertEqual(module.classify_finance_event_type("今天天气不错"), "unknown")

    def test_finance_event_types_constant_has_seven_categories(self):
        module = load_module()
        self.assertIn("FINANCE_EVENT_TYPES", dir(module))
        event_types = module.FINANCE_EVENT_TYPES
        for category in ("monetary_policy", "regulatory", "macro_data", "market_structure", "geopolitical", "corporate", "sentiment"):
            self.assertIn(category, event_types, f"Missing category: {category}")

    def test_build_finance_intelligence_analysis_payload_includes_event_type_schema(self):
        module = load_module()
        item = {"title": "央行降息", "url": "https://pbc.gov.cn/test", "source": "央行", "content": "央行宣布降息50bp"}
        payload = module.build_finance_intelligence_analysis_payload(item)
        content = json.dumps(payload, ensure_ascii=False)
        self.assertIn("event_type", content)
        self.assertIn("signal_direction", content)
        self.assertIn("affected_sectors", content)

    def test_build_event_signal_context_from_graph(self):
        module = load_module()
        graph = {
            "nodes": [
                {"id": "evt-1", "type": "event", "properties": {
                    "event_type": "monetary_policy", "signal_direction": "bullish",
                    "risk_level": "low", "key_points": ["央行降息50bp"],
                    "affected_sectors": ["银行", "地产"],
                }},
                {"id": "evt-2", "type": "event", "properties": {
                    "event_type": "regulatory", "signal_direction": "bearish",
                    "risk_level": "high", "key_points": ["监管处罚"],
                    "affected_sectors": ["互联网"],
                }},
                {"id": "evt-3", "type": "event", "properties": {
                    "event_type": "macro_data", "signal_direction": "neutral",
                    "risk_level": "medium", "key_points": ["CPI 2.1%"],
                    "affected_sectors": [],
                }},
            ],
            "edges": [],
        }
        context = module.build_event_signal_context(graph)
        self.assertIn("monetary_policy_signals", context)
        self.assertIn("regulatory_signals", context)
        self.assertIn("composite_risk_level", context)
        self.assertIn("affected_sectors", context)
        self.assertEqual(len(context["monetary_policy_signals"]), 1)
        self.assertEqual(context["monetary_policy_signals"][0]["direction"], "bullish")
        self.assertEqual(len(context["regulatory_signals"]), 1)
        self.assertEqual(context["regulatory_signals"][0]["direction"], "bearish")
        # composite_risk_level should be "high" because one event is high risk
        self.assertEqual(context["composite_risk_level"], "high")
        self.assertIn("银行", context["affected_sectors"])
        self.assertIn("互联网", context["affected_sectors"])

    def test_build_event_signal_context_persists_to_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            graph = {
                "nodes": [{"id": "e1", "type": "event", "properties": {"event_type": "monetary_policy", "signal_direction": "bullish", "risk_level": "low", "key_points": [], "affected_sectors": []}}],
                "edges": [],
            }
            result = module.build_event_signal_context(graph, artifact_root=artifact_root, run_date="20260512")
            signal_file = artifact_root / "event-signals" / "signal-context.json"
            self.assertTrue(signal_file.exists())
            saved = json.loads(signal_file.read_text(encoding="utf-8"))
            self.assertIn("monetary_policy_signals", saved)

    def test_build_strategy_implementation_payload_injects_event_signal_context(self):
        module = load_module()
        summary = {"title": "Momentum", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "rank by momentum"}}
        signal_context = {
            "composite_risk_level": "high",
            "monetary_policy_signals": [{"direction": "bearish", "strength": 0.8, "summary": "加息"}],
            "regulatory_signals": [],
            "geopolitical_signals": [],
            "affected_sectors": ["科技"],
        }
        payload = module.build_strategy_implementation_payload(summary, event_signal_context=signal_context)
        content = json.dumps(payload, ensure_ascii=False)
        self.assertIn("event_signal_context", content)
        self.assertIn("composite_risk_level", content)
        self.assertIn("high", content)
        # Should include instructions about adjusting position size and universe
        self.assertIn("position", content.lower())
        self.assertIn("universe", content.lower())

    def test_build_strategy_query_uses_site_restriction_not_source_prefix(self):
        module = load_module()
        q = module.build_strategy_query("arXiv q-fin", "alpha factor backtest")
        self.assertEqual(q["source"], "arXiv q-fin")
        self.assertIn("site:arxiv.org", q["query"])
        self.assertNotIn("arXiv q-fin arXiv", q["query"])
        q_ssrn = module.build_strategy_query("SSRN", "equity anomaly")
        self.assertIn("site:papers.ssrn.com", q_ssrn["query"])
        self.assertNotIn("SSRN SSRN", q_ssrn["query"])

    def test_build_research_query_plan_no_doubled_source_in_query_text(self):
        module = load_module()
        plan = module.build_research_query_plan(["alpha factor backtest", "多因子 策略"])
        for item in plan["queries"]:
            query = item["query"]
            source = item.get("source", "")
            if source and source + " " + source in query:
                self.fail(f"Query contains doubled source name: source={source!r}, query={query!r}")

    def test_build_research_query_plan_tick_offset_shuffles_differently(self):
        module = load_module()
        plan0 = module.build_research_query_plan(["alpha", "momentum", "factor"], tick_offset=0)
        plan1 = module.build_research_query_plan(["alpha", "momentum", "factor"], tick_offset=1)
        queries0 = [q["query"] for q in plan0["queries"]]
        queries1 = [q["query"] for q in plan1["queries"]]
        self.assertNotEqual(queries0, queries1, "Different tick_offset should produce different query ordering")

    def test_is_strategy_listing_or_code_url_rejects_arxiv_category_pages(self):
        module = load_module()
        self.assertTrue(module.is_strategy_listing_or_code_url("https://arxiv.org/list/q-fin/new"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://arxiv.org/archive/q-fin"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://arxiv.org/search/?query=momentum"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://arxiv.org/conference/q-fin"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://arxiv.org/html/2501.12345v1"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://github.com/user/repo"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://github.com/user/repo/issues"))
        self.assertTrue(module.is_strategy_listing_or_code_url("https://github.com/user/repo/tree/main"))
        self.assertFalse(module.is_strategy_listing_or_code_url("https://arxiv.org/abs/2501.12345"))
        self.assertFalse(module.is_strategy_listing_or_code_url("https://arxiv.org/pdf/2501.12345"))
        self.assertFalse(module.is_strategy_listing_or_code_url("https://github.com/user/repo/blob/main/strategy.py"))
        self.assertFalse(module.is_strategy_listing_or_code_url("https://raw.githubusercontent.com/user/repo/main/strategy.py"))

    def test_should_crawl_strategy_result_rejects_github_repos_and_ssrn_abstracts(self):
        module = load_module()
        result = module.should_crawl_strategy_result({"url": "https://github.com/user/repo", "title": "My Repo"})
        self.assertFalse(result["accepted"])
        self.assertIn("github_repo", result["reject_reasons"])
        result2 = module.should_crawl_strategy_result({"url": "https://arxiv.org/list/q-fin/new", "title": "Listing"})
        self.assertFalse(result2["accepted"])
        # raw.githubusercontent.com should be accepted
        result3 = module.should_crawl_strategy_result({"url": "https://raw.githubusercontent.com/user/repo/main/strategy.py", "title": "Strategy"})
        self.assertTrue(result3["accepted"])
        # SSRN abstract-only pages should be rejected
        result4 = module.should_crawl_strategy_result({"url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=12345", "title": "Paper"})
        self.assertFalse(result4["accepted"])
        self.assertIn("ssrn_abstract_only", result4["reject_reasons"])
        # SSRN PDF URLs should be accepted
        result5 = module.should_crawl_strategy_result({"url": "https://papers.ssrn.com/sol3/Delivery.cfm/12345/pdf", "title": "Paper PDF"})
        self.assertTrue(result5["accepted"])

        # Social media should be rejected
        result6 = module.should_crawl_strategy_result({"url": "https://www.reddit.com/r/algotrading/comments/abc/how", "title": "Reddit post"})
        self.assertFalse(result6["accepted"])
        self.assertIn("social_media", result6["reject_reasons"])

        # Competition platform homepage should be rejected
        result7 = module.should_crawl_strategy_result({"url": "https://numer.ai/", "title": "Numerai"})
        self.assertFalse(result7["accepted"])
        self.assertIn("competition_homepage", result7["reject_reasons"])

        # Exchange listing page should be rejected
        result8 = module.should_crawl_strategy_result({"url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/", "title": "公开市场业务交易公告"})
        self.assertFalse(result8["accepted"])
        self.assertIn("exchange_listing", result8["reject_reasons"])

    def test_persist_crawled_items_rejects_short_strategy_content(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {"category": "strategy", "source": "arXiv", "title": "Short", "url": "https://x.test/short", "content": "too short"},
                {"category": "strategy", "source": "arXiv", "title": "Long", "url": "https://x.test/long", "content": "x" * 600},
            ]
            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")
            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["rejected_count"], 1)

    def test_persist_crawled_items_rejects_oversized_strategy_content(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {"category": "strategy", "source": "arXiv", "title": "Book", "url": "https://x.test/book", "content": "x" * (module.STRATEGY_MAX_CONTENT_CHARS + 1)},
                {"category": "strategy", "source": "arXiv", "title": "Paper", "url": "https://x.test/paper", "content": "x" * 600},
            ]
            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")
            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["rejected_count"], 1)

    def test_prepare_reproduction_summaries_skips_oversized_content(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            # Write a crawled strategy file with oversized content
            crawled_root = artifact_root / "crawled" / "strategy"
            crawled_root.mkdir(parents=True, exist_ok=True)
            big_item = {"category": "strategy", "source": "arXiv", "title": "Book", "url": "https://x.test/book", "content": "x" * (module.STRATEGY_MAX_CONTENT_CHARS + 1), "content_hash": "abc123"}
            module.write_json_payload(crawled_root / "test-book.json", big_item)
            small_item = {"category": "strategy", "source": "arXiv", "title": "Paper", "url": "https://x.test/paper", "content": "x" * 600, "content_hash": "def456"}
            module.write_json_payload(crawled_root / "test-paper.json", small_item)
            # Write index
            module.write_json_payload(crawled_root / "index.json", {"items": [big_item, small_item]})

            def fake_llm(payload):
                return {"choices": [{"message": {"content": json.dumps({"core_idea": "test", "signals": [], "risk_controls": [], "data_requirements": [], "reproduction_steps": []})}}]}

            with mock.patch.object(module, "download_and_extract_pdf_text", return_value=None):
                report = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["skipped_count"], 1)

    def test_download_and_extract_pdf_text_returns_error_for_oversized_content(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            # Create a fake PDF text file with oversized content
            crawled_root = artifact_root / "crawled" / "strategy"
            crawled_root.mkdir(parents=True, exist_ok=True)
            pdf_dir = artifact_root / "pdf" / "strategy"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            big_content = "x" * (module.STRATEGY_MAX_CONTENT_CHARS + 1)
            text_path = pdf_dir / "test-book.txt"
            text_path.write_text(big_content, encoding="utf-8")
            pdf_path = pdf_dir / "test-book.pdf"
            pdf_path.write_bytes(b"fake pdf")

            item = {"category": "strategy", "source": "test", "title": "Book", "url": "https://x.test/book", "pdf_url": "https://x.test/book.pdf"}
            with mock.patch.object(module, "pdf_artifact_paths", return_value=(pdf_path, text_path, pdf_dir / "test-book.md")):
                result = module.download_and_extract_pdf_text(item, artifact_root, category="strategy", run_date="20260510")
            self.assertIsNotNone(result)
            self.assertIn("PDF content too large", result.get("pdf_error", ""))

    def test_normalize_paper_url_deduplicates_arxiv_abs_and_pdf(self):
        module = load_module()
        # /abs/ and /pdf/ should normalize to the same canonical URL
        self.assertEqual(module.normalize_paper_url("https://arxiv.org/abs/2409.06289"), "https://arxiv.org/abs/2409.06289")
        self.assertEqual(module.normalize_paper_url("https://arxiv.org/pdf/2409.06289"), "https://arxiv.org/abs/2409.06289")
        # SSRN abstract_id should normalize
        self.assertEqual(module.normalize_paper_url("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=12345"), "https://papers.ssrn.com/abstract_id=12345")
        self.assertEqual(module.normalize_paper_url("https://papers.ssrn.com/sol3/Delivery.cfm/12345/pdf"), "https://papers.ssrn.com/abstract_id=12345")

    def test_persist_crawled_items_deduplicates_same_paper_different_urls(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {"category": "strategy", "source": "arXiv", "title": "Alpha Mining", "url": "https://arxiv.org/abs/2409.06289", "content": "x" * 600},
                {"category": "strategy", "source": "arXiv", "title": "Alpha Mining", "url": "https://arxiv.org/pdf/2409.06289", "content": "y" * 600},
            ]
            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")
            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["duplicate_count"], 1)

    def test_persist_crawled_items_deduplicates_by_title(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            items = [
                {"category": "strategy", "source": "Quantpedia", "title": "Small Cap Premium", "url": "https://quantpedia.com/strategies/small-cap", "content": "x" * 600},
                {"category": "strategy", "source": "SSRN", "title": "Small Cap Premium", "url": "https://ssrn.com/abstract_id=9999", "content": "y" * 600},
            ]
            report = module.persist_crawled_items(items, artifact_root, run_date="20260510")
            self.assertEqual(report["written_count"], 1)
            self.assertEqual(report["duplicate_count"], 1)

    def test_prepare_reproduction_summaries_skips_already_summarized_urls(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "paper.json").write_text(
                json.dumps({"title": "Momentum", "url": "https://arxiv.org/abs/2501.1", "source": "arXiv", "content": "x" * 600}),
                encoding="utf-8",
            )
            llm_calls = 0

            def fake_llm(payload):
                nonlocal llm_calls
                llm_calls += 1
                return {"summary": {"core_idea": "momentum strategy"}}

            # First call: writes summary
            report1 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            self.assertEqual(report1["written_count"], 1)
            self.assertEqual(llm_calls, 1)
            # Second call: should skip (dedup by URL)
            report2 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            self.assertEqual(report2["written_count"], 0)
            self.assertGreaterEqual(report2["skipped_count"], 1)
            self.assertEqual(llm_calls, 1, "GLM should not be called again for already-summarized URL")

    def test_generate_strategy_implementation_package_skips_existing_manifest(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({"title": "ExistingStrategy", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "momentum"}}),
                encoding="utf-8",
            )
            algo_root = root / "Algorithm.CSharp" / "SoloQuantGenerated"
            strategy_dir = algo_root / "existingstrategy"
            strategy_dir.mkdir(parents=True)
            (strategy_dir / "SoloQuantGeneratedExistingStrategyAlgorithm.cs").write_text("// code", encoding="utf-8")
            (strategy_dir / "manifest.json").write_text(
                json.dumps({"strategy_id": "existingstrategy", "class_name": "SoloQuantGeneratedExistingStrategyAlgorithm", "code_file": str(strategy_dir / "SoloQuantGeneratedExistingStrategyAlgorithm.cs")}),
                encoding="utf-8",
            )
            llm_calls = 0

            def fake_llm(payload):
                nonlocal llm_calls
                llm_calls += 1
                return {"class_name": "SoloQuantGeneratedExistingStrategyAlgorithm", "code": "class X {}", "parameters": {}, "risk_controls": [], "data_requirements": []}

            result = module.generate_strategy_implementation_package(
                summary_path,
                generated_root=root / "generated",
                algorithm_root=algo_root,
                llm_implementation_client=fake_llm,
            )
            self.assertTrue(result.get("skipped_existing"))
            self.assertEqual(llm_calls, 0, "GLM should not be called when manifest already exists")

    def test_generate_strategy_implementation_package_regenerates_when_code_missing(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({"title": "BrokenStrategy", "url": "https://x.test", "source": "arXiv", "summary": {"core_idea": "momentum"}}),
                encoding="utf-8",
            )
            algo_root = root / "Algorithm.CSharp" / "SoloQuantGenerated"
            strategy_dir = algo_root / "brokenstrategy"
            strategy_dir.mkdir(parents=True)
            # Manifest exists but .cs file is missing (deleted by compile step)
            (strategy_dir / "manifest.json").write_text(
                json.dumps({"strategy_id": "brokenstrategy", "class_name": "SoloQuantGeneratedBrokenStrategyAlgorithm", "code_file": str(strategy_dir / "SoloQuantGeneratedBrokenStrategyAlgorithm.cs")}),
                encoding="utf-8",
            )
            # .cs.broken file exists from previous compile failure
            (strategy_dir / "SoloQuantGeneratedBrokenStrategyAlgorithm.cs.broken").write_text("// broken code", encoding="utf-8")
            llm_calls = 0

            def fake_llm(payload):
                nonlocal llm_calls
                llm_calls += 1
                return {
                    "class_name": "SoloQuantGeneratedBrokenStrategyAlgorithm",
                    "code": "using QuantConnect.Algorithm;\nnamespace QuantConnect.Algorithm.CSharp { class SoloQuantGeneratedBrokenStrategyAlgorithm : QCAlgorithm {} }",
                    "parameters": {},
                    "risk_controls": [],
                    "data_requirements": [],
                }

            result = module.generate_strategy_implementation_package(
                summary_path,
                generated_root=root / "generated",
                algorithm_root=algo_root,
                llm_implementation_client=fake_llm,
                config_or_none=None,  # no dotnet binary → compile_validate will use default and likely fail gracefully
            )
            self.assertFalse(result.get("skipped_existing"), "Should regenerate when code file is missing")
            self.assertGreaterEqual(llm_calls, 1, "LLM should be called at least once to regenerate code")
            # After regeneration, either .cs or .cs.broken should exist (compile may fail in test env)
            cs_exists = (strategy_dir / "SoloQuantGeneratedBrokenStrategyAlgorithm.cs").exists()
            broken_exists = (strategy_dir / "SoloQuantGeneratedBrokenStrategyAlgorithm.cs.broken").exists()
            self.assertTrue(cs_exists or broken_exists, "New .cs or .cs.broken file should be written")

    def test_generate_strategy_implementations_continues_on_individual_failure(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repro_dir = root / "reproduction" / "strategy"
            repro_dir.mkdir(parents=True)
            (repro_dir / "bad.json").write_text(
                json.dumps({"title": "BadStrategy", "url": "https://x.test/bad", "source": "arXiv", "summary": {"core_idea": "bad"}}),
                encoding="utf-8",
            )
            (repro_dir / "good.json").write_text(
                json.dumps({"title": "GoodStrategy", "url": "https://x.test/good", "source": "arXiv", "summary": {"core_idea": "good"}}),
                encoding="utf-8",
            )
            call_count = 0
            py_code = (
                "class SoloQuantGeneratedGoodStrategyAlgorithm(QCAlgorithm):\n"
                "    def Initialize(self):\n"
                "        self.SetStartDate(2020, 1, 1)\n"
                "    def OnData(self, data):\n"
                "        pass\n"
            )

            def fake_llm(payload):
                nonlocal call_count
                call_count += 1
                title = ""
                # Check which strategy is being generated by inspecting the payload
                source = payload.get("source", {}) if isinstance(payload.get("source"), dict) else {}
                if isinstance(payload.get("reproduction_summary"), dict):
                    title = payload["reproduction_summary"].get("title", "")
                if "BadStrategy" in str(payload):
                    raise RuntimeError("GLM persistent failure for bad strategy")
                return {"class_name": "SoloQuantGeneratedGoodStrategyAlgorithm", "code": py_code, "parameters": {}, "risk_controls": [], "data_requirements": []}

            result = module.generate_strategy_implementations(
                artifact_root=root,
                generated_root=root / "generated",
                algorithm_root=root / "algo",
                llm_implementation_client=fake_llm,
                language="Python",
            )
            self.assertGreaterEqual(result["error_count"], 1)
            self.assertGreaterEqual(result["reproduced_count"], 1)

    def test_collect_strategy_progress_lines_reads_registry_and_manifests(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {
                                "strategy_id": "alpha-a",
                                "best_version": "baseline",
                                "best_score": 0.12,
                                "live_score": 0.10,
                                "status": "serving",
                                "evaluated_versions": [
                                    {"version": "baseline", "score": 0.12, "summary": {"total_return": 0.12}},
                                ],
                            },
                            {
                                "strategy_id": "beta-b",
                                "best_version": "baseline",
                                "best_score": 0.05,
                                "status": "candidate",
                                "evaluated_versions": [
                                    {"version": "baseline", "score": 0.05, "summary": {"total_return": 0.05}},
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "artifact-root": str(artifact_root),
                "registry-file": str(registry_path),
            }
            lines = module.collect_strategy_progress_lines(config)
            self.assertGreaterEqual(len(lines), 2)
            alpha_line = next(l for l in lines if "alpha-a" in l)
            beta_line = next(l for l in lines if "beta-b" in l)
            self.assertIn("serving", alpha_line)
            self.assertIn("backtested", beta_line)
            self.assertIn("pipeline_stage_code=4", alpha_line)
            self.assertIn("pipeline_stage_code=2", beta_line)
            self.assertIn("best_score=0.12", alpha_line)
            self.assertIn("live_score=0.1", alpha_line)

    def test_collect_pipeline_funnel_lines_counts_stages(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "index.json").write_text(
                json.dumps({"category": "strategy", "count": 42, "items": [{"url": f"https://arxiv.org/abs/2501.{i}"} for i in range(42)]}),
                encoding="utf-8",
            )
            repro_dir = artifact_root / "reproduction" / "strategy"
            repro_dir.mkdir(parents=True)
            (repro_dir / "summary-abc.json").write_text("{}", encoding="utf-8")
            (repro_dir / "summary-def.json").write_text("{}", encoding="utf-8")
            (repro_dir / "index.json").write_text(
                json.dumps({"category": "strategy", "count": 2, "items": [{"url": "https://arxiv.org/abs/2501.0"}, {"url": "https://arxiv.org/abs/2501.1"}]}),
                encoding="utf-8",
            )
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "strategies": [
                            {"strategy_id": "a", "best_score": 0.1, "status": "serving", "live_score": 0.09},
                            {"strategy_id": "b", "best_score": 0.05, "status": "candidate"},
                            {"strategy_id": "c", "best_score": 0.08, "status": "retired", "live_score": 0.02},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "artifact-root": str(artifact_root),
                "registry-file": str(registry_path),
            }
            lines = module.collect_pipeline_funnel_lines(config)
            # First line is the aggregate funnel, followed by 7 per-stage lines
            self.assertGreaterEqual(len(lines), 8)
            funnel_line = lines[0]
            self.assertIn("crawled_count=42", funnel_line)
            self.assertIn("summarized_count=2", funnel_line)
            self.assertIn("backtested_count=3", funnel_line)
            self.assertIn("serving_count=1", funnel_line)
            self.assertIn("retired_count=1", funnel_line)
            self.assertIn("live_paper_count=2", funnel_line)
            # Verify per-stage lines exist
            stage_lines = lines[1:]
            self.assertEqual(len(stage_lines), 7)
            self.assertTrue(any("已爬取" in l for l in stage_lines))
            self.assertTrue(any("已提炼" in l for l in stage_lines))
            self.assertTrue(any("已复现" in l for l in stage_lines))
            self.assertTrue(any("已回测" in l for l in stage_lines))
            self.assertTrue(any("服役中" in l for l in stage_lines))
            self.assertTrue(any("已除役" in l for l in stage_lines))

    def test_export_strategy_pipeline_progress_uses_env_token_and_supports_dry_run(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry_path = root / "strategy-registry.json"
            registry_path.write_text(
                json.dumps({"strategies": [{"strategy_id": "x", "best_score": 0.1, "status": "candidate", "evaluated_versions": []}]}),
                encoding="utf-8",
            )
            config = {
                "artifact-root": str(root / "artifacts"),
                "registry-file": str(registry_path),
                "influxdb": {
                    "url": "http://localhost:8086",
                    "org": "lean",
                    "bucket": "quant",
                    "token-env-var": "INFLUXDB_TOKEN",
                },
            }
            calls = []

            def fake_writer(lines, influx_url, org, bucket, token):
                calls.append({"lines": list(lines), "token": token})
                return len(calls[0]["lines"])

            dry_report = module.export_strategy_pipeline_progress(config, dry_run=True, writer=fake_writer)
            with mock.patch.dict(module.os.environ, {"INFLUXDB_TOKEN": "test-token"}, clear=False):
                report = module.export_strategy_pipeline_progress(config, writer=fake_writer)

            self.assertEqual(dry_report["written"], 0)
            self.assertGreater(dry_report["progress_lines"], 0)
            self.assertGreater(report["written"], 0)
            self.assertEqual(calls[0]["token"], "test-token")


    def test_prepare_reproduction_summaries_cross_date_dedup_prevents_duplicate_files(self):
        """URL summarized on day 1 must NOT be re-summarized on day 2.

        The dedup must check ALL historical reproduction indexes, not just today's,
        to prevent the same URL from producing duplicate files across dates.
        """
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            # Day 1: crawl and summarize
            crawled_dir_d1 = artifact_root / "crawled" / "strategy"
            crawled_dir_d1.mkdir(parents=True)
            (crawled_dir_d1 / "paper.json").write_text(
                json.dumps({"title": "Momentum", "url": "https://arxiv.org/abs/2501.1", "source": "arXiv", "content": "x" * 600}),
                encoding="utf-8",
            )
            llm_calls = 0

            def fake_llm(payload):
                nonlocal llm_calls
                llm_calls += 1
                return {"core_idea": f"momentum strategy variant {llm_calls}"}

            report_d1 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            self.assertEqual(report_d1["written_count"], 1)
            self.assertEqual(llm_calls, 1)

            # Day 2: same URL crawled again
            crawled_dir_d2 = artifact_root / "crawled" / "strategy"
            # File already exists from day 1, no need to recreate

            report_d2 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260511")
            self.assertEqual(report_d2["written_count"], 0, "Same URL must not produce a second summary file")
            self.assertGreaterEqual(report_d2["skipped_count"], 1)
            self.assertEqual(llm_calls, 1, "LLM must not be called again for already-summarized URL")

    def test_prepare_reproduction_summaries_filename_stable_across_llm_calls(self):
        """Reproduction summary filename must be URL-based, not summary-dependent.

        If the filename includes the LLM summary hash, non-deterministic LLM output
        creates different filenames for the same URL, causing duplicate files.
        """
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "paper.json").write_text(
                json.dumps({"title": "Momentum", "url": "https://arxiv.org/abs/2501.1", "source": "arXiv", "content": "x" * 600}),
                encoding="utf-8",
            )
            call_count = 0

            def fake_llm(payload):
                nonlocal call_count
                call_count += 1
                # Return different summaries each call to simulate non-deterministic LLM
                return {"core_idea": f"variant {call_count}"}

            # First call
            report1 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            self.assertEqual(report1["written_count"], 1)
            file1 = Path(report1["files"][0])

            # Delete the index to force re-processing (simulates a fresh tick where index was lost)
            index_path = artifact_root / "reproduction" / "strategy" / "index.json"
            index_path.unlink(missing_ok=True)

            # Second call with different LLM output - should produce the SAME filename
            report2 = module.prepare_reproduction_summaries(artifact_root, fake_llm, run_date="20260510")
            if report2["written_count"] > 0:
                file2 = Path(report2["files"][0])
                self.assertEqual(file1.name, file2.name, "Filename must be stable regardless of LLM output variation")

    def test_collect_pipeline_funnel_lines_counts_unique_urls_not_inflated_index(self):
        """Funnel metrics must count unique URLs, not inflated index entries.

        When the same URL appears in the index multiple times, the count must
        reflect unique URLs, not the sum of all index counts.
        """
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"

            # Create crawled index with unique URLs
            crawled_dir = artifact_root / "crawled" / "strategy"
            crawled_dir.mkdir(parents=True)
            (crawled_dir / "index.json").write_text(
                json.dumps({
                    "category": "strategy",
                    "count": 2,
                    "items": [
                        {"url": "https://arxiv.org/abs/2501.1", "title": "Paper A"},
                        {"url": "https://arxiv.org/abs/2501.2", "title": "Paper B"},
                    ],
                }),
                encoding="utf-8",
            )

            # Create reproduction index with unique URLs
            repro_dir = artifact_root / "reproduction" / "strategy"
            repro_dir.mkdir(parents=True)
            (repro_dir / "index.json").write_text(
                json.dumps({
                    "category": "strategy",
                    "count": 2,
                    "items": [
                        {"url": "https://arxiv.org/abs/2501.1", "title": "Paper A"},
                        {"url": "https://arxiv.org/abs/2501.2", "title": "Paper B"},
                    ],
                }),
                encoding="utf-8",
            )

            registry_path = root / "strategy-registry.json"
            registry_path.write_text(json.dumps({"strategies": []}), encoding="utf-8")

            config = {
                "artifact-root": str(artifact_root),
                "registry-file": str(registry_path),
            }
            lines = module.collect_pipeline_funnel_lines(config)
            self.assertGreaterEqual(len(lines), 8)  # 1 aggregate + 7 per-stage
            line = lines[0]

            # With unique URL counting: crawled=2, summarized=2
            self.assertIn("crawled_count=2", line, "crawled_count must count unique URLs")
            self.assertIn("summarized_count=2", line, "summarized_count must count unique URLs")

    def test_postprocess_generated_python_code_adds_algorithm_imports(self):
        module = load_module()
        code = "class SoloQuantGeneratedTestAlgorithm(QCAlgorithm):\n    pass\n"
        result = module.postprocess_generated_python_code(code)
        self.assertIn("from AlgorithmImports import *", result)

    def test_postprocess_generated_python_code_fixes_rsi_to_relative_strength_index(self):
        module = load_module()
        code = "from AlgorithmImports import *\nclass A(QCAlgorithm):\n    rsi = RSI('SPY', 14)\n"
        result = module.postprocess_generated_python_code(code)
        self.assertNotIn("RSI(", result)
        self.assertIn("RelativeStrengthIndex", result)

    def test_postprocess_generated_python_code_adds_market_to_add_equity(self):
        module = load_module()
        code = "from AlgorithmImports import *\nclass A(QCAlgorithm):\n    def Initialize(self):\n        self.AddEquity('000001.SZ', Resolution.Daily)\n"
        result = module.postprocess_generated_python_code(code)
        self.assertIn("Market.SSE", result)

    def test_postprocess_generated_python_code_adds_cny_currency_for_ashare(self):
        module = load_module()
        code = "from AlgorithmImports import *\nclass A(QCAlgorithm):\n    def Initialize(self):\n        self.AddEquity('000001.SZ', Resolution.Daily, Market.SSE)\n"
        result = module.postprocess_generated_python_code(code)
        self.assertIn("SetAccountCurrency('CNY')", result)

    def test_postprocess_generated_python_code_fixes_is_ready_method_to_property(self):
        module = load_module()
        code = "from AlgorithmImports import *\nclass A(QCAlgorithm):\n    def Initialize(self):\n        if self.rsi.IsReady():\n            pass\n"
        result = module.postprocess_generated_python_code(code)
        self.assertNotIn("IsReady()", result)
        self.assertIn("IsReady", result)

    def test_postprocess_generated_python_code_removes_forbidden_imports(self):
        module = load_module()
        code = "import requests\nimport subprocess\nfrom AlgorithmImports import *\nclass A(QCAlgorithm):\n    pass\n"
        result = module.postprocess_generated_python_code(code)
        self.assertNotIn("import requests\n", result)
        self.assertNotIn("import subprocess\n", result)

    def test_compile_validate_generated_python_code_passes_valid_code(self):
        module = load_module()
        # Use simple code that doesn't reference LEAN types (which aren't importable in test)
        code = "x = 1\ny = 2\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = module.compile_validate_generated_python_code(
                code, "SoloQuantGeneratedTestAlgorithm",
                algorithm_root=Path(temp_dir),
            )
            self.assertTrue(result["success"], f"Valid code should pass: {result.get('errors')}")

    def test_compile_validate_generated_python_code_catches_syntax_error(self):
        module = load_module()
        code = "class SoloQuantGeneratedBadAlgorithm(QCAlgorithm):\n    def Initialize(\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = module.compile_validate_generated_python_code(
                code, "SoloQuantGeneratedBadAlgorithm",
                algorithm_root=Path(temp_dir),
            )
            self.assertFalse(result["success"])
            self.assertGreater(len(result["errors"]), 0)

    def test_parse_broken_cs_files_extracts_error_paths(self):
        module = load_module()
        build_output = "/some/path/SoloQuantGeneratedBadAlgorithm.cs(10,5): error CS0103: The name 'bad' does not exist"
        with tempfile.TemporaryDirectory() as temp_dir:
            gen_root = Path(temp_dir) / "SoloQuantGenerated"
            gen_root.mkdir()
            result = module.parse_broken_cs_files(build_output, gen_root)
            # The path in the build output doesn't start with gen_root, so it won't match
            # Test with a path that does start with gen_root
            cs_file = gen_root / "SoloQuantGeneratedBadAlgorithm.cs"
            cs_file.write_text("// bad", encoding="utf-8")
            build_output2 = f"{cs_file}(10,5): error CS0103: The name 'bad' does not exist"
            result2 = module.parse_broken_cs_files(build_output2, gen_root)
            self.assertEqual(len(result2), 1)
            self.assertEqual(result2[0], cs_file)

    def test_smoke_test_generated_strategies_skips_missing_code(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Create a manifest pointing to a non-existent code file
            cs_root = root / "Algorithm.CSharp" / "SoloQuantGenerated" / "test-strat"
            cs_root.mkdir(parents=True)
            manifest = {
                "strategy_id": "test-strat",
                "class_name": "SoloQuantGeneratedTestAlgorithm",
                "algorithm_language": "CSharp",
                "code_file": str(cs_root / "SoloQuantGeneratedTestAlgorithm.cs"),
            }
            (cs_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            config = {"lean": {"dotnet-binary": "/usr/local/dotnet/dotnet"}}
            # Mock iter_generated_strategy_manifest_files to return our test manifest
            with mock.patch.object(module, "iter_generated_strategy_manifest_files", return_value=[cs_root / "manifest.json"]):
                report = module.smoke_test_generated_strategies(config)
            self.assertEqual(report["skipped_count"], 1)
            self.assertEqual(report["passed_count"], 0)
            self.assertEqual(report["failed_count"], 0)


if __name__ == "__main__":
    unittest.main()


class SoloQuantLocalIngestTests(unittest.TestCase):
    def test_ingest_local_strategies_ingests_markdown_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            # Write a markdown strategy file
            md_content = "# Momentum Strategy\n\nThis strategy uses cross-sectional momentum ranking.\n\n" + "x" * 600
            (local_dir / "momentum-strategy.md").write_text(md_content, encoding="utf-8")
            report = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["ingested"], 1)
            self.assertEqual(report["crawled_items"], 1)
            # Verify file appeared in crawled/strategy/
            crawled_files = list((artifact_root / "crawled" / "strategy").glob("*.json"))
            self.assertGreaterEqual(len(crawled_files), 1)

    def test_ingest_local_strategies_ingests_text_file(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            txt_content = "Mean reversion pairs trading strategy using cointegration.\n\n" + "y" * 600
            (local_dir / "pairs-trading.txt").write_text(txt_content, encoding="utf-8")
            report = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["ingested"], 1)
            self.assertEqual(report["crawled_items"], 1)

    def test_ingest_local_strategies_ingests_json_summary(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            summary = {
                "core_idea": "Risk parity with equal risk contribution",
                "signals": [{"name": "volatility", "formula": "std(returns, 252)", "direction": "inverse"}],
                "risk_controls": ["max drawdown 20%"],
                "data_requirements": [],
                "reproduction_steps": ["compute volatility", "allocate by inverse vol"],
            }
            (local_dir / "risk-parity.json").write_text(json.dumps(summary), encoding="utf-8")
            report = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["ingested"], 1)
            self.assertEqual(report["reproduction_items"], 1)
            # Verify file appeared in reproduction/strategy/
            repro_files = list((artifact_root / "reproduction" / "strategy").glob("*.json"))
            self.assertGreaterEqual(len(repro_files), 1)

    def test_ingest_local_strategies_skips_already_ingested(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            md_content = "# Strategy\n\n" + "z" * 600
            (local_dir / "strategy.md").write_text(md_content, encoding="utf-8")
            # First run
            report1 = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report1["ingested"], 1)
            # Second run - same file, same mtime
            report2 = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report2["ingested"], 0)
            self.assertEqual(report2["skipped"], 1)

    def test_ingest_local_strategies_ignores_missing_dir(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "nonexistent"
            artifact_root = Path(temp_dir) / "artifacts"
            report = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["ingested"], 0)

    def test_ingest_local_strategies_respects_max_content_chars(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            # Write an oversized text file
            big_content = "x" * (module.STRATEGY_MAX_CONTENT_CHARS + 1)
            (local_dir / "huge-strategy.txt").write_text(big_content, encoding="utf-8")
            report = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["ingested"], 0)
            self.assertEqual(report["skipped"], 1)
            # Verify tracking file records it as skipped
            tracking = module.load_json_payload(local_dir / ".ingested.json")
            file_entries = tracking.get("files", {})
            self.assertTrue(any(v.get("status") == "skipped_too_large" for v in file_entries.values()))

    def test_ingest_local_strategies_detects_new_file_after_first_ingest(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_dir = Path(temp_dir) / "local-strategies"
            local_dir.mkdir()
            artifact_root = Path(temp_dir) / "artifacts"
            # First file
            (local_dir / "strategy-a.md").write_text("# A\n\n" + "a" * 600, encoding="utf-8")
            report1 = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report1["ingested"], 1)
            # Add a second file
            (local_dir / "strategy-b.md").write_text("# B\n\n" + "b" * 600, encoding="utf-8")
            report2 = module.ingest_local_strategies(local_dir, artifact_root, run_date="20260522")
            self.assertEqual(report2["ingested"], 1)
            self.assertEqual(report2["skipped"], 1)  # strategy-a already ingested
