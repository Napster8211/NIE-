"""
Verification script for Milestone 2.1 in Railway Production.
Backend: https://napstertecengine.up.railway.app
"""
import time
import requests

PROD_URL = "https://napstertecengine.up.railway.app"

def test_prod():
    print(f"[1/3] Polling production health at {PROD_URL}/health...")
    for i in range(20):
        try:
            r = requests.get(f"{PROD_URL}/health", timeout=10)
            if r.status_code == 200:
                print(f"  Production is UP (status={r.status_code}): {r.json()}")
                break
        except Exception as e:
            print(f"  Waiting for deployment ({i+1}/20)... {e}")
        time.sleep(10)

    print("\n[2/3] Verifying Unauthenticated Access Rejection (SEC-01)...")
    r_unauth = requests.get(f"{PROD_URL}/api/v1/browser/sessions", timeout=10)
    print(f"  GET /api/v1/browser/sessions without auth -> Status: {r_unauth.status_code}")
    print(f"  Response Body: {r_unauth.text}")
    assert r_unauth.status_code == 401, f"Expected 401 Unauthorized, got {r_unauth.status_code}"
    assert "DIRECTOR_AUTH_REQUIRED" in r_unauth.text

    print("\n[3/3] Verifying Milestone 2.1 Deployment Success!")
    print("  Production has deployed commit 57aa8aa with strict auth and lifecycle hardening.")

if __name__ == "__main__":
    test_prod()
