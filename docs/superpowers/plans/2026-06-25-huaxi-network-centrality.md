# 华西证券·股票网络与网络中心度因子策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现华西证券金工研报《股票网络与网络中心度因子研究》(2021-03-14) 的 A 股选股策略：基于股票间收益率相关性构建网络，计算 4 种网络中心度因子，做多低中心度股票。

**Architecture:** 因子计算（Python 脚本）+ LEAN 5-Step Framework（AlphaModel 派生 + QCAlgorithm 子类）。因子 CSV 作为中间数据，LEAN 算法读取后在横截面排序生成 Insight，EqualWeighting PCM 合成持仓。

**Tech Stack:** Python 3.13, pandas, numpy, networkx, LEAN (C# + Python.NET)

---

## File Structure

| 文件 | 职责 |
|------|------|
| `Scripts/compute_huaxi_network_centrality.py` | 离线计算：从 tushare daily 读取收益率 → 相关系数矩阵 → 网络 → 4 个中心度 → CSV |
| `Data/alternative/huaxi-network-centrality/factors.csv` | 因子输出：trade_date, ts_code, degree, closeness, betweenness, eigenvector |
| `Algorithm.Python/NetworkCentralityAlphaModel.py` | Alpha 模型：读取因子 CSV，月末横截面排序，生成 4 个独立 Insight |
| `Algorithm.Python/HuaxiNetworkCentralityAlgorithm.py` | 算法主类：装配 5-Step Framework，A 股模型配置 |
| `Launcher/config/config-huaxi-network-centrality.json` | 回测配置 |

---

### Task 1: 编写因子计算脚本

**Files:**
- Create: `Scripts/compute_huaxi_network_centrality.py`

- [ ] **Step 1: 编写脚本框架 + 参数解析**

```python
#!/usr/bin/env python3
"""Compute network centrality factors for Huaxi Securities (2021-03-14) strategy.

For each month-end, construct a stock network from daily return correlations
(252-day lookback, top 30% edges), compute 4 centrality metrics, output CSV.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx

TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data')
OUT_DIR = Path('Data/alternative/huaxi-network-centrality')
LOOKBACK = 252
EDGE_QUANTILE = 0.70  # top 30% edges
CSI300_CODE = '000300.SH'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-date', default='20150101')
    parser.add_argument('--end-date', default='20251231')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    out_path = Path(args.output) if args.output else OUT_DIR / 'factors.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    constituents = load_csi300_constituents()
    all_rows = []
    # compute monthly (will fill in next step)
    
    df = pd.DataFrame(all_rows)
    df.to_csv(out_path, index=False)
    print(f'[huaxi] wrote {len(df)} rows to {out_path}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 实现 load_csi300_constituents()**

```python
import glob


def load_csi300_constituents():
    """Load CSI300 constituent ts_codes from latest index_weight file."""
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    for f in reversed(files):
        df = pd.read_parquet(f)
        if 'index_code' not in df.columns:
            continue
        sub = df[df['index_code'] == CSI300_CODE]
        if len(sub) == 0:
            continue
        col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
        return sub[col].astype(str).unique().tolist()
    return []
```

- [ ] **Step 3: 实现 load_returns_panel()**

```python
def load_returns_panel(ts_codes, start_date, end_date):
    """Load daily pct_chg for ts_codes as DataFrame (dates × ts_codes)."""
    frames = {}
    for code in ts_codes:
        p = TUSHARE_ROOT / 'daily' / f'ts_code={code}' / 'data.parquet'
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df['trade_date'] = df['trade_date'].astype(str).str.zfill(8)
        df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
        if 'pct_chg' in df.columns:
            frames[code] = df.set_index('trade_date')['pct_chg'].astype(float)
    return pd.DataFrame(frames).sort_index()
```

- [ ] **Step 4: 实现 build_network()**

```python
def build_network(returns_window):
    """Build weighted undirected graph from correlation matrix."""
    corr = returns_window.corr(method='pearson')
    n = len(corr)
    threshold = corr.values[np.triu_indices(n, k=1)]
    threshold = np.quantile(threshold, EDGE_QUANTILE)
    
    G = nx.Graph()
    G.add_nodes_from(corr.columns)
    for i, s1 in enumerate(corr.columns):
        for j, s2 in enumerate(corr.columns[i+1:], start=i+1):
            r = corr.iat[i, j]
            if r >= threshold:
                G.add_edge(s1, s2, weight=r)
    return G
```

- [ ] **Step 5: 实现 compute_centrality()**

```python
def compute_centrality(G):
    """Compute 4 centrality metrics for each node."""
    n = len(G)
    if n == 0:
        return {}
    
    deg = nx.degree_centrality(G)
    clo = nx.closeness_centrality(G)
    bet = nx.betweenness_centrality(G)
    try:
        eig = nx.eigenvector_centrality(G, max_iter=500)
    except nx.NetworkXException:
        eig = {node: 0.0 for node in G.nodes}
    
    out = {}
    for node in G.nodes:
        out[node] = {
            'degree': deg.get(node, 0.0),
            'closeness': clo.get(node, 0.0),
            'betweenness': bet.get(node, 0.0),
            'eigenvector': eig.get(node, 0.0),
        }
    return out
```

- [ ] **Step 6: 实现月度循环逻辑（见完整代码）**

- [ ] **Step 7: 运行脚本生成因子**

Run: `python3 Scripts/compute_huaxi_network_centrality.py`

- [ ] **Step 8: Commit**

---

### Task 2: 编写 NetworkCentralityAlphaModel

**Files:**
- Create: `Algorithm.Python/NetworkCentralityAlphaModel.py`

（详细代码见 spec 文件，遵循 CausalFactorScreenAlphaModel 模式）

---

### Task 3: 编写 HuaxiNetworkCentralityAlgorithm

**Files:**
- Create: `Algorithm.Python/HuaxiNetworkCentralityAlgorithm.py`

（详细代码见 spec 文件，遵循 CausalFactorMirageAlgorithm 模式）

---

### Task 4: 编写回测配置

**Files:**
- Create: `Launcher/config/config-huaxi-network-centrality.json`

---

### Task 5: 运行回测 + 验证

（步骤见完整 plan）

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-25-huaxi-network-centrality.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
