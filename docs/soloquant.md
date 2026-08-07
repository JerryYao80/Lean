# SoloQuant Design

## Development Phases

SoloQuant extends this A-share LEAN repository into a dynamic automated quant system. It is not a request to add one fixed strategy. Development proceeds one feature at a time under TDD: write or update the focused test, confirm it fails, implement the smallest production change, then run the feature tests before moving on.

### Phase 0 - Architecture, Safety, And Contracts

Status: in progress.

Functional points:

- Define SoloQuant as an orchestration layer around LEAN, not as a strategy embedded in LEAN.
- Keep all service credentials out of source files; configs may only reference environment variable names such as `GLM_API_KEY`, `INFLUXDB_TOKEN`, and `GRAFANA_TOKEN`.
- Reject inline service secrets in SoloQuant config loading.
- Standardize all strategy backtest and live-paper execution through:

  `/usr/local/dotnet/dotnet ./Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll --config <strategy-json>`

- Maintain a versioned strategy registry so live paper always runs the current best validated strategy.
- Keep generated artifacts under this repository's `Results/soloquant/` and reusable config under `Launcher/config/config-soloquant.json`. SoloQuant must not depend on `/home/project/Results/soloquant` as a runtime root.
- Treat data lookahead as a hard correctness violation. For daily Tushare bars, `TushareHistoryProvider` may only return bars whose `EndTime` is less than or equal to the LEAN history request end time. A strategy evaluating before the A-share close cannot see that same day's close.
- Require every generated or template strategy to use LEAN `History` through the configured provider rather than reading full Parquet files directly inside algorithm code.
- Prefer close-after-close advisory generation for daily signals, or next-session execution semantics when signals are evaluated after market close. Intraday/open-time daily strategies must use only the previous completed daily bar.
- Fail or quarantine backtest results when inputs contain placeholder fields, missing OHLCV values, or unverified synthetic prices; such results may be used for pipeline smoke tests but not promoted to `serving`.

### Phase 1 - Scheduled Data And Research Acquisition

Functional points:

- Reuse `/home/project/tushare-downloader` for historical trading data sync.
- Reuse `/home/project/tushare-downloader` realtime daily tasks for live daily market updates.
- Schedule strategy crawling from arXiv q-fin, SSRN, Quantpedia, WorldQuant BRAIN, QuantConnect Community, Numerai, and Chinese broker research sources.
- Schedule finance-intelligence crawling from exchanges, regulators, authoritative financial media, and major investment banks.
- Use SearxNG for discovery and crawl4ai for page extraction.
- Persist raw crawl artifacts by category and run date.
- Make scheduler runs idempotent and stateful so retries do not duplicate work.

### Phase 2 - GLM Screening And Structured Extraction

Functional points:

- Use GLM-5.1 only through env-provided credentials.
- Screen strategy items for complete quant idea, implementation steps, and backtest/live evidence.
- Screen finance intelligence for market relevance, key points, impact analysis, and risk level.
- Persist screened strategy and finance-intelligence artifacts.
- Skip low-quality crawler outputs before spending GLM calls.
- Parse GLM responses defensively, including markdown JSON blocks and length-limited incomplete responses.

### Phase 3 - Data Requirement Resolution

Functional points:

- Extract each strategy's data requirements.
- Check requirements against `/home/project/tushare-downloader/tushare_field_mapping.json`.
- Read available datasets from `/home/project/tushare-downloader/tushare-data` or configured equivalent paths.
- For missing fields, attempt external crawl-based supplementation.
- If data still cannot be found, create random placeholder values only as an explicit fallback.
- Log every missing field and placeholder to `Results/soloquant/missing-data.jsonl`.

### Phase 4 - Strategy Materialization And LEAN Config Generation

Functional points:

- Convert screened and reproduction-ready strategy specs into versioned SoloQuant strategy packages.
- Generate distinct backtest and live-paper LEAN config JSON files per strategy version.
- Forbid binding generated strategies to existing single-purpose algorithms that are not SoloQuant-generated.
- Ensure generated configs include InfluxDB env-var references and never inline tokens.
- Validate each generated manifest before optimization or live paper.

### Phase 5 - Backtest Optimization And Version Selection

Functional points:

- Generate a baseline plus at least two learned variants for each accepted strategy.
- Run each variant through the required LEAN launcher command.
- Read each variant's summary output.
- Score variants using the configured best metric.
- Persist the selected best version in the strategy registry.
- Fail fast on LEAN launcher errors before any live-paper promotion.

### Phase 6 - Live Paper Execution

Functional points:

- Select the best registered strategy unless a specific strategy id is requested.
- Run live paper through the same LEAN launcher command shape.
- Consume realtime daily data from the configured A-share data path.
- Persist live-paper summaries, daily snapshots, trades, and portfolio snapshots.
- Keep paper brokerage settings explicit in generated live-paper configs.

### Phase 7 - InfluxDB And Grafana Observability

Functional points:

- Export screened research artifacts to InfluxDB line protocol.
- Export finance event graph nodes and edges to InfluxDB line protocol.
- Export historical/realtime OHLC data to InfluxDB for Grafana.
- Export backtest and live-paper results to InfluxDB.
- Update Grafana dashboards from the current LEAN backtest/live-paper dashboards instead of replacing them wholesale.
- Keep all write tokens in environment variables.

### Phase 8 - Operational Hardening

Functional points:

- Add daemon and one-shot run modes for scheduler jobs.
- Add structured logs for task start, skip, success, and error events.
- Add runbooks for recovery, manual replay, token setup, and dashboard validation.
- Add integration tests around external-service clients with mocked HTTP.
- Add smoke tests that verify generated LEAN configs exist and use the required launcher contract.

## Current TDD Progress

- Completed: SoloQuant default config uses environment variable names for GLM, InfluxDB, and Grafana credentials.
- Completed: `load_config` rejects inline service secrets such as `api-key`, `token`, `password`, `secret`, and `authorization`.
- Completed: LEAN launcher command builder uses the required dotnet + launcher DLL + `--config` shape.
- Completed: strategy/finance crawl scheduling, artifact persistence, GLM screening payloads, reproduction summaries, finance-intelligence analysis, and finance event graph generation have focused Python tests.
- Completed: screened research artifacts export to InfluxDB line protocol without secrets.
- Completed: finance event graph nodes and edges export to InfluxDB line protocol without secrets.
- Completed: finance event graph InfluxDB export entrypoint supports dry-run, line printing, and env-token writes.
- Completed: finance event graph InfluxDB export is included in generated job specs.
- Completed: generated job specs include reproduction preparation, finance-intelligence analysis, and finance event graph build steps.
- Completed: `--optimize-strategies` scans strategy manifests, enforces at least three variants, runs LEAN backtests, and updates the strategy registry.
- Completed: reproduction summaries can be materialized into baseline + two learned LEAN config variants through `--materialize-reproduction-summaries`.
- Completed: GLM-5.1 can generate bounded, validated SoloQuant C# strategy implementation drafts under `Algorithm.CSharp/SoloQuantGenerated/`, with audit manifests in `Results/soloquant/generated-code/`.
- Completed: strategy optimization can score generated T1 advisory algorithms from portfolio snapshots when a dedicated summary file is not produced.
- Completed: strategy result export writes backtest optimization rows and the latest live-paper snapshot to `soloquant_strategy_result`.
- Completed: generated job specs include `soloquant-export-strategy-results-influx`.
- Completed: Grafana SoloQuant dashboard includes strategy backtest vs live-paper return panels.
- Completed: Grafana SoloQuant research dashboard is provisioned as `soloquant-research-overview.json`.
- Completed: Grafana asset installation now requires `INFLUXDB_TOKEN` from the environment instead of using an inline default token.
- Completed: `Scripts/soloquant_job_runner.py` can list, one-shot run, and daemon-run generated job specs with state tracking.
- Completed: long-running jobs such as Tushare incremental supervisor and SoloQuant live paper are explicitly marked and skipped by default in one-shot runs.
- Completed: missing strategy data fields are supplemented through SearxNG/crawl4ai into `field-cache-root` before falling back to random placeholders.
- Completed: `Scripts/soloquant_pipeline_runner.py` runs the full automatic chain every 5 minutes: crawl research and finance intelligence, ask GLM for reproduction summaries and LEAN C# implementations, compile `Algorithm.CSharp`, materialize three variants from `Algorithm.CSharp/SoloQuantGenerated`, optimize backtests, prepare GBM live-market data when the A-share session is closed, maintain serving/retired lifecycle state, start non-retired live-paper strategies in the background, and export research/strategy results to InfluxDB.
- Completed: generated strategy variants can bind directly to `SoloQuantGenerated*Algorithm` classes under `Algorithm.CSharp/SoloQuantGenerated/` instead of reusing fixed template algorithms.
- Completed: strategy lifecycle fields support `candidate`, `serving`, and `retired`; strategies with degraded live score are retired and skipped by the live-paper process manager.
- Completed: pipeline runner unit tests fixed — `removed` UnboundLocalError, `compile_strategies` hard-stop classification, and `env` kwarg mock.
- Completed: GLM screening uses tiered criteria — quant idea is required; implementation steps and backtest evidence are bonus. Items include `confidence_level: high/medium/low`.
- Completed: InfluxDB token fallback from config (`influxdb.token-default`) when `INFLUXDB_TOKEN` env var is not set.
- Completed: compile-validate-retry loop — `compile_validate_generated_code()` validates generated C# compiles, retries with LLM-assisted fixes up to 2 times.
- Completed: targeted file removal in `compile_strategies()` — only removes broken .cs files identified by build errors, not all generated code.
- Completed: Grafana dashboards migrated from legacy measurement names to LEAN-native (`lean_chart`, `lean_metric`, `lean_order`, `lean_holding`, `lean_portfolio`). Mode tag fixed to `"backtesting"`.
- Completed: lifecycle InfluxDB export now logs failures instead of silently swallowing exceptions.
- Completed: crawl scheduler health check — pings SearxNG and crawl4ai before crawling, degrades gracefully if either is unreachable.

## Goal

SoloQuant is a personal quant system, not a single strategy.
LEAN is only the execution engine for strategy backtests and live paper.

## Stack

- Python orchestration for research ingestion, job scheduling, and artifact management
- SearxNG for search
- crawl4ai for page extraction
- GLM-5.1 for strategy filtering, extraction, and version scoring
- LEAN C# for backtest and live paper execution
- InfluxDB for time-series storage
- Grafana for dashboards

## Principles

1. Keep orchestration separate from trading logic.
2. Treat every strategy as a versioned artifact.
3. Make all external services configurable through env vars.
4. Persist every missing-data fallback in logs.
5. Run backtest and live paper through the same LEAN launcher command shape.

## Data Flow

1. Download Tushare historical and realtime daily data.
2. Search strategy and finance content through SearxNG.
3. Crawl high-value pages through crawl4ai.
4. Ask GLM-5.1 to keep only content with an understandable quant idea. Implementation steps and backtest/live evidence are bonus, not required. Items are tagged with `confidence_level: high/medium/low`.
5. For crawled quantitative papers, read the full crawled content and generate a reproduction-preparation summary.
6. Map required fields against `tushare_field_mapping.json`.
7. Read existing data from `tushare-data`.
8. If a field is missing, crawl for it.
9. If it still cannot be found, create a placeholder value and log the gap.
10. Generate strategy variants.
11. Backtest each variant with LEAN.
12. Pick the best version.
13. Publish backtest and live paper results to InfluxDB and Grafana.

## Scheduling

Use one coordinator process with idempotent jobs and file-based state.
Recommended job groups:

- historical data sync
- realtime daily data sync
- strategy/news crawl
- research and finance-intelligence export to InfluxDB
- historical and realtime OHLC export to InfluxDB
- strategy generation
- multi-version backtest
- live paper replay

This avoids overlapping work and keeps retries explicit.

## LEAN Contract

All strategy execution uses the same launcher form:

`/usr/local/dotnet/dotnet ./Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll --config <json>`

The JSON config is strategy-specific and generated by SoloQuant.

## Storage

- `Results/soloquant/` for manifests, versions, and job state
- InfluxDB bucket `quant`
- Grafana dashboards built on existing LEAN measurements

## Runtime Inputs

- `GLM_API_KEY`
- `INFLUXDB_TOKEN`
- `GRAFANA_TOKEN`

## Baseline Config

Use `Launcher/config/config-soloquant.json` as the system entry config.

## Current Implementation

- `Scripts/soloquant_orchestrator.py`
  - builds research query plans
  - builds reusable job specs for Tushare sync, crawling, optimization, and live paper
  - provides SearxNG, crawl4ai, and GLM-5.1 HTTP client adapters
  - builds GLM-5.1 screening payloads
  - parses screening decisions
  - reads crawled quantitative papers and prepares structured reproduction summaries
  - generates bounded SoloQuant C# implementation drafts from reproduction summaries
  - resolves Tushare field requirements
  - logs missing data requirements
  - creates random placeholder data for unresolved fields
  - materializes strategy packages with backtest and live-paper LEAN configs
  - optimizes baseline + at least two generated versions through the LEAN launcher
  - updates the strategy registry
  - runs the best registered strategy in live paper through the same LEAN launcher command
  - exports screened strategy and finance-intelligence artifacts into InfluxDB line protocol for Grafana
- `Scripts/soloquant_pipeline_runner.py`
  - runs the complete end-to-end SoloQuant loop every 300 seconds
  - stops before backtest/live paper if the generated LEAN C# project does not compile
  - uses GBM-simulated A-share market snapshots outside trading hours
  - starts all non-retired registered live-paper strategies as background LEAN launcher processes without duplicating already-running PIDs
  - treats crawler timeout as degraded input and continues with existing artifacts, while compile/backtest/live/influx failures still stop the tick

Example query-plan generation:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --build-query-plan \
  --keyword "A股资金流" \
  --keyword "momentum crash"
```

Example strategy package generation:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --materialize-strategy Results/soloquant/example-strategy-spec.json
```

Materialize all reproduction summaries for a run date into three-version strategy packages:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --materialize-reproduction-summaries \
  --run-date 20260510 \
  --universe 000001.SZ \
  --universe 600000.SH \
  --universe 300750.SZ
```

Generate auditable C# implementation drafts from reproduction summaries:

```bash
export GLM_API_KEY="..."
python3 Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --generate-strategy-implementations \
  --run-date 20260510 \
  --max-items 1
```

Generated C# strategy source is written under:

`Algorithm.CSharp/SoloQuantGenerated/`

Audit manifests are written under:

`Results/soloquant/generated-code/`

The generator rejects inline secrets, forbidden algorithm reuse, network/process/file-delete calls, and classes that do not follow the `SoloQuantGenerated*Algorithm` naming contract. Generated strategies live inside the LEAN project tree and still require review plus compilation before they are promoted into optimization/live-paper rotation.

Optimize materialized strategy packages:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --optimize-strategies
```

Example job-spec generation:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --build-job-specs
```

Run the currently best registered strategy in live paper:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --run-live-paper
```

Export screened strategy and finance-intelligence artifacts to InfluxDB:

```bash
export INFLUXDB_TOKEN="..."
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --export-research-influx
```

Inspect the line protocol without writing to InfluxDB:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --export-research-influx \
  --dry-run \
  --print-lines
```

Export finance event graph nodes and edges to InfluxDB:

```bash
export INFLUXDB_TOKEN="..."
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --export-finance-event-graph-influx
```

Inspect finance event graph line protocol without writing:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --export-finance-event-graph-influx \
  --dry-run \
  --print-lines
```

Example real crawl and write:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --crawl \
  --category strategy \
  --keyword "A股资金流" \
  --max-queries 8
```

Example crawl plus GLM screening:

```bash
export GLM_API_KEY="..."
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --crawl \
  --screen-with-glm \
  --category finance_intelligence \
  --keyword "交易所公告 风险" \
  --max-queries 6
```

Prepare reproduction summaries from crawled quantitative papers:

```bash
export GLM_API_KEY="..."
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --prepare-reproduction \
  --run-date 20260510 \
  --max-items 3
```

By default this sends the full crawled paper content to GLM-5.1 and does not set an output token cap. Use `--max-content-chars <n>` or `--glm-max-tokens <n>` only for manual debugging.

Reproduction outputs:

- `Results/soloquant/artifacts/reproduction/strategy/YYYYMMDD/*.json`
- `Results/soloquant/artifacts/reproduction/strategy/YYYYMMDD/index.json`

Each summary includes the core idea, tradable universe, signal definitions, portfolio construction, risk controls, data requirements, reported results, and LEAN reproduction steps.

Analyze crawled finance intelligence with GLM-5.1:

```bash
export GLM_API_KEY="..."
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --analyze-finance-intelligence \
  --run-date 20260510 \
  --max-items 3
```

By default this sends the full crawled finance-intelligence content to GLM-5.1. The input is not truncated. The analyzer sets a 4096 output-token budget so GLM-5.1 has enough room to produce final JSON after reasoning; override with `--glm-max-tokens <n>` only for manual debugging.

Finance-intelligence analysis outputs:

- `Results/soloquant/artifacts/intelligence-analysis/finance_intelligence/YYYYMMDD/*.json`
- `Results/soloquant/artifacts/intelligence-analysis/finance_intelligence/YYYYMMDD/index.json`

Each analysis includes key points, market impact, and risk level.

Build a finance event graph from analyzed intelligence:

```bash
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_orchestrator.py \
  --config Launcher/config/config-soloquant.json \
  --build-finance-event-graph \
  --run-date 20260510
```

Event graph output:

- `Results/soloquant/artifacts/event-graph/finance_intelligence/YYYYMMDD/graph.json`

This graph is the local "thing layer" for finance intelligence. It turns analyzed items into typed nodes and edges:

- Nodes: `event`, `entity`, `theme`, `source`
- Edges: `SOURCE_REPORTED_EVENT`, `EVENT_IMPACTS_ENTITY`, `EVENT_HAS_THEME`, `EVENT_RELATED_TO_EVENT`

The design borrows from current open-source graph approaches:

- Graphiti/Zep: temporal knowledge graph concepts for changing facts and relationships
- GraphRAG: graph-shaped context instead of flat document lists
- OpenSPG: domain-constrained knowledge modeling with explicit relation types

SoloQuant starts with a deterministic local JSON graph so the pipeline is testable and does not require a graph database. A later storage adapter can write the same graph to Neo4j, Graphiti, OpenSPG, or Influx/Grafana-derived graph panels.

Install or refresh Grafana assets:

```bash
export INFLUXDB_TOKEN="..."
bash monitoring/grafana/install_assets.sh
```

The installed dashboards include:

- `LEAN Results Overview`
- `SoloQuant Research Overview`

## Scheduled Crawl Tasks

The scheduled strategy and finance-intelligence acquisition entrypoint is:

`Scripts/soloquant_crawl_scheduler.py`

It reads crawl task definitions from `Launcher/config/config-soloquant.json` under `crawl-tasks`.

Inspect configured tasks without network calls:

```bash
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --list-tasks
```

Run all due tasks once:

```bash
export GLM_API_KEY="..."
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --once
```

Force strategy crawl now:

```bash
export GLM_API_KEY="..."
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --task strategy \
  --force \
  --once
```

Force finance-intelligence crawl now:

```bash
export GLM_API_KEY="..."
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --task finance_intelligence \
  --force \
  --once
```

Run as a long-lived scheduler:

```bash
export GLM_API_KEY="..."
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --daemon \
  --poll-seconds 60
```

The scheduler prints structured progress logs to stdout and also appends them to:

`Results/soloquant/soloquant-crawl-scheduler.log`

Important log events:

- `scheduler_start`: scheduler boot parameters
- `tick_start`: one polling round started
- `task_start`: a crawl task started
- `task_skip`: a task was skipped because its interval has not elapsed
- `task_success`: crawl/screening finished; includes crawled, duplicate, screened, missing-field counts
- `task_error`: external service or task failure; daemon continues
- `tick_complete`: one polling round finished and scheduler is sleeping

Use a custom log file:

```bash
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --daemon \
  --poll-seconds 600 \
  --log-file Results/soloquant/my-crawl.log
```

Run without GLM screening, storing only raw crawled artifacts:

```bash
python3 Scripts/soloquant_crawl_scheduler.py \
  --config Launcher/config/config-soloquant.json \
  --task strategy \
  --force \
  --once \
  --no-glm
```

Crawler outputs:

- Raw crawled strategy pages: `Results/soloquant/artifacts/crawled/strategy/YYYYMMDD/`
- Raw crawled finance intelligence: `Results/soloquant/artifacts/crawled/finance_intelligence/YYYYMMDD/`
- Screened strategy items: `Results/soloquant/artifacts/screened/strategy/YYYYMMDD/valuable-items.json`
- Screened finance intelligence: `Results/soloquant/artifacts/screened/finance_intelligence/YYYYMMDD/valuable-items.json`
- Externally supplemented missing data fields: `local_data/soloquant-field-cache/`
- Scheduler state: `Results/soloquant/crawl-scheduler-state.json`

Each raw directory also contains an `index.json` with the files written for that day.

## Unified Job Runner

The full SoloQuant system job runner is:

`Scripts/soloquant_job_runner.py`

It reads `build_job_specs()` from `Scripts/soloquant_orchestrator.py` and tracks execution state in:

`Results/soloquant/soloquant-job-runner-state.json`

List all generated jobs:

```bash
python3 Scripts/soloquant_job_runner.py \
  --config Launcher/config/config-soloquant.json \
  --list-jobs
```

Run due short-lived jobs once:

```bash
export INFLUXDB_TOKEN="..."
python3 Scripts/soloquant_job_runner.py \
  --config Launcher/config/config-soloquant.json \
  --once
```

Run a specific job immediately:

```bash
export INFLUXDB_TOKEN="..."
python3 Scripts/soloquant_job_runner.py \
  --config Launcher/config/config-soloquant.json \
  --job soloquant-export-strategy-results-influx \
  --once \
  --timeout-seconds 60
```

Run continuously:

```bash
python3 Scripts/soloquant_job_runner.py \
  --config Launcher/config/config-soloquant.json \
  --daemon \
  --poll-seconds 60
```

Long-running jobs are marked with `long-running: true` and are skipped by default:

- `tushare-incremental-scheduler`
- `soloquant-live-paper`

Start one only when explicitly intended:

```bash
python3 Scripts/soloquant_job_runner.py \
  --config Launcher/config/config-soloquant.json \
  --job soloquant-live-paper \
  --once \
  --include-long-running
```

## Full Automatic Pipeline

The full background pipeline is:

```bash
INFLUXDB_TOKEN='admin-token-leansystem' \
GLM_API_KEY='<glm-key>' \
/root/miniconda3/envs/quant311/bin/python Scripts/soloquant_pipeline_runner.py \
  --config Launcher/config/config-soloquant.json \
  --daemon \
  --force \
  --poll-seconds 300 \
  --log-file Results/soloquant/soloquant-pipeline.log
```

For controlled validation, run one tick with a timeout:

```bash
INFLUXDB_TOKEN='admin-token-leansystem' \
GLM_API_KEY='<glm-key>' \
timeout 900 /root/miniconda3/envs/quant311/bin/python Scripts/soloquant_pipeline_runner.py \
  --config Launcher/config/config-soloquant.json \
  --once \
  --force \
  --timeout-seconds 900
```

The daemon writes:

- Pipeline state: `Results/soloquant/soloquant-pipeline-state.json`
- Pipeline log: `Results/soloquant/soloquant-pipeline.log`
- Live-paper PID files and stdout/stderr: `Results/soloquant/live-paper-processes/`
- GBM or realtime market snapshot: `Results/shared-live-market/ashare-live-price-snapshot.json`

`Launcher/config/config-soloquant.json` controls per-tick crawling with `pipeline.crawl-max-queries-per-task`, `pipeline.crawl-max-results-per-query`, and `pipeline.crawl-timeout-seconds` so an external search/crawl stall does not block backtest/live-paper lifecycle work forever. GLM stages are also bounded by `pipeline.glm-max-items-per-tick` and `pipeline.glm-stage-timeout-seconds`.

## Notes

- Do not bind the system to `AShareLlmQuantLeanAlgorithm`.
- SoloQuant can generate multiple strategy types.
- Missing data is allowed only as a logged fallback, never silently.
- API keys are read from environment variables and are not written to crawler artifacts.

## InfluxDB Measurement Schema

### LEAN-Native Measurements (written by `InfluxDbResultExporter.cs`)

All measurements share `algorithm_id` and `mode` tags. Mode values: `"backtesting"` or `"live"`.

| Measurement | Key Tags | Key Fields | Purpose |
|-------------|----------|------------|---------|
| `lean_portfolio` | algorithm_id, mode | total_value, cash, total_holdings_value | Portfolio state |
| `lean_chart` | algorithm_id, mode, chart, series | value | Chart data (equity curves, drawdown) |
| `lean_metric` | algorithm_id, mode, category, key | numeric_value, text_value | Statistics and runtime metrics |
| `lean_order` | algorithm_id, mode, symbol, market, direction | quantity, price | Orders |
| `lean_order_event` | algorithm_id, mode, order_id | status, fill_price | Order events |
| `lean_trade` | algorithm_id, mode, symbol | entry/exit price, profit | Closed trades |
| `lean_holding` | algorithm_id, mode, symbol, market, direction | quantity, value, unrealized_pnl | Current holdings |
| `lean_cash` | algorithm_id, mode, currency | amount | Cash balances |
| `lean_message` | algorithm_id, mode | message | Log messages |

### lean_metric category values

| category | Source | Example keys |
|----------|--------|-------------|
| `summary` | RecordStatisticsObject | Total Net Profit, Sharpe Ratio, Max Drawdown |
| `runtime` | RecordMetricDictionary | Current holdings count, trade count |
| `server` | RecordMetricDictionary | Server status |
| `state` | RecordMetricDictionary | Algorithm state |
| `portfolio_statistics` | RecordStatisticsObject | Portfolio-level stats |
| `trade_statistics` | RecordStatisticsObject | Trade-level stats |

### Pipeline Custom Measurements

| Measurement | Key Tags | Key Fields | Purpose |
|-------------|----------|------------|---------|
| `strategy_lifecycle` | strategy_id, status | best_score, live_score | Strategy lifecycle state |

## Compile-Validate-Retry

Generated C# code goes through a multi-stage validation:

1. `postprocess_generated_csharp_code()` — regex-based fixes for common LEAN API mistakes
2. `validate_generated_strategy_code()` — security validation (no network calls, file reads, etc.)
3. `compile_validate_generated_code()` — actual `dotnet build` + LLM-assisted fix retries (up to 2)
4. `compile_strategies()` (pipeline stage) — targeted removal of only broken .cs files on build failure

If compilation fails in the pipeline, `parse_broken_cs_files()` extracts specific file paths from the build error output and only those files are removed, preserving previously working generated strategies.

## Grafana Dashboards

| Dashboard | Measurements | Description |
|-----------|-------------|-------------|
| `LEAN Results Overview` | lean_* | Native LEAN results (already correct) |
| `Backtest Overview` | lean_chart, lean_metric, lean_order | Backtest detail panels |
| `Live Trading` | lean_chart, lean_portfolio, lean_metric, lean_order, lean_holding | Live paper detail panels |
| `Strategy Lifecycle` | strategy_lifecycle, lean_chart, lean_metric | Lifecycle management |
| `SoloQuant Research Overview` | Custom research measurements | Research pipeline status |
