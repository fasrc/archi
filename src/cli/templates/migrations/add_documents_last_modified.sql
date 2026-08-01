-- Migration: Add last_modified column to documents table
-- Issue: https://github.com/fasrc/archi/issues/155
--
-- Run this script against existing deployments to update the schema.
-- New deployments using init.sql already have the correct column.
--
-- Idempotent (IF NOT EXISTS) so this migration is safe to re-run.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS last_modified TIMESTAMPTZ;
