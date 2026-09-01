"""Run the Director STT benchmark against user-supplied recordings.

Audio files must be named <sample-id>.<supported-extension>. Missing recordings
are reported, never synthesized or assigned fabricated measurements.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import replace
from pathlib import Path

from app.services.whisper_stt_provider import WhisperSTTConfig, WhisperSTTProvider


def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_item in enumerate(left, start=1):
        current = [row]
        for column, right_item in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_item != right_item),
            ))
        previous = current
    return previous[-1]


def normalized_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", value.casefold())


def find_audio(directory: Path, sample_id: str) -> Path | None:
    for suffix in (".webm", ".mp4", ".ogg", ".wav", ".mp3"):
        candidate = directory / f"{sample_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


async def run(args: argparse.Namespace) -> list[dict]:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    config = replace(WhisperSTTConfig.from_env(), model_size=args.model)
    provider = WhisperSTTProvider(config)
    results = []
    try:
        for sample in manifest:
            audio = find_audio(args.audio_dir, sample["id"])
            if audio is None:
                results.append({**sample, "status": "MISSING_AUDIO"})
                continue
            started_at = time.perf_counter()
            result = await provider.transcribe(audio)
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            expected_words = normalized_words(sample["expected"])
            actual_words = normalized_words(result.text)
            expected_chars = list("".join(expected_words))
            actual_chars = list("".join(actual_words))
            results.append({
                **sample,
                "status": "MEASURED",
                "actual": result.text,
                "wer": edit_distance(expected_words, actual_words) / max(1, len(expected_words)),
                "cer": edit_distance(expected_chars, actual_chars) / max(1, len(expected_chars)),
                "stt_latency_ms": latency_ms,
                "whisper_inference_ms": result.inference_ms,
                "meaning_preserved": None,
                "clarification_required": None,
            })
    finally:
        await provider.shutdown()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--model", default="tiny.en", choices=("tiny.en", "base.en", "small.en"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/director_voice_commands.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = asyncio.run(run(args))
    payload = json.dumps(results, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
