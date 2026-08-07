"""Pytest conftest — add Scripts/auto_optimize to sys.path so tests can `from manifest_loader import ...` without PYTHONPATH env var."""
import sys
import pathlib

_AUTO_OPTIMIZE = pathlib.Path(__file__).resolve().parents[2] / "Scripts" / "auto_optimize"
if str(_AUTO_OPTIMIZE) not in sys.path:
    sys.path.insert(0, str(_AUTO_OPTIMIZE))
