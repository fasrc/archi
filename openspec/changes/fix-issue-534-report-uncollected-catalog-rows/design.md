## Context

`DataManager.run_ingestion` (`src/data_manager/data_manager.py:69`) runs three collection
steps (local files, scrapers, tickets), then `self.persistence.flush_index()`, then
`catalog.refresh()` and the `Catalog contains N resources after flush` log, then
`delete_existing_collection_if_reset()` and `update_vectorstore()`. It has two callers:
`DataManager.__init__` (`data_manager.py:67`) and, in production, the ingestion thread of
`src/bin/service_data_manager.py` (`DataManager(run_ingestion=False)` at `:44`, then
`build_ingestion_helpers(data_manager.run_ingestion, lock)` at `:199`). That thread catches
an ingest exception and the process keeps running; the scheduled jobs then use the same
`data_manager.persistence`.

Every collector writes through `PersistenceService.persist_resource`
(`src/data_manager/collectors/persistence.py:40`), which always upserts the catalog row,
also when the file already exists on disk. So "this hash went through `persist_resource`
in this pass" is an exact "collected" signal.

The failure signal is the hard part. The collectors swallow most errors:

| Place | Today | Scope it affects |
|---|---|---|
| `GitScraper.collect` (`git_scraper.py:107-115`) | clone error: `logger.error`, `continue`; bad URL `ValueError`: `logger.info`, `continue` | one repo, or all git if the repo name is unknown |
| `GitScraper._iter_code_files` (`git_scraper.py:355-360`) | `os.walk` with no `onerror`: an unreadable directory is silently left out | one repo |
| `GitScraper._harvest_code` (`git_scraper.py:221-241`) | `stat()` `OSError` and read `Exception`: silent `continue` | one repo |
| `GitScraper._looks_binary` (`git_scraper.py:363-370`) | an open or read error returns `True`, so the file is skipped as "likely binary" | one repo |
| `ScraperManager._handle_standard_url` (`scraper_manager.py:822-862`) | any crawl error: `logger.error`, returns the partial count | the seed's host and the host of every page the crawl yielded |
| `LinkScraper.crawl_iter` (`scraper.py:~309`) | per-page error: `logger.info`, page skipped | the page's host |
| `LinkScraper.crawl_iter` `max_pages` stop | `logger.info`, crawl ends early | every host still queued (a seed can redirect to another host) |
| `LinkScraper.crawl_iter` SSO login (`scraper.py:228-245`) | `authenticate()` or `authenticate_and_navigate()` returns `None` (`sso_scraper.py:319-357`); the crawl continues unauthenticated | the seed's host |
| `ScraperManager._refresh_sitemap_lastmod_map` (`scraper_manager.py:678-716`) | a failed child sitemap is stored in `_sitemap_expansion_failures`; no exception, shorter page list | the failed sitemap's host and every host in its `allowed_hosts` |
| `run_seeds` (`scrape_pool.py:~285`) | seed future error: `logger.warning` | the seed's host |
| `ScraperManager._collect_sso_from_urls` (`scraper_manager.py:516-541`) | missing selenium, secrets, or authenticator: `logger.error`, `return` | all `sso` |
| `LocalFileManager._iter_files` (`localfile_manager.py:90-93`) | `rglob` silently skips an unreadable directory | all `local_files` |
| `LocalFileManager._persist_file` (`localfile_manager.py:95-118`) | read or persist error: `logger.warning`, `return` | all `local_files` |
| `TicketManager.__init__` / `_init_client` (`ticket_manager.py:30-54`) | init error: `logger.warning`, client `None`; runs before any pass is open, and `collect_all_from_config` skips a `None` client | one ticket provider |
| `JiraClient` / `RedmineClient` init (`jira.py:43-81`, `redmine_tickets.py:26-64`) | login or missing token: logged, no connection; `collect()` then yields nothing | one ticket provider |
| `TicketManager._collect_from_client` (`ticket_manager.py:127-154`) | the `try` wraps only the generator's creation; an error during iteration at `:153` escapes and aborts the ingest | one ticket provider |
| `RedmineClient` per-ticket `except` (`redmine_tickets.py:134-136`) and Jira `max_tickets` stop (`jira.py:~195-199`) | a ticket is silently dropped, or the fetch is cut short | one ticket provider |

An exception that is not swallowed (for example `SitemapExpansionError`) aborts
`run_ingestion` before the report. That behavior does not change.

## Goals / Non-Goals

**Goals:** a read-only report of catalog rows that a scope with a recorded success did not
collect, safe enough that #555 can act on the same computation.

**Non-Goals:** any write to `documents`, any change to the vectorstore reconcile, any new
config key.

## Decisions

### D1. Scope model

A scope is a pair `(source_type, scope_key)`. The function `scope_for(metadata)` derives
it from a catalog row's metadata (the dict that `PostgresCatalogService._row_to_metadata`
returns) and from a resource's metadata at persist time. The same function serves both
sides, so the two sides always agree.

| `source_type` | `scope_key` |
|---|---|
| `git` | the `parent` metadata value (the repo name that `GitScraper` writes); if absent, `git_repo`; if both absent, `None` |
| `web` | the lowercased host (`urlparse(url).netloc.lower()`) of the `url` metadata value |
| `sso` | the lowercased host of the `url` metadata value |
| `local_files` | `"*"` (the whole source type) |
| `ticket` | the `ticket_provider` metadata value (`jira` or `redmine`, written at `jira.py:128` and `redmine_tickets.py:116`); if absent, `None` |
| any other value | `None` |

A row with a `None` scope key is never a candidate. The report counts these rows as
"unscoped" in the INFO line.

**Indico and ELOG rows** (`source_type == "web"` and `scraper` metadata in
`{"indico", "elog"}`) map to the scope `("web", "indico")` or `("web", "elog")`. Their
collectors (`IndicoScraper.collect`, `collect_elog`) do not expose a per-source success
signal, so the report always treats these two scopes as unsafe and skips them with a
WARNING when the catalog holds rows for them. This keeps the rule "no success signal, no
candidates" without a change to those scrapers.

### D2. The recorder

`CollectionPass` in `catalog_reconcile.py` holds, under one `threading.Lock` (the link
crawl persists from a thread pool):

- `collected`: a dict `scope -> set[resource_hash]`.
- `ran`: the set of source types that `record_collected` or `record_failure` saw.
- `failed`: a dict `scope -> reason`, and `failed_types`: a dict
  `source_type -> reason` for a failure that covers the whole source type.

Methods: `record_collected(resource_hash, metadata)` and
`record_failure(source_type, scope_key, reason)` where `scope_key=None` marks the whole
source type as failed.

`PersistenceService` gets an attribute `collection_pass: Optional[CollectionPass] = None`
and two methods, `begin_collection_pass()` (creates and returns a new recorder) and
`end_collection_pass()` (returns the recorder and resets the attribute to `None`).
`_persist_resource_locked` calls `record_collected` only after `upsert_resource` returns,
and only while `collection_pass` is not `None`. Scheduled collections and chat-app uploads
do not open a pass, so they record nothing and the report does not run for them.

A collector that has no `PersistenceService` at hand (the scrapers) reports through a
small helper `record_failure(persistence, source_type, scope_key, reason)` in
`catalog_reconcile.py` that does nothing when no pass is open. Collector code never
checks `collection_pass` itself.

### D3. Where each failure is recorded

- **Git.** `GitScraper.collect` keeps a list `self.last_failures` of
  `(repo_name_or_None, reason)`, cleared at the start of each `collect` call. A
  `ValueError` from `_parse_url` appends `(None, ...)`. A clone error appends the repo name
  (parse the URL first so the name is known). In the harvest, these append the repo name:
  an `os.walk` `onerror` callback in `_iter_code_files`; the `stat()` and read errors in
  `_harvest_code`; and an open or read error in the binary check (split the error out of
  `_looks_binary` so it is recorded, not reported as "binary").
  `ScraperManager._collect_git_resources` reads `last_failures` after `collect` and calls
  `record_failure(persistence, "git", name, reason)` for each.
- **Link and SSO crawl.** `_handle_standard_url` takes a `source_type` keyword argument
  (`"sso"` from `_collect_sso_from_urls`, else `"web"`). It keeps the set of hosts of the
  resources the crawl yielded. In its `except`, it records a failure for the seed host and
  for each host in that set.
- `LinkScraper.crawl_iter` gets an optional keyword argument
  `on_page_error: Callable[[str, str], None]` (URL, reason). It calls it: in the per-page
  `except`; at the `max_pages` stop, once for each distinct host among the URLs still in
  `to_visit` and `level_links`; and when `browserclient.authenticate(...)` or
  `browserclient.authenticate_and_navigate(...)` returns `None` (URL = the start URL,
  reason `authentication failed`). Callers that do not pass it see no change.
  `_handle_standard_url` passes a callback that records a failure for that URL's host under
  its `source_type`.
- **Sitemaps.** In `ScraperManager.collect_all_from_config`, after
  `_refresh_sitemap_lastmod_map`, record `("web", host)` for each `(url, exc)` in
  `self._sitemap_expansion_failures`: the failed sitemap's host and each host in that
  sitemap source's `allowed_hosts`. If the allowed hosts of a failed child cannot be
  resolved, record `("web", None)`.
- `run_seeds` already logs and continues on a seed error. `_scrape_one_seed` cannot reach
  that branch today because `_handle_standard_url` catches everything, so no change there.
- `_collect_sso_from_urls`: each early `return` records `("sso", None)`.
- **Local files.** Replace `rglob` in `LocalFileManager._iter_files` with
  `os.walk(directory, onerror=...)`; the callback records `("local_files", None)`. Both
  `except` branches of `_persist_file` record `("local_files", None)`. `_iter_files` needs
  the persistence object for this; pass it in.
- **Tickets.** In `TicketManager.collect_all_from_config` (this runs inside the pass), for
  each provider enabled in config with configured projects, record
  `("ticket", provider)` when the manager's client is `None` (init failed in `__init__`) or
  the client has no connection (`jira_client.client is None`; `redmine_client.redmine` is
  falsy). In `_collect_from_client`, move the persist loop inside the `try` path so an
  error raised while iterating records `("ticket", provider)` and then re-raises (the
  ingest still aborts as today). `RedmineClient` keeps a `last_failures` list for tickets
  its per-ticket `except` dropped, and `JiraClient` notes a `max_tickets` stop; the manager
  records `("ticket", provider)` for either after the loop.

A source type counts as "ran" when `record_collected` or `record_failure` first sees it.
There is no separate `record_ran` call. A source type that neither collected nor failed
did not run, and its rows are never candidates (acceptance criterion 5).

### D4. The pure function

`find_uncollected(rows, collection_pass) -> ReconcileReport` where `rows` is a list of
`(resource_hash, metadata)` pairs. For each scope that the catalog holds rows for:

1. Indico or ELOG scope → skip, reason `no per-source success signal`.
2. Source type not in `ran` → no candidates, no WARNING (the source did not run).
3. Source type in `failed_types` → skip, reason from the failure.
4. Scope in `failed` → skip, reason from the failure.
5. Scope collected zero hashes this pass → skip, reason
   `collected 0 resources while the catalog holds N rows`.
6. Else: candidates are the rows whose hash is not in `collected[scope]`.

`ReconcileReport` holds the candidates (hash, path or URL, source type, suffix, scope), the
skipped scopes with reasons and row counts, and the unscoped row count.

`log_reconcile_report(report)` writes one INFO line (total candidates, counts by source
type, counts by suffix, skipped scope count, unscoped count), one DEBUG line per
candidate, and one WARNING line per skipped scope.

### D5. The catalog read

`run_ingestion` reads the rows with
`self.persistence.catalog.get_metadata_by_filter("source_type")`, which runs
`SELECT * FROM documents WHERE NOT is_deleted AND source_type IS NOT NULL AND source_type != ''`
and returns `(resource_hash, metadata)` pairs. This is read-only and needs no new SQL.
Rows with an empty `source_type` are not returned, so they are never candidates.

### D6. Call site

In `run_ingestion`, call `self.persistence.begin_collection_pass()` before the first
collection step, and wrap the collection steps in `try`/`except` so that an exception
calls `self.persistence.end_collection_pass()` (discard, no report) and then re-raises.
The pass never outlives `run_ingestion`, so scheduled jobs in the long-running service
never record into a stale pass. After the `Catalog contains ...` log and before
`delete_existing_collection_if_reset()`, call one helper
`report_uncollected(self.persistence)` from `catalog_reconcile.py` that ends the pass,
reads the rows, computes, and logs. The helper catches and logs any exception from the
report itself at WARNING, so a defect in the report never fails an ingest. If a collection
step raises, `run_ingestion` still aborts as today, after the pass is discarded.

## Risks / Trade-offs

- **Pages added outside the input lists.** A URL added through the chat app is a `web` row
  in a host scope that can succeed, so it can show up as a candidate. This is acceptable in
  report-only mode. #555 must account for it before any deletion; the proposal names it.
- **Scope collisions.** Two git repos with the same short name share one scope. A failure in
  either one skips both, which is the safe direction.
- **Unknown swallow points.** The table lists every swallow point found on `origin/dev`
  `5564e016`. A new collector, or a new silent skip in an existing one, needs its own
  `record_failure` call; #555 must re-audit this list before it deletes anything.
- **Host-level web scope.** A single page error on a host skips the whole host. This is
  coarse but safe; the report then shows no candidates for that host and a WARNING.
- **Coverage.** All new logic lives in `catalog_reconcile.py` with unit tests; the edits to
  collectors are short call sites that the tests reach through fakes.
