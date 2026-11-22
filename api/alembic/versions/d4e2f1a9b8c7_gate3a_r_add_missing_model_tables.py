"""gate3a-r: create five model tables missing from the migration chain

Five SQLAlchemy models (action_idempotency_records, customer_suppression_dnc,
ecommerce_order_records, school_fee_records, telephony_number_health) existed
only on databases that received manual SQL patches. A fresh ``alembic upgrade
head`` database was missing all five, breaking usage idempotency, post-call
analysis idempotency, DNC compliance, industry records and number health.

This single revision creates each table per its model contract. For databases
that already contain a table (historical/manual creation), the table is
structurally validated and left untouched; incompatible schemas raise a clear
exception (fail-closed, never rebuilt or truncated). The downgrade is
intentionally fail-closed: these tables may hold financial, compliance or
operational data, so they are never dropped automatically.

Revision ID: d4e2f1a9b8c7
Revises: c02a1d3e5f70
Create Date: 2026-11-20
"""
from alembic import op

revision = "d4e2f1a9b8c7"
down_revision = "c02a1d3e5f70"
branch_labels = None
depends_on = None

_UPGRADE_SQL = r"""
DO $$
DECLARE
    missing_count integer;
BEGIN
    -- 1. action_idempotency_records ------------------------------------
    IF to_regclass('public.action_idempotency_records') IS NULL THEN
        CREATE TABLE action_idempotency_records (
            id SERIAL PRIMARY KEY,
            idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            workflow_run_id INTEGER NULL REFERENCES workflow_runs(id),
            action_type VARCHAR(64) NOT NULL,
            action_payload JSON NOT NULL DEFAULT '{}'::json,
            status VARCHAR(32) NOT NULL DEFAULT 'IN_PROGRESS',
            result JSON NULL,
            created_at TIMESTAMPTZ NULL
        );
        CREATE INDEX ix_action_idempotency_records_id
            ON action_idempotency_records (id);
        CREATE INDEX ix_action_idempotency_records_idempotency_key
            ON action_idempotency_records (idempotency_key);
    ELSE
        SELECT count(*) INTO missing_count
        FROM unnest(ARRAY['id','idempotency_key','organization_id',
                          'workflow_run_id','action_type','action_payload',
                          'status','result','created_at']::text[]) AS n(name)
        LEFT JOIN information_schema.columns c
               ON c.table_schema='public'
              AND c.table_name='action_idempotency_records'
              AND c.column_name = n.name
        WHERE c.column_name IS NULL;
        IF missing_count > 0 THEN
            RAISE EXCEPTION 'action_idempotency_records exists but is missing required columns (structural drift; manual reconciliation required)';
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name='action_idempotency_records'
                     AND column_name='idempotency_key'
                     AND data_type <> 'character varying') THEN
            RAISE EXCEPTION 'action_idempotency_records.idempotency_key has an unexpected type (structural drift)';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.action_idempotency_records'::regclass
                         AND contype = 'u') THEN
            RAISE EXCEPTION 'action_idempotency_records exists without its unique idempotency constraint (structural drift)';
        END IF;
    END IF;

    -- 2. customer_suppression_dnc --------------------------------------
    IF to_regclass('public.customer_suppression_dnc') IS NULL THEN
        CREATE TABLE customer_suppression_dnc (
            id SERIAL PRIMARY KEY,
            phone_number VARCHAR(32) NOT NULL,
            organization_id INTEGER NOT NULL DEFAULT 0,
            reason VARCHAR(255) NOT NULL DEFAULT 'manual_opt_out',
            source VARCHAR(64) NOT NULL DEFAULT 'manual',
            created_at TIMESTAMPTZ NULL,
            expires_at TIMESTAMPTZ NULL,
            evidence JSON NOT NULL DEFAULT '{}'::json,
            CONSTRAINT uq_org_phone_dnc UNIQUE (organization_id, phone_number)
        );
        CREATE INDEX ix_customer_suppression_dnc_id
            ON customer_suppression_dnc (id);
        CREATE INDEX ix_customer_suppression_dnc_phone_number
            ON customer_suppression_dnc (phone_number);
        CREATE INDEX idx_dnc_org_phone
            ON customer_suppression_dnc (organization_id, phone_number);
    ELSE
        SELECT count(*) INTO missing_count
        FROM unnest(ARRAY['id','phone_number','organization_id','reason',
                          'source','created_at','expires_at','evidence']::text[]) AS n(name)
        LEFT JOIN information_schema.columns c
               ON c.table_schema='public'
              AND c.table_name='customer_suppression_dnc'
              AND c.column_name = n.name
        WHERE c.column_name IS NULL;
        IF missing_count > 0 THEN
            RAISE EXCEPTION 'customer_suppression_dnc exists but is missing required columns (structural drift; manual reconciliation required)';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.customer_suppression_dnc'::regclass
                         AND contype = 'u'
                         AND conname = 'uq_org_phone_dnc') THEN
            RAISE EXCEPTION 'customer_suppression_dnc exists without uq_org_phone_dnc (structural drift)';
        END IF;
    END IF;

    -- 3. ecommerce_order_records ----------------------------------------
    IF to_regclass('public.ecommerce_order_records') IS NULL THEN
        CREATE TABLE ecommerce_order_records (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            order_id VARCHAR(64) NOT NULL,
            customer_name VARCHAR(128) NOT NULL,
            customer_phone VARCHAR(32) NOT NULL,
            customer_email VARCHAR(128) NULL,
            items JSON NOT NULL DEFAULT '[]'::json,
            total_amount NUMERIC(10,2) NOT NULL,
            payment_type VARCHAR(32) NOT NULL DEFAULT 'COD',
            order_status VARCHAR(64) NOT NULL DEFAULT 'NEW',
            delivery_status VARCHAR(64) NULL,
            shipping_carrier VARCHAR(64) NULL,
            tracking_number VARCHAR(128) NULL,
            cod_confirmed BOOLEAN NOT NULL DEFAULT false,
            delivery_rescheduled_date DATE NULL,
            return_reason VARCHAR(255) NULL,
            store_provider VARCHAR(64) NOT NULL DEFAULT 'manual_import',
            interaction_history JSON NOT NULL DEFAULT '[]'::json,
            last_sync TIMESTAMPTZ NULL,
            CONSTRAINT uq_ecommerce_order UNIQUE (organization_id, order_id)
        );
        CREATE INDEX ix_ecommerce_order_records_id
            ON ecommerce_order_records (id);
        CREATE INDEX ix_ecommerce_order_records_customer_phone
            ON ecommerce_order_records (customer_phone);
        CREATE INDEX idx_ecommerce_org_order
            ON ecommerce_order_records (organization_id, order_status);
    ELSE
        SELECT count(*) INTO missing_count
        FROM unnest(ARRAY['id','organization_id','order_id','customer_name',
                          'customer_phone','customer_email','items','total_amount',
                          'payment_type','order_status','delivery_status',
                          'shipping_carrier','tracking_number','cod_confirmed',
                          'delivery_rescheduled_date','return_reason',
                          'store_provider','interaction_history','last_sync']::text[]) AS n(name)
        LEFT JOIN information_schema.columns c
               ON c.table_schema='public'
              AND c.table_name='ecommerce_order_records'
              AND c.column_name = n.name
        WHERE c.column_name IS NULL;
        IF missing_count > 0 THEN
            RAISE EXCEPTION 'ecommerce_order_records exists but is missing required columns (structural drift; manual reconciliation required)';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.ecommerce_order_records'::regclass
                         AND contype = 'u'
                         AND conname = 'uq_ecommerce_order') THEN
            RAISE EXCEPTION 'ecommerce_order_records exists without uq_ecommerce_order (structural drift)';
        END IF;
    END IF;

    -- 4. school_fee_records --------------------------------------------
    IF to_regclass('public.school_fee_records') IS NULL THEN
        CREATE TABLE school_fee_records (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            student_id VARCHAR(64) NOT NULL,
            student_name VARCHAR(128) NOT NULL,
            guardian_name VARCHAR(128) NOT NULL,
            guardian_phone VARCHAR(32) NOT NULL,
            secondary_phone VARCHAR(32) NULL,
            class_section VARCHAR(64) NOT NULL,
            academic_term VARCHAR(64) NOT NULL,
            fee_category VARCHAR(64) NOT NULL,
            total_fee NUMERIC(10,2) NOT NULL,
            paid_amount NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            outstanding_amount NUMERIC(10,2) NOT NULL,
            concessions_waivers NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            due_date DATE NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            data_source VARCHAR(64) NOT NULL DEFAULT 'manual_import',
            last_sync TIMESTAMPTZ NULL,
            reminder_history JSON NOT NULL DEFAULT '[]'::json,
            metadata_info JSON NOT NULL DEFAULT '{}'::json,
            CONSTRAINT uq_school_student_fee UNIQUE
                (organization_id, student_id, academic_term, fee_category)
        );
        CREATE INDEX ix_school_fee_records_id
            ON school_fee_records (id);
        CREATE INDEX ix_school_fee_records_guardian_phone
            ON school_fee_records (guardian_phone);
        CREATE INDEX idx_school_org_status
            ON school_fee_records (organization_id, status);
    ELSE
        SELECT count(*) INTO missing_count
        FROM unnest(ARRAY['id','organization_id','student_id','student_name',
                          'guardian_name','guardian_phone','secondary_phone',
                          'class_section','academic_term','fee_category',
                          'total_fee','paid_amount','outstanding_amount',
                          'concessions_waivers','due_date','status','data_source',
                          'last_sync','reminder_history','metadata_info']::text[]) AS n(name)
        LEFT JOIN information_schema.columns c
               ON c.table_schema='public'
              AND c.table_name='school_fee_records'
              AND c.column_name = n.name
        WHERE c.column_name IS NULL;
        IF missing_count > 0 THEN
            RAISE EXCEPTION 'school_fee_records exists but is missing required columns (structural drift; manual reconciliation required)';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.school_fee_records'::regclass
                         AND contype = 'u'
                         AND conname = 'uq_school_student_fee') THEN
            RAISE EXCEPTION 'school_fee_records exists without uq_school_student_fee (structural drift)';
        END IF;
    END IF;

    -- 5. telephony_number_health ----------------------------------------
    IF to_regclass('public.telephony_number_health') IS NULL THEN
        CREATE TABLE telephony_number_health (
            id SERIAL PRIMARY KEY,
            phone_number VARCHAR(32) NOT NULL,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            state VARCHAR(32) NOT NULL DEFAULT 'active',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            active_calls INTEGER NOT NULL DEFAULT 0,
            total_calls INTEGER NOT NULL DEFAULT 0,
            successful_calls INTEGER NOT NULL DEFAULT 0,
            failed_calls INTEGER NOT NULL DEFAULT 0,
            last_failure_reason VARCHAR(255) NULL,
            last_failure_code VARCHAR(64) NULL,
            cooldown_until TIMESTAMPTZ NULL,
            quarantine_reason VARCHAR(255) NULL,
            quarantined_at TIMESTAMPTZ NULL,
            carrier VARCHAR(64) NOT NULL DEFAULT 'voicelink',
            dlt_header_id VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT uq_org_phone_health UNIQUE (organization_id, phone_number)
        );
        CREATE INDEX ix_telephony_number_health_id
            ON telephony_number_health (id);
        CREATE INDEX idx_number_health_org_phone
            ON telephony_number_health (organization_id, phone_number);
    ELSE
        SELECT count(*) INTO missing_count
        FROM unnest(ARRAY['id','phone_number','organization_id','state',
                          'consecutive_failures','active_calls','total_calls',
                          'successful_calls','failed_calls','last_failure_reason',
                          'last_failure_code','cooldown_until','quarantine_reason',
                          'quarantined_at','carrier','dlt_header_id','created_at',
                          'updated_at']::text[]) AS n(name)
        LEFT JOIN information_schema.columns c
               ON c.table_schema='public'
              AND c.table_name='telephony_number_health'
              AND c.column_name = n.name
        WHERE c.column_name IS NULL;
        IF missing_count > 0 THEN
            RAISE EXCEPTION 'telephony_number_health exists but is missing required columns (structural drift; manual reconciliation required)';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conrelid = 'public.telephony_number_health'::regclass
                         AND contype = 'u'
                         AND conname = 'uq_org_phone_health') THEN
            RAISE EXCEPTION 'telephony_number_health exists without uq_org_phone_health (structural drift)';
        END IF;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    raise RuntimeError(
        "gate3a-r d4e2f1a9b8c7: downgrade is intentionally fail-closed. "
        "These five tables may contain financial, DNC-compliance, industry "
        "and number-health records and are never dropped automatically. "
        "Rollback strategy: restore the database from a verified backup taken "
        "before this migration was applied."
    )

