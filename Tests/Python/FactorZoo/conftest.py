"""Shared pytest fixtures for factor zoo tests.

All fixtures use SYNTHETIC data (never raw production). Field names and
partition layouts mirror the real tushare_data_v2 convention so tests
exercise the real read paths.
"""
import pytest


@pytest.fixture
def tmp_data_root(tmp_path):
    """A scratch tushare_data_v2-style root the test can populate."""
    return tmp_path
