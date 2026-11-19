"""
Live production verification of Milestone 2 on Railway.
"""
import urllib.request
import json
import sys

BASE = "https://napstertecengine.up.railway.app/api/v1"

def get_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def delete_req(url):
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def main():
    print("=== 1. CHECK ACTIVE SESSIONS BEFORE TEST ===")
    st, initial_sessions = get_json(f"{BASE}/browser/sessions")
    print(f"Initial sessions count: {len(initial_sessions)}")

    print("\n=== 2. RUN SESSION 1 (Navigation + Ready Event) ===")
    sid1 = "prod_m2_sess_1"
    stream_data = json.dumps({
        "goal": "Navigate to https://example.com and inspect",
        "session_id": sid1,
        "start_url": "https://example.com",
        "allow_interactions": True
    }).encode("utf-8")

    req1 = urllib.request.Request(f"{BASE}/browser/stream", data=stream_data, headers={"Content-Type": "application/json"})
    events_sid1 = []
    with urllib.request.urlopen(req1, timeout=90) as resp:
        for line in resp:
            decoded = line.decode("utf-8").strip()
            if decoded.startswith("event: "):
                events_sid1.append(decoded[7:])

    print(f"Session 1 events received: {events_sid1}")
    assert "browser.session_created" in events_sid1
    assert "browser.ready" in events_sid1
    assert "browser.action_completed" in events_sid1
    assert "completion" in events_sid1

    print("\n=== 3. QUERY SESSION 1 VIA GET /browser/session/{session_id} ===")
    st, s1_info = get_json(f"{BASE}/browser/session/{sid1}")
    state = s1_info.get("state") or s1_info.get("status")
    url = s1_info.get("url")
    print(f"Session 1 state: {state}, URL: {url}")
    assert state in ("ACTIVE", "COMPLETED", "WAITING_FOR_USER")
    assert "example.com" in url

    print("\n=== 4. RUN SESSION 2 (Independent Session) ===")
    sid2 = "prod_m2_sess_2"
    stream_data2 = json.dumps({
        "goal": "Navigate to https://httpbin.org/get",
        "session_id": sid2,
        "start_url": "https://httpbin.org/get",
        "allow_interactions": True
    }).encode("utf-8")

    req2 = urllib.request.Request(f"{BASE}/browser/stream", data=stream_data2, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=90) as resp:
        pass

    print("\n=== 5. LIST SESSIONS (Verify multi-session tracking in BrowserSessionManager) ===")
    st, active_sessions = get_json(f"{BASE}/browser/sessions")
    print(f"Active sessions tracked in manager: {len(active_sessions)}")
    for s in active_sessions:
        print(f" - ID: {s.get('session_id')}, State: {s.get('state')}, URL: {s.get('url')}")
    assert len(active_sessions) >= 2

    print("\n=== 6. CLEANUP / TERMINATE SESSION 1 VIA DELETE ===")
    del_st, del_res = delete_req(f"{BASE}/browser/session/{sid1}")
    print(f"Delete response: {del_res}")
    assert del_res.get("terminated") is True
    assert del_res.get("status") == "CLOSED"

    print("\n=== 7. VERIFY SESSION 1 IS CLOSED ===")
    st, s1_closed_info = get_json(f"{BASE}/browser/session/{sid1}")
    print(f"Session 1 closed status: {s1_closed_info.get('status')}")
    assert s1_closed_info.get("status") == "CLOSED"

    print("\n=== 8. CLEANUP SESSION 2 VIA DELETE ===")
    del_st, del_res2 = delete_req(f"{BASE}/browser/session/{sid2}")
    print(f"Delete response 2: {del_res2}")

    print("\n=== 9. VERIFY FAILURE BEHAVIOR IN PRODUCTION ===")
    fail_data = json.dumps({
        "goal": "Navigate to https://127.0.0.1:9999",
        "session_id": "prod_m2_fail_test",
        "start_url": "https://127.0.0.1:9999",
        "allow_interactions": True
    }).encode("utf-8")
    req_fail = urllib.request.Request(f"{BASE}/browser/stream", data=fail_data, headers={"Content-Type": "application/json"})
    fail_event_found = False
    with urllib.request.urlopen(req_fail, timeout=40) as resp:
        for line in resp:
            decoded = line.decode("utf-8").strip()
            if decoded.startswith("data: "):
                try:
                    payload = json.loads(decoded[6:])
                    if payload.get("status") == "FAILED" or payload.get("data", {}).get("status") == "FAILED":
                        print(f"Found FAILED payload: {payload.get('data') or payload}")
                        fail_event_found = True
                        break
                except Exception:
                    pass
    delete_req(f"{BASE}/browser/session/prod_m2_fail_test")
    assert fail_event_found, "Failed to capture expected FAILED event"

    print("\nALL 9 PRODUCTION VERIFICATION STEPS PASSED 100%!")

if __name__ == "__main__":
    main()
