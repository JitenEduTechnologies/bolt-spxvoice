"""Real PostgreSQL contract tests for P1 idempotency and atomic publication.

Runs only against the dedicated throwaway database provisioned for this batch
(``P1_POSTGRES_TESTS=1``). It never targets the application database.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select

from api.db import db_client
from api.db.models import (
    ActionIdempotencyRecordModel,
    OrganizationModel,
    OrganizationUsageCycleModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.db.workflow_client import workflow_revision_fingerprint

pytestmark = pytest.mark.skipif(
    os.environ.get("P1_POSTGRES_TESTS") != "1",
    reason="requires P1_POSTGRES_TESTS=1 and the throwaway test database",
)


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


async def _create_org(suffix: str) -> int:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"p1-org-{suffix}")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org.id


async def _create_workflow_with_versions(
    suffix: str,
    *,
    published_configs: dict,
    draft_configs: dict,
) -> tuple[int, int, dict, dict]:
    async with db_client.async_session() as session:
        org = OrganizationModel(provider_id=f"p1-org-{suffix}")
        session.add(org)
        await session.flush()
        user = UserModel(
            provider_id=f"p1-user-{suffix}",
            email=f"p1-{suffix}@example.test",
            selected_organization_id=org.id,
        )
        session.add(user)
        await session.flush()
        workflow = WorkflowModel(
            name=f"p1-workflow-{suffix}",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={"nodes": [], "edges": []},
            workflow_configurations=published_configs,
        )
        session.add(workflow)
        await session.flush()
        published = WorkflowDefinitionModel(
            workflow_id=workflow.id,
            workflow_json={"nodes": [], "edges": []},
            workflow_configurations=published_configs,
            status="published",
            version_number=1,
            is_current=True,
        )
        draft = WorkflowDefinitionModel(
            workflow_id=workflow.id,
            workflow_json={"nodes": [], "edges": []},
            workflow_configurations=draft_configs,
            status="draft",
            version_number=2,
            is_current=False,
        )
        session.add_all([published, draft])
        await session.flush()
        workflow_id = workflow.id
        draft_id = draft.id
        draft_json = draft.workflow_json
        draft_configurations = draft.workflow_configurations
        workflow.released_definition_id = published.id
        await session.commit()
        return workflow_id, draft_id, draft_json, draft_configurations


@pytest.mark.asyncio
async def test_usage_idempotent_single_increment():
    suffix = _suffix()
    org_id = await _create_org(suffix)
    key = f"workflow_run_usage:p1-{suffix}"

    first = await db_client.update_usage_after_run(
        org_id, 10.5, 60, idempotency_key=key, workflow_run_id=None
    )
    second = await db_client.update_usage_after_run(
        org_id, 10.5, 60, idempotency_key=key, workflow_run_id=None
    )

    assert first is True
    assert second is False
    async with db_client.async_session() as session:
        cycle = (
            await session.execute(
                select(OrganizationUsageCycleModel).where(
                    OrganizationUsageCycleModel.organization_id == org_id
                )
            )
        ).scalar_one()
        assert float(cycle.used_dograh_tokens) == pytest.approx(10.5)
        assert cycle.total_duration_seconds == 60
        record = (
            await session.execute(
                select(ActionIdempotencyRecordModel).where(
                    ActionIdempotencyRecordModel.idempotency_key == key
                )
            )
        ).scalar_one()
        assert record.status == "COMPLETED"


@pytest.mark.asyncio
async def test_usage_concurrent_claim_increments_once():
    suffix = _suffix()
    org_id = await _create_org(suffix)
    # Pre-create the cycle so the race is exclusively about the claim key.
    await db_client.get_or_create_current_cycle(org_id)
    key = f"workflow_run_usage:p1-concurrent-{suffix}"

    results = await asyncio.gather(
        db_client.update_usage_after_run(org_id, 7.0, 30, idempotency_key=key),
        db_client.update_usage_after_run(org_id, 7.0, 30, idempotency_key=key),
    )

    assert sorted(results) == [False, True]
    async with db_client.async_session() as session:
        cycle = (
            await session.execute(
                select(OrganizationUsageCycleModel).where(
                    OrganizationUsageCycleModel.organization_id == org_id
                )
            )
        ).scalar_one()
        assert float(cycle.used_dograh_tokens) == pytest.approx(7.0)
        assert cycle.total_duration_seconds == 30
        count = (
            await session.execute(
                select(func.count())
                .select_from(ActionIdempotencyRecordModel)
                .where(ActionIdempotencyRecordModel.idempotency_key == key)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_atomic_publish_rejects_concurrent_edit():
    suffix = _suffix()
    workflow_id, draft_id, draft_json, draft_configs = (
        await _create_workflow_with_versions(
            suffix,
            published_configs={
                "max_call_duration_seconds": 60,
                "max_call_duration": 60,
            },
            draft_configs={
                "max_call_duration_seconds": 180,
                "max_call_duration": 180,
            },
        )
    )
    validated_revision = workflow_revision_fingerprint(draft_json, draft_configs)

    # A concurrent edit lands between validation and publication.
    async with db_client.async_session() as session:
        draft = (
            await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.id == draft_id
                )
            )
        ).scalar_one()
        draft.workflow_configurations = {
            "max_call_duration_seconds": 600,
            "max_call_duration": 600,
        }
        await session.commit()

    with pytest.raises(ValueError, match="workflow_draft_changed"):
        await db_client.publish_workflow_draft(
            workflow_id,
            expected_draft_id=draft_id,
            expected_revision=validated_revision,
            prepared_configurations={
                "max_call_duration_seconds": 180,
                "max_call_duration": 180,
            },
        )

    async with db_client.async_session() as session:
        published = (
            await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "published",
                )
            )
        ).scalars().all()
        assert [
            (
                row.version_number,
                row.workflow_configurations["max_call_duration_seconds"],
            )
            for row in published
        ] == [(1, 60)]
        draft = (
            await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.id == draft_id
                )
            )
        ).scalar_one()
        assert draft.status == "draft"
        assert draft.workflow_configurations["max_call_duration_seconds"] == 600


@pytest.mark.asyncio
async def test_atomic_publish_promotes_matching_revision():
    suffix = _suffix()
    workflow_id, draft_id, draft_json, draft_configs = (
        await _create_workflow_with_versions(
            suffix,
            published_configs={
                "max_call_duration_seconds": 60,
                "max_call_duration": 60,
            },
            draft_configs={"max_call_duration": 180},
        )
    )
    revision = workflow_revision_fingerprint(draft_json, draft_configs)
    prepared = {"max_call_duration": 180, "max_call_duration_seconds": 180}

    published = await db_client.publish_workflow_draft(
        workflow_id,
        expected_draft_id=draft_id,
        expected_revision=revision,
        prepared_configurations=prepared,
    )

    assert published.status == "published"
    assert published.version_number == 2
    assert published.workflow_configurations["max_call_duration_seconds"] == 180

    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(WorkflowDefinitionModel)
                .where(WorkflowDefinitionModel.workflow_id == workflow_id)
                .order_by(WorkflowDefinitionModel.version_number)
            )
        ).scalars().all()
        assert [(row.version_number, row.status) for row in rows] == [
            (1, "archived"),
            (2, "published"),
        ]
        # The old published snapshot stays immutable.
        assert rows[0].workflow_configurations["max_call_duration_seconds"] == 60
        workflow = (
            await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
        ).scalar_one()
        assert workflow.released_definition_id == draft_id
