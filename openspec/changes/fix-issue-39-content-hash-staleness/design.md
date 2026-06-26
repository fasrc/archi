## Context

`VectorStoreManager.update_vectorstore()` (`src/data_manager/vectorstore/manager.py`)
syncs filesystem documents with the vectorstore. Resource hashes are identity-based
(`ScrapedResource.get_hash()` = `md5(url)`, `LocalFileResource.get_hash()` =
`md5(path)`), so they do not change when a document's *content* changes in place.

PR #38 added a filename-based staleness signal: `_collect_stale_hashes()` reads the
`filename` recorded on each chunk's metadata (`document_chunks.metadata->>'filename'`,
via `_collect_embedded_filenames()`) and marks a hash stale when the current on-disk
basename differs from what was embedded, or when a hash carries chunks under more than
one filename. Stale hashes are removed (`_remove_from_postgres`) then re-embedded
(`_add_to_postgres`). This catches the HTML→Markdown extension flip but, as its own
docstring states, misses a content-only change under an unchanged filename and hash.

The `documents` catalog table already has `size_bytes` and `file_modified_at` columns;
chunk metadata already carries `resource_hash` and `filename`.

## Goals / Non-Goals

**Goals:**
- Detect a content-only change under an unchanged filename and unchanged hash on
  re-ingest, and refresh that document's chunks via the existing remove-then-re-embed
  path.
- Reuse existing storage (chunk metadata and/or `documents` columns); no new dependency
  and, ideally, no schema migration.
- Preserve the no-change fast path: an unchanged corpus must still short-circuit to
  "up to date" with no per-document recomputation beyond what filename detection
  already does.

**Non-Goals:**
- Changing how resource hashes are computed (they stay identity-based).
- Detecting content changes for documents that are not in the vectorstore.
- Any live-deployment benchmarking (that is issue #32, separate).

## Decisions

### Decision 1 — Store the content signal in chunk metadata, mirroring `filename`
Persist a content signal on each chunk's metadata at embed time (e.g.
`metadata['content_hash']`), exactly as `filename` is stored today. Add a
`_collect_embedded_content_signals()` reader symmetric to
`_collect_embedded_filenames()` that maps `resource_hash -> set of embedded content
signals`.

- **Why:** keeps the "what was true at embed time" signal co-located with the chunks
  and queried the same way (`document_chunks.metadata->>...`), so detection logic stays
  inside `manager.py` and matches the existing pattern. No cross-table join, no
  dependency on the `documents` catalog being in sync with the vectorstore.
- **Alternative considered — reuse `documents.size_bytes` + `file_modified_at`:**
  cheaper (no re-hash) but weaker (size/mtime can coincide or be unreliable for
  re-scrapes that rewrite mtime), and it couples staleness to a different table than
  where chunks live. Kept as a fallback option if computing a content hash proves too
  costly during apply, but the content hash is the primary choice for correctness.

### Decision 2 — Content signal = hash of the persisted file bytes
Compute the signal from the same persisted file the loader reads (the on-disk
`file_path`), so the "current" value compared at re-ingest is derived identically to
the "embedded" value stored earlier. Compute it once per file in the embed path.

- **Why:** a hash of the persisted bytes is the most direct content-change signal and
  is symmetric across the store side and the compare side.
- **Cost control:** only computed for files being embedded (new + already-stale), and
  compared only for candidate hashes already in the vectorstore — never for the whole
  corpus on the no-change path.

### Decision 3 — Extend `_collect_stale_hashes`, don't fork the flow
Add the content-signal comparison inside the existing `_collect_stale_hashes()` so the
returned stale set already includes content-stale hashes; the downstream
remove-then-re-embed path in `update_vectorstore()` needs no change.

- **Why:** #38 already wires stale hashes through removal + re-embed. Reusing it means
  the only new behavior is "which hashes are stale", keeping the diff small.

### Decision 4 — Graceful fallback for chunks lacking the signal
If a candidate hash has no recorded content signal (chunks embedded before this
change), skip content comparison for it and rely on filename detection, without error.

- **Why:** backward compatibility with vectorstores populated before this change; never
  regress to a crash, and never force a full re-embed of the existing corpus.

## Risks / Trade-offs

- **Re-hashing cost on large corpora** → only hash files in the embed set and only
  compare candidates already in the vectorstore; the no-change fast path stays O(hash
  set) as today.
- **Signal absent on legacy chunks** → fall back to filename detection (Decision 4); a
  legacy doc's content change is picked up the next time it is otherwise re-embedded.
- **False "stale" if the signal is computed differently on store vs compare** →
  Decision 2 computes both sides from the persisted file bytes with the same function,
  eliminating the asymmetry.

## Migration Plan

No schema migration if the content hash lives in chunk metadata (JSONB, additive).
Existing chunks without the field degrade gracefully (Decision 4). Rollback is reverting
the code; the extra metadata key is inert to readers that ignore it.

## Open Questions

- None blocking. During apply, confirm the content-hash-in-metadata approach is
  cheaper-or-equal to reusing `size_bytes`/`file_modified_at`; if hashing proves too
  costly at ingest scale, fall back to the size+mtime signal (Decision 1 alternative)
  without changing the spec'd behavior.
