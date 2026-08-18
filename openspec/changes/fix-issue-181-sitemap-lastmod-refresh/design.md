## Context

All anchors below verified against `origin/dev@0a157cdc` (the issue cited `bd2d519c`; the
line numbers still hold).

`ScraperManager.collect_all_from_config` (`scraper_manager.py:172`) derives
`sitemap_urls` from `self.input_lists` (`:174–176`), initializes
`self._sitemap_lastmod_map: Dict[str, str] = {}` (`:188` — the only writer in the file),
then, when there are sitemap sources:

1. builds `existing_keys` = the *normalized* hand-list URLs (`:206`) using a local
   `_dedup_key` closure (`:194–198`) that falls back to the raw string on `ValueError`;
2. calls `self._expand_sitemaps(sitemap_urls)` → `List[Tuple[str, Optional[str]]]`
   (`:207`, helper at `:575–586`), which lets `SitemapExpansionError` propagate by design
   (`:184–187`);
3. walks the pairs (`:215–220`) and, for each URL *not* already hand-listed, appends it to
   `link_urls` **and** records `self._sitemap_lastmod_map[url] = lastmod`.

Step 3 fuses two jobs in one loop: growing the crawl set, and building the lastmod map.
The comment at `:208–214` records why the map is populated *inside* the dedup branch — a
page that is both hand-listed and in a sitemap belongs to the hand-list, and the
`incremental-reingest` spec says a hand-listed source's `last_modified` is `NULL`. Building
the map from every expanded pair instead would hand that page a timestamp, because
`_handle_standard_url` looks the map up by the resource's *normalized* URL.

`schedule_collect_links` (`:298–313`) queries the catalog for `source_type="web"` URLs and
calls `collect_links` with them. It never touches sitemaps, so the map it scrapes under is
whatever startup left behind. `_handle_standard_url` (`:673`) reads it via
`getattr(self, "_sitemap_lastmod_map", {})` (`:693`) and stamps
`resource.metadata["last_modified"]` **only when the map is non-empty**, per-resource, by
normalized URL. The scheduler calls in at `src/bin/service_data_manager.py:89`.

`SitemapExpansionError` is defined at `sitemap_source.py:61` and raised for below-floor
(`:513`) and over-cap (`:526`) sources.

## Goals / Non-Goals

**Goals**: a scheduled collection scrapes under a map rebuilt from the sitemaps *now*; a
failed refresh degrades to the previous map with a warning instead of failing the
collection or blanking the map; initial ingest still fails fast; the hand-list exclusion
survives the refactor; no change to which pages any path fetches; ≥80% diff coverage.

**Non-Goals**: TTL/ETag-based refresh cadence; any change to `_expand_sitemaps` or to
`sitemap_source`; changing the initial-ingest error contract; growing the *scheduled*
crawl set with pages newly discovered in a sitemap; any `deploy/`, `config/`, or schema
change; backfilling rows.

## Decisions

### D1 — One helper with one behavior; the strict-vs-degraded policy lives at the call site
Extract steps 1–3 into `_refresh_sitemap_lastmod_map(...)` that **always propagates**
`SitemapExpansionError`, and let each caller choose the policy:

- `collect_all_from_config` calls it bare → the error propagates and fails the ingest,
  exactly as `:184–187` documents today.
- `schedule_collect_links` wraps the call in `try/except SitemapExpansionError` → logs a
  warning and continues.

Rejected: a `strict: bool` parameter on the helper. A boolean that silently flips whether
a corpus-integrity error is fatal is the kind of flag that gets passed wrong once and
degrades the ingest path forever; the two policies are two lines at two call sites, and
this way the helper has one contract to test.

### D2 — The helper refreshes the map and *returns* newly-discovered URLs; it never mutates the crawl set
Signature:

```python
def _refresh_sitemap_lastmod_map(
    self, sitemap_urls: List[str], existing_keys: Set[str]
) -> List[str]:
```

It expands, walks the pairs in order, skips URLs already in `existing_keys` (mutating that
set as the current loop does, so the caller's dedup state stays correct), publishes the new
map, and returns the not-already-seen page URLs **in order**.

- `collect_all_from_config` does `link_urls.extend(self._refresh_sitemap_lastmod_map(...))`
  — byte-for-byte the same crawl set as today.
- `schedule_collect_links` **discards** the return value. Its crawl set stays exactly the
  catalog query's result (Non-Goal above): a page that appeared in the sitemap since
  startup gets a map entry but is not scraped tonight. Harmless — the map is only ever
  consulted for a resource that *is* being scraped — and it keeps this change to the
  timestamp bug the issue actually reports.

This is what un-fuses the two jobs of the `:215–220` loop without duplicating the dedup
rule in two places.

### D3 — Publish the map atomically: build a local dict, assign to `self` only on success
The helper builds into a local `Dict[str, str]` and assigns `self._sitemap_lastmod_map`
once, after expansion has fully succeeded. It must **not** clear `self._sitemap_lastmod_map`
up front and populate it in place.

This is the decision the degraded path depends on. `_handle_standard_url:693` stamps
`last_modified` only `if lastmod_map:` — so an in-place rebuild that raised partway through
would leave an empty or half-filled map, and the scheduled collection would proceed to
re-persist every page with **no** timestamp at all. That is strictly worse than the stale
value this change exists to fix, and nothing would report it. Assigning once means the
`except` branch in `schedule_collect_links` has a genuinely intact previous map to fall
back to, with no restore logic.

### D4 — The scheduled path re-derives the hand-list exclusion keys from `self.input_lists`
`schedule_collect_links` needs both the sitemap source URLs and the hand-list keys. Both
come from re-running the existing derivation:

```python
link_urls, _, _, _, _, sitemap_urls = self._collect_urls_from_lists_by_type(self.input_lists)
existing_keys = {_dedup_key(u) for u in link_urls}
```

`self.input_lists` is set in `__init__` (`:142`) and `_collect_urls_from_lists`
(`:520–534`) reads local files under `weblists/`, warning-and-skipping a missing one — so
re-deriving is cheap, network-free, and cannot raise. No new instance state is needed
(the issue's plan step 4 asked this be verified: it holds).

Consequence to keep in mind: the scheduled map is filtered by the hand-list as it is
*configured now*, not as it was at startup — which is the intent. Promote a page from a
hand-list to a sitemap and its next scheduled scrape starts carrying a timestamp; do the
reverse and it stops. Both match what a fresh ingest would do with the same config, which
is the property that makes the two paths consistent.

`_dedup_key` is currently a closure inside `collect_all_from_config` (`:194–198`). Lift it
to a module-level function (or a `@staticmethod`) so both call sites share one definition
and the normalize-or-fall-back-to-raw rule cannot drift between them.

### D5 — A wholesale map replacement is the point, including for pages that left the sitemap
The refresh **replaces** the map rather than merging into it. A page dropped from the
sitemap between startup and a scheduled run therefore has no map entry, so
`_handle_standard_url` stamps nothing, so `upsert_resource` writes `NULL` over that row's
previous `last_modified` (per the `incremental-reingest` upsert contract: absent → `NULL`).

That is the correct direction and is deliberate, not incidental: the page is no longer
sitemap-dated, so we have no change signal for it, and `NULL` is exactly how the future
skip-unchanged logic reads "unknown" — unknown means re-fetch. Merging instead would
preserve a timestamp no sitemap vouches for any more, and a stale-but-confident value is
what makes a skip decision *wrong* rather than merely conservative. A spec scenario pins
this so a later reader does not "fix" it into a merge.

### D6 — One warning line on the degraded path, asserted in tests
The `except` branch logs at `warning` with the exception text — enough for an operator
grepping data-manager logs to see that tonight's scrape ran on a stale map and why. The
test asserts the warning is emitted (via `caplog`), because "fell back silently" and "fell
back loudly" are indistinguishable from the persisted data alone, and the silent version
is the one that hides a sitemap that has been broken for a week.

## Risks

- **Refactor regression on the ingest path.** The extraction must leave
  `collect_all_from_config`'s crawl set and map identical. Mitigation: the existing
  `test_scraper_sitemap_dedup_unaffected.py` and `test_scraper_no_duplication.py` cover the
  dedup/append behavior and must stay green untouched; a new test asserts the initial-ingest
  error still propagates.
- **An extra fetch per scheduled collection.** Bounded by the same policy as ingest and
  degrades to a warning on failure, so the worst case is the behavior we have today.
