Each task is one red-green unit: write the failing tests, watch them fail, add the minimum
code, run `bash scripts/gate.sh`, and commit. Do not end a task on a red test. Read
`design.md` before task 1. Do not touch `src/interfaces/chat_app/app.py`,
`src/data_manager/vectorstore/manager.py`, any `delete_resource`, `deploy/**`, or `config/**`.

## 1. The pure reconcile module

- [x] 1.1 Create `tests/unit/test_catalog_reconcile.py` and
  `src/data_manager/collectors/utils/catalog_reconcile.py` with `scope_for`,
  `CollectionPass` (`record_collected`, `record_failure`, thread-safe), `find_uncollected`,
  `ReconcileReport`, and `log_reconcile_report`, as `design.md` D1, D2, and D4 describe.
  Tests (write them first, see them fail on the missing import, then implement):
  `scope_for` for each row of the D1 table, including Indico and ELOG rows and an unknown
  `source_type`; a successful git scope with catalog rows `a,b,c,d` and collected `a,b`
  yields exactly `c,d`; a scope with a recorded failure yields zero candidates and one
  WARNING (`caplog`); a whole-type failure (`scope_key=None`) skips every scope of that
  type; a scope that collected zero while the catalog holds rows yields zero and a WARNING
  that states the row count; a source type that never recorded anything yields zero and no
  WARNING; Indico and ELOG scopes are always skipped with a WARNING; rows with a `None`
  scope key are counted as unscoped and never candidates; the INFO summary for 4 `py`,
  2 `sbatch`, 2 `md` candidates reports 8 with those suffix counts; each candidate appears
  at DEBUG; concurrent `record_collected` calls from 8 threads lose no hash. Run the gate
  and commit (`feat(#534): add the report-only catalog reconcile module`).

## 2. Record collected hashes in PersistenceService

- [x] 2.1 Add tests to `tests/unit/test_catalog_reconcile.py` (or a new
  `tests/unit/test_persistence_collection_pass.py`): with an open pass,
  `persist_resource` records the hash under the resource's scope only after
  `upsert_resource` returns (a fake catalog whose `upsert_resource` raises records
  nothing); with no open pass it records nothing and behaves as before;
  `end_collection_pass()` returns the recorder and resets it to `None`; the
  module-level `record_failure(persistence, ...)` helper is a no-op with no open pass.
  Use a fake catalog object; do not connect to Postgres. Implement
  `begin_collection_pass`, `end_collection_pass`, and the record call in
  `_persist_resource_locked` (`design.md` D2). Run the gate and commit.

## 3. Record failures at the swallow points

- [x] 3.1 Git: tests with a fake `GitScraper` path or by patching `_prepare_repository` /
  `_parse_url`: a clone error records `("git", <repo_name>)`; a bad URL `ValueError`
  records `("git", None)`; a `stat()` error and a read error in `_harvest_code` record the
  repo; an `os.walk` `onerror` in `_iter_code_files` records the repo; an open error in the
  binary check records the repo (and is not reported as "binary");
  `last_failures` is cleared at the start of each `collect`. Implement
  `GitScraper.last_failures` and the forwarding in
  `ScraperManager._collect_git_resources` (`design.md` D3). Run the gate and commit.
- [ ] 3.1a Review fix (code). Two defects, one red-green unit:
  (1) `GitScraper._harvest_code` calls `file_path.stat()` before the suffix and exclusion
  checks, so a dangling symlink (for example `docs/img/logo.png -> ../missing.png`) or a
  symlink loop records `(repo_name, err)` in `last_failures` and the whole git scope is
  skipped on every run. Test first: a temp repo with `a.py` and a dangling symlink records
  nothing in `last_failures` and still yields `a.py`. Fix: do the suffix and exclusion
  checks before `stat()`, and treat a symlink whose target is missing (or ELOOP) as a skip,
  not a failure. A real `stat()` error on a regular allowed file still records the repo.
  (2) `find_uncollected` derives the candidate suffix from `Path(url).suffix`, which gives
  `""` for `https://example.org/guide` and `py?download=1` for a query string. Test first:
  a candidate row with `metadata["suffix"] = ".md"` and URL `https://example.org/guide`
  reports suffix `md`. Fix: use `metadata["suffix"]` (strip a leading dot) and parse the
  path only when it is absent. Run the gate and commit.
- [ ] 3.1b Review fix (tests that cannot fail). Make each of these tests able to go red:
  `test_binary_open_error_records_repo_not_binary` must let the real `_looks_binary` run
  (patch `Path.open` to raise) and assert no "likely binary" WARNING;
  `test_info_summary_reports_total_and_suffix_counts` must assert `4 py`, `2 sbatch`,
  `2 md` exactly; the skip-WARNING tests must assert the reason text (`clone failed`,
  `collected 0`, `selenium missing`) and the scope; the 8-thread test must force a thread
  switch (a `threading.Barrier` inside a patched `setdefault` path, or similar) so it fails
  with `_lock` replaced by `contextlib.nullcontext()`. Check each one red against a broken
  variant, then restore. Run the gate and commit.
- [ ] 3.2 Web and SSO: tests that `_handle_standard_url` records the seed host and the
  host of every page it yielded, under the passed `source_type`, when the crawl raises;
  that `LinkScraper.crawl_iter` calls `on_page_error` on a per-page fetch error, once per
  distinct queued host when it stops at `max_pages` (a seed on `h1` that redirects to `h2`
  and stops with `h2` pages queued reports `h2`), and when a fake browserclient's
  `authenticate` or `authenticate_and_navigate` returns `None` (the crawl still yields the
  seed page; `("sso", <seed host>)` is recorded); that a call without `on_page_error`
  behaves as before; that each early `return` in `_collect_sso_from_urls` records
  `("sso", None)`; that each entry of `_sitemap_expansion_failures` after
  `_refresh_sitemap_lastmod_map` in `collect_all_from_config` records the failed sitemap's
  host and its `allowed_hosts` under `"web"`. Implement as `design.md` D3 says. Run the
  gate and commit.
- [ ] 3.3 Local files: tests that a read error and a persist error in
  `LocalFileManager._persist_file` record `("local_files", None)`, and that an unreadable
  directory during discovery (`os.walk` `onerror`, monkeypatched) records
  `("local_files", None)`. Replace `rglob` with `os.walk` as `design.md` D3 says.
  Implement. Run the gate and commit.
- [ ] 3.4 Tickets: change the `ticket` scope key in `scope_for` to `ticket_provider` (update
  the task-1 tests). Test through `TicketManager.collect_all_from_config`, not
  `_collect_from_client` alone: Jira enabled with a client that failed in `__init__`
  records `("ticket", "jira")`; Jira with a client whose `.client` is `None` (login
  failed) and Redmine persisting tickets records `("ticket", "jira")` and, through
  `find_uncollected`, yields no Jira candidates and one WARNING; an error raised while
  iterating `collect()` records the provider and still propagates; a Redmine ticket
  dropped by the per-ticket `except` and a Jira `max_tickets` stop record the provider.
  Implement as `design.md` D3 says. Run the gate and commit.

## 4. Wire the pass into run_ingestion

- [ ] 4.1 Add `report_uncollected(persistence)` to `catalog_reconcile.py` (`design.md` D5
  and D6) and tests for it: it ends the pass, reads rows with
  `catalog.get_metadata_by_filter("source_type")`, and logs the report; with a catalog
  fake whose read raises, it logs a WARNING and returns normally; it never calls any
  `delete_resource` (use a fake catalog and a fake persistence whose `delete_resource`
  fails the test if called, and assert no SQL text with `UPDATE` or `DELETE` reaches a
  fake cursor). Then add `run_ingestion` tests that builds a `DataManager` with
  `object.__new__` and fakes for the three managers, persistence, and vector manager, and
  asserts the order: `begin_collection_pass` before the first step, `report_uncollected`
  after `catalog.refresh()` and before `delete_existing_collection_if_reset()`; and, when a
  collection step raises, the exception propagates and `persistence.collection_pass` is
  `None` afterwards. Add the thin call sites and the `try`/`except` to `run_ingestion`
  (`design.md` D6). Run the gate and commit.

## 5. Verify, push, and open the PR

- [ ] 5.1 Run `git diff origin/dev | grep -iE 'delete from documents|is_deleted = true'` and
  confirm it prints nothing. Run `bash scripts/gate.sh` on the branch tip and confirm it is
  green with patch coverage of 80 % or more. Push with
  `git push -u origin fix/issue-534-report-uncollected-catalog-rows`. Open the PR with
  `gh pr create --repo fasrc/archi --base dev`, with `closes #534` in the PR **body** (not
  the title). The body names #555 as the follow-up, lists the skipped-scope rules, and says
  that the dev check (expect about 8 candidates: 4 `py`, 2 `sbatch`, 2 `md`) needs a
  redeploy by a human. No `Co-Authored-By` trailer. Do not merge.
