## Context

The Knowledge Base panel on `/ssb/status` is built by `src/interfaces/chat_app/status_provenance.py`. `load_status_provenance()` runs four reads in this order (`status_provenance.py:265-300` on `origin/dev` `c2e885ca`):

1. `_SQL_LATEST_DEPLOYMENT` → the deployment row.
2. `_SQL_LATEST_INGEST_RUN` → the newest completed run with status `updated` or `up_to_date`.
3. `_SQL_CURRENT_DATA_MANAGER_CONFIG` via `_read_current_snapshot()` → the live snapshot, or `None`.
4. `_SQL_LATEST_FAILED_INGEST_RUN` → the newest failed attempt's time.

`build_knowledge_base_panel(run, current_snapshot, last_failed_at=None)` then shows the run's times and counts, uses the run's `config_snapshot` as the config table, and passes it to `compare_ingest_config()` for drift.

An `up_to_date` run embeds nothing but records the config that runs now (`manager.py:311-325`). So after a config change and one `up_to_date` run, the table and the drift check describe a config that built no vector.

## Goals / Non-Goals

**Goals:**
- After `updated` (config X) then `up_to_date` (config Y), the panel shows X as the config at ingest and reports drift for every key that differs between Y-now and X.
- "Last ingest" still shows the newest successful run of either status.
- An `up_to_date` run with no recorded `updated` run is reported as "config at ingest unknown", not as "no drift".

**Non-Goals:**
- A partial `updated` run (re-embeds some documents, or only removes documents) still counts as the corpus run. The row has no per-run re-embed count to detect it. Tracked in #703.
- A `failed` run that embedded some documents before it failed is not considered. The failed-attempt warning does NOT cover this case once a later successful run exists: `_newer_failed_at()` (`status_provenance.py:206-219`) hides a failed run that is older than the newest successful run. So for `updated` (X) → `failed` (Y, some documents embedded) → `up_to_date` (Y), the board shows X and no failed-attempt warning, although part of the corpus is under Y. This is the same "part of the corpus is under a newer config" family as #703 and is tracked there.
- No change to the ingest path, to what `ingest_run` records, or to the schema.

## Decisions

### D1: Read-side selection (issue Option B), not write-side carry-forward (Option A)

The status query picks the corpus run; the ingest path keeps recording each run's own config.

Why not Option A (an `up_to_date` run copies the previous `updated` snapshot): rows already written on deployed hosts keep the wrong snapshot, so Option A does not fix a host that already hit the bug. Option A also puts a database read into the ingest path, which the issue does not need. Option B fixes existing rows at read time, and the change stays in `status_provenance.py`, which the unit suite covers directly.

### D2: One extra query, only when the newest successful run is `up_to_date`

Add a module constant:

```python
# The newest run that embedded documents. Its snapshot is the config that built
# the corpus; an up_to_date run embeds nothing, so its snapshot does not.
_SQL_LATEST_CORPUS_RUN = f"""
    SELECT {", ".join(_INGEST_RUN_COLUMNS)}
    FROM ingest_run
    WHERE completed_at IS NOT NULL AND status = 'updated'
    ORDER BY completed_at DESC
    LIMIT 1
"""
```

In `load_status_provenance()`, run it as the **fifth and last** read, inside the same `try`, only when `run_row` is not `None` and `run_row.get("status") == "up_to_date"`:

```python
corpus_row = None
...
            if run_row and run_row.get("status") == "up_to_date":
                corpus_row = _fetch_row(
                    cursor, _SQL_LATEST_CORPUS_RUN, _INGEST_RUN_COLUMNS
                )
```

Why last: the existing tests feed the fake cursor its results by position. A new read at the end leaves every existing position unchanged. Also, if this read raises, the outer `except` logs it and the four earlier values are already set; there is no later read for an aborted transaction to break, so no rollback is needed.

Why only for `up_to_date`: an `updated` run is its own corpus run (D3), so the query would add nothing. Existing tests use `status: "updated"` rows, so their query sequence does not change.

No upper bound on `completed_at` is needed: the newest successful run is `up_to_date`, so every `updated` run is older than it.

Pass the result to the panel: `build_knowledge_base_panel(run_row, current_snapshot, last_failed_at=last_failed_at, corpus_run=corpus_row)`.

### D3: Panel rule

New signature: `build_knowledge_base_panel(run, current_snapshot, last_failed_at=None, corpus_run=None)`.

Add a small helper, `_corpus_run_for(run, corpus_run)`, called only with a non-`None` `run` (the panel returns early for `None`, so the helper has no `None` arm — an arm there would be dead, uncovered code):
- `run.get("status") == "up_to_date"` → `corpus_run` (can be `None`).
- any other status → `run` itself.

In `build_knowledge_base_panel()`, for a non-`None` `run`:
- Times, duration, status, counts, and `last_attempt_failed_at` still come from `run` (unchanged).
- `corpus = _corpus_run_for(run, corpus_run)`.
- If `corpus` is `None`: `config = {}`, `drift = []`, `corpus_config_available = False`, `corpus_built_at = None`. `current_config_available` still says whether the live config was read.
- Else: `config = _as_flag_mapping(corpus.get("config_snapshot"))`; drift is computed against `config` exactly as now (empty and `current_config_available = False` when `current_snapshot` is `None`); `corpus_config_available = True`; `corpus_built_at = corpus.get("completed_at")`.

For `run is None` (the unavailable panel), add `corpus_config_available: False` and `corpus_built_at: None`.

The docstring's line "The flag values come from the run's own snapshot" changes to say they come from the corpus run's snapshot, and why.

### D4: Template

In `src/interfaces/chat_app/templates/status.html`, inside `{% if kb and kb.available %}`:

- Directly under the "Ingest configuration (as used at ingest)" sub-title, when `kb.corpus_built_at` is set and differs from `kb.completed_at`, render:
  `<div class="prov-label">From the last run that embedded documents, completed {{ kb.corpus_built_at.strftime('%Y-%m-%d %H:%M UTC') }}.</div>`
- Next to the existing "Current configuration unavailable" line, when `not kb.corpus_config_available`, render:
  `<div class="prov-unavailable">Configuration at ingest unknown — no run that embedded documents could be read; drift not evaluated.</div>`
  The wording is neutral on purpose: `corpus_row` is also `None` when the corpus read raised, or when an earlier read raised and the corpus read never ran. The text must not claim that no `updated` row exists.
- Change the drift-box title from "⚠ configuration changed after this ingest" to "⚠ configuration changed after the corpus was built". The old title is false in the exact case this change fixes: "this ingest" reads as the Last ingest row, which is the `up_to_date` run that ran under the current config. The body sentence ("The corpus above was not built with the configuration running now.") stays. Two existing assertions in `tests/unit/test_status_template_render.py` (lines 116 and 230) name the old title; they change to the new title and are the only existing-test edits this change makes.

### D5: Docs

In `docs/docs/services.md`, Knowledge Base panel section (lines 86-99 on `c2e885ca`):
- Line 86, the section lead: say that the times, status and counts come from the newest successful run (`updated` or `up_to_date`), and the configuration and the drift check come from the newest `updated` run.
- Line 91, the **Ingest configuration (as used at ingest)** bullet: add one sentence: the settings come from the newest `updated` run, because an `up_to_date` run embeds nothing; if that run is older than the last ingest, its completion time is shown. In the same bullet, change "still does not show as drift until the next ingest" to "still does not show as drift until the next ingest that embeds documents (an `updated` run)".
- Line 92: rename the bullet to **⚠ configuration changed after the corpus was built** and say the running config is compared with the config of the last run that embedded documents.
- Line 97, the failed-attempt bullet: "The counts and config are from the last successful run" becomes "The counts are from the last successful run and the config is from the last run that embedded documents".
- Under "Unknown and failure states", add a bullet for "Configuration at ingest unknown — no run that embedded documents could be read; drift not evaluated." Say that it shows when every recorded successful run is `up_to_date`, or when that run could not be read, and that it does not mean there is no drift.
- Add one sentence that a run which embeds only some documents (an `updated` run that re-embeds a few documents, or a `failed` run) does not make the board treat the rest of the corpus as rebuilt; this case is not detected yet (#703).

## Risks / Trade-offs

- [A host whose `ingest_run` history starts with `up_to_date` rows (the corpus predates the record)] → the panel shows "configuration at ingest unknown" until the next `updated` run. That is honest: the config that built those vectors was never recorded.
- [Partial `updated` runs] → still treated as the corpus run. Out of scope, #703.
