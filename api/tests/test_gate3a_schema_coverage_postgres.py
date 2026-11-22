"""Gate 3A-R — fresh-install schema-coverage regression (real PostgreSQL).

Runs only with CP02_PG_TESTS=1 against the disposable 'postgres_test'
database. Proves that ``alembic upgrade head`` produces every table defined
in the SQLAlchemy metadata, that the five Gate 3A-R tables carry their full
contracts (columns, foreign keys, uniqueness), that minimal inserts succeed,
that the idempotency unique constraint is enforced, and that a second
verification pass shows no drift.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import constants
from api.db import db_client
from api.db.models import (
    ActionIdempotencyRecordModel,
    Base,
    OrganizationModel,
)

_DB_NAME = constants.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
pytestmark = pytest.mark.skipif(
    os.environ.get("CP02_PG_TESTS") != "1" or _DB_NAME != "postgres_test",
    reason="requires CP02_PG_TESTS=1 and DATABASE_URL pointing at the disposable 'postgres_test' database",
)

EXPECTED_TABLES = set(Base.metadata.tables)

FIVE_TABLES = {
    "action_idempotency_records": [
        "id", "idempotency_key", "organization_id", "workflow_run_id",
        "action_type", "action_payload", "status", "result", "created_at",
    ],
    "customer_suppression_dnc": [
        "id", "phone_number", "organization_id", "reason", "source",
        "created_at", "expires_at", "evidence",
    ],
    "ecommerce_order_records": [
        "id", "organization_id", "order_id", "customer_name", "customer_phone",
        "customer_email", "items", "total_amount", "payment_type",
        "order_status", "delivery_status", "shipping_carrier", "tracking_number",
        "cod_confirmed", "delivery_rescheduled_date", "return_reason",
        "store_provider", "interaction_history", "last_sync",
    ],
    "school_fee_records": [
        "id", "organization_id", "student_id", "student_name", "guardian_name",
        "guardian_phone", "secondary_phone", "class_section", "academic_term",
        "fee_category", "total_fee", "paid_amount", "outstanding_amount",
        "concessions_waivers", "due_date", "status", "data_source", "last_sync",
        "reminder_history", "metadata_info",
    ],
    "telephony_number_health": [
        "id", "phone_number", "organization_id", "state", "consecutive_failures",
        "active_calls", "total_calls", "successful_calls", "failed_calls",
        "last_failure_reason", "last_failure_code", "cooldown_until",
        "quarantine_reason", "quarantined_at", "carrier", "dlt_header_id",
        "created_at", "updated_at",
    ],
}

UNIQUE_CONSTRAINTS = {
    "customer_suppression_dnc": {"uq_org_phone_dnc"},
    "ecommerce_order_records": {"uq_ecommerce_order"},
    "school_fee_records": {"uq_school_student_fee"},
    "telephony_number_health": {"uq_org_phone_health"},
}

# customer_suppression_dnc intentionally has NO foreign key in the model.
FK_COUNTS = {
    "action_idempotency_records": 2,
    "ecommerce_order_records": 1,
    "school_fee_records": 1,
    "telephony_number_health": 1,
}


@pytest_asyncio.fixture()
async def pg():
    engine = create_async_engine(constants.DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_client.async_session
    db_client.async_session = maker
    yield maker
    db_client.async_session = original
    await engine.dispose()


async def _tables(maker) -> set[str]:
    async with maker() as s:
        rows = (
            await s.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
        ).scalars().all()
    return set(rows)


async def _columns(maker, table) -> set[str]:
    async with maker() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            )
        ).scalars().all()
    return set(rows)


async def _unique_names(maker, table) -> set[str]:
    async with maker() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid=('public.' || :t)::regclass AND contype='u'"
                ),
                {"t": table},
            )
        ).scalars().all()
    return set(rows)


async def _fk_count(maker, table) -> int:
    async with maker() as s:
        return (
            await s.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints tc "
                    "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' "
                    "AND tc.table_name=:t"
                ),
                {"t": table},
            )
        ).scalar()


@pytest.mark.asyncio
async def test_alembic_head_covers_all_model_tables(pg):
    missing = EXPECTED_TABLES - await _tables(pg)
    assert not missing, f"model tables missing after upgrade head: {sorted(missing)}"


@pytest.mark.asyncio
async def test_alembic_head_is_gate3ar_revision(pg):
    async with pg() as s:
        v = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert v == "d4e2f1a9b8c7"


@pytest.mark.asyncio
async def test_five_gate3ar_tables_have_full_columns(pg):
    for table, cols in FIVE_TABLES.items():
        actual = await _columns(pg, table)
        assert set(cols) <= actual, f"{table} missing: {sorted(set(cols) - actual)}"


@pytest.mark.asyncio
async def test_gate3ar_unique_constraints_and_fks(pg):
    for table, names in UNIQUE_CONSTRAINTS.items():
        actual = await _unique_names(pg, table)
        assert names <= actual, f"{table} missing unique constraints: {names - actual}"
    assert await _unique_names(pg, "action_idempotency_records"), (
        "action_idempotency_records has no unique constraint"
    )
    for table, count in FK_COUNTS.items():
        assert await _fk_count(pg, table) == count, f"{table} FK count mismatch"


@pytest.mark.asyncio
async def test_minimal_insert_and_idempotency_uniqueness(pg):
    tag = uuid.uuid4().hex[:8]
    key = f"g3ar-key-{tag}"
    async with pg() as s:
        org = OrganizationModel(provider_id=f"g3ar-org-{tag}")
        s.add(org)
        await s.flush()
        org_id = org.id
        rec = ActionIdempotencyRecordModel(
            idempotency_key=key, organization_id=org.id, action_type="test"
        )
        s.add(rec)
        await s.commit()
        assert rec.id is not None
        assert rec.status == "IN_PROGRESS"  # server default applied
    async with pg() as s:
        dup = ActionIdempotencyRecordModel(
            idempotency_key=key, organization_id=org_id, action_type="test"
        )
        s.add(dup)
        with pytest.raises(Exception):
            await s.commit()
        await s.rollback()
    async with pg() as s:
        await s.execute(
            text("DELETE FROM action_idempotency_records WHERE idempotency_key=:k"),
            {"k": key},
        )
        await s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": org_id})
        await s.commit()


@pytest.mark.asyncio
async def test_second_verification_shows_no_drift(pg):
    missing = EXPECTED_TABLES - await _tables(pg)
    assert not missing, f"drift after first verification: {sorted(missing)}"
    for table, cols in FIVE_TABLES.items():
        actual = await _columns(pg, table)
        assert set(cols) <= actual, f"{table} drifted: {sorted(set(cols) - actual)}"

