## 1. Config knobs

- [x] 1.1 Write a failing test in `tests/unit/` asserting `ScraperManager` resolves `scrape_workers` to 8 and `scrape_per_host_workers` to 4 when `data_manager` config omits both.
- [x] 1.2 Write failing tests asserting invalid values (`"many"`, `None`-like junk) fall back to the defaults **and log a warning**, and that `0` / negative values clamp to 1. Mirror the tolerant-parse pattern at `src/data_manager/vectorstore/manager.py:148-160`.
- [x] 1.3 Implement the two-knob parsing in `ScraperManager.__init__` to turn 1.1 and 1.2 green. Keep the diff to `scraper_manager.py` minimal.
- [x] 1.4 Document both knobs in `src/cli/templates/base-config.yaml` next to the existing `parallel_workers` entry (`:212`), including the comment that raising `scrape_workers` above the Postgres pool max (20, `src/utils/connection_pool.py`) requires raising that pool in tandem.
- [x] 1.5 Add a test asserting the embedding `parallel_workers` value and behavior are unchanged when `scrape_workers` is set (no knob cross-talk).

## 2. Per-host semaphore registry (new helper module)

- [x] 2.1 Write a failing test for a `HostLimiter` in `src/data_manager/collectors/scrapers/scrape_pool.py`: N threads acquiring the same host never exceed the cap (assert an observed-peak counter, not wall-clock).
- [x] 2.2 Write a failing test that distinct hosts do not contend — with cap 1 and 4 distinct hosts, all 4 hold slots simultaneously.
- [x] 2.3 Write a failing test that the slot is released when the guarded callable raises, so a later acquirer for that host still proceeds (no leak, no deadlock).
- [x] 2.4 Implement `HostLimiter` (a lock-guarded `dict[str, threading.Semaphore]` plus a context manager keyed by hostname) to turn 2.1–2.3 green.

## 3. Bounded seed pool (new helper module)

- [x] 3.1 Write a failing test for a `run_seeds` function in `scrape_pool.py`: with workers=8 and an injected blocking fake fetch over 8 independent seeds, observed peak concurrency is > 1.
- [x] 3.2 Write a failing test that the pool is bounded — workers=2 over 8 seeds never exceeds 2 in flight.
- [x] 3.3 Write a failing test combining both bounds: workers=8, per-host cap 4, 8 seeds on one host → peak per-host concurrency ≤ 4.
- [x] 3.4 Write a failing test for per-seed fail-open: one of 4 seeds raises → the other 3 still run, the failure is logged, and the returned total is the sum of the 3 successes. Add the all-seeds-raise case returning 0.
- [x] 3.5 Write a failing test that the total is accumulated without loss across many concurrent seeds (exact sum of per-seed counts).
- [x] 3.6 Write a failing test that `workers=1` runs seeds strictly one at a time **in input order** (record call order; peak concurrency is 1).
- [x] 3.7 Implement `run_seeds` (a `ThreadPoolExecutor` bounded by `workers`, each task wrapped in the `HostLimiter`, results summed on the calling thread via `as_completed`) to turn 3.1–3.6 green.

## 4. Per-worker crawler isolation

- [x] 4.1 Write a failing test that two concurrent crawls each yield exactly their own URLs and neither observes the other's `visited_urls`/`seen_urls` reset — the race that a single shared `self.web_scraper` (`scraper_manager.py:100`) would cause against the per-call state reset at `scraper.py:194-196`.
- [x] 4.2 Write a failing test that each per-worker `LinkScraper` is constructed with the manager's own `verify_urls` / `enable_warnings` values.
- [x] 4.3 Implement a scraper-factory seam on `ScraperManager` that returns a fresh `LinkScraper` per seed crawl, and route the parallel path through it. Leave `self.web_scraper` in place for the existing sequential/selenium callers.

## 5. Wire the pool into the standard link path

- [x] 5.1 Write a failing test that `_collect_links_from_urls` dispatches the standard non-selenium path through `run_seeds` with the configured worker and per-host values.
- [x] 5.2 Replace the `for url in urls:` loop at `scraper_manager.py:346` with the `run_seeds` call. Preserve the existing `try/finally` authenticator close and the per-seed `try/except` fail-open already in `_handle_standard_url` (`:589-601`).
- [x] 5.3 Write a failing test then implement the one-line completion summary: seed count, effective workers, effective per-host cap, elapsed wall-clock — logged exactly once after the pool drains.

## 6. Selenium/SSO stays sequential

- [x] 6.1 Write a failing test that a shared authenticator is never used from two threads at once during SSO collection (assert observed peak concurrent authenticator use is 1).
- [x] 6.2 Write a test that the authenticator is closed exactly once per run, including when a seed raises.
- [x] 6.3 Confirm by test that `_collect_sso_from_urls` (`:363-404`) is untouched by the pool and remains a plain sequential loop.

## 7. Determinism guarantee

- [x] 7.1 Write a failing test scraping the same fixture seeds twice — once at `scrape_workers=1`, once at `scrape_workers=8` — asserting the **set of persisted resource URLs is identical** and both return the same total.
- [x] 7.2 Write a test asserting no resource is dropped or duplicated under concurrency (persisted URL multiset has no unexpected repeats).
- [x] 7.3 Verify the pre-loop sitemap dedup (`collect_all_from_config`, `:125-147`) is unaffected — the pool receives an already-deduped list and does not re-dedup.

## 8. Gate, docs, and PR

- [x] 8.1 Confirm the embedding phase is untouched: no diff to `src/data_manager/vectorstore/manager.py`, and no diff to `src/data_manager/collectors/scrapers/scraper.py` internals.
- [x] 8.2 Update `docs/` for the two new user-facing config knobs (project convention: user-facing config changes ship docs in the same change).
- [x] 8.3 Run `bash scripts/gate.sh` (black 24.10.0 + isort 6.0.1, `pytest tests/unit/`, diff-cover `--fail-under=80` vs `origin/dev`) and confirm exit 0 before every commit. Never `--no-verify`.
- [x] 8.4 Open a PR against `fasrc/archi:dev` with `closes #136`. No `Co-Authored-By` trailers. Do not merge.

## 9. Review round 2 (Codex, PR #145)

Eight findings. Six confirmed and fixed, one confirmed and fixed as a stale
comment, one disputed on its premise. Every fix has a failing-first test and a
mutation check (revert the fix, watch the owning test fail, restore).

- [x] 9.1 **Share the host limiter across concurrent batches** (`scrape_pool.py:164`). Confirmed: `service_data_manager.py` runs the cron ingest thread and the uploader's `/document_index/upload_url` handler against one `ScraperManager`, so batches overlap and a per-call limiter lets each spend the per-host budget again. Added a process-wide registry keyed by the effective cap (`shared_host_limiter`) plus a `limiter=` override on `run_seeds`.
- [x] 9.2 **Key redirected crawls by the destination host** (`scrape_pool.py:169`). Confirmed: `requests` follows redirects and `reap` resolves the rest of the crawl against `response.url`, so a seed spends its crawl off its origin host while holding the origin's slot. Added `HostLimiter.rekey_current` (release-then-acquire, so no thread ever holds two host slots and swaps cannot deadlock) and an `on_request_url` callback from `crawl_iter` through `_handle_standard_url`.
- [x] 9.3 **Isolate malformed URL parsing to its seed** (`scrape_pool.py:58`). Confirmed: `urlsplit("http://[broken/path")` raises `ValueError`, and `interleave_by_host` keys every seed on the calling thread before any future exists — outside the per-seed isolation. `host_key` now falls back to the raw seed string.
- [x] 9.4 **Serialize persistence for overlapping seed crawls** (`scraper_manager.py:418`). Confirmed: `ScrapedResource.get_hash()` is `md5(url)` and the filename derives from it, so overlapping seed graphs give two workers the same path and row; `persist_resource` is an unsynchronised exists/write/stat/upsert over that. Added a per-resource-hash lock in `PersistenceService`.
- [x] 9.5 **Fall back on non-finite worker settings** (`scraper_manager.py:23`). Confirmed: `int(float("inf"))` raises `OverflowError`, not `ValueError`. Fixed the class, not the instance — `_parse_worker_knob`, the `max_pages` parse, and `_expand_sitemaps._as_int` all now share `_COERCION_ERRORS`.
- [x] 9.6 **Correct the generated config's database tuning guidance** (`base-config.yaml:219`). Confirmed: the template still told operators to raise `src/utils/connection_pool.py`, which the scrape write path never uses. Replaced with the `max_connections` guidance already in `docs/docs/configuration.md`, and added a test that fails if the two artifacts drift apart again.
- [x] 9.7 **Preserve zero-valued scrape limits during rendering** (`base-config.yaml:221`). Confirmed: `default(x, true)` replaces every falsey value, so `scrape_workers: 0` rendered as `8`. Switched both knobs to the undefined-only form already used by `min_pages`.
- [x] 9.8 **Avoid PEP 585 annotations in Python 3.7 tests** — **disputed on the premise**, no change. The floor in `pyproject.toml` / `AGENTS.md` is stale: `src/bin/service_benchmark.py` uses `match` statements (3.10+), several pinned dependencies require >= 3.9, and CI pins 3.11. There is no environment in which collection fails, and eight files repo-wide use the same annotation form. Recorded as a follow-up to correct `requires-python` rather than patching two of the eight.
