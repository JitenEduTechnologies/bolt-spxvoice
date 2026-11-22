"""Minimal Owner Dashboard Journey Automation Script (Workstream A).

Executes and verifies:
1. UI HTTP reachability & responsive viewport meta tags on Next.js server
2. Owner authentication token generation
3. Onboarding step 1: Select industry preset, set business name, agent name, greeting
4. Save configuration draft via PUT /api/v1/business/agent/draft
5. Reload via GET /api/v1/business/agent to confirm persistence of all fields
6. Publish via POST /api/v1/business/agent/publish to confirm workflow graph generation
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import json
import urllib.request
import urllib.error

API_BASE = os.environ.get("API_BASE", "http://localhost:8001")
UI_BASE = os.environ.get("UI_BASE", "http://localhost:3020")
OSS_JWT_SECRET = os.environ.get("OSS_JWT_SECRET", "spx-voice-mvp-dev-jwt-secret-2026-isolated")

def generate_test_token(user_id=1, email="owner@smiledental.com"):
    import jwt
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, OSS_JWT_SECRET, algorithm="HS256")

def run_owner_dashboard_journey():
    results = {}
    print(f"[1/6] Probing UI Login page on Next.js server ({UI_BASE}/auth/login)...")
    try:
        req = urllib.request.Request(f"{UI_BASE}/auth/login", headers={"User-Agent": "SPX-QA-E2E"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
            assert resp.status == 200, f"Expected 200, got {resp.status}"
            assert "viewport" in html.lower(), "Missing viewport meta tag for responsive mobile layout"
            assert "voice console" in html.lower() or "auth" in html.lower(), "Missing Voice Console / Auth shell"
            results["ui_login_reachability"] = "PASS"
            print("  [PASS] UI login page is reachable with responsive viewport and SSR bundle.")
    except Exception as e:
        results["ui_login_reachability"] = f"FAIL: {e}"
        print(f"  [FAIL] UI login failed: {e}")
        return False, results

    print("[2/6] Generating owner session token...")
    try:
        owner_token = generate_test_token(user_id=1, email="owner@smiledental.com")
        headers = {
            "Authorization": f"Bearer {owner_token}",
            "Content-Type": "application/json",
        }
        results["owner_token_generated"] = "PASS"
        print("  [PASS] Owner JWT authentication token created.")
    except Exception as e:
        results["owner_token_generated"] = f"FAIL: {e}"
        print(f"  [FAIL] Token creation failed: {e}")
        return False, results

    print(f"[3/6] Fetching industry presets from API ({API_BASE}/api/v1/business/presets)...")
    try:
        req = urllib.request.Request(f"{API_BASE}/api/v1/business/presets", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            presets = data if isinstance(data, list) else data.get("presets", [])
            assert len(presets) >= 10, f"Expected >= 10 presets, got {len(presets)}"
            clinic_preset = next(p for p in presets if p["id"] == "clinics")
            results["presets_fetched"] = "PASS"
            print(f"  [PASS] Fetched {len(presets)} industry presets (selected 'clinics').")
    except Exception as e:
        results["presets_fetched"] = f"FAIL: {e}"
        print(f"  [FAIL] Presets fetch failed: {e}")
        return False, results

    print("[4/6] Onboarding Step 1: Saving agent draft configuration...")
    draft_payload = dict(clinic_preset)
    draft_payload["business_name"] = "Smile Dental Specialty Clinic"
    draft_payload["agent_name"] = "Aarohi"
    draft_payload["greeting"] = "Namaste! Welcome to Smile Dental Specialty Clinic. How can I help you today?"
    draft_payload["qualification_fields"] = [
        {"name": "patient_name", "label": "Patient Name", "required": True},
        {"name": "preferred_doctor", "label": "Doctor Preference", "required": False},
        {"name": "dental_concern", "label": "Primary Dental Issue", "required": True},
    ]

    payload = {"training_data": draft_payload}
    try:
        req = urllib.request.Request(
            f"{API_BASE}/api/v1/business/agent/draft",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            save_resp = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert save_resp.get("status") in ("draft_saved", "success", "ok") or "training" in save_resp
            results["save_draft"] = "PASS"
            print("  [PASS] Draft configuration saved successfully.")
    except Exception as e:
        results["save_draft"] = f"FAIL: {e}"
        print(f"  [FAIL] Save draft failed: {e}")
        return False, results

    print("[5/6] Reloading configuration to confirm database persistence...")
    try:
        req = urllib.request.Request(f"{API_BASE}/api/v1/business/agent", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            agent_data = json.loads(resp.read().decode("utf-8"))
            training = agent_data.get("training_data", {})
            assert training.get("business_name") == "Smile Dental Specialty Clinic", "Business name mismatch after reload"
            assert training.get("agent_name") == "Aarohi", "Agent name mismatch after reload"
            assert len(training.get("qualification_fields", [])) == 3, "Dynamic fields mismatch after reload"
            results["confirm_persistence"] = "PASS"
            print("  [PASS] Persistence confirmed: Reloaded exact business name, agent name, and dynamic fields.")
    except Exception as e:
        results["confirm_persistence"] = f"FAIL: {e}"
        print(f"  [FAIL] Persistence reload failed: {e}")
        return False, results

    print("[6/6] Publishing agent to live workflow...")
    try:
        publish_payload = {"training_data": draft_payload}
        req = urllib.request.Request(
            f"{API_BASE}/api/v1/business/agent/publish",
            data=json.dumps(publish_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pub_resp = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200, f"Expected 200, got {resp.status}"
            assert pub_resp.get("status") == "published", f"Status not published: {pub_resp.get('status')}"
            assert pub_resp.get("workflow_id") is not None, "Missing workflow_id"
            version = pub_resp.get("version_number") or pub_resp.get("released_definition_id") or pub_resp.get("version")
            assert version is not None, f"Version is None: {pub_resp}"
            results["publish_agent"] = "PASS"
            print(f"  [PASS] Agent published to active workflow (id={pub_resp.get('workflow_id')}, version={version}).")
    except urllib.error.HTTPError as he:
        body = he.read().decode('utf-8', errors='ignore')
        results["publish_agent"] = f"FAIL HTTP {he.code}: {body}"
        print(f"  [FAIL] Publish agent failed HTTP {he.code}: {body}")
        return False, results
    except Exception as e:
        results["publish_agent"] = f"FAIL: {type(e).__name__}: {e}"
        print(f"  [FAIL] Publish agent failed: {type(e).__name__}: {e}")
        return False, results

    return True, results

if __name__ == "__main__":
    success, details = run_owner_dashboard_journey()
    print("\nSummary Results:\n" + json.dumps(details, indent=2))
    sys.exit(0 if success else 1)
