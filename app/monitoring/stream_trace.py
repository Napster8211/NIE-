import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StreamTraceTracker:
    request_sent_time: Optional[float] = None
    first_token_time: Optional[float] = None
    last_token_time: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    chunk_count: int = 0
    chunk_timestamps: List[float] = field(default_factory=list)

    def record_request_sent(self) -> None:
        self.request_sent_time = time.perf_counter()

    def record_chunk(self, token_count: int = 1) -> None:
        now = time.perf_counter()
        if self.first_token_time is None:
            self.first_token_time = now
        self.last_token_time = now
        self.chunk_count += 1
        self.output_tokens += token_count
        self.chunk_timestamps.append(now)

    @property
    def ttft_ms(self) -> float:
        if self.request_sent_time and self.first_token_time:
            return round((self.first_token_time - self.request_sent_time) * 1000, 2)
        return 0.0

    @property
    def ttlt_ms(self) -> float:
        if self.request_sent_time and self.last_token_time:
            return round((self.last_token_time - self.request_sent_time) * 1000, 2)
        return 0.0

    @property
    def streaming_duration_ms(self) -> float:
        if self.first_token_time and self.last_token_time:
            return round((self.last_token_time - self.first_token_time) * 1000, 2)
        return 0.0

    @property
    def tokens_per_second(self) -> float:
        duration_sec = self.streaming_duration_ms / 1000.0
        if duration_sec > 0 and self.output_tokens > 0:
            return round(self.output_tokens / duration_sec, 2)
        return 0.0

    @property
    def avg_chunk_interval_ms(self) -> float:
        if len(self.chunk_timestamps) < 2:
            return 0.0
        intervals = [
            (self.chunk_timestamps[i] - self.chunk_timestamps[i - 1]) * 1000
            for i in range(1, len(self.chunk_timestamps))
        ]
        return round(sum(intervals) / len(intervals), 2)