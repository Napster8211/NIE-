import os
import unittest
from unittest.mock import patch

from scripts.validate_engineering_staging import validate as validate_staging
from scripts.verify_engineering_postgres import _validate_disposable_url


class EngineeringPostgresVerifierTests(unittest.TestCase):
    def test_staging_files_have_fail_closed_required_configuration(self):
        validate_staging()

    def test_accepts_explicitly_disposable_database_name(self):
        _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/nie_engineering_test")

    def test_rejects_identifiable_production_database(self):
        with self.assertRaisesRegex(SystemExit, "PRODUCTION_POSTGRES_URL_REJECTED"):
            _validate_disposable_url("postgresql://user:password@database.example/nie_production")

    def test_rejects_unlabelled_database_without_separate_explicit_override(self):
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": ""}, clear=False):
            with self.assertRaisesRegex(SystemExit, "MUST_IDENTIFY_DISPOSABLE_USE"):
                _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")

    def test_unlabelled_override_is_deliberate_and_case_sensitive(self):
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": "YES"}, clear=False):
            _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": "yes"}, clear=False):
            with self.assertRaises(SystemExit):
                _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")


if __name__ == "__main__":
    unittest.main()
