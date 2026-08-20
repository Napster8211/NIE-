import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.engine.mission_engine import _cross_process_registry_lock, mission_registry


MISSION_OBJECTIVE = (
    "Research exactly one business prospect and produce exactly one valid LeadArtifact."
)


class MissionRegistryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_file = Path(self.temp_dir.name) / "missions.json"
        self.original_file = mission_registry.mission_file
        with mission_registry._lock:
            mission_registry.mission_file = str(self.test_file)
            mission_registry.missions = {}

    def tearDown(self):
        with mission_registry._lock:
            mission_registry.mission_file = self.original_file
            mission_registry.missions = {}
        with mission_registry.locked():
            pass
        self.temp_dir.cleanup()

    def _create_mission(self):
        return mission_registry.create_mission(MISSION_OBJECTIVE)

    def test_initial_atomic_save(self):
        mission = self._create_mission()

        self.assertTrue(self.test_file.exists())
        persisted = json.loads(self.test_file.read_text(encoding="utf-8"))
        self.assertEqual([mission.mission_id], list(persisted))

    def test_atomic_replacement_of_existing_registry(self):
        mission = self._create_mission()
        mission.health = "DEGRADED"
        mission_registry.save_mission(mission)

        persisted = json.loads(self.test_file.read_text(encoding="utf-8"))
        self.assertEqual("DEGRADED", persisted[mission.mission_id]["health"])

    def test_repeated_sequential_saves(self):
        mission = self._create_mission()
        for index in range(5):
            mission.action_history.append(f"save-{index}")
            mission_registry.save_mission(mission)

        persisted = json.loads(self.test_file.read_text(encoding="utf-8"))
        self.assertEqual([f"save-{index}" for index in range(5)], persisted[mission.mission_id]["action_history"])

    def test_replacement_failure_preserves_old_registry(self):
        mission = self._create_mission()
        before = self.test_file.read_bytes()
        mission.health = "MUST_NOT_PERSIST"

        with patch("app.engine.mission_engine.os.replace", side_effect=PermissionError(5, "Access is denied")):
            with self.assertRaises(PermissionError):
                mission_registry.save_mission(mission)

        self.assertEqual(before, self.test_file.read_bytes())
        self.assertNotEqual(
            "MUST_NOT_PERSIST",
            mission_registry.get_mission(mission.mission_id).health,
        )

    def test_failed_replacement_cleans_temporary_file(self):
        mission = self._create_mission()
        with patch("app.engine.mission_engine.os.replace", side_effect=PermissionError(5, "Access is denied")):
            with self.assertRaises(PermissionError):
                mission_registry.save_mission(mission)

        self.assertEqual([], list(Path(self.temp_dir.name).glob(".missions-*.tmp")))

    def test_malformed_serialization_cannot_corrupt_old_registry(self):
        mission = self._create_mission()
        before = self.test_file.read_bytes()
        mission.success_evidence = [{"not_json_serializable": {"set-value"}}]

        with self.assertRaises(TypeError):
            mission_registry.save_mission(mission)

        self.assertEqual(before, self.test_file.read_bytes())
        self.assertEqual([], list(Path(self.temp_dir.name).glob(".missions-*.tmp")))

    def test_concurrent_thread_access_is_serialized(self):
        mission = self._create_mission()

        def append_history(index):
            with mission_registry.locked():
                current = mission_registry.missions[mission.mission_id]
                current.action_history.append(f"thread-{index}")
                mission_registry.save_mission(current)

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(append_history, range(20)))

        persisted = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(20, len(persisted.action_history))
        self.assertEqual(
            {f"thread-{index}" for index in range(20)},
            set(persisted.action_history),
        )

    @unittest.skipUnless(os.name == "nt", "Windows named-mutex behavior")
    def test_cross_process_reader_is_serialized_before_replace(self):
        target = Path(self.temp_dir.name) / "cross-process.json"
        target.write_text(json.dumps({"version": 1}), encoding="utf-8")
        child_code = (
            "import sys,time; "
            "from app.engine.mission_engine import _cross_process_registry_lock; "
            "ctx=_cross_process_registry_lock(sys.argv[1]); ctx.__enter__(); "
            "handle=open(sys.argv[1],chr(114)); print('READY',flush=True); "
            "time.sleep(0.5); handle.close(); ctx.__exit__(None,None,None)"
        )
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", child_code, str(target)],
            cwd=str(Path.cwd()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        temp_path = None
        try:
            self.assertEqual("READY", child.stdout.readline().strip())
            started = time.monotonic()
            with _cross_process_registry_lock(str(target)):
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix=".missions-",
                    suffix=".tmp",
                    dir=self.temp_dir.name,
                    delete=False,
                ) as handle:
                    temp_path = handle.name
                    json.dump({"version": 2}, handle, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, target)
                temp_path = None
            elapsed = time.monotonic() - started
            child.wait(timeout=10)

            self.assertEqual(0, child.returncode, child.stderr.read())
            self.assertGreaterEqual(elapsed, 0.25)
            self.assertEqual({"version": 2}, json.loads(target.read_text(encoding="utf-8")))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
            if temp_path and Path(temp_path).exists():
                Path(temp_path).unlink()

    def test_mission_creation_persistence_failure_fails_closed(self):
        with patch("app.engine.mission_engine.os.replace", side_effect=PermissionError(5, "Access is denied")):
            with self.assertRaises(PermissionError):
                self._create_mission()

        self.assertFalse(self.test_file.exists())

    def test_failed_creation_leaves_no_phantom_mission(self):
        with patch("app.engine.mission_engine.os.replace", side_effect=PermissionError(5, "Access is denied")):
            with self.assertRaises(PermissionError):
                self._create_mission()

        self.assertEqual({}, mission_registry.missions)
        self.assertEqual({}, mission_registry.snapshot())


if __name__ == "__main__":
    unittest.main()
