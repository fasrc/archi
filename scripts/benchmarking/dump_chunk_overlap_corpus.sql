-- Corpus dump for scripts/benchmarking/measure_chunk_overlap.py.
--
-- Emits one JSON record per loader document — {"text", "metadata", "path",
-- "children"} — reconstructed from the parents the ingest stored:
--   * only parents referenced by a chunk retrieval can return are used: the
--     target collection, no soft-deleted document (document_parent_nodes keeps
--     the parents of every past ingest run, and a database can hold more than
--     one collection);
--   * parents that share a document and metadata belong to one loader document
--     (a PDF page, a file); their text is re-joined in parent_index order;
--   * metadata is projected down to what the loader attached, because the
--     splitter subtracts its token length from every budget: TextLoader /
--     PythonLoader / NotebookLoader attach `source`; PyPDFLoader adds the PDF
--     keys; BSHTMLLoader adds `title` (loaders: src/data_manager/vectorstore/
--     loader_utils.py; the suffix is stored as ".html" by LocalFileResource and
--     as "html" by the web collector, so it is normalized first). Measured
--     2026-09-02 on the claw KB: with this projection the re-chunk reproduced
--     593/593 sampled children byte for byte; with the stored (post-parse)
--     metadata it produced a third more chunks;
--   * the children that reference each parent ride along, in order, so the
--     script can report how many the re-chunk reproduces, and `path` (the file
--     under the data manager's data directory) lets `--data-root` re-read the
--     original loader document instead of the reconstruction.
--
-- Usage (the collection is the one the data manager logs at startup):
--   docker exec -i postgres-dev psql -U archi -d archi-db -t -A \
--     -v collection=default_collection_with_HuggingFaceEmbeddings \
--     < scripts/benchmarking/dump_chunk_overlap_corpus.sql > corpus.jsonl
WITH live AS (
  -- the chunks retrieval can return: one collection (NULL = legacy rows, as in
  -- VectorStoreManager._remove_from_postgres) and no soft-deleted document (as
  -- in PostgresVectorStore's search filters)
  SELECT (c.metadata->>'parent_id')::int AS parent_id,
         jsonb_agg(c.chunk_text ORDER BY c.chunk_index) AS children
  FROM document_chunks c
  LEFT JOIN documents d ON d.id = c.document_id
  WHERE c.metadata ? 'parent_id'
    AND (c.metadata->>'collection' = :'collection' OR c.metadata->>'collection' IS NULL)
    AND (d.id IS NULL OR d.is_deleted = FALSE)
  GROUP BY 1
), parents AS (
  SELECT p.document_id,
         p.parent_index,
         p.parent_text,
         live.children,
         p.metadata - 'parent_index' AS loader_doc,
         (SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb)
            FROM jsonb_each(p.metadata)
           WHERE key = 'source'
              OR (p.metadata ? 'page' AND key IN (
                    'producer', 'creator', 'creationdate', 'moddate', 'total_pages',
                    'page', 'page_label', 'title', 'author', 'subject', 'keywords',
                    'aapl:keywords'))
              OR (lower(ltrim(p.metadata->>'suffix', '.')) IN ('html', 'htm')
                  AND key = 'title')
         ) AS loader_metadata
  FROM document_parent_nodes p
  JOIN live ON live.parent_id = p.id
)
SELECT json_build_object(
  'text', string_agg(p.parent_text, E'\n\n' ORDER BY p.parent_index),
  'metadata', p.loader_metadata,
  'path', min(p.loader_doc->>'path'),
  'children', (SELECT jsonb_agg(c.child ORDER BY q.parent_index, c.ord)
                 FROM parents q,
                      jsonb_array_elements_text(q.children)
                        WITH ORDINALITY AS c(child, ord)
                WHERE q.document_id = p.document_id AND q.loader_doc = p.loader_doc)
)
FROM parents p
GROUP BY p.document_id, p.loader_doc, p.loader_metadata
ORDER BY p.document_id, min(p.parent_index);
