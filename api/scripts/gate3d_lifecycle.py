"""Gate 3D — workflow configuration lifecycle over the live staging API.

draft -> validate -> publish -> reload readback -> version history -> rollback
(functional: publish a new revision restoring the original configuration).
Synthetic staging workflow only (org-a workflow), no real services.
"""
import sys

import requests

API = "http://127.0.0.1:8013/api/v1"
PASSWORD = "Gate3b-Test-Only-2026"

BASE_CFG = {
    "product_mode": "standard",
    "max_call_duration_seconds": 600,
    "max_call_duration": 600,
    "model_overrides": {
        "is_realtime": True,
        "realtime": {
            "provider": "google_realtime",
            "model": "gemini-3.8-live",
            "voice": "Kore",
            "language": "en",
        },
    },
}

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if detail else ""))


def cfg_duration(cfg):
    return cfg.get("max_call_duration_seconds")


def main():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": "gate3b-owner-a@example.com", "password": PASSWORD},
        timeout=20,
    )
    token = r.json()["token"]
    ha = {"Authorization": f"Bearer {token}"}

    r = requests.get(f"{API}/workflow/fetch", headers=ha, timeout=20)
    wf_id = r.json()[0]["id"]

    r = requests.get(f"{API}/workflow/fetch/{wf_id}", headers=ha, timeout=20)
    check("initial readback has published config", r.status_code == 200, f"status={r.status_code}")
    initial = r.json()
    base_duration = initial["workflow_configurations"].get("max_call_duration_seconds")
    check("base duration is 600 (seeded)", base_duration == 600, f"value={base_duration}")

    r = requests.post(f"{API}/workflow/{wf_id}/create-draft", headers=ha, timeout=20)
    check("create-draft accepted", r.status_code in (200, 201, 409), f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/{wf_id}/versions", headers=ha, timeout=20)
    versions_before = r.json()
    max_before = max(int(v.get("version_number", 0)) for v in versions_before)
    new_cfg = dict(BASE_CFG)
    new_cfg["max_call_duration_seconds"] = 300
    new_cfg["max_call_duration"] = 300
    r = requests.put(
        f"{API}/workflow/{wf_id}",
        headers=ha,
        json={"workflow_configurations": new_cfg},
        timeout=20,
    )
    check("draft save (PUT) 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/{wf_id}/versions", headers=ha, timeout=20)
    versions = r.json()
    check("draft version visible", any(v.get("status") == "draft" for v in versions), f"count={len(versions)}")

    r = requests.post(f"{API}/workflow/{wf_id}/validate", headers=ha, timeout=20)
    check("validate 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.post(f"{API}/workflow/{wf_id}/publish", headers=ha, timeout=20)
    check("publish 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/fetch/{wf_id}", headers=ha, timeout=20)
    published = r.json()
    check(
        "reload readback shows new 300 config",
        published["workflow_configurations"].get("max_call_duration_seconds") == 300,
        f"value={published['workflow_configurations'].get('max_call_duration_seconds')}",
    )

    r = requests.get(f"{API}/workflow/{wf_id}/versions", headers=ha, timeout=20)
    versions = r.json()
    pubs = [v for v in versions if v.get("status") == "published"]
    archs = [v for v in versions if v.get("status") == "archived"]
    pub_max = max(int(v.get("version_number", 0)) for v in pubs)
    arch_max = max((int(v.get("version_number", 0)) for v in archs), default=0)
    check("exactly one published version", len(pubs) == 1, f"published={len(pubs)}")
    check("previous version archived (immutability)", len(archs) >= 1, f"archived={len(archs)}")
    check(
        "published version is the newest (number grew)",
        pub_max > arch_max,
        f"archived_max={arch_max} published={pub_max}",
    )

    # Functional rollback: publish a new revision restoring 600.
    requests.post(f"{API}/workflow/{wf_id}/create-draft", headers=ha, timeout=20)
    r = requests.put(
        f"{API}/workflow/{wf_id}",
        headers=ha,
        json={"workflow_configurations": dict(BASE_CFG)},
        timeout=20,
    )
    r = requests.post(f"{API}/workflow/{wf_id}/publish", headers=ha, timeout=20)
    r = requests.get(f"{API}/workflow/fetch/{wf_id}", headers=ha, timeout=20)
    restored = r.json()["workflow_configurations"].get("max_call_duration_seconds")
    check("rollback publish restores 600", restored == 600, f"value={restored}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nLIFECYCLE RESULT: {len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
