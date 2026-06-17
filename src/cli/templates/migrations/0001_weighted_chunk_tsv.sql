-- Migration 0001: weighted title/filename full-text index for document_chunks
-- ---------------------------------------------------------------------------
-- Converts EXISTING deployments to the title-aware full-text representation
-- that fresh deployments already get from init.sql. init.sql only runs on a
-- fresh database volume, so existing corpora keep the old unweighted
-- `to_tsvector('english', chunk_text)` column until this migration is applied.
--
-- What it does:
--   * pg_textsearch present  -> (re)build `chunk_search_text` (title/filename
--     surfaced and repeated) + the `idx_chunks_bm25` BM25 index.
--   * pg_textsearch absent    -> (re)build the weighted `chunk_tsv` tsvector
--     (display_name/filename = 'A', chunk_text = 'B') + the `idx_chunks_fts`
--     GIN index.
--
-- The generated columns are derived from existing `metadata`/`chunk_text`, so
-- this re-ranks the current corpus immediately, WITHOUT re-embedding.
--
-- Idempotent: a stale (unweighted) generated column is detected via its
-- generation expression and dropped so the weighted definition is recreated;
-- when the column is already weighted it is left untouched. Safe to re-run.
-- Requires: PostgreSQL 17+ (matches init.sql). Run during a maintenance window
-- on large tables: dropping/recreating a STORED generated column rewrites it.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_textsearch') THEN
        -- BM25 has no per-field setweight; title/filename are surfaced into the
        -- indexed text and repeated so they carry a higher term frequency than
        -- body-only mentions. Drop the column only if its definition predates
        -- this (no display_name surfaced), then recreate the weighted column.
        IF EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = 'document_chunks'::regclass
              AND a.attname = 'chunk_search_text'
              AND pg_get_expr(d.adbin, d.adrelid) NOT ILIKE '%display_name%'
        ) THEN
            -- Dropping the column also drops idx_chunks_bm25 that depends on it.
            ALTER TABLE document_chunks DROP COLUMN chunk_search_text;
        END IF;
        EXECUTE 'ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_search_text TEXT
            GENERATED ALWAYS AS (
                coalesce(metadata->>''display_name'', '''') || '' '' ||
                coalesce(metadata->>''filename'', '''') || '' '' ||
                coalesce(metadata->>''display_name'', '''') || '' '' ||
                coalesce(metadata->>''filename'', '''') || '' '' ||
                chunk_text
            ) STORED';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_bm25 ON document_chunks
            USING bm25(chunk_search_text) WITH (text_config=''english'')';
        RAISE NOTICE 'Migration 0001: weighted BM25 index ensured on document_chunks';
    ELSE
        -- Fallback: weighted tsvector. display_name/filename get weight 'A' so
        -- title/filename matches outrank body-only matches (weight 'B'). Drop
        -- the column only if its definition predates this (no setweight), then
        -- recreate the weighted column.
        IF EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = 'document_chunks'::regclass
              AND a.attname = 'chunk_tsv'
              AND pg_get_expr(d.adbin, d.adrelid) NOT ILIKE '%setweight%'
        ) THEN
            -- Dropping the column also drops idx_chunks_fts that depends on it.
            ALTER TABLE document_chunks DROP COLUMN chunk_tsv;
        END IF;
        EXECUTE 'ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_tsv tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector(''english'', coalesce(metadata->>''display_name'', '''')), ''A'') ||
                setweight(to_tsvector(''english'', coalesce(metadata->>''filename'', '''')), ''A'') ||
                setweight(to_tsvector(''english'', chunk_text), ''B'')
            ) STORED';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING gin(chunk_tsv)';
        RAISE NOTICE 'Migration 0001: weighted GIN tsvector index ensured on document_chunks';
    END IF;
END $$;
