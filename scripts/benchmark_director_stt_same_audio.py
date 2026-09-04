"""Compare Whisper configurations against one owner-supplied audio recording.

This is an explicit offline diagnostic. It never records, uploads, or persists
audio; every configuration reads the same input path supplied by the operator.
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


DOMAIN_PROMPT = (
    "Sayibu. NapsterTec. NapsterTec Intelligence Engine. NIE. Director. "
    "Executive OS. Engineering and Delivery. Growth and Marketing. "
    "Sales and Revenue. Operations and Success. Finance."
)


def _edit_distance(left: list[str], right: list[str]) -> int:
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


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", value.casefold())


async def _measure(
    audio: Path,
    expected: str,
    *,
    model: str,
    beam_size: int,
    initial_prompt: str | None,
) -> dict:
    config = replace(
        WhisperSTTConfig.from_env(),
        model_size=model,
        beam_size=beam_size,
        initial_prompt=initial_prompt,
    )
    provider = WhisperSTTProvider(config)
    try:
        started_at = time.perf_counter()
        result = await provider.transcribe(audio)
        inference_total_ms = round((time.perf_counter() - started_at) * 1000)
    finally:
        await provider.shutdown()

    expected_words = _words(expected)
    actual_words = _words(result.text)
    expected_chars = list("".join(expected_words))
    actual_chars = list("".join(actual_words))
    return {
        "model": model,
        "beam_size": beam_size,
        "domain_prompt": bool(initial_prompt),
        "transcript": result.text,
        "avg_logprob": result.avg_logprob,
        "no_speech_probability": result.no_speech_probability,
        "wer": _edit_distance(expected_words, actual_words) / max(1, len(expected_words)),
        "cer": _edit_distance(expected_chars, actual_chars) / max(1, len(expected_chars)),
        "meaning_preserved": None,
        "inference_ms": result.inference_ms,
        "total_ms": inference_total_ms,
        "memory_impact": "NOT_MEASURED",
    }


async def run(args: argparse.Namespace) -> list[dict]:
    if not args.audio.is_file():
        raise FileNotFoundError("AUDIO_FILE_NOT_FOUND")
    results = []
    for model in args.models:
        for beam_size in args.beam_sizes:
            prompts = (None, DOMAIN_PROMPT) if args.compare_domain_prompt else (None,)
            for prompt in prompts:
                results.append(await _measure(
                    args.audio,
                    args.expected,
                    model=model,
                    beam_size=beam_size,
                    initial_prompt=prompt,
                ))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument(
        "--models",
        nargs="+",
        default=["tiny.en", "base.en"],
        choices=("tiny.en", "base.en", "small.en"),
    )
    parser.add_argument("--beam-sizes", nargs="+", type=int, default=[1, 5])
    parser.add_argument("--compare-domain-prompt", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=2))


if __name__ == "__main__":
    main()
