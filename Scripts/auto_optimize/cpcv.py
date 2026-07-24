"""Combinatorial Purged K-Fold. López de Prado, AFML Ch.7. Spec §5.4."""
import itertools
import numpy as np
import pandas as pd


def split(days, n_folds: int = 6, purge_bars: int = 5, embargo_bars: int = 5):
    days = pd.DatetimeIndex(days)
    folds = np.array_split(np.arange(len(days)), n_folds)
    splits = []
    for test_combo in itertools.combinations(range(n_folds), 1):
        test_idx = np.concatenate([folds[i] for i in test_combo])
        train_idx = np.concatenate([folds[i] for i in range(n_folds) if i not in test_combo])
        # purge: 删除 train 中与 test 边界 ±purge_bars 重叠的样本
        test_min, test_max = test_idx.min(), test_idx.max()
        purge_mask = np.ones(len(train_idx), dtype=bool)
        for j, idx in enumerate(train_idx):
            for t in test_idx:
                if abs(idx - t) <= purge_bars:
                    purge_mask[j] = False
                    break
        train_purged = train_idx[purge_mask]
        # embargo: test_max 后 embargo_bars 天内不进 train
        if embargo_bars > 0:
            emb_mask = np.array([not (test_max < idx <= test_max + embargo_bars) for idx in train_purged])
            train_purged = train_purged[emb_mask]
        splits.append((train_purged, test_idx))
    return splits
