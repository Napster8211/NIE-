from typing import Dict, Tuple
from app.monitoring.request_trace import StageRecord


class LatencyCalculator:
    
    @staticmethod
    def find_slowest_stage(stages: Dict[str, StageRecord], total_ms: float) -> Tuple[str, float, float]:
        slowest_name = "N/A"
        slowest_ms = 0.0
        
        for name, record in stages.items():
            if record.duration_ms > slowest_ms:
                slowest_ms = record.duration_ms
                slowest_name = name
                
        pct = round((slowest_ms / total_ms * 100), 1) if total_ms > 0 else 0.0
        return slowest_name, slowest_ms, pct

    @staticmethod
    def calculate_grade(total_ms: float, ttft_ms: float) -> str:
        # Grade evaluation combining total latency & TTFT SLA criteria
        if total_ms < 1500 and (ttft_ms == 0 or ttft_ms < 800):
            return "A+"
        elif total_ms < 3000 and (ttft_ms == 0 or ttft_ms < 1800):
            return "A"
        elif total_ms < 5000:
            return "B"
        elif total_ms < 8000:
            return "C"
        elif total_ms < 12000:
            return "D"
        return "F"