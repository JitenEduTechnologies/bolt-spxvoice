"""P0 regression: creating a workflow run on REAL PostgreSQL with the PRODUCTION session settings.

`create_workflow_run` read `workflow.organization_id` after `session.commit()`. Production sessions expire
objects on commit (async_sessionmaker default), so the read lazy-loaded inside async code and raised
MissingGreenlet -> HTTP 500 on every run-creation path, after the run row had already been committed.
Earlier suites masked it with expire_on_commit=False. Runs only with CP02_PG_TESTS=1 on `postgres_test`.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import constants
from api.db import db_client
from api.db.models import OrganizationModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.events.realtime_bus import realtime_dashboard_bus

_DB_NAME = constants.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
pytestmark = pytest.mark.skipif(
    os.environ.get("CP02_PG_TESTS") != "1" or _DB_NAME != "postgres_test",
    reason="requires CP02_PG_TESTS=1 and DATABASE_URL pointing at the disposable 'postgres_test' database",
)


@pytest_asyncio.fixture()
async def pg():
    engine = create_async_engine(constants.DATABASE_URL, poolclass=NullPool)
    # Exactly the production session configuration (only the engine differs, for per-test event loops).
    prod_kw = {k: v for k, v in db_client.async_session.kw.items() if k != "bind"}
    maker = async_sessionmaker(bind=engine, **prod_kw)
    assert maker.kw.get("expire_on_commit", True) is True, "test must use expire-on-commit like production"
    original = db_client.async_session
    db_client.async_session = maker
    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        org = OrganizationModel(provider_id=f"p0-run-{tag}")
        s.add(org)
        await s.flush()
        user = UserModel(provider_id=f"p0-run-u-{tag}", email=f"p0-{tag}@spx.invalid", selected_organization_id=org.id)
        s.add(user)
        await s.flush()
        wf = WorkflowModel(name=f"p0-wf-{tag}", user_id=user.id, organization_id=org.id, workflow_definition={"nodes": [], "edges": []})
        s.add(wf)
        await s.flush()
        env = SimpleNamespace(org=org.id, user=user.id, wf=wf.id)
        await s.commit()
    yield env
    async with maker() as s:
        run_ids = (await s.execute(select(WorkflowRunModel.id).where(WorkflowRunModel.workflow_id == env.wf))).scalars().all()
        cols = (await s.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('organization_id', 'workflow_run_id')"))).all()
        for table, column in cols:
            if table in ("organizations", "workflow_runs", "workflows"):
                continue
            ids = [env.org] if column == "organization_id" else list(run_ids)
            if ids:
                await s.execute(text(f'DELETE FROM "{table}" WHERE "{column}" = ANY(:ids)'), {"ids": ids})
        await s.execute(text("DELETE FROM workflow_runs WHERE workflow_id = :wf"), {"wf": env.wf})
        await s.execute(text("DELETE FROM workflows WHERE id = :wf"), {"wf": env.wf})
        await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": env.user})
        await s.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": env.org})
        await s.commit()
    db_client.async_session = original
    await engine.dispose()


async def _runs(env):
    async with db_client.async_session() as s:
        return (await s.execute(select(func.count()).select_from(WorkflowRunModel).where(WorkflowRunModel.workflow_id == env.wf))).scalar()


@pytest.mark.asyncio
async def test_create_workflow_run_with_production_session_settings(pg):
    """The route path (`POST /workflow/{id}/runs`): exactly one run, returned to the caller, event to the right tenant."""
    publish = AsyncMock()
    with patch.object(realtime_dashboard_bus, "publish_event", publish):
        run = await db_client.create_workflow_run("WR-P0", pg.wf, "livekit", pg.user, use_draft=True, organization_id=pg.org)
    # the response the route builds reads these attributes after the session closed
    assert run.id and run.workflow_id == pg.wf and run.mode == "livekit" and run.created_at is not None
    assert run.initial_context == {} and run.gathered_context == {}
    assert await _runs(pg) == 1  # one row, owned by the caller: nothing orphaned
    publish.assert_awaited_once()
    kwargs = publish.await_args.kwargs
    assert kwargs["organization_id"] == pg.org and kwargs["event_type"] == "conversation_started"
    assert kwargs["data"]["workflow_run_id"] == run.id and kwargs["data"]["state"] == "initialized"


@pytest.mark.asyncio
async def test_other_tenant_cannot_create_runs_on_the_workflow(pg):
    """Tenant isolation is unchanged: a foreign organization gets an error and no row is written."""
    with patch.object(realtime_dashboard_bus, "publish_event", AsyncMock()) as publish:
        with pytest.raises(Exception):
            await db_client.create_workflow_run("WR-P0-X", pg.wf, "livekit", pg.user, use_draft=True, organization_id=pg.org + 999_999)
    assert await _runs(pg) == 0
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_failure_never_breaks_run_creation(pg):
    with patch.object(realtime_dashboard_bus, "publish_event", AsyncMock(side_effect=RuntimeError("bus down"))):
        run = await db_client.create_workflow_run("WR-P0-E", pg.wf, "livekit", pg.user, use_draft=True, organization_id=pg.org)
    assert run.id and await _runs(pg) == 1
