"""Opt-in private Supabase Storage smoke test; never runs without explicit safeguards."""

import hashlib
import os
import unittest
import uuid

from app.services.supabase_workspace_storage import SupabaseStorageHttpClient, SupabaseStorageSettings


@unittest.skipUnless(
    os.getenv("NIE_RUN_SUPABASE_STORAGE_SMOKE", "") == "YES"
    and os.getenv("NIE_TEST_SUPABASE_CONFIRM_DISPOSABLE", "") == "YES",
    "requires explicit disposable Supabase Storage credentials",
)
class SupabaseStorageIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_object_round_trip_and_scoped_cleanup(self):
        settings = SupabaseStorageSettings.from_environment()
        bucket = settings.bucket.casefold()
        if not any(marker in bucket for marker in ("test", "staging", "stage", "disposable")):
            self.fail("SUPABASE_TEST_BUCKET_MUST_IDENTIFY_DISPOSABLE_USE")
        if os.getenv("NIE_ENV", "").strip().casefold() in {"production", "prod"}:
            self.fail("SUPABASE_STORAGE_SMOKE_REJECTED_IN_PRODUCTION")

        client = SupabaseStorageHttpClient(settings)
        prefix = f"integration-tests/{uuid.uuid4().hex}"
        content = b"NIE_SUPABASE_STORAGE_OK"
        key = f"{prefix}/{hashlib.sha256(content).hexdigest()}"
        try:
            await client.upload(key, content)
            self.assertEqual(content, await client.download(key))
            self.assertIn(key, await client.list(prefix))
        finally:
            await client.delete([key])


if __name__ == "__main__":
    unittest.main()
