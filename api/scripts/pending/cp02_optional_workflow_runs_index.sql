-- CP-02 PREPARED, NOT APPLIED. Requires separate owner approval and a staging rehearsal.
--
-- Purpose: speed up the organization-scoped, newest-first conversation list at volume.
-- The CP-02 code works WITHOUT this index (it is an optimisation, not a dependency); the
-- list/count/facet statements already join through workflows.organization_id and filter on
-- workflow_runs.workflow_id, which is indexed today (idx_workflow_runs_workflow_id).
--
-- Apply (online, no table lock):
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_runs_workflow_created
    ON workflow_runs (workflow_id, created_at DESC, id DESC);

-- Roll back:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_workflow_runs_workflow_created;
--
-- Not included (needs its own design review): a trigram/FTS index for transcript search. CP-02
-- searches a bounded, lower-cased annotations.analysis.search_text value, which is a sequential
-- scan per request and is acceptable only at today's volume (hundreds of rows per organization).
