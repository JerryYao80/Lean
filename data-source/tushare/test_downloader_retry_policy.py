import threading
import unittest
from unittest.mock import patch

from downloader import TushareDownloader


class DummyRateLimiter:
    def wait_for_token(self):
        return None


class DummyPro:
    def __init__(self, effects):
        self.effects = list(effects)
        self.calls = 0

    def test_api(self, **kwargs):
        self.calls += 1
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class DownloaderRetryPolicyTests(unittest.TestCase):
    def make_downloader(self, effects):
        downloader = TushareDownloader.__new__(TushareDownloader)
        downloader.rate_limiter = DummyRateLimiter()
        downloader._api_lock = threading.Lock()
        downloader.pro = DummyPro(effects)
        return downloader

    def test_timeout_errors_use_limited_retry_count(self):
        downloader = self.make_downloader([
            Exception("Read timed out. (read timeout=30)"),
            Exception("Read timed out. (read timeout=30)"),
            {"should": "not reach"},
        ])

        with patch("downloader.time.sleep", return_value=None):
            result = downloader._call_api_with_retry("test_api")

        self.assertIsNone(result)
        self.assertEqual(2, downloader.pro.calls)

    def test_timeout_detection_does_not_match_non_timeout_errors(self):
        downloader = self.make_downloader([])

        self.assertTrue(downloader._is_timeout_error(Exception("Connection to host timed out")))
        self.assertFalse(downloader._is_timeout_error(Exception("Token已过期")))


if __name__ == "__main__":
    unittest.main()
