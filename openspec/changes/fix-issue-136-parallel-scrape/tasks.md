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
- [ ] 7.2 Write a test asserting no resource is dropped or duplicated under concurrency (persisted URL multiset has no unexpected repeats).
- [ ] 7.3 Verify the pre-loop sitemap dedup (`collect_all_from_config`, `:125-147`) is unaffected — the pool receives an already-deduped list and does not re-dedup.

## 8. Gate, docs, and PR

- [ ] 8.1 Confirm the embedding phase is untouched: no diff to `src/data_manager/vectorstore/manager.py`, and no diff to `src/data_manager/collectors/scrapers/scraper.py` internals.
- [ ] 8.2 Update `docs/` for the two new user-facing config knobs (project convention: user-facing config changes ship docs in the same change).
- [ ] 8.3 Run `bash scripts/gate.sh` (black 24.10.0 + isort 6.0.1, `pytest tests/unit/`, diff-cover `--fail-under=80` vs `origin/dev`) and confirm exit 0 before every commit. Never `--no-verify`.
- [ ] 8.4 Open a PR against `fasrc/archi:dev` with `closes #136`. No `Co-Authored-By` trailers. Do not merge.
