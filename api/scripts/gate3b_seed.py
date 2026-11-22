"""Gate 3B staging seed — synthetic tenants, roles, configs, published workflows.

Runs ONLY against the disposable staging DATABASE_URL. Creates:
- Org A: owner-a + staff-a; Org B: owner-b; superadmin root (in Org A).
- Per-owner user configuration (Standard / Google realtime, synthetic key).
- One synthetic published workflow per org (max_call_duration_seconds=600).
Idempotent-ish: skips users/workflows whose unique identifiers already exist.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.db import db_client
from api.db.models import (
    OrganizationModel,
    UserConfigurationModel,
    UserModel,
    WorkflowModel,
    organization_users_association,
)
from api.utils.auth import hash_password

PASSWORD = "Gate3b-Test-Only-2026"

GRAPH = {
    "nodes": [
        {
            "id": "start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": "Greet the caller and ask how you can help.",
                "is_start": True,
                "allow_interrupt": True,
            },
        },
        {
            "id": "end",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {"name": "End", "prompt": "Close the call politely.", "is_end": True},
        },
    ],
    "edges": [
        {
            "id": "start-end",
            "source": "start",
            "target": "end",
            "data": {"label": "End", "condition": "When the conversation is finished."},
        }
    ],
}

WF_CFG = {
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

USER_CFG = {
    "is_realtime": True,
    "realtime": {
        "provider": "google_realtime",
        "model": "gemini-3.8-live",
        "voice": "Kore",
        "language": "en",
        "api_key": "gate3b-staging-synthetic-key",
    },
}


async def ensure_user(session, *, provider_id, email, org, is_superuser=False):
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    existing = (
        await session.execute(select(UserModel).where(UserModel.email == email))
    ).scalars().first()
    if existing:
        existing.selected_organization_id = org.id
        stmt = pg_insert(organization_users_association).values(
            user_id=existing.id, organization_id=org.id
        )
        await session.execute(
            stmt.on_conflict_do_nothing(index_elements=["user_id", "organization_id"])
        )
        return existing
    user = UserModel(
        provider_id=provider_id,
        email=email,
        password_hash=hash_password(PASSWORD),
        selected_organization_id=org.id,
        is_superuser=is_superuser,
    )
    session.add(user)
    await session.flush()
    # Enforce correct org assignment even if first-run default setup later
    # reassigns selected_organization_id (staging-only repair).
    user.selected_organization_id = org.id
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(organization_users_association).values(
        user_id=user.id, organization_id=org.id
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["user_id", "organization_id"]))
    session.add(UserConfigurationModel(user_id=user.id, configuration=USER_CFG))
    return user


async def ensure_org(session, provider_id):
    from sqlalchemy import select

    existing = (
        await session.execute(
            select(OrganizationModel).where(OrganizationModel.provider_id == provider_id)
        )
    ).scalars().first()
    if existing:
        return existing
    org = OrganizationModel(provider_id=provider_id)
    session.add(org)
    await session.flush()
    return org


async def main() -> None:
    async with db_client.async_session() as session:
        org_a = await ensure_org(session, "gate3b-org-a")
        org_b = await ensure_org(session, "gate3b-org-b")
        await session.flush()

        owner_a = await ensure_user(
            session,
            provider_id="gate3b-owner-a",
            email="gate3b-owner-a@example.com",
            org=org_a,
        )
        owner_a_id = owner_a.id
        staff_a = await ensure_user(
            session,
            provider_id="gate3b-staff-a",
            email="gate3b-staff-a@example.com",
            org=org_a,
        )
        staff_a_id = staff_a.id
        root = await ensure_user(
            session,
            provider_id="gate3b-root",
            email="gate3b-root@example.com",
            org=org_a,
            is_superuser=True,
        )
        root_id = root.id
        owner_b = await ensure_user(
            session,
            provider_id="gate3b-owner-b",
            email="gate3b-owner-b@example.com",
            org=org_b,
        )
        owner_b_id = owner_b.id
        org_a_id = org_a.id
        org_b_id = org_b.id
        await session.commit()

    for label, user_id, org_id in (
        ("a", owner_a_id, org_a_id),
        ("b", owner_b_id, org_b_id),
    ):
        from sqlalchemy import select

        name = f"Gate3B Synthetic {label.upper()}"
        async with db_client.async_session() as session:
            existing_wf = (
                await session.execute(
                    select(WorkflowModel).where(
                        WorkflowModel.name == name,
                        WorkflowModel.organization_id == org_id,
                    )
                )
            ).scalars().first()
        if existing_wf:
            wf = existing_wf
            print(f"WORKFLOW {label}: (existing) id={wf.id} org={org_id} owner={user_id}")
            continue
        wf = await db_client.create_workflow(
            f"Gate3B Synthetic {label.upper()}",
            GRAPH,
            user_id=user_id,
            organization_id=org_id,
        )
        await db_client.save_workflow_draft(
            wf.id, workflow_configurations=WF_CFG
        )
        await db_client.publish_workflow_draft(wf.id)
        refreshed = await db_client.get_workflow(wf.id)
        print(
            f"WORKFLOW {label}: id={wf.id} org={org_id} owner={user_id} "
            f"published_version={refreshed.released_definition_id}"
        )
    print(
        f"USERS: owner-a={owner_a_id} staff-a={staff_a_id} root={root_id} "
        f"owner-b={owner_b_id}; ORGS: a={org_a_id} b={org_b_id}"
    )


if __name__ == "__main__":
    asyncio.run(main())
