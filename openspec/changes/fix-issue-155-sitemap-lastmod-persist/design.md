## Context

`sitemap_source.py` parses a `<urlset>`/`<sitemapindex>` and, in `_locs()`, reads the
first `<loc>` of each `<url>`/`<sitemap>` wrapper — then `break`s, so any sibling
`<lastmod>` is never read. `parse_sitemap_document()` returns `Tuple[str, List[str]]`
(`(root_kind, loc_values)`); `expand_sitemap_source()`/`expand_sitemaps()` normalize,
trust-filter, dedupe, and floor/cap those locs into a flat `List[str]` of page URLs.
`ScraperManager._expand_sitemaps()` (`scraper_manager.py:482`) is the thin call site that
builds the policy + fetch and delegates; its result is merged into `link_urls` and scraped
generically (`scraper_manager.py:143`).

Persistence flows resource → `PersistenceService.persist_resource` → `metadata_dict`
(built from `resource.get_metadata()`, `persistence.py:47-71`) →
`catalog.upsert_resource(hash, path, metadata_dict)`. In `catalog_postgres.upsert_resource`
(`:174`), keys present in module-level `_METADATA_COLUMN_MAP` (`:49`) are promoted to real
`documents` columns; everything else is folded into `extra_json`. So the metadata→column
plumbing already exists — the signal just needs a column and a map entry.

Two facts bound the blast radius:
- `_locs`/`parse_sitemap_document` have **external callers**: `sources_builder.py`
  (`_locs` ×3, at :281/:285/:291) and `goldenset_maintenance.py`
  (`parse_sitemap_document` :534, `expand_sitemaps` :582, both imported :52/:55).
- There are **two** independent catalog backends, each with its OWN
  `_METADATA_COLUMN_MAP`: Postgres `documents` (`catalog_postgres.py:49`) and SQLite
  `resources` (`index_utils.py:37`). Adding to one does not touch the other.

## Goals / Non-Goals

**Goals**: capture the per-entry `<lastmod>`; carry it per emitted URL through expansion;
add a nullable `documents.last_modified` column (fresh schema + idempotent migration);
store it on upsert; populate it for sitemap-derived pages in a real ingest — all with zero
change to fetch behavior and ≥80% diff coverage.

**Non-Goals**: any fetch-skip logic (PR-2), the git `source_ref` skip (PR-3), the
`min_pages` interaction (PR-2), the SQLite `resources` backend, and any backfill of
existing rows.

## Decisions

### D1 — Capture without breaking existing parse callers
Extend the parser to yield `(loc, lastmod|None)` per entry via a **new lastmod-aware
helper** rather than repurposing `_locs`. Keep `_locs(root, wrapper) -> List[str]`
byte-for-byte so `sources_builder.py`'s three call sites are untouched. Add a sibling
(e.g. `_loc_entries(root, wrapper) -> List[Tuple[str, Optional[str]]]`) that reads the
first `<loc>` and the first `<lastmod>` in each wrapper, and expose the captured signal
through `parse_sitemap_document` **without changing its existing return contract for
current callers**. Two acceptable shapes — pick the one that keeps `validate`/gate green
with the smallest diff:
- (preferred) add a parallel function `parse_sitemap_entries(text) -> Tuple[str,
  List[Tuple[str, Optional[str]]]]` used by the expander, leaving
  `parse_sitemap_document` and its `goldenset_maintenance` caller as-is; or
- change `parse_sitemap_document` to return entries and update **every** caller
  (`_fetch_and_parse`, `goldenset_maintenance.py:534`) plus a back-compat accessor.

The hard constraint (spec: "Existing sitemap parse callers keep their current behavior")
is that `sources_builder` and `goldenset` emit the identical URL set afterward. `<lastmod>`
is captured as raw trimmed text (no date parsing) — validation/coercion is PR-2's concern
when it becomes a comparison input.

### D2 — Carry lastmod through expansion, first-seen wins on dedupe
`expand_sitemap_source`/`expand_sitemaps` associate `lastmod` with each emitted normalized
URL. The natural carrier is a `List[Tuple[str, Optional[str]]]` (URL, lastmod) preserving
the existing order; the trust filter, floor, and cap operate on the URL exactly as today.
On a normalization collision (two entries → same URL), the **first occurrence wins** — its
URL is emitted once with its own lastmod, the duplicate dropped — matching the existing
`seen`-set dedupe in both functions and in `scraper_manager`'s cross-seed dedupe
(`:143`). `goldenset_maintenance.py:582`'s `expand_sitemaps` call must be updated to the
new shape while preserving its URL-set behavior (it consumes URLs; it can ignore lastmod).

### D3 — Persist via the existing metadata→column path
Add `last_modified TIMESTAMPTZ` (nullable) to `documents`:
- **fresh installs**: a line in `src/cli/templates/init.sql`'s `documents` DDL (Timestamps
  block, next to `file_modified_at`);
- **existing DBs**: a new forward-only migration `src/cli/templates/migrations/
  add_documents_last_modified.sql` using `ALTER TABLE documents ADD COLUMN IF NOT EXISTS
  last_modified TIMESTAMPTZ;` — idempotent, no backfill.

Add `"last_modified": "last_modified"` to `catalog_postgres._METADATA_COLUMN_MAP` and add
the column to the INSERT column list, the `VALUES` tuple, and the `ON CONFLICT DO UPDATE
SET` clause (`= EXCLUDED.last_modified`). Then any resource whose metadata carries
`last_modified` stores it; absent → the column is `NULL`. The SQLite `index_utils` backend
and its map are deliberately left unchanged (Non-Goal); its INSERT never references the new
column, so it is unaffected.

### D4 — Bridge: sitemap lastmod → resource metadata (the enabler's payoff)
For the stored value to appear in a real ingest, the page's captured lastmod must reach
`persist_resource`'s `metadata_dict`. `ScraperManager` builds a `{normalized_url:
lastmod}` map from `_expand_sitemaps` (now returning pairs) and, when a sitemap-derived
page is persisted, injects `last_modified` into that resource's metadata (so it flows
through the existing `_normalise_metadata` → `upsert_resource` → `_METADATA_COLUMN_MAP`
path). Only pages that came from a sitemap AND had a `<lastmod>` get a value; hand-listed
URLs and lastmod-less pages stay `NULL`. This adds a stored attribute only — no page is
fetched, skipped, or reordered, satisfying "Fetch behavior is unchanged". The exact
injection seam (map held on the manager vs. threaded into `collect_links`) is Ralph's
test-first call; the conservative default is a manager-scoped map keyed by the same
normalized URL used for cross-seed dedupe, so lookup and dedupe agree.

## Risks / Trade-offs

- **Return-type ripple** (D1/D2): the mitigation is the back-compat helper + a spec
  requirement asserting `sources_builder`/`goldenset` URL sets are unchanged; a regression
  test over an existing caller guards it.
- **Bridge testability** (D4): the URL→resource association is the least unit-pure step.
  Mitigate by unit-testing the map construction and metadata injection in isolation (fake
  persistence), not through a live scrape; keep the seam small.
- **Self-reported signal**: `<lastmod>` is advertiser-controlled. Acceptable here because
  PR-1 only records it; PR-2 uses it only as a *conservative* skip (skip only when a stored
  value exists AND sitemap lastmod ≤ it), so a stale/absent value can only cause a re-fetch,
  never a wrongful skip.

## Migration Plan

Forward-only, additive, reversible by dropping a nullable column. `ADD COLUMN IF NOT
EXISTS` makes the migration safe to re-run and safe on a DB already carrying the column
from a fresh `init.sql`. No data backfill; pre-existing rows read `NULL`. No rollback
script needed for PR-1 (nothing reads the column yet).
