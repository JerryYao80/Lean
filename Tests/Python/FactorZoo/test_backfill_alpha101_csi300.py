"""Test backfill_alpha101_csi300 drives builder.build_day per trade day with CSI300 ts_codes."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Scripts" / "factor_zoo"))


def test_backfill_calls_builder_per_trade_day(tmp_path):
    from backfill_alpha101_csi300 import backfill_range

    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    csi300 = ["600519.SH", "000001.SZ", "300750.SZ"]
    with patch("backfill_alpha101_csi300.build_day") as mock_build:
        mock_build.return_value = {"rows": 9, "failed": {}}
        backfill_range(dates, csi300, result_root=str(tmp_path))
    assert mock_build.call_count == 3
    for i, d in enumerate(dates):
        args, kwargs = mock_build.call_args_list[i]
        assert kwargs.get("date_yyyy_mm_dd") == d or args[0] == d
        assert "600519.SH" in (kwargs.get("ts_codes") or args[1])


def test_backfill_8_alpha_list_is_canonical():
    from backfill_alpha101_csi300 import ALPHAS_TO_FILL
    assert ALPHAS_TO_FILL == [
        "alpha001", "alpha006", "alpha030", "alpha040",
        "alpha042", "alpha055", "alpha058", "alpha101",
    ]


def test_backfill_skips_when_csi300_empty(tmp_path):
    from backfill_alpha101_csi300 import backfill_range
    with patch("backfill_alpha101_csi300.build_day") as mock_build:
        backfill_range(["2024-01-02"], [], result_root=str(tmp_path))
    mock_build.assert_not_called()
