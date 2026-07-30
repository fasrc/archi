## Why

`docs.rc.fas.harvard.edu` 301-redirects the no-slash URL form to the trailing-slash
form (`/kb/x` → `/kb/x/`). The web crawler's URL normalizer keeps the two forms
distinct, so both are fetched, stored, and embedded — each with a different
`resource_hash`, which defeats the existing hash/URL dedup. On the dev corpus
(2026-07-17) this produced ~181 duplicate canonical page groups and ~1,161 redundant
chunks that pollute retrieval. The scraper-side canonicalization is the only durable
fix (a re-ingest regenerates both slash forms).

## What Changes

- `_normalize_url` in the web scraper canonicalizes the URL **path** by stripping a
  trailing slash, so `…/kb/x` and `…/kb/x/` normalize to one identical string.
- The site-root lone `/` (e.g. `https://host/`) is preserved — it is never stripped to
  an empty path.
- Query/params handling is pinned: the trailing slash is stripped from the path only,
  leaving query string and params intact and consistent (`…/x/?a=1` and `…/x?a=1`
  collapse to the same normalized string).
- Because `_normalize_url` feeds the crawl frontier (`visited_urls`/`seen_urls` via
  `_mark_visited`) and `get_links_with_same_hostname`, this dedups both what the crawler
  visits and what is ultimately stored/embedded.

## Capabilities

### New Capabilities
- `web-crawl-url-canonicalization`: The web scraper's crawl-time URL normalizer canonically
  collapses trailing-slash and no-slash variants of the same path to one form, while
  preserving the site root, so redirect-driven slash twins are not crawled or stored twice.

### Modified Capabilities
<!-- None: ingest-processing governs content processing (HTML→Markdown, categorization,
     dedup-across-conversion), not crawl-time URL normalization. This is a new,
     scraper-level capability. -->

## Impact

- **Code:** `src/data_manager/collectors/scrapers/scraper.py` — `_normalize_url`
  (~line 302). No signature change; a pure-function behavior change.
- **Tests:** new unit test in `tests/unit/` exercising `_normalize_url` (pure/unit-testable,
  so the new branch is measured by diff-cover — must be covered ≥80%).
- **Corpus (deploy-time, out of scope for this change's code gate):** after this lands, a
  dev redeploy re-ingest collapses the slash twins, changing retrieval results. This is a
  corpus change: it must land before any RAGAS baseline is locked, and a post-change run
  must not be compared to a pre-change one.
- **No** change to config schema, CLI, providers, or the persistence/embedding path.
