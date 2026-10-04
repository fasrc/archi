## Why

`data_manager.sources.<name>.visible: false` hides a source's documents from chat citations
only for `git`, `sso`, and `local_files`. `ChatWrapper._get_doc_visibility`
(`src/interfaces/chat_app/app.py:813-828`) looks up the stored `source_type` directly in
`sources_config`. For `links`, `indico`, and `elog` the stored value is `web`; for `jira`
and `redmine` it is `ticket`. Neither is a config key, so the lookup misses, logs an ERROR,
and shows the citation (fasrc/archi#459).

The operator decided the approach on 2026-09-26: map at lookup time, no migration.

## What Changes

- New helper module `src/interfaces/chat_app/source_visibility.py` with two pure functions:
  - `visibility_config_key(metadata) -> str | None` maps document metadata to the config key:
    `web` + `scraper == "indico"` → `indico`; `web` + `scraper == "elog"` → `elog`; other
    `web` → `links`; `ticket` + `ticket_provider == "jira"` → `jira`; `ticket` +
    `ticket_provider == "redmine"` → `redmine`; `git`, `sso`, `local_files` → the same name;
    anything else (absent `source_type`, uploaded files, `ticket` with no known provider) →
    `None`.
  - `is_visible(metadata, sources_config) -> bool` applies the key: `None` → visible (DEBUG
    log only); a key missing from `sources_config` → visible with one WARNING; else the
    entry's `visible` value (default `True`).
- `ChatWrapper._get_doc_visibility` becomes a thin call into `is_visible`. `get_top_sources`
  keeps its call shape.
- No ERROR log line for a document with no `source_type`.

## Out of scope

- Changing stored `source_type` values, or a data migration.
- `catalog_postgres.py` SQL and the scheduler's per-source filters.
- `_get_display_name` (`app.py:794`).

## Impact

- Code: new `src/interfaces/chat_app/source_visibility.py`; `app.py` `_get_doc_visibility`
  body only.
- Tests: new `tests/unit/test_source_visibility.py`; new end-to-end cases in
  `tests/unit/test_get_top_sources.py`.
