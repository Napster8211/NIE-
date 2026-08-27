import unittest
import asyncio
from datetime import datetime, timezone
from app.schemas.executive_events import ExecutiveLiveEvent
from app.services.executive_event_service import executive_event_service, MAX_EVENT_BUFFER_SIZE
from app.engine.event_bus import BusinessEvent

class TestDirectorLiveEvents(unittest.TestCase):
    def setUp(self):
        # Reset the singleton state for clean testing
        executive_event_service._event_buffer.clear()
        executive_event_service._queues.clear()

    def _simulate_internal_event(self, event_type: str, evidence: str = "Test", metadata: dict = None):
        evt = BusinessEvent(
            event_id=f"test_{datetime.now().timestamp()}",
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lead_id="test_lead",
            business_name="Test Business",
            # Strict required fields added for Pydantic
            communication_id="",
            conversation_id="",
            correlation_id="",
            workflow_id="",
            channel="",
            evidence=evidence,
            confidence=1.0,
            execution_metadata=metadata or {}
        )
        asyncio.run(executive_event_service._handle_internal_event(evt))
        return evt.event_id

    def test_safe_projection_removes_secrets_and_retains_safe_fields(self):
        # Inject an event loaded with theoretically sensitive metadata
        self._simulate_internal_event(
            event_type="MISSION_STARTED",
            metadata={"mission_id": "mis_123", "secret_token": "sk-123", "provider_raw": "{...}"}
        )
        
        buf = executive_event_service._event_buffer
        self.assertEqual(len(buf), 1)
        safe_evt = buf[0]
        
        self.assertIsInstance(safe_evt, ExecutiveLiveEvent)
        self.assertEqual(safe_evt.entity_id, "mis_123")
        dump = safe_evt.model_dump()
        self.assertNotIn("secret_token", dump.get("metadata_safe", {}))
        self.assertNotIn("provider_raw", dump.get("metadata_safe", {}))

    def test_event_has_stable_id_and_timestamp(self):
        evt_id = self._simulate_internal_event("MISSION_VERIFIED")
        safe_evt = executive_event_service._event_buffer[-1]
        self.assertEqual(safe_evt.event_id, evt_id)
        self.assertIsNotNone(safe_evt.timestamp)

    def test_buffer_is_strictly_bounded(self):
        for i in range(MAX_EVENT_BUFFER_SIZE + 50):
            self._simulate_internal_event(f"EVENT_{i}")
        
        # Buffer should top out exactly at its bound, truncating oldest
        self.assertEqual(len(executive_event_service._event_buffer), MAX_EVENT_BUFFER_SIZE)

    def test_subscriber_queue_receives_live_events(self):
        q = executive_event_service.subscribe()
        self._simulate_internal_event("TEST_ACTIVITY")
        
        self.assertEqual(q.qsize(), 1)
        received = q.get_nowait()
        self.assertEqual(received.event_type, "TEST_ACTIVITY")

    def test_replay_last_event_id_semantics(self):
        ids = []
        for i in range(5):
            ids.append(self._simulate_internal_event(f"EVENT_{i}"))
            
        # Client reconnects and asks for everything AFTER index 2
        q = executive_event_service.subscribe(last_event_id=ids[2])
        
        # Should replay index 3 and 4
        self.assertEqual(q.qsize(), 2)
        evt3 = q.get_nowait()
        self.assertEqual(evt3.event_id, ids[3])
        evt4 = q.get_nowait()
        self.assertEqual(evt4.event_id, ids[4])

    def test_slow_subscriber_failure_does_not_block_mission_execution(self):
        # Create a queue with an artificially tiny maxsize
        q = executive_event_service.subscribe()
        q = executive_event_service._queues[-1]
        
        # We manipulate the queue purely to simulate backpressure 
        while not q.full():
            q.put_nowait(ExecutiveLiveEvent(event_id="fill", event_type="fill", entity_type="test", entity_id="test", headline="fill"))

        # This should trigger QueueFull internally, drop the event for THIS subscriber, and NOT throw.
        try:
            self._simulate_internal_event("OVERFLOW_EVENT")
            blocked = False
        except Exception:
            blocked = True
            
        self.assertFalse(blocked, "Execution loop blocked by slow SSE subscriber!")

    def test_stream_disconnect_cleans_subscriber_resources(self):
        q = executive_event_service.subscribe()
        self.assertEqual(len(executive_event_service._queues), 1)
        
        executive_event_service.unsubscribe(q)
        self.assertEqual(len(executive_event_service._queues), 0)

    def test_severity_mapping_is_conservative(self):
        self._simulate_internal_event("DEAL_WON")
        self._simulate_internal_event("MISSION_BLOCKED")
        self._simulate_internal_event("INFO_EVENT")
        
        buf = executive_event_service._event_buffer
        self.assertEqual(buf[0].severity, "SUCCESS")
        self.assertEqual(buf[1].severity, "WARNING")
        self.assertEqual(buf[2].severity, "INFO")

if __name__ == "__main__":
    unittest.main()