## Why

The `/ssb/status` Deployment and Knowledge Base panels (added by PR #542) can show values that the ingest run and the deployment did not use. Codex reported six defects on PR #542 before the merge (comment ids 4086343297, 4086343269, 4086343276, 4086343308, 4086343317, 4086343288). No one answered or fixed them. Issue #565 is the work order. All six still hold on `origin/dev` at `0189e2dd`:

- **F1 (P1):** `src/utils/ingest_provenance.py:45` records 384 dimensions for every embedder with no `dimensions` key. The ingest path (`src/data_manager/vectorstore/manager.py:156-164`) uses 1536 for `OpenAIEmbeddings`, the rendered default. The board shows the wrong value, and drift on that key never fires.
- **F2 (P2):** `src/interfaces/chat_app/status_provenance.py:65-71` selects the newest row with `completed_at IS NOT NULL`. `failed` rows also get `completed_at = NOW()` (`src/utils/ingest_run.py:31-37`), so a failed attempt is shown as the run that produced the serving corpus.
- **F3 (P2):** `src/utils/ingest_run.py:46` counts every row in `document_chunks`. `PostgresVectorStore.count()` scopes to the active collection, so the recorded count includes rows of an old collection.
- **F4 (P2):** `status_provenance.py:212` uses `{}` for "current config unavailable". `compare_ingest_config({}, snapshot)` then reports drift on every key with `None now`.
- **F5 (P2):** `src/interfaces/chat_app/templates/status.html:308` renders "The deployed config is not the pinned commit." when `pin_matched` is `None` (no verdict recorded).
- **F6 (P2):** `docs/docs/services.md:64-68` does not describe the two panels or their warnings.

## What Changes

- Restate the manager's per-model default dimension table in `build_ingest_config_snapshot`, with a test that compares it to the manager's table.
- Scope the recorded chunk count to the active collection with the same predicate as `PostgresVectorStore.count()`.
- Select the corpus run only from rows with status `updated` or `up_to_date`. Expose a newer failed attempt as a separate view-model field, `last_attempt_failed_at`, and render it.
- Use `None` as the sentinel for "current config unavailable". Report no drift and `current_config_available: False` in that case, and render that state.
- Add `pin_mismatch` (true only when `pin_matched is False`) to the Deployment panel and use it in the template.
- Document both panels, both warnings, and the unknown states in `docs/docs/services.md`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-status-board`: corpus-run selection, failed-attempt visibility, unavailable current config, pin-mismatch wording, docs.
- `ingest-run-provenance`: recorded embedding dimensions, collection-scoped chunk count.

Both capabilities live only in the unarchived change `add-ssb-deployment-provenance`, so this change's deltas use `ADDED` requirements, not `MODIFIED`.

## Impact

- Code: `src/utils/ingest_provenance.py`, `src/utils/ingest_run.py`, `src/data_manager/vectorstore/manager.py` (one call site), `src/interfaces/chat_app/status_provenance.py`, `src/interfaces/chat_app/templates/status.html`.
- Tests: `tests/unit/test_ingest_provenance.py`, `tests/unit/test_ingest_run.py`, `tests/unit/test_status_provenance.py`, `tests/unit/test_status_template_render.py`.
- Docs: `docs/docs/services.md`.
- No schema change. No change to `app.py`. No deploy change. Existing `ingest_run` rows keep their recorded values; the fixes apply to new runs and to every page render.
