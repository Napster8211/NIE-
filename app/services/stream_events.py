"""Small, deterministic Server-Sent Event framing helpers."""

import json
from typing import Any


def sse_event(event_type: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"
