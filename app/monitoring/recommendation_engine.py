from typing import List, Tuple
from app.monitoring.request_trace import RequestTraceContext
from app.monitoring.provider_trace import ProviderTraceContext
from app.monitoring.stream_trace import StreamTraceTracker


class RecommendationEngine:

    @staticmethod
    def analyze(
        req_ctx: RequestTraceContext,
        prov_ctx: ProviderTraceContext,
        stream_ctx: StreamTraceTracker,
        total_ms: float
    ) -> Tuple[List[str], List[str]]:
        warnings: List[str] = []
        recommendations: List[str] = []

        # 1. TTFT Threshold Warning (> 3s)
        if stream_ctx.ttft_ms > 3000:
            warnings.append(f"⚠ TTFT exceeds 3 seconds ({round(stream_ctx.ttft_ms / 1000, 2)}s)")
            recommendations.append("⚠ Provider queue appears slow or model load time is high")

        # 2. Streaming Duration Warning (> 10s)
        if stream_ctx.streaming_duration_ms > 10000:
            warnings.append(f"⚠ Streaming exceeds 10 seconds ({round(stream_ctx.streaming_duration_ms / 1000, 2)}s)")

        # 3. Low Throughput Check
        if prov_ctx.streaming_enabled and stream_ctx.tokens_per_second < 8.0 and stream_ctx.output_tokens > 5:
            recommendations.append("⚠ Streaming throughput below expected; model inference appears slow")

        # 4. Fallback & Retry Checks
        if prov_ctx.fallback_used:
            warnings.append("⚠ Fallback triggered")
            recommendations.append(f"⚠ Switched to fallback provider ({prov_ctx.fallback_provider or 'Secondary'})")

        if prov_ctx.retry_count > 0:
            warnings.append(f"⚠ Retry detected ({prov_ctx.retry_count} retries)")

        if prov_ctx.provider_health_status != "Healthy":
            warnings.append(f"⚠ Provider unavailable or degraded: {prov_ctx.provider_health_status}")

        # 5. Efficiency Positives
        intent_stage = req_ctx.stages.get("Intent Detection")
        if intent_stage and intent_stage.duration_ms < 50:
            recommendations.append("✓ Intent detection excellent")

        routing_stage = req_ctx.stages.get("Capability Routing")
        if routing_stage and routing_stage.duration_ms < 20:
            recommendations.append("✓ Router latency excellent")

        if not recommendations:
            recommendations.append("✓ Pipeline operating within optimal parameters")

        return warnings, recommendations