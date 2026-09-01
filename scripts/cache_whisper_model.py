"""Download and verify the configured faster-whisper model during deployment build."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional


DEFAULT_MODEL_SIZE = "tiny.en"
DEFAULT_CACHE_DIR = Path(".cache") / "whisper"
REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")


def prepare_model_cache(
    *,
    model_size: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    downloader: Optional[Callable[..., str]] = None,
) -> dict:
    """Populate the Hugging Face cache and prove it works without network access."""
    selected_model = (
        model_size
        or os.getenv("WHISPER_MODEL_SIZE", DEFAULT_MODEL_SIZE).strip()
        or DEFAULT_MODEL_SIZE
    )
    selected_cache = Path(
        cache_dir
        or os.getenv("WHISPER_MODEL_CACHE_DIR", str(DEFAULT_CACHE_DIR)).strip()
        or DEFAULT_CACHE_DIR
    ).resolve()
    selected_cache.mkdir(parents=True, exist_ok=True)

    if downloader is None:
        from faster_whisper.utils import download_model

        downloader = download_model

    downloader(selected_model, cache_dir=str(selected_cache))
    verified_path = Path(
        downloader(
            selected_model,
            cache_dir=str(selected_cache),
            local_files_only=True,
        )
    )
    missing = [name for name in REQUIRED_MODEL_FILES if not (verified_path / name).is_file()]
    if missing:
        raise RuntimeError("WHISPER_MODEL_CACHE_INCOMPLETE:" + ",".join(missing))

    model_bytes = sum(
        file.stat().st_size
        for file in verified_path.rglob("*")
        if file.is_file()
    )
    return {
        "model": selected_model,
        "cache_dir": str(selected_cache),
        "model_bytes": model_bytes,
    }


def main() -> int:
    try:
        result = prepare_model_cache()
    except Exception as exc:
        print(
            "WHISPER_MODEL_CACHE_FAILED "
            f"error_type={type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    print(
        "WHISPER_MODEL_CACHE_READY "
        f"model={result['model']} bytes={result['model_bytes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
