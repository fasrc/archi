-- Migration: Add deployment_record and ingest_run provenance tables
-- Change: add-ssb-deployment-provenance
--
-- Run this script against existing deployments to update the schema.
-- New deployments using init.sql already have both tables.
--
-- Existing deployments need this because redeploys preserve their data volumes,
-- so their database never re-runs init.sql.
--
-- Idempotent (IF NOT EXISTS) so this migration is safe to re-run.

-- One row per deploy, append-only; readers take the newest row by deployed_at.
CREATE TABLE IF NOT EXISTS deployment_record (
    id SERIAL PRIMARY KEY,
    config_ref VARCHAR(200),
    config_sha VARCHAR(64),
    config_head VARCHAR(64),
    pin_matched BOOLEAN,
    dirty_paths TEXT,
    app_version VARCHAR(100),
    deployed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deployment_record_time
    ON deployment_record (deployed_at DESC);

-- One row per ingest run, append-only. config_snapshot holds the
-- ingest-affecting flags that governed THAT run.
CREATE TABLE IF NOT EXISTS ingest_run (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    documents_embedded INTEGER,
    documents_failed INTEGER,
    documents_pending INTEGER,
    chunk_count INTEGER,
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT valid_ingest_run_status
        CHECK (status IN ('running', 'updated', 'up_to_date', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_ingest_run_completed
    ON ingest_run (completed_at DESC);

-- Existing deployments carry no history: the first row of each table appears
-- after the next deploy and the next ingest run respectively. The status board
-- renders an explicit "unavailable" state until then.
