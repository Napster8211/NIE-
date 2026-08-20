import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List


@dataclass
class StageRecord:
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: float = 0.0


@dataclass
class RequestTraceContext:
    request_id: str = "N/A"
    conversation_id: str = "N/A"
    stages: Dict[str, StageRecord] = field(default_factory=dict)
    stage_order: List[str] = field(default_factory=list)
    request_start_time: float = field(default_factory=time.perf_counter)
    request_end_time: Optional[float] = None
    total_duration_ms: float = 0.0

    def start_stage(self, stage_name: str) -> None:
        now = time.perf_counter()
        if stage_name not in self.stages:
            self.stage_order.append(stage_name)
        self.stages[stage_name] = StageRecord(name=stage_name, start_time=now)

    def end_stage(self, stage_name: str) -> None:
        now = time.perf_counter()
        record = self.stages.get(stage_name)
        if record:
            record.end_time = now
            record.duration_ms = round((now - record.start_time) * 1000, 2)