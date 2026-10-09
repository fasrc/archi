## Context

Issue #565 is a cold-start work order with six findings (F1–F6). This design records each decision so the implementer does not need to choose. Where this design and the issue body differ, this design wins. Line numbers are on `origin/dev` at `0189e2dd`.

Two rules from the original change stay in force:

- Nothing in `status_provenance.py`, `ingest_run.py`, or `ingest_provenance.py` raises. Each new path must keep that.
- Design note D7 of `add-ssb-deployment-provenance`: all branching lives in the view model. The template only reads fields. (`tests/unit/test_status_template_render.py` does render the template, so template changes get render tests.)

## Decisions

### D1 — F1: per-model default dimensions

Add a module constant in `src/utils/ingest_provenance.py`:

```python
_DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL = {
    "all-MiniLM-L6-v2": 384,
    "OpenAIEmbeddings": 1536,
    "HuggingFaceEmbeddings": 384,
}
```

Keep `_DEFAULT_EMBEDDING_DIMENSIONS = 384` as the tail fallback. The snapshot value is `embedding_entry.get("dimensions", _DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL.get(embedding_model, _DEFAULT_EMBEDDING_DIMENSIONS))`. Add a comment that names `manager.py`'s `default_dimensions` table as the origin.

The manager's table is a local variable inside `__init__` (`manager.py:156-160`), not a module constant, so a test cannot import it. The parity test reads `src/data_manager/vectorstore/manager.py` as text, parses it with `ast`, finds the `Assign` whose target is the name `default_dimensions`, evaluates its value with `ast.literal_eval`, and asserts it equals `_DEFAULT_EMBEDDING_DIMENSIONS_BY_MODEL`. The test must fail with a clear message if it finds no such assignment. Do not import the data manager in the test.

### D2 — F3: collection-scoped chunk count

Change `collect_ingest_counts(conn)` to `collect_ingest_counts(conn, collection_name: Optional[str] = None)`.

- With a `collection_name`, run `SELECT COUNT(*) FROM document_chunks WHERE metadata->>'collection' = %s OR metadata->>'collection' IS NULL` with `(collection_name,)`. This is the same predicate as `PostgresVectorStore.count()` (`src/data_manager/vectorstore/postgres_vectorstore.py`; grep `metadata->>'collection'`).
- With `None`, run an unscoped count (no `WHERE`, no params), so a caller with no collection name still works.
- Build both queries from one base string, `_SQL_CHUNK_COUNT_BASE = "SELECT COUNT(*) FROM document_chunks"`, plus an optional `WHERE` suffix. The one-line literal `"SELECT COUNT(*) FROM document_chunks"` followed directly by its closing quote must not stay in `ingest_run.py`: issue #565's grep `grep -n 'SELECT COUNT(\*) FROM document_chunks"' src/utils/ingest_run.py` must print nothing. Name the base constant so that the closing quote does not follow `document_chunks` directly on one line (for example, put the base in a parenthesised two-part string or end it with a trailing space).
- In `manager.py` `_record_ingest_run`, pass `collection_name=getattr(self, "collection_name", None)`. Use `getattr` because existing tests build the manager with `__new__` and can lack the attribute.

### D3 — F2: corpus run vs failed attempt

- Change `_SQL_LATEST_INGEST_RUN` to `WHERE completed_at IS NOT NULL AND status IN ('updated', 'up_to_date')`. Fix the comment above it to say what the SQL now does.
- Add `_SQL_LATEST_FAILED_INGEST_RUN`: `SELECT completed_at FROM ingest_run WHERE status = 'failed' AND completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1`.
- In `load_status_provenance`, run the failed query **last**, after the config query. Order: deployment, corpus run, current config, failed attempt. Reason: existing test fakes return results in call order, and the F4 tests raise on the third `execute` (the config query). A raise in the failed query must not discard the config read.
- `build_knowledge_base_panel(run, current_snapshot, last_failed_at=None)` returns a new key `last_attempt_failed_at`. Its value is `last_failed_at` when there is no corpus run, or when `last_failed_at` is newer than the corpus run's `completed_at`. Otherwise it is `None`. Put this comparison in a small pure helper. If the comparison raises (for example, naive vs aware datetimes), return `last_failed_at` — showing a failure is the safe side.
- The key is present in both the available and the unavailable dict.
- Template: render a `prov-warn` line "⚠ the most recent ingest attempt failed at <time> — counts shown are from the last successful run; the failed attempt may have changed the live corpus" inside the available block when the field is set. In the unavailable branch, when the field is set, render "The most recent ingest attempt failed at <time>; no successful run is recorded." below the unavailable text. Use the same `strftime('%Y-%m-%d %H:%M UTC')` form as the Deployed row.

`running` rows (from other open work) are excluded by the status filter, which is correct: a running ingest does not describe the serving corpus.

### D4 — F4: unavailable current config

- In `load_status_provenance`, initialise `current_snapshot: Optional[Dict[str, Any]] = None`. Set it only when the `static_config` row exists.
- `build_knowledge_base_panel` accepts `current_snapshot: Optional[Mapping]`. When it is `None`: `drift` is `[]` and `current_config_available` is `False`. Otherwise `drift` is `compare_ingest_config(current_snapshot, snapshot)` as today and `current_config_available` is `True`.
- The unavailable dict carries `current_config_available: current_snapshot is not None`.
- Template: inside the available block, when `not kb.current_config_available`, render "Current configuration unavailable — drift not evaluated." in a `prov-unavailable` div. The drift warning is unchanged.
- Update every existing caller and test of `build_knowledge_base_panel` that passes `{}` only if its assertion depends on the old behavior. A `{}` mapping still means "a config exists with no keys", which is not the unavailable case.

### D5 — F5: pin mismatch

- `build_deployment_panel` adds `pin_mismatch`: `False` in the unavailable branch, `row.get("pin_matched") is False` in the available branch.
- Template line 308 becomes `{% if dep.pin_mismatch %}`.
- Keep the `pin_matched` key as is; other code can read it.

### D6 — F6: docs

Add a subsection "Deployment and Knowledge Base provenance" under "Service Status Board & Alert Banners" in `docs/docs/services.md`, after the list at lines 64-68. Describe each row of both panels, the "live-edited config" warning, the "configuration changed after this ingest" warning, and the unknown states: no verdict recorded (F5), current configuration unavailable (F4), and a newer failed attempt (F2). Describe only fields the view model produces; grep each one in `status_provenance.py`. Also add one bullet to the list at lines 64-68 that points to the new subsection.

## Risks

- **Old rows keep wrong values.** A row recorded before this fix keeps 384 or an unscoped count. Accepted: the board shows what was recorded; the next ingest records the right value. The docs say so.
- **A `failed` row from a partial sync** (`manager.py:385-392`) hides behind a successful row that is older. That is the point of D3: the warning line shows it.
