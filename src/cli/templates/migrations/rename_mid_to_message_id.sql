-- Migration: Rename 'mid' columns to 'message_id' for consistency
-- Issue: https://github.com/archi-physics/archi/issues/343
--
-- Run this script against existing deployments to update the schema.
-- New deployments using init.sql already have the correct column names.
--
-- Every guard below resolves the relation with to_regclass() rather than matching
-- a bare table_name in information_schema. Two reasons:
--
--   1. information_schema.columns matches that name in EVERY schema the role can
--      see, so a same-named table in another schema makes the predicate describe
--      the wrong relation. ALTER TABLE resolves through search_path, so the guard
--      and the statement it guards could disagree about which table they mean.
--   2. to_regclass() resolves exactly the way the following ALTER does, and returns
--      NULL rather than raising when the relation is absent — so a database that
--      never had the table skips the rename instead of failing the file.
--
-- This matches _SCHEMA_CHECK_SQL in catalog_postgres.py, which resolves the
-- documents table the same way.

-- feedback table: rename 'mid' -> 'message_id'
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('feedback')
      AND attname = 'mid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('feedback')
      AND attname = 'message_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    ALTER TABLE feedback RENAME COLUMN mid TO message_id;
  END IF;
END $$;

-- A rename is idempotent only when guarded on BOTH sides. `ALTER INDEX IF EXISTS
-- idx_feedback_mid RENAME TO idx_feedback_message_id` covers only "the source is
-- gone"; on a half-migrated or hand-repaired schema carrying both names it raises
-- `relation "idx_feedback_message_id" already exists`. The sidecar runs psql with
-- ON_ERROR_STOP=1 under `set -e`, so that one error aborts the rest of this file
-- and fails db-migrate — and config-seed and the data manager gate on its
-- successful completion, so the whole stack stops starting.
DO $$
BEGIN
  IF to_regclass('idx_feedback_mid') IS NOT NULL
     AND to_regclass('idx_feedback_message_id') IS NULL THEN
    ALTER INDEX idx_feedback_mid RENAME TO idx_feedback_message_id;
  END IF;
END $$;

-- timing table: rename 'mid' -> 'message_id'
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('timing')
      AND attname = 'mid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('timing')
      AND attname = 'message_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    ALTER TABLE timing RENAME COLUMN mid TO message_id;
  END IF;
END $$;

-- ab_comparisons table: rename the three *_mid columns
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'user_prompt_mid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'user_prompt_message_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    ALTER TABLE ab_comparisons RENAME COLUMN user_prompt_mid TO user_prompt_message_id;
  END IF;
END $$;
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'response_a_mid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'response_a_message_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    ALTER TABLE ab_comparisons RENAME COLUMN response_a_mid TO response_a_message_id;
  END IF;
END $$;
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'response_b_mid' AND attnum > 0 AND NOT attisdropped
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_attribute
    WHERE attrelid = to_regclass('ab_comparisons')
      AND attname = 'response_b_message_id' AND attnum > 0 AND NOT attisdropped
  ) THEN
    ALTER TABLE ab_comparisons RENAME COLUMN response_b_mid TO response_b_message_id;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- /v1 OpenAI-compatible API columns.
--
-- init.sql only runs on a fresh Postgres data directory, so existing volumes
-- never receive columns added there. The /v1 paths read/write these three:
--   users.api_token_hash / api_token_created_at   (token generation + bearer auth)
--   conversation_metadata.external_chat_id        (OpenWebUI chat-id mapping)
-- Without them, enabling the /v1 API after upgrade fails with undefined_column.
-- Idempotent (IF NOT EXISTS) so this migration is safe to re-run.
-- ---------------------------------------------------------------------------
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS api_token_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS api_token_created_at TIMESTAMPTZ;

ALTER TABLE conversation_metadata
    ADD COLUMN IF NOT EXISTS external_chat_id VARCHAR(200);

-- Bearer-token lookup is by hash, so a hash must map to at most one user.
-- UNIQUE (partial, NULL-tolerant) matches init.sql. Any pre-existing index of the
-- same name that is NOT that is dropped first, so the upgrade is deterministic.
--
-- The drop is guarded on the existing index's DEFINITION, not merely on its
-- existence. `DROP INDEX IF EXISTS` is re-runnable but it is not a no-op, and this
-- file is replayed on every startup: an unconditional drop-then-create rebuilds
-- the index on every boot — a scan and sort of `users` on the critical path, before
-- any application service is allowed to start. And because psql commits each
-- statement separately, the drop lands in its own transaction, leaving a window
-- with no uniqueness constraint at all; a duplicate hash arriving in it makes the
-- CREATE fail, which under ON_ERROR_STOP=1 keeps the whole stack down.
--
-- What counts as "already correct" is checked against the relation, not just the
-- name: the index must belong to THIS `users` (so a same-named index resolved from
-- another schema on the search_path cannot vouch for it), be unique and partial,
-- cover exactly the one column `api_token_hash`, and be both valid and ready — a
-- failed CREATE INDEX CONCURRENTLY leaves an index that exists, reports unique, and
-- enforces nothing, and skipping the drop for it would leave the uniqueness
-- invariant silently unenforced.
--
-- The predicate's TEXT is deliberately not compared. `pg_get_expr` output is
-- normalized by the server and its formatting is not contractual across versions,
-- so a cosmetic difference would drop and rebuild the index on every startup —
-- reinstating the exact defect this guard exists to remove. A unique partial index
-- on that single column is treated as the required one; the name is owned by
-- `init.sql`, so a hand-built index of that name with a different predicate is the
-- residual case this does not repair.
DO $$
BEGIN
  IF to_regclass('idx_users_api_token') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1 FROM pg_index i
       WHERE i.indexrelid = to_regclass('idx_users_api_token')
         AND i.indrelid = to_regclass('users')
         AND i.indisunique
         AND i.indisvalid
         AND i.indisready
         AND i.indpred IS NOT NULL
         AND i.indnatts = 1
         AND (
           SELECT a.attname FROM pg_attribute a
           WHERE a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
         ) = 'api_token_hash'
     ) THEN
    DROP INDEX idx_users_api_token;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_token
    ON users(api_token_hash) WHERE api_token_hash IS NOT NULL;

-- Prevent cross-user collision on X-OpenWebUI-Chat-Id (matches init.sql).
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_meta_external_chat
    ON conversation_metadata(user_id, external_chat_id) WHERE external_chat_id IS NOT NULL;

-- Anonymous chats (user_id IS NULL) need continuity keyed on external_chat_id
-- alone, since NULL user_id never matches the composite index above.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_meta_external_chat_anon
    ON conversation_metadata(external_chat_id) WHERE user_id IS NULL AND external_chat_id IS NOT NULL;
