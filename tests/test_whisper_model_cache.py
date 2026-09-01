import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.cache_whisper_model import REQUIRED_MODEL_FILES, prepare_model_cache


class TestWhisperModelCache(unittest.TestCase):
    def test_download_is_followed_by_offline_cache_verification(self):
        calls = []
        with TemporaryDirectory() as cache_root:
            snapshot = Path(cache_root) / "snapshot"

            def downloader(model, *, cache_dir, local_files_only=False):
                calls.append((model, Path(cache_dir), local_files_only))
                snapshot.mkdir(parents=True, exist_ok=True)
                if not local_files_only:
                    for name in REQUIRED_MODEL_FILES:
                        (snapshot / name).write_bytes(b"model-data")
                return str(snapshot)

            result = prepare_model_cache(
                model_size="tiny.en",
                cache_dir=Path(cache_root),
                downloader=downloader,
            )

        self.assertEqual([call[2] for call in calls], [False, True])
        self.assertEqual(result["model"], "tiny.en")
        self.assertGreater(result["model_bytes"], 0)

    def test_incomplete_cache_fails_build(self):
        with TemporaryDirectory() as cache_root:
            snapshot = Path(cache_root) / "snapshot"
            snapshot.mkdir()
            (snapshot / "config.json").write_text("{}", encoding="utf-8")

            def downloader(_model, *, cache_dir, local_files_only=False):
                return str(snapshot)

            with self.assertRaisesRegex(
                RuntimeError,
                "WHISPER_MODEL_CACHE_INCOMPLETE",
            ):
                prepare_model_cache(
                    model_size="tiny.en",
                    cache_dir=Path(cache_root),
                    downloader=downloader,
                )


if __name__ == "__main__":
    unittest.main()
