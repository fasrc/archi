## Why

A clean re-ingest on the dev deployment spends **~19.5 minutes** in a fully
single-threaded scrape loop (`Scraping documents onto filesystem` 18:01:31 →
`Web scraping was completed successfully` 18:21:05) for 815 catalog documents,
while the *embedding* phase downstream already runs 32-way parallel
(`ThreadPoolExecutor` at `src/data_manager/vectorstore/manager.py:578-581`). The
scrape phase is almost entirely network-bound I/O blocked on `requests.get`, so
the wall-clock cost is idle waiting, not work. Re-ingest latency is now the
slowest step in the KB refresh loop and gates how often the corpus can be
rebuilt.

Raw speed is not the goal — **politeness is a hard constraint**. The 815 docs
concentrate on a handful of hosts (`docs.rc.fas.harvard.edu` contributes 212
pages from a single sitemap, `slurm.schedmd.com` crawls broadly), and
`en.wikipedia.org` already returns 403 to us. A naive global fan-out of 32
concurrent requests would risk rate-limiting or an IP block at our own
documentation host, which is a strictly worse outcome than a slow run.

## What Changes

- Add a **`data_manager.scrape_workers`** config knob (conservative default `8`)
  documented in `src/cli/templates/base-config.yaml`, parsed with the same
  tolerant-fallback pattern as the existing `parallel_workers` knob. This is a
  *separate* knob from the embedding pool — scrape and embed have different safe
  ceilings and must not share one number.
- Add a **`data_manager.scrape_per_host_workers`** knob (conservative default
  `4`) capping concurrent in-flight requests to any single host.
- Replace the sequential `for url in urls:` loop in
  `ScraperManager._collect_links_from_urls`
  (`src/data_manager/collectors/scrapers/scraper_manager.py:346`) with a bounded
  `ThreadPoolExecutor` over **seeds**, gated by a **per-host semaphore**.
- Give each worker its **own `LinkScraper` instance**. The current single shared
  `self.web_scraper` (`scraper_manager.py:100`) mutates instance state
  (`self.visited_urls`, `self.seen_urls`, `self.page_data`) and *resets it at the
  top of every `crawl_iter` call* (`scraper.py:194-196`) — sharing it across
  threads would corrupt every concurrent crawl. See design.md.
- Keep the **selenium/SSO path strictly sequential**. The authenticator is a
  shared, non-thread-safe browser session; only the standard non-selenium link
  path is parallelized.
- Preserve **per-seed fail-open**: one seed raising must not abort the batch, and
  `total_count` is accumulated thread-safely.
- Log a one-line completion summary: seed count, workers, per-host cap, elapsed.
- **No behavior change to the embedding phase, sitemap expansion, git collection,
  elog collection, or Indico collection.** Not a breaking change:
  `scrape_workers: 1` reproduces the exact sequential path.

## Capabilities

### New Capabilities
- `parallel-scraping`: bounded, per-host-polite concurrency for the document
  scrape/fetch phase — worker-pool sizing and configuration, per-host request
  capping, per-worker crawler isolation, sequential selenium/SSO handling,
  per-seed fault isolation, and determinism of the resulting document set with
  respect to the sequential path.

### Modified Capabilities
<!-- None. `ingest-processing` governs persist-time transforms (HTML→Markdown,
     categorization, metadata), not the fetch loop; none of its requirements
     change. -->

## Impact

**Code**
- `src/data_manager/collectors/scrapers/scraper_manager.py` — `__init__` (worker
  config parsing, scraper factory), `_collect_links_from_urls` (pool + per-host
  gating). Kept minimal: the concurrency logic itself lives in a new helper
  module so this large, black-reflow-prone file takes a small diff.
- **New** `src/data_manager/collectors/scrapers/scrape_pool.py` — the per-host
  semaphore registry and the bounded seed-pool executor, unit-testable in
  isolation with injected fakes.
- `src/cli/templates/base-config.yaml` — two new documented knobs.

**Explicitly untouched**
- `src/data_manager/vectorstore/manager.py` (embedding is already parallel).
- `src/data_manager/collectors/scrapers/scraper.py` (`LinkScraper` internals).
- `_collect_sso_from_urls`, `_collect_git_resources`, `_collect_elog_*`,
  `_collect_indico_resources` — all remain sequential.

**Systems / operational**
- Postgres: catalog upserts go through a bounded connection pool
  (`src/utils/connection_pool.py`, min=5 max=20). Default scrape concurrency (8)
  stays well under the pool ceiling; the config documents the relationship so an
  operator raising `scrape_workers` knows to raise the pool max in tandem.
- Outbound request rate to third-party documentation hosts increases up to the
  per-host cap. Default (4/host) is deliberately conservative.

**Dependencies**
- None added. `concurrent.futures` and `threading` are stdlib, and
  `ThreadPoolExecutor` is already the in-repo precedent for this pattern.
