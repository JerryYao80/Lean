# GDELT Integration

GDELT Project data is free and open through the public GDELT Project endpoints. The downloader in this repository uses the public DOC 2.0 API and does not require an API key or paid SDK.

The newer GDELT Cloud API is a separate API-key product surface with plan and quota controls. Use the public Project endpoint first unless a strategy needs Cloud-only features or guaranteed commercial API quotas.

## Data Shape

`Scripts/gdelt_news_downloader.py` writes daily custom-data CSV files to:

```text
Data/alternative/gdelt-news-sentiment/{market}/daily/{ticker}.csv
```

Columns:

```text
trade_date,query,article_count,mean_tone,positive_count,negative_count,neutral_count,source_count,top_domain
```

LEAN reads those files through `GdeltNewsSentimentData`. `Value` is `mean_tone`.

## Download Example

Create a JSON symbol query file:

```json
{
  "600000.SH": "Shanghai Pudong Development Bank",
  "510300.SH": "CSI 300 ETF"
}
```

Run:

```bash
python3 Scripts/gdelt_news_downloader.py \
  --symbol-query-file local_data/gdelt_symbol_queries.json \
  --output-root Data/alternative/gdelt-news-sentiment \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --expand-financial-intelligence \
  --lifecycle-events 10 \
  --report-file Results/gdelt-news-report.json
```

`--expand-financial-intelligence` keeps the LEAN CSV shape unchanged, but broadens collection beyond the base symbol query with macro, monetary-policy, credit-risk, sector, commodity, geopolitical, and regulatory query scopes. The JSON report includes the expanded `intelligence_queries`, downloaded `sample_articles`, and `event_lifecycles` summaries.

Each lifecycle summary groups related article titles and reports:

```text
event_key,first_seen,last_seen,duration_days,status,article_count,source_count,peak_date,mean_tone,top_domains,sample_titles
```

## Algorithm Usage

```csharp
var equity = AddEquity("600000", Resolution.Daily, Market.SSE).Symbol;
var gdelt = AddData<GdeltNewsSentimentData>(equity).Symbol;
GdeltNewsSentimentData.SetBaseDirectory(
    Path.Combine(Globals.DataFolder, "alternative", "gdelt-news-sentiment"));
```

In `OnData`, read `GdeltNewsSentimentData` from the slice and use `MeanTone`, `ArticleCount`, or the positive/negative counts as features.
