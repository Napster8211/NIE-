import time
from typing import Optional


class HighPrecisionTimer:
    """Provides high-resolution sub-millisecond timing utilities."""

    @staticmethod
    def now() -> float:
        """Returns current monotonic timestamp in seconds."""
        return time.perf_counter()

    @staticmethod
    def delta_ms(start_time: float, end_time: Optional[float] = None) -> float:
        """Calculates elapsed time in milliseconds."""
        if end_time is None:
            end_time = time.perf_counter()
        return round((end_time - start_time) * 1000, 2)