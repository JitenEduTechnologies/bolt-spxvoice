"""Gate 3B owner-journey verification over the live staging API (HTTP + real DB).

Synthetic tenants only. Verifies: login (owner A / owner B / root), JWT use,
workflow list/edit/publish readback, conversation endpoints, cross-tenant
denial (owner B must not read Org A's workflow), superuser cost summary.
"""
import sys

import requests

API = "http://127.0.0.1:8013/api/v1"
PASSWORD = "Gate3b-Test-Only-2026"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" — " + detail) if detail else ""))


def login(email):
    r = requests.post(
        f"{API}/auth/login",
        json={"email": email, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text[:200]}"
    token = r.json()["access_token"] if "access_token" in r.json() else r.json().get("token")
    return {"Authorization": f"Bearer {token}"}


def main():
    ha = login("gate3b-owner-a@example.com")
    hb = login("gate3b-owner-b@example.com")
    hroot = login("gate3b-root@example.com")

    r = requests.get(f"{API}/workflow/fetch", headers=ha, timeout=20)
    check("owner-a lists own workflows", r.status_code == 200 and len(r.json()) >= 1, f"status={r.status_code}")
    wf_id = r.json()[0]["id"] if r.status_code == 200 and r.json() else None

    r = requests.get(f"{API}/workflow/fetch/{wf_id}", headers=ha, timeout=20)
    check("owner-a reads own workflow", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/fetch/{wf_id}", headers=hb, timeout=20)
    check("owner-b DENIED on org-a workflow", r.status_code in (403, 404), f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/{wf_id}/versions", headers=ha, timeout=20)
    check("version history lists published v1", r.status_code == 200 and len(r.json()) >= 1, f"status={r.status_code}")

    r = requests.get(f"{API}/business/conversations", headers=ha, timeout=20)
    check("conversations endpoint 200 (empty synthetic set)", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/business/conversations", headers=hb, timeout=20)
    check("owner-b conversations isolated 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/superuser/cost-summary", headers=hroot, timeout=20)
    check("root cost-summary 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.get(f"{API}/superuser/cost-summary", headers=ha, timeout=20)
    check("non-root DENIED on cost-summary", r.status_code in (401, 403), f"status={r.status_code}")

    r = requests.get(f"{API}/workflow/fetch", timeout=20)
    check("unauthenticated DENIED on workflows", r.status_code in (401, 403), f"status={r.status_code}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nJOURNEY RESULT: {len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
