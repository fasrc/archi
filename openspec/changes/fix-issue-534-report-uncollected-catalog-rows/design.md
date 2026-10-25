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
| `LinkScraper.crawl_iter` SSO login (`scraper.py:228-245`) | `authenticate()` or `authenticate_and_navigate()` returns `None` (`sso_scraper.py:319-357`); the crawl continues unauthenticated. `CERNSSOScraper.login` returns `True` right after it clicks sign-in and never checks the result (`sso_scraper.py:401-405`), so a rejected password also returns cookies; the seed fetch then ends on the identity-provider host | the seed's host |
| `LinkScraper.crawl_iter` start (`scraper.py:207-227`) | an unnormalizable start URL, or the selenium path with no browser client: `logger.error`, `return` with nothing yielded | the seed's host |
| `LinkScraper.crawl_iter` per-page error after a redirect (`scraper.py:273-311`) | the `except` knows only `current_url`; the redirect target (`response.url`) is lost | the seed's host and the redirect target's host |
| `SSOScraper.get_links_with_same_hostname` (`sso_scraper.py:~153-191`) | per-anchor error: `logger.error`, link dropped (selenium SSO path) | the page's host |
| `ScraperManager._collect_urls_from_lists` (`scraper_manager.py:667-697`) | a missing or unreadable input list: `logger.warning`, `continue`; only `_input_lists_complete = False` remains. The list's sources (any type, any host) are not collected | all `web`, `sso`, and `git` |
| `ScraperManager._refresh_sitemap_lastmod_map` (`scraper_manager.py:678-716`) | a failed child sitemap is stored in `_sitemap_expansion_failures` as `(child_url, exc)`; no exception, shorter page list. A child can sit on an allowed host while its pages sit on the top-level source's host (`sitemap_source.py:454-511`) | the host of every top-level sitemap URL and every global `allowed_hosts` entry |
| `run_seeds` (`scrape_pool.py:~285`) | seed future error: `logger.warning` | the seed's host |
| `ScraperManager._collect_sso_from_urls` (`scraper_manager.py:516-541`) | missing selenium, secrets, or authenticator: `logger.error`, `return` | all `sso` |
| `LocalFileManager.collect_all_from_config` (`localfile_manager.py:45-48`) | local files enabled but the staging directory is missing: `logger.info`, `return` | all `local_files` |
| `LocalFileManager._iter_files` (`localfile_manager.py:90-93`) | `rglob` silently skips an unreadable directory | all `local_files` |
| `LocalFileManager._persist_file` (`localfile_manager.py:95-118`) | read or persist error: `logger.warning`, `return` | all `local_files` |
| `TicketManager.__init__` / `_init_client` (`ticket_manager.py:30-54`) | init error: `logger.warning`, client `None`; runs before any pass is open, and `collect_all_from_config` skips a `None` client | one ticket provider |
| `JiraClient` / `RedmineClient` init (`jira.py:43-81`, `redmine_tickets.py:26-64`) | login or missing token: logged, no connection; `collect()` then yields nothing | one ticket provider |
| `TicketManager._collect_from_client` (`ticket_manager.py:127-154`) | the `try` wraps only the generator's creation; an error during iteration at `:153` escapes and aborts the ingest | one ticket provider |
| `RedmineClient` per-ticket `except` (`redmine_tickets.py:134-136`) and Jira `max_tickets` stop (`jira.py:~195-199`) | a ticket is silently dropped, or the fetch is cut short | one ticket provider |
| `JiraClient._get_project_issues` short page (`jira.py:192-193`) | `if len(batch) < max_batch_results: break` ends the fetch without a check against the result's `total`; a server that caps the page size truncates the project | one Jira project |
| `JiraClient.__init__` empty token (`jira.py:43-81`) | `read_secret("JIRA_PAT")` returns `""` when unset; `jira.JIRA(url, token_auth="")` opens an anonymous session, so `client` is not `None` and a search returns only what anonymous users can see | every configured Jira project |

Line numbers in the table come from `5564e016` and `6b8afe98` (rows added on 2026-10-24
after the Loop-1 review); find each symbol by name.

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
| `web` | `scope_host(url)` of the `url` metadata value (see below) |
| `sso` | `scope_host(url)` of the `url` metadata value |
| `local_files` | `"*"` (the whole source type) |
| `ticket`, provider `jira` | `"jira:<project>"`, from the `project` metadata value (else `parent`), written at `jira.py:116-128`; if both are absent, `"jira"` |
| `ticket`, provider `redmine` | `"redmine"` (`redmine_tickets.py:116`); Redmine projects come only from config |
| `ticket`, no `ticket_provider` | `None` |
| any other value | `None` |

A row with a `None` scope key is never a candidate. The report counts these rows as
"unscoped" in the INFO line.

**Host keys.** `scope_host(url_or_host)` returns the lowercased `.hostname` of a URL, with
the port and userinfo dropped, the same form as `scrape_pool.host_key` and as the
`allowed_hosts` config entries. It also accepts a bare host such as `docs.example.org`
(parse it as `"//" + value`). It returns `None` when no host results. `scope_for` uses it for
`web` and `sso`, and every web or SSO failure site records through it (D2), so the two sides
of the comparison always use the same key. A coarser key is the safe direction: two ports on
one host share a scope, and a failure on either one skips both.

**Jira scope per project.** The uploader's `add_jira_project`
(`src/interfaces/uploader_app/app.py:307-317`) collects a project that is not in config into
the same catalog. A full pass collects only the configured projects. With one scope for all
of Jira, every ticket of an uploader-added project would be a candidate on each pass in which
the configured projects succeed. With a scope per project, the uploader-added project
collects 0 in the pass and is skipped (rule 5). Rows written before the `project` key existed
map to `"jira"`, which the pass never collects, so they are skipped too.

**Indico and ELOG rows** map to the scope `("web", "indico")` or `("web", "elog")` when
`source_type == "web"` and either the `scraper` metadata is `"indico"` or `"elog"`, or the row
carries no `scraper` marker and its URL has the source's shape: `is_elog_url(url)`
(`src/cli/source_registry.py`) for ELOG, and a path that contains `/event/` on a host that
contains `indico` for Indico (the shape test of `ScraperManager._is_indico_url`, without its
`indico_enabled` check). A scheduled links re-fetch upserts an ELOG or Indico row under the
same hash and replaces `extra_json`, so older rows can lack the marker
(`scraper_manager.py:375-383`). Their collectors (`IndicoScraper.collect`, `collect_elog`) do
not expose a per-source success signal, so the report always treats these two scopes as
unsafe and skips them with a WARNING when the catalog holds rows for them. This keeps the rule
"no success signal, no candidates" without a change to those scrapers. The shape fallback can
also catch a plain link page with an Indico-like URL; that only removes candidates.

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

Web and SSO failure sites never build a host key themselves. They call
`record_url_failure(persistence, source_type, url_or_host, reason)`, which computes the key
with `scope_host` and forwards to `record_failure`. When `scope_host` returns `None`, it
records the whole source type (`scope_key=None`), which is the safe direction.

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
- **Input lists.** In `ScraperManager.collect_all_from_config`, right after
  `_collect_urls_from_lists_by_type`, when `self._input_lists_complete` is `False`, record a
  whole-type failure (`scope_key=None`, reason `input list missing or unreadable`) for `web`
  if `self.links_enabled`, for `sso` if `self.sso_enabled`, and for `git` if
  `self.git_enabled`. The missing list can hold sources of any of these types on any host,
  so no narrower scope is safe. A disabled type does not run, so it records nothing.
- **Sitemaps.** In the same method, in the branch that calls
  `_refresh_sitemap_lastmod_map` (never from a stale attribute of an earlier pass), when
  `self._sitemap_expansion_failures` is not empty, call `record_url_failure` under `"web"`
  for each top-level URL in `sitemap_urls` and for each entry of the global
  `self.sitemap_config["allowed_hosts"]` (a scalar string is one host, as
  `_expand_sitemaps` treats it). A failed child's pages can belong to the top-level host or to
  any allowed host, and the failure entry carries only the child URL.
- **Link and SSO crawl, in `LinkScraper.crawl_iter`.** It gets an optional keyword argument
  `on_page_error: Callable[[str, str], None]` (URL, reason). Callers that do not pass it see
  no change. It calls it:
  - before each early `return` (an unnormalizable start URL; the selenium path with no
    browser client), with the start URL;
  - in the per-page `except`, with `current_url`, and also with the final URL of the
    response when this iteration obtained one (`response.url`; set `response = None` at
    the top of each iteration), so a redirect target's host is recorded too;
  - at the `max_pages` stop, once for each distinct host among the URLs still in
    `to_visit` and `level_links`;
  - when `browserclient.authenticate(...)` or `browserclient.authenticate_and_navigate(...)`
    returns `None` (start URL, reason `authentication failed`). Today the return value of
    `authenticate_and_navigate` is ignored; check it;
  - when a browser client is in use and the first page fetched ends on a host other than the
    start URL's host (requests path: `response.url`; selenium path:
    `getattr(getattr(browserclient, "driver", None), "current_url", None)`), with the start
    URL and reason `authentication not established`. `CERNSSOScraper.login` cannot tell a
    rejected password from a good one, so a fetch that lands on the identity provider is the
    only signal. A legitimate cross-host redirect on an SSO seed also trips it; that only
    removes candidates;
  - after `reap` on the selenium path, when `browserclient.last_link_errors` (read with
    `getattr`, default 0) is not zero, with `current_url` and reason
    `link extraction failed`. `SSOScraper.get_links_with_same_hostname` resets
    `self.last_link_errors = 0` at its start and adds 1 in its per-anchor `except`.
- **Link and SSO crawl, in `ScraperManager`.** `_handle_standard_url` takes a
  `source_type` keyword argument (`"sso"` from `_collect_sso_from_urls`, else `"web"`). It
  passes `on_page_error` as a callback that calls
  `record_url_failure(persistence, source_type, url, reason)`. It wraps `on_request_url` (and
  still calls the caller's callback, if any) to keep the set of final URLs the crawl
  requested, and it keeps the URLs of the resources the crawl yielded. In its `except`, it
  records a failure for the seed URL, for each yielded URL, and for each requested URL.
- `run_seeds` already logs and continues on a seed error. `_scrape_one_seed` cannot reach
  that branch today because `_handle_standard_url` catches everything, so no change there.
- `_collect_sso_from_urls`: each early `return` records `("sso", None)`.
- **Local files.** When local files are enabled and the staging directory does not exist,
  `collect_all_from_config` records `("local_files", None)` (reason
  `staging directory missing`) before it returns. Replace `rglob` in
  `LocalFileManager._iter_files` with `os.walk(directory, onerror=...)`; the callback
  records `("local_files", None)`. Keep the `is_file()` filter, so a dangling symlink or a
  non-regular file is skipped, not read (the same trap as task 3.1a for git). Both `except`
  branches of `_persist_file` record `("local_files", None)`. `_iter_files` needs the
  persistence object for this; pass it in.
- **Tickets.** Scope keys come from one helper, `ticket_scope_key(provider, project)` in
  `catalog_reconcile.py`, which `scope_for` also uses (`"jira:<project>"`, `"jira"` with no
  project, `"redmine"`). In `TicketManager.collect_all_from_config` (this runs inside the
  pass), for each provider enabled in config: record a failure for every configured Jira
  project when the Jira client is `None` (init failed in `__init__`), when
  `jira_client.client is None` (login failed), or when the Jira token is empty
  (`read_secret("JIRA_PAT")` returns `""`, and `jira.JIRA(url, token_auth="")` then opens an
  anonymous session; keep that behavior, but record it); record `("ticket", "redmine")` when
  the Redmine client is `None` or `redmine_client.redmine` is falsy. In
  `_collect_from_client`, move the persist loop inside the `try` path so an error raised
  while iterating records the failure (each project passed in for Jira, `"redmine"` for
  Redmine) and then re-raises (the ingest still aborts as today). `JiraClient` keeps a list
  `last_failures` of `(project, reason)`, cleared at the start of `collect`, for a
  `max_tickets` stop and for a short page that ends the fetch while the result's `total`
  (`getattr(batch, "total", None)`) is greater than `start_at + len(batch)`.
  `RedmineClient` keeps a `last_failures` list for tickets its per-ticket `except` dropped.
  The manager records each of these after the loop.

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
collection step. Wrap everything from there through the `report_uncollected` call (the
collection steps, `flush_index()`, `catalog.refresh()`, and the report) in `try`/`finally`,
and call `self.persistence.end_collection_pass()` in the `finally`. `end_collection_pass` is
idempotent, so the call after `report_uncollected` already ended the pass is harmless. An
exception from a collection step, `flush_index()`, or `catalog.refresh()` discards the pass
(no report) and still propagates. The pass never outlives `run_ingestion`, so scheduled jobs
in the long-running service never record into a stale pass. After the `Catalog contains ...`
log and before `delete_existing_collection_if_reset()`, call one helper
`report_uncollected(self.persistence)` from `catalog_reconcile.py` that ends the pass, reads
the rows, computes, and logs. The helper catches and logs any exception from the report
itself at WARNING, so a defect in the report never fails an ingest.

## Risks / Trade-offs

- **Known false candidates (for #555).** These rows can show up as candidates in a scope
  that succeeded. That is acceptable in report-only mode, but #555 must handle each one
  before any deletion. The proposal and the PR body list them.
  - A page added through the uploader's `upload_url` (`collect_links`) on a host that the
    input lists also crawl.
  - A git repo added through the uploader's `add_git_repo` whose short name matches a listed
    repo. The two share one scope; when the listed repo succeeds, the added repo's rows are
    candidates.
  - Any uploader write (a local-file upload, `upload_url`, `add_jira_project` into a
    configured project) made during the ingest window. The uploader does not take the
    ingestion lock to collect; only the vectorstore update is locked.
  - Git rows of a directory that the clone lacks but upstream still has. On 2026-10-24,
    `fasrc/User_Codes` master still holds `Training/Checkpointing/Exercise1`–`6`, the
    directory of the 6 git rows that the issue calls relics. If the clone, not the source,
    dropped the directory, those rows are collection failures, not relics. A human on the dev
    host must find out why before #555 deletes them.
- **Scope collisions.** Two git repos with the same short name share one scope. A failure in
  either one skips both, which is the safe direction when both ran.
- **Unknown swallow points.** The table lists every swallow point found on `origin/dev`
  `5564e016` and `6b8afe98`. A new collector, or a new silent skip in an existing one, needs
  its own `record_failure` call; #555 must re-audit this list before it deletes anything.
- **Host-level web scope.** A single page error on a host skips the whole host. This is
  coarse but safe; the report then shows no candidates for that host and a WARNING.
- **Coverage.** All new logic lives in `catalog_reconcile.py` with unit tests; the edits to
  collectors are short call sites that the tests reach through fakes.
