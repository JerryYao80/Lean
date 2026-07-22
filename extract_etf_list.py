#!/usr/bin/env python3
"""
Extract T+0 ETF list from tushare data and generate AShareETFRegistry code
"""
import pandas as pd
import json

# Read ETF basic info
df_etf = pd.read_parquet('/home/project/tushare-downloader/tushare_data/etf_basic/data.parquet')

# Filter for active, domestic ETFs
active_etfs = df_etf[
    (df_etf['etf_type'] == '纯境内') &
    (df_etf['list_status'] == 'L')
].copy()

print(f"Total active domestic ETFs: {len(active_etfs)}")

# Extract ticker from ts_code (e.g., "510050.SH" -> "510050")
active_etfs['ticker'] = active_etfs['ts_code'].str.split('.').str[0]
active_etfs['market'] = active_etfs['exchange'].map({'SH': 'SSE', 'SZ': 'SZSE'})

# T+0 ETFs are typically:
# 1. Cross-border ETFs (跨境ETF)
# 2. Bond ETFs (债券ETF)
# 3. Gold/commodity ETFs (黄金ETF)
# 4. Money market ETFs (货币ETF)
# 5. Specific equity ETFs approved for T+0

# For now, let's identify known T+0 categories
t0_keywords = ['货币', '黄金', '债券', '跨境', '港股通', 'QDII']
t0_etfs = active_etfs[
    active_etfs['csname'].str.contains('|'.join(t0_keywords), na=False)
].copy()

# Add known T+0 equity ETFs
known_t0_tickers = ['510050', '510300', '510500', '518880', '511880', '511990',
                     '159915', '159919', '159949']
known_t0 = active_etfs[active_etfs['ticker'].isin(known_t0_tickers)]
t0_etfs = pd.concat([t0_etfs, known_t0]).drop_duplicates(subset=['ticker'])

print(f"Identified T+0 ETFs: {len(t0_etfs)}")

# Determine price limit percentage
def get_price_limit(name):
    if '创业板' in name or '科创板' in name:
        return 0.20  # 20% for ChiNext/STAR
    return 0.10  # 10% default

t0_etfs['price_limit'] = t0_etfs['csname'].apply(get_price_limit)

# Generate C# code
print("\n" + "="*80)
print("C# Code for AShareETFRegistry:")
print("="*80)

print("private static readonly Dictionary<string, AShareETFMetadata> _etfMetadata = new Dictionary<string, AShareETFMetadata>")
print("{")

for _, row in t0_etfs.sort_values(['market', 'ticker']).iterrows():
    ticker = row['ticker']
    name = row['csname']
    market = row['market']
    price_limit = row['price_limit']

    if price_limit == 0.20:
        limit_str = ", PriceLimitPercentage = AShareETF.GrowthBoardPriceLimitPercentage"
    else:
        limit_str = ""

    print(f'    {{ "{ticker}", new AShareETFMetadata {{ Ticker = "{ticker}", Name = "{name}", TradingMode = ETFTradingMode.T0, Market = "{market}"{limit_str} }} }},')

print("};")

# Save to JSON for reference
output = []
for _, row in t0_etfs.iterrows():
    output.append({
        'ticker': row['ticker'],
        'name': row['csname'],
        'ts_code': row['ts_code'],
        'market': row['market'],
        'price_limit': float(row['price_limit']),
        'list_date': row['list_date']
    })

with open('/home/project/hope/Lean/t0_etf_list.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✓ Saved {len(output)} T+0 ETFs to t0_etf_list.json")
