## Why

A row enters the `documents` catalog when a collector persists it, and nothing removes it
when the collector stops collecting it. The vectorstore reconcile in
`src/data_manager/vectorstore/manager.py` only makes the vectorstore match the catalog; it
never makes the catalog match what was collected. On dev, 8 of 1163 catalog rows are relics
(4 `py` and 2 `sbatch` under `User_Codes/Training/Checkpointing/`, 2 `websites/*.md`), and no
log line shows them (#534).

The operator decided on 2026-09-26 to ship a **report-only** pass first. A later, gated
prune (#555) acts on this pass's candidate list only after a human reads its output on dev.
As a result, the candidate computation must already be safe now: "not collected" and
"collection failed" look the same to the catalog.

## What Changes

- Add a pure, tested module `src/data_manager/collectors/utils/catalog_reconcile.py`. It
  records what a full ingestion pass collected and which scopes failed, and it computes the
  catalog rows that a successful scope did not collect.
- `PersistenceService` gets an optional collection-pass recorder. `persist_resource` records
  each persisted `resource_hash` with its scope while a pass is open.
- The collectors record a failure for a scope at each place where they swallow an error
  today (git clone, walk, and file reads; a missing or unreadable input list; link seed
  crawl, per-page crawl error and its redirect target, crawl stopped at `max_pages`, failed
  or unconfirmed SSO login, SSO link extraction, failed child sitemap; SSO preconditions;
  local staging directory, file discovery, read, and persist; ticket provider init, login,
  empty Jira token, iteration, short or capped Jira fetch, and dropped tickets).
- Scope keys are safe by construction: web and SSO hosts use one helper on both sides
  (lowercased hostname, port dropped); Jira rows are scoped per project, so a project added
  through the uploader is never a candidate; Redmine rows are scoped per provider; ELOG and
  Indico rows that lost their `scraper` marker are still recognized by URL shape and skipped.
- `run_ingestion` discards the pass when a collection step, the index flush, or the catalog
  refresh raises, so the pass never outlives the ingest in the long-running data-manager
  service.
- `DataManager.run_ingestion` opens a pass before the three collection steps and calls the
  report after the catalog refresh, before `delete_existing_collection_if_reset()`.
- The pass logs one INFO summary line, the full candidate list at DEBUG, and one WARNING
  for each scope that it skipped as unsafe.
- The pass writes nothing. It never calls `delete_resource` and never runs `UPDATE` or
  `DELETE` against `documents`.

## Out of scope

- Any deletion, soft or hard, and the `data_manager.catalog_pruning.*` config keys (#555).
- `CatalogPostgres.delete_resource` and `PersistenceService.delete_resource`.
- The vectorstore reconcile in `manager.py`.
- Config-repo changes, `deploy/**`, suffix allowlists, and
  `src/interfaces/chat_app/app.py`.

## Known false candidates (for #555)

A report-only pass can list rows that are not relics. #555 must handle each class before it
deletes anything; `design.md` "Risks" gives the detail.

- Pages added through the uploader's `upload_url` on a host that the input lists crawl.
- Git repos added through the uploader's `add_git_repo` whose short name matches a listed
  repo.
- Any uploader write made during the ingest window (the uploader does not take the ingestion
  lock to collect).
- Git rows of a directory that the clone lacks but upstream still has. `fasrc/User_Codes`
  master still holds `Training/Checkpointing/` (checked 2026-10-24), the directory of the 6
  git rows that the issue calls relics.

## Impact

- Code: `src/data_manager/collectors/utils/catalog_reconcile.py` (new),
  `src/data_manager/collectors/persistence.py`, `src/data_manager/data_manager.py`,
  `src/data_manager/collectors/scrapers/scraper_manager.py`,
  `src/data_manager/collectors/scrapers/scraper.py`,
  `src/data_manager/collectors/scrapers/integrations/git_scraper.py`,
  `src/data_manager/collectors/scrapers/integrations/sso_scraper.py`,
  `src/data_manager/collectors/localfile_manager.py`,
  `src/data_manager/collectors/tickets/ticket_manager.py`,
  `src/data_manager/collectors/tickets/integrations/jira.py`,
  `src/data_manager/collectors/tickets/integrations/redmine_tickets.py`.
- Behavior: new log lines during a full ingest only. Scheduled collections and chat-app
  uploads do not open a pass and record nothing.
- Follow-up: #555 consumes the candidate list after a human reviews the dev output.
