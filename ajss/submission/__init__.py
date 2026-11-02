"""Public submission wiring API; implementation lives in wiring and processing."""
from ajss.submission.wiring import configured_strategies, strategy
from ajss.submission.processing import submit_prepared

__all__ = ["configured_strategies", "strategy", "submit_prepared"]
