-- Migration: Rename 'mid' columns to 'message_id' for consistency
-- Issue: https://github.com/archi-physics/archi/issues/343
--
-- Run this script against existing deployments to update the schema.
-- New deployments using init.sql already have the correct column names.

-- feedback table: rename 'mid' -> 'message_id'
ALTER TABLE feedback RENAME COLUMN mid TO message_id;
ALTER INDEX IF EXISTS idx_feedback_mid RENAME TO idx_feedback_message_id;

-- timing table: rename 'mid' -> 'message_id'
ALTER TABLE timing RENAME COLUMN mid TO message_id;

-- ab_comparisons table: rename the three *_mid columns
ALTER TABLE ab_comparisons RENAME COLUMN user_prompt_mid TO user_prompt_message_id;
ALTER TABLE ab_comparisons RENAME COLUMN response_a_mid TO response_a_message_id;
ALTER TABLE ab_comparisons RENAME COLUMN response_b_mid TO response_b_message_id;

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
-- UNIQUE (partial, NULL-tolerant) matches init.sql. Drop any pre-existing
-- non-unique index of the same name first so the upgrade is deterministic.
DROP INDEX IF EXISTS idx_users_api_token;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_token
    ON users(api_token_hash) WHERE api_token_hash IS NOT NULL;

-- Prevent cross-user collision on X-OpenWebUI-Chat-Id (matches init.sql).
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_meta_external_chat
    ON conversation_metadata(user_id, external_chat_id) WHERE external_chat_id IS NOT NULL;

-- Anonymous chats (user_id IS NULL) need continuity keyed on external_chat_id
-- alone, since NULL user_id never matches the composite index above.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_meta_external_chat_anon
    ON conversation_metadata(external_chat_id) WHERE user_id IS NULL AND external_chat_id IS NOT NULL;
