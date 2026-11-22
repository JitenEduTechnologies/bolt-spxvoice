"""VOICE-01 finalization recovery sweep against REAL PostgreSQL.

The sweep's candidate queries used `.astext`, which does not exist for the JSON column type, so every
sweep raised AttributeError ("recovery sweep failed; will retry") and stranded finalizations were never
recovered. Runs only with CP02_PG_TESTS=1 against the disposable `postgres_test` database.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.livekit import finalization_recovery as fr
from api.services.livekit import post_call_triggers

_DB_NAME = constants.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
pytestmark = pytest.mark.skipif(
    os.environ.get("CP02_PG_TESTS") != "1" or _DB_NAME != "postgres_test",
    reason="requires CP02_PG_TESTS=1 and DATABASE_URL pointing at the disposable 'postgres_test' database",
)

HISTORY = {
    "livekit_history": {
        "items": [
            {"type": "message", "role": "assistant", "content": ["Namaste, how can I help?"]},
            {"type": "message", "role": "user", "content": ["I need a 2BHK flat in Gachibowli, budget 80 lakhs. Please book a site visit Saturday."]},
        ]
    }
}


def _marker(state, minutes_ago, **extra):
    updated = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return {"livekit_finalization": {"state": state, "recovery_version": 1, "updated_at": updated, **extra}}


@pytest_asyncio.fixture()
async def pg():
    engine = create_async_engine(constants.DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_client.async_session
    db_client.async_session = maker
    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        org = OrganizationModel(provider_id=f"voice01-rec-{tag}")
        s.add(org)
        await s.flush()
        user = UserModel(provider_id=f"voice01-rec-u-{tag}", email=f"rec-{tag}@voice01.invalid", selected_organization_id=org.id)
        s.add(user)
        await s.flush()
        wf = WorkflowModel(name=f"voice01-rec-wf-{tag}", user_id=user.id, organization_id=org.id, workflow_definition={"nodes": [], "edges": []})
        s.add(wf)
        await s.commit()
        env = SimpleNamespace(org=org.id, user=user.id, wf=wf.id, runs=[])

    async def add_run(**kw):
        base = dict(
            name=f"WR-LK-IN-{uuid.uuid4().hex[:6]}", workflow_id=env.wf, mode="livekit", call_type="inbound",
            state="completed", is_completed=True, usage_info={"call_duration_seconds": 75}, cost_info={},
            initial_context={"phone_number": "+919800000001"}, gathered_context={}, logs=HISTORY, annotations={},
            created_at=datetime.now(UTC) - timedelta(minutes=30), is_simulation=False, call_disposition="UNKNOWN",
        )
        base.update(kw)
        async with maker() as s:
            run = WorkflowRunModel(**base)
            s.add(run)
            await s.commit()
            env.runs.append(run.id)
            return run.id

    async def get(run_id):
        async with maker() as s:
            return (await s.execute(select(WorkflowRunModel).where(WorkflowRunModel.id == run_id))).scalars().first()

    env.add_run, env.get = add_run, get
    yield env

    # Remove every row that references this test's organization or runs (postgres_test only).
    async with maker() as s:
        cols = (await s.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('organization_id', 'workflow_run_id')"
        ))).all()
        for table, column in cols:
            if table in ("organizations", "workflow_runs", "workflows"):
                continue
            ids = [env.org] if column == "organization_id" else env.runs
            if ids:
                await s.execute(text(f'DELETE FROM "{table}" WHERE "{column}" = ANY(:ids)'), {"ids": ids})
        await s.execute(text("DELETE FROM workflow_runs WHERE workflow_id = :wf"), {"wf": env.wf})
        await s.execute(text("DELETE FROM workflows WHERE id = :wf"), {"wf": env.wf})
        await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": env.user})
        await s.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": env.org})
        await s.commit()
    db_client.async_session = original
    await engine.dispose()


def _no_external_effects():
    return [
        patch.object(post_call_triggers.realtime_dashboard_bus, "publish_event", AsyncMock()),
        patch.object(post_call_triggers.chatwoot_outbox_service, "enqueue_message", AsyncMock()),
    ]


async def _sweep_ours(env):
    outcomes = await fr.recover_pending_livekit_finalizations(limit=200)
    return {o["workflow_run_id"]: o for o in outcomes if o["workflow_run_id"] in env.runs}


@pytest.mark.asyncio
async def test_candidate_queries_execute_on_postgres(pg):
    stranded = await pg.add_run(annotations=_marker("pending", 10))
    assert stranded in [r.id for r in await fr._load_candidates(200)]
    incomplete = await pg.add_run(is_completed=False, state="running", annotations=_marker("failed", 10))
    assert incomplete in [r.id for r in await fr._load_incomplete_candidates(200)]


@pytest.mark.asyncio
async def test_sweep_recovers_stranded_finalizations_and_leaves_everything_else_alone(pg):
    stranded = await pg.add_run(annotations=_marker("pending", 10))
    crashed = await pg.add_run(is_completed=False, state="running", annotations=_marker("failed", 10))
    untouched = {
        "within_grace": await pg.add_run(annotations=_marker("pending", 1)),
        "other_version": await pg.add_run(annotations={"livekit_finalization": {**_marker("pending", 10)["livekit_finalization"], "recovery_version": 2}}),
        "other_mode": await pg.add_run(mode="vobiz", annotations=_marker("pending", 10)),
        "leased": await pg.add_run(annotations=_marker("recovering", 10, lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())),
        "already_done": await pg.add_run(annotations=_marker("succeeded", 10)),
    }
    before = {k: (await pg.get(v)).annotations for k, v in untouched.items()}

    patches = _no_external_effects()
    for p in patches:
        p.start()
    try:
        first = await _sweep_ours(pg)
        second = await _sweep_ours(pg)
    finally:
        for p in patches:
            p.stop()

    assert first[stranded]["state"] == "succeeded", first
    assert first[crashed]["state"] == "succeeded" and first[crashed]["incomplete_recovery"] is True, first
    assert set(first) == {stranded, crashed}  # nothing else was picked up
    assert second == {}  # idempotent: a recovered run is never processed again

    run = await pg.get(stranded)
    marker = run.annotations["livekit_finalization"]
    assert marker["state"] == "succeeded" and marker["attempts"] == 1 and marker["lease_owner"] is None
    assert run.annotations["analysis"]["status"] in ("complete", "completed", "succeeded")  # post-call analysis persisted
    assert run.annotations.get("lead_intelligence", {}).get("lead_classification")
    crashed_run = await pg.get(crashed)
    assert crashed_run.is_completed is True
    assert crashed_run.annotations["livekit_finalization"]["incomplete_recovery"] is True
    for k, v in untouched.items():
        assert (await pg.get(v)).annotations == before[k], k


@pytest.mark.asyncio
async def test_concurrent_sweeps_recover_each_run_exactly_once(pg):
    ids = [await pg.add_run(annotations=_marker("pending", 10)) for _ in range(3)]
    patches = _no_external_effects()
    for p in patches:
        p.start()
    try:
        await asyncio.gather(_sweep_ours(pg), _sweep_ours(pg))
    finally:
        for p in patches:
            p.stop()
    for run_id in ids:
        marker = (await pg.get(run_id)).annotations["livekit_finalization"]
        assert marker["state"] == "succeeded" and marker["attempts"] == 1, (run_id, marker)
