"""
NapsterTec AI - Milestone 3 Production Verification Script
URL: https://napstertecengine.up.railway.app

Verifies:
1. Production API Health
2. Milestone 3 Viewport & Control Handoff Endpoints
3. Strict authentication enforcement on Milestone 3 endpoints
"""

import sys
import httpx

PROD_URL = "https://napstertecengine.up.railway.app"

def main():
    print("=" * 60)
    print("NAPSTERTEC AI — MILESTONE 3 PRODUCTION VERIFICATION")
    print(f"Target: {PROD_URL}")
    print("=" * 60)

    with httpx.Client(base_url=PROD_URL, timeout=15.0) as client:
        # 1. Health Check
        try:
            res = client.get("/api/v1/health")
            print(f"[1] Health Check: HTTP {res.status_code}")
            assert res.status_code == 200, f"Expected 200, got {res.status_code}"
            print("    -> Production backend is HEALTHY.")
        except Exception as e:
            print(f"    -> Health check failed: {e}")
            sys.exit(1)

        # 2. Viewport Endpoint Auth Enforcement
        res = client.get("/api/v1/browser/session/test-m3-probe/viewport")
        print(f"[2] GET /browser/session/.../viewport: HTTP {res.status_code}")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        assert "DIRECTOR_AUTH_REQUIRED" in res.text
        print("    -> Viewport endpoint deployed & protected with 401.")

        # 3. Direct Interact Endpoint Auth Enforcement
        res = client.post(
            "/api/v1/browser/session/test-m3-probe/interact",
            json={"type": "mouse", "action": "click", "x": 100, "y": 100},
        )
        print(f"[3] POST /browser/session/.../interact: HTTP {res.status_code}")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        assert "DIRECTOR_AUTH_REQUIRED" in res.text
        print("    -> Interaction endpoint deployed & protected with 401.")

        # 4. Take Control Endpoint Auth Enforcement
        res = client.post("/api/v1/browser/session/test-m3-probe/take-control")
        print(f"[4] POST /browser/session/.../take-control: HTTP {res.status_code}")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        assert "DIRECTOR_AUTH_REQUIRED" in res.text
        print("    -> Take-control endpoint deployed & protected with 401.")

        # 5. Return Control Endpoint Auth Enforcement
        res = client.post("/api/v1/browser/session/test-m3-probe/return-control")
        print(f"[5] POST /browser/session/.../return-control: HTTP {res.status_code}")
        assert res.status_code == 401, f"Expected 401, got {res.status_code}"
        assert "DIRECTOR_AUTH_REQUIRED" in res.text
        print("    -> Return-control endpoint deployed & protected with 401.")

    print("\n" + "=" * 60)
    print("ALL MILESTONE 3 VERIFICATION CHECKS PASSED.")
    print("=" * 60)

if __name__ == "__main__":
    main()
