"""WorldQuant 101 Formulaic Alphas — factor zoo package."""
try:
    from alpha101.builder import build_day  # noqa: F401
except ImportError:
    pass
try:
    from alpha101.formulas import ALPHAS, INDCLASS_LEVELS  # noqa: F401
except ImportError:
    pass
