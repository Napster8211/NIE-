import os
import sys
import time
from typing import Optional, Dict, Any
from app.monitoring.request_trace import RequestTraceContext
from app.monitoring.provider_trace import ProviderTraceContext, ProviderMetadataProvider
from app.monitoring.stream_trace import StreamTraceTracker
from app.monitoring.latency_metrics import LatencyCalculator
from app.monitoring.recommendation_engine import RecommendationEngine


class PerformanceProfiler:
    """
    Enterprise-grade performance profiler for NIE execution pipeline.
    Active only when PERFORMANCE_DEBUG=true.
    """

    def __init__(self, request_id: str = "N/A"):
        self.enabled: bool = os.getenv("PERFORMANCE_DEBUG", "false").lower() in ("true", "1", "yes")
        self.req_ctx = RequestTraceContext(request_id=request_id)
        self.prov_ctx = ProviderTraceContext(request_id=request_id)
        self.stream_ctx = StreamTraceTracker()

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        extracted = ProviderMetadataProvider.extract_metadata(metadata)
        extracted.request_id = self.req_ctx.request_id
        if self.req_ctx.conversation_id != "N/A":
            extracted.conversation_id = self.req_ctx.conversation_id
        self.prov_ctx = extracted

    def set_conversation_id(self, conversation_id: str) -> None:
        if not self.enabled:
            return
        self.req_ctx.conversation_id = conversation_id
        self.prov_ctx.conversation_id = conversation_id

    def start(self, stage_name: str) -> None:
        if not self.enabled:
            return
        
        if stage_name == "Provider Request Sent":
            self.stream_ctx.record_request_sent()

        self.req_ctx.start_stage(stage_name)

    def end(self, stage_name: str) -> None:
        if not self.enabled:
            return
        self.req_ctx.end_stage(stage_name)

    def record_chunk(self, token_count: int = 1) -> None:
        if not self.enabled:
            return
        self.stream_ctx.record_chunk(token_count=token_count)

    def report(self) -> None:
        if not self.enabled:
            return

        self.req_ctx.request_end_time = time.perf_counter()
        total_ms = round((self.req_ctx.request_end_time - self.req_ctx.request_start_time) * 1000, 2)
        
        slowest_name, slowest_ms, slowest_pct = LatencyCalculator.find_slowest_stage(
            self.req_ctx.stages, total_ms
        )
        grade = LatencyCalculator.calculate_grade(total_ms, self.stream_ctx.ttft_ms)
        warnings, recommendations = RecommendationEngine.analyze(
            self.req_ctx, self.prov_ctx, self.stream_ctx, total_ms
        )

        lines = [
            "\n" + "=" * 52,
            "NapsterTec Intelligence Engine Trace Report",
            "=" * 52,
            f"Request ID:      {self.req_ctx.request_id}",
            f"Conversation:    {self.req_ctx.conversation_id}",
            "",
            f"Provider:        {self.prov_ctx.provider_name}",
            f"Model:           {self.prov_ctx.model_id}",
            "",
            f"Capability:      {self.prov_ctx.capability_used}",
            f"Skill:           {self.prov_ctx.skill_used}",
            "",
            f"Streaming:       {'Yes' if self.prov_ctx.streaming_enabled else 'No'}",
            f"Fallback:        {'Yes (' + str(self.prov_ctx.fallback_provider) + ')' if self.prov_ctx.fallback_used else 'No'}",
            "-" * 52,
            f"{'Pipeline Stage':<35} {'Time (ms)':>15}",
            "-" * 52
        ]

        for name in self.req_ctx.stage_order:
            record = self.req_ctx.stages.get(name)
            if record:
                lines.append(f"{record.name:<35} {int(round(record.duration_ms)):>15}")

        lines.extend([
            "-" * 52,
            f"Total Response Time {int(round(total_ms)):>31} ms",
            f"Performance Grade {grade:>33}",
            "-" * 52,
            "Provider Metrics",
            f"Time To First Token:     {int(round(self.stream_ctx.ttft_ms))} ms",
            f"Time To Last Token:      {int(round(self.stream_ctx.ttlt_ms))} ms",
            f"Input Tokens:            {self.stream_ctx.input_tokens}",
            f"Output Tokens:           {self.stream_ctx.output_tokens}",
            f"Total Tokens:            {self.stream_ctx.input_tokens + self.stream_ctx.output_tokens}",
            f"Tokens / Second:         {self.stream_ctx.tokens_per_second}",
            f"Chunk Count:             {self.stream_ctx.chunk_count}",
            f"Average Chunk Interval:  {self.stream_ctx.avg_chunk_interval_ms} ms",
            "-" * 52,
            f"Slowest Stage: {slowest_name} ({slowest_pct}%)",
            "-" * 52,
            "Warnings & Recommendations:"
        ])

        for warn in warnings:
            lines.append(f"  {warn}")
        for rec in recommendations:
            lines.append(f"  {rec}")

        lines.append("=" * 52 + "\n")

        report = "\n".join(lines)
        try:
            print(report, flush=True)
        except UnicodeEncodeError:
            # Windows services commonly inherit a cp1252 console. Diagnostics
            # must never fail an otherwise completed agent execution.
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(report.encode(encoding, errors="replace").decode(encoding), flush=True)
