import importlib.util
import sys
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "data-source" / "tushare" / "rt_daily_downloader.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")

    spec = importlib.util.spec_from_file_location("rt_daily_downloader", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TushareRtDailyDownloaderTests(unittest.TestCase):
    def test_is_etf_code_distinguishes_stock_and_etf(self):
        module = load_module()
        client = module.TushareRtDailyClient

        self.assertTrue(client.is_etf_code("510300.SH"))
        self.assertTrue(client.is_etf_code("159915.SZ"))
        self.assertTrue(client.is_etf_code("511880.SH"))

        self.assertFalse(client.is_etf_code("600000.SH"))
        self.assertFalse(client.is_etf_code("000001.SZ"))
        self.assertFalse(client.is_etf_code("300750.SZ"))


if __name__ == "__main__":
    unittest.main()
