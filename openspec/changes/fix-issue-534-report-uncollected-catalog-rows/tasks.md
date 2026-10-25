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
- [x] 3.1a Review fix (code). Two defects, one red-green unit:
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
- [x] 3.1b Review fix (tests that cannot fail). Make each of these tests able to go red:
  `test_binary_open_error_records_repo_not_binary` must let the real `_looks_binary` run
  (patch `Path.open` to raise) and assert no "likely binary" WARNING;
  `test_info_summary_reports_total_and_suffix_counts` must assert `4 py`, `2 sbatch`,
  `2 md` exactly; the skip-WARNING tests must assert the reason text (`clone failed`,
  `collected 0`, `selenium missing`) and the scope; the 8-thread test must force a thread
  switch (a `threading.Barrier` inside a patched `setdefault` path, or similar) so it fails
  with `_lock` replaced by `contextlib.nullcontext()`. Check each one red against a broken
  variant, then restore. Run the gate and commit.
- [x] 3.2 Scope keys (pure module, `catalog_reconcile.py`; `design.md` D1 and D2). Write the
  tests first in `tests/unit/test_catalog_reconcile.py`, see them fail, then implement:
  `scope_host("https://Docs.Example.org:8443/x")` and `scope_host("Docs.Example.org")` both
  return `docs.example.org`, and `scope_host("")` returns `None`; `scope_for` uses
  `scope_host` for `web` and `sso`; with an open pass, `record_url_failure(persistence,
  "web", "https://docs.example.org/x", "r")` makes `find_uncollected` skip a row at
  `https://Docs.Example.org:8443/y` (no candidate, one WARNING), a URL with no host records
  the whole `web` type, and with no open pass it records nothing; `ticket_scope_key` gives
  `jira:<project>`, `jira` with no project, and `redmine`, and `scope_for` uses it (a Jira
  row with `project` `B` maps to `("ticket", "jira:B")`; check the existing ticket tests
  still pass or update them); with project `A` collected and catalog rows of project `B`,
  `find_uncollected` yields no `B` candidate and one WARNING that names `jira:B`; a `web`
  row with no `scraper` marker and an ELOG-shaped URL (`is_elog_url`) maps to
  `("web", "elog")`, and one with `/event/` on a host that contains `indico` maps to
  `("web", "indico")`; rows with the marker still map as before. Run the gate and commit
  (`feat(#534): harden reconcile scope keys`).
- [x] 3.3 Input lists and sitemaps in `ScraperManager.collect_all_from_config` (`design.md`
  D3 "Input lists" and "Sitemaps"). Tests with the collect steps patched out
  (`collect_links`, `collect_sso`, `collect_git`, `collect_elog`, `collect_indico`) and an
  open pass on a fake persistence: a missing input list (a temp `weblists/` dir under a
  monkeypatched cwd) records a whole-type failure for `web`, `sso`, and `git` when all three
  are enabled, and none for a type that is disabled (`git_enabled = False` records no `git`
  failure); all lists readable records nothing; a sitemap expansion whose failure entry is a
  child URL on allowed host `b.example.org`, with the top-level sitemap on `a.example.org`,
  records `("web", "a.example.org")` and `("web", "b.example.org")`, and through
  `find_uncollected` no row of either host is a candidate; no sitemap failure records nothing.
  Patch `manager._expand_sitemaps` as the existing tests in
  `tests/unit/test_scraper_sitemap_refresh.py` do (`monkeypatch.setattr(manager,
  "_expand_sitemaps", ...)`, about line 308); the fake must set
  `manager._sitemap_expansion_failures` and `manager._sitemap_expansion_incomplete` itself,
  as the real method does, and return the page pairs. Implement. Run the gate and commit.
- [ ] 3.4 Crawl failure callbacks in `LinkScraper.crawl_iter` and the selenium link
  extractor (`design.md` D3 "Link and SSO crawl, in `LinkScraper.crawl_iter`"). Tests with a
  fake `requests.Session` and fake browser clients (no network, no real browser; build an
  `SSOScraper` with `__new__` and a fake driver): an unnormalizable start URL calls
  `on_page_error` with the start URL; an HTTP error after a redirect from `h1` to `h2` calls
  it with the `h1` URL and the `h2` URL; a connection error (no response) calls it with the
  `h1` URL only; a `max_pages` stop with `h2` links queued calls it once for `h2`; on the
  requests path, `authenticate` returning `None` calls it with reason
  `authentication failed` and the crawl still yields the seed page; on the selenium path,
  `authenticate_and_navigate` returning `None` calls it; `authenticate` returning cookies
  while the seed response ends on another host calls it with reason
  `authentication not established`, and the same on the selenium path through
  `browserclient.driver.current_url`; `get_links_with_same_hostname` with one anchor whose
  `get_attribute` raises sets `last_link_errors` to 1 and resets it to 0 on the next call,
  and `crawl_iter` then calls `on_page_error` with reason `link extraction failed`; a call
  without `on_page_error` yields the same resources as before. Implement. Run the gate and
  commit.
- [ ] 3.5 Crawl wiring in `ScraperManager` (`design.md` D3 "Link and SSO crawl, in
  `ScraperManager`"). Tests with a fake scraper whose `crawl_iter` drives the callbacks:
  a page error recorded through `on_page_error` lands under `("web", host)`, and under
  `("sso", host)` when `_collect_sso_from_urls` is the caller; when `persist_resource`
  raises on the second resource, the seed host, the host of the yielded resource, and a host
  passed to `on_request_url` are all recorded; the caller's own `on_request_url` is still
  called; with no open pass nothing is recorded; each early `return` in
  `_collect_sso_from_urls` records `("sso", None)`. Update `fake_handle` in
  `tests/unit/test_scraper_manager_sso_sequential.py` to accept `**kwargs`, since
  `_collect_sso_from_urls` now passes `source_type`. Implement. Run the gate and commit.
- [ ] 3.6 Local files (`design.md` D3 "Local files"). Tests: local files enabled with a
  missing staging directory records `("local_files", None)`; a read error and a persist
  error in `LocalFileManager._persist_file` record `("local_files", None)`; an unreadable
  directory during discovery (`os.walk` `onerror`, monkeypatched) records
  `("local_files", None)`; a staging dir with `a.txt` and a dangling symlink records nothing
  and still persists `a.txt`. Replace `rglob` with `os.walk` and keep the `is_file()`
  filter. Implement. Run the gate and commit.
- [ ] 3.7 Tickets (`design.md` D3 "Tickets"; the scope keys are task 3.2). Test through
  `TicketManager.collect_all_from_config`, not `_collect_from_client` alone: Jira enabled with
  projects `A` and `B` and a client that failed in `__init__` records `jira:A` and `jira:B`;
  Jira with a client whose `.client` is `None` and Redmine persisting tickets records the
  Jira projects and, through `find_uncollected`, yields no Jira candidates and one WARNING
  per Jira project scope that holds rows; an empty `JIRA_PAT` records the Jira projects and
  still builds the client as today; an error raised while iterating `collect()` records the
  projects (or `redmine`) and still propagates; a Redmine ticket dropped by the per-ticket
  `except` records `redmine`; a Jira `max_tickets` stop records that project; a fake
  `search_issues` that returns 50 rows of `total=120` with a page size of 100 records that
  project, and one that returns 50 of `total=50` records nothing. Implement. Run the gate and
  commit.

## 4. Wire the pass into run_ingestion

- [ ] 4.1 Add `report_uncollected(persistence)` to `catalog_reconcile.py` (`design.md` D5
  and D6) and tests for it: it ends the pass, reads rows with
  `catalog.get_metadata_by_filter("source_type")`, and logs the report; with a catalog
  fake whose read raises, it logs a WARNING and returns normally; it never calls any
  `delete_resource` (use a fake catalog and a fake persistence whose `delete_resource`
  fails the test if called, and assert that no SQL that matches the regex
  `\b(UPDATE|DELETE)\b` (case-insensitive) reaches a fake cursor; do not write the literal
  phrases `delete from documents` or `is_deleted = true` in any case in the test). Then add
  `run_ingestion` tests that build a `DataManager` with `object.__new__` and fakes for the
  three managers, persistence, and vector manager, and assert the order:
  `begin_collection_pass` before the first step, `report_uncollected` after
  `catalog.refresh()` and before `delete_existing_collection_if_reset()`; and, when a
  collection step, `flush_index()`, or `catalog.refresh()` raises, the exception propagates
  and `persistence.collection_pass` is `None` afterwards. Give `_bare_data_manager` in
  `tests/unit/test_data_manager_embedding_progress.py` a `collection_pass` attribute and
  `begin_collection_pass` and `end_collection_pass` methods, so its existing tests stay
  green. Add the thin call sites and the `try`/`finally` to `run_ingestion` (`design.md`
  D6). Run the gate and commit.

## 5. Verify, push, and open the PR

- [ ] 5.1 Run `git diff origin/dev -- src/ | grep -iE '^\+.*(delete from documents|is_deleted = true)'`
  and confirm it prints nothing. (The check reads only added `src/` lines: this file and
  existing test fixtures hold the same phrases.) Tick this box, then commit the tick through
  the pre-commit hook (the hook runs `bash scripts/gate.sh`; confirm it is green with patch
  coverage of 80 % or more). Then push with
  `git push -u origin fix/issue-534-report-uncollected-catalog-rows`. Open the PR with
  `gh pr create --repo fasrc/archi --base dev`, with `closes #534` in the PR **body** (not
  the title). The body names #555 as the follow-up, lists the skipped-scope rules, lists
  the "Known false candidates (for #555)" from `proposal.md`, and says why the deletion
  check reads only `src/`. It says that the dev check needs a redeploy by a human: the issue
  expects about 8 candidates (4 `py`, 2 `sbatch`, 2 `md`), but `fasrc/User_Codes` master
  still holds `Training/Checkpointing/`, so a human must check each git candidate against
  upstream before it is recorded on #555 as a relic. No `Co-Authored-By` trailer. Do not
  merge.
