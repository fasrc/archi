## Why

The status board compares the live ingest config against the snapshot of the newest successful ingest run, and an `up_to_date` run also records a snapshot. So the board hides real config drift after one ingest that embeds nothing (issue #697, from the Codex P1 finding on PR #695).

On `origin/dev` at `c2e885ca`:

- `VectorStoreManager.update_vectorstore()` (`src/data_manager/vectorstore/manager.py:311-325`) records an `ingest_run` row with `build_ingest_config_snapshot()` of the running config after every sync, also for a sync that returns `up_to_date`.
- `_sync_vectorstore()` returns `up_to_date` when the document hash sets match and no hash is stale (`manager.py:357-359`). A change of `chunk_size`, `chunking.strategy`, or `embedding_dimensions` does not cause a re-embed.
- `_SQL_LATEST_INGEST_RUN` (`src/interfaces/chat_app/status_provenance.py:64-71`) selects the newest completed run with status `updated` or `up_to_date`. `build_knowledge_base_panel()` (`status_provenance.py:153-203`) shows that run's snapshot as the config "as used at ingest" and passes it to `compare_ingest_config()`.

Repro: ingest under config X (`updated`). Change `chunk_size`, redeploy, and let one scheduled ingest run with no document change (`up_to_date`, snapshot Y). The board shows Y as the config at ingest and reports no drift, but every vector was built under X.

## What Changes

- When the newest successful run has status `up_to_date`, `load_status_provenance()` reads one more row: the newest completed run with status `updated`. That run is the **corpus run**: the run whose config built the corpus.
- `build_knowledge_base_panel()` takes the corpus run as a new keyword argument. The "Last ingest" times, the status, and the counts still come from the newest successful run (of either status). The config table and the drift comparison come from the corpus run's snapshot.
- When the newest successful run is `updated`, it is its own corpus run. No new query runs, and the panel does not change.
- When the newest successful run is `up_to_date` and no `updated` run is recorded, the config at ingest is unknown. The panel shows no config table and no drift, and sets `corpus_config_available` to `False`. The same happens when the corpus read raises. The template renders a neutral "Configuration at ingest unknown … drift not evaluated" line. This is the same rule as an unreadable current config: unknown is not "no drift".
- The template names the corpus run's completion time under the config table when the corpus run is older than the last ingest, and the drift title becomes "configuration changed after the corpus was built" (the old "after this ingest" is false when the last ingest is an `up_to_date` run under the current config).
- `docs/docs/services.md` explains which run the config table and the drift check use.

Out of scope: a partial `updated` run (a run that re-embeds only some documents, or only removes documents) records the new config, so the board still treats the whole corpus as built under it. The `ingest_run` row has no per-run re-embed count to detect this. A `failed` run that embedded some documents before a later successful run is the same family. Both are tracked in #703.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-status-board`: the config table and the drift check use the newest `updated` run; an `up_to_date` run alone gives no config baseline.

The `deployment-status-board` capability lives only in the unarchived changes `add-ssb-deployment-provenance` and `fix-issue-565-status-provenance-honesty`, so this change's delta uses `ADDED` requirements, not `MODIFIED`.

## Impact

- Code: `src/interfaces/chat_app/status_provenance.py`, `src/interfaces/chat_app/templates/status.html`.
- Tests: `tests/unit/test_status_provenance.py`, `tests/unit/test_status_template_render.py`.
- Docs: the Knowledge Base panel section of `docs/docs/services.md`.
- No schema change, no change to the ingest path, no change to `app.py` or the deploy. Existing `ingest_run` rows are read as they are.
