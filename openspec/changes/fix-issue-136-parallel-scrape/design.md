## Context

The document scrape phase is the slowest step in an archi re-ingest. On dev it
took ~19.5 min single-threaded for 815 documents, while the downstream embedding
phase already runs 32-way parallel via `ThreadPoolExecutor`
(`src/data_manager/vectorstore/manager.py:578-581`). The work is network-bound
`requests.get`, so nearly all of that wall-clock is idle waiting.

**Current shape of the code (verified on `origin/dev` @ `be2d164c`):**

- `ScraperManager.collect_all_from_config`
  (`scraper_manager.py:109-154`) expands `sitemap-` sources and dedups them
  against normalized hand-list keys (`:125-147`) **before** calling
  `collect_links`. So the loop we are parallelizing receives an already-deduped
  seed list — concurrency cannot affect dedup correctness.
- `ScraperManager._collect_links_from_urls` (`:329-361`) is the sequential
  bottleneck: `for url in urls:` at `:346`, calling `_handle_standard_url`
  (`:578-602`) per seed.
- `_handle_standard_url` already swallows per-seed exceptions
  (`try/except Exception` at `:589-601`, logs and returns whatever count it got),
  so **fail-open is an existing property we must preserve, not invent**.
- `ScraperManager.__init__` builds **one** `LinkScraper` and stores it as
  `self.web_scraper` (`:100-105`).
- `PersistenceService.persist_resource` (`collectors/persistence.py:24-72`)
  writes a file then calls `self.catalog.upsert_resource`, which opens a
  connection per call (`catalog_postgres.py:197`) from the bounded pool
  (`src/utils/connection_pool.py`, min=5 max=20).

**Constraint that dominates the design:** politeness. 815 docs concentrate on a
few hosts — `docs.rc.fas.harvard.edu` alone contributes 212 sitemap pages, and
`en.wikipedia.org` already 403s us. Getting rate-limited or IP-blocked at our own
documentation host is a strictly worse outcome than a slow ingest.

## Goals / Non-Goals

**Goals:**

- Cut scrape wall-clock by fetching independent seeds concurrently.
- Bound concurrency twice: globally (`scrape_workers`) and per host
  (`scrape_per_host_workers`), with conservative defaults.
- Guarantee the resulting document set is byte-for-byte the same set as the
  sequential path — only wall-clock changes.
- Keep the diff to `scraper_manager.py` small; put the new logic in a fresh,
  directly unit-testable helper module.
- Make `scrape_workers: 1` an exact reproduction of today's behavior, so the
  change is trivially revertible by config without a redeploy of code.

**Non-Goals:**

- Touching the embedding phase. It is already parallel and out of scope.
- Parallelizing *within* a single crawl's link discovery (see Decision 2).
- Parallelizing git, elog, Indico, or SSO/selenium collection.
- Async/`aiohttp` rewrite of the scraper (see Decision 1, alternatives).
- Skipping unchanged sources — that is sibling issue #135, which composes with
  this one but is separately scoped.

## Decisions

### Decision 1: Thread pool over seeds, not async I/O

Use `concurrent.futures.ThreadPoolExecutor` over the seed list.

*Why:* the workload is blocking `requests.get` inside a deeply nested generator
(`LinkScraper.crawl_iter`) that also does BeautifulSoup parsing and Selenium
handoff. Threads parallelize it with no rewrite of the crawl logic, and
`ThreadPoolExecutor` is already the established in-repo pattern for exactly this
(the embedding pool at `manager.py:578-581`), so operators and reviewers read one
idiom, not two.

*Alternatives considered:*
- **`asyncio` + `aiohttp`** — theoretically better for thousands of sockets, but
  requires rewriting `crawl_iter`, `reap`, and the selenium interop as coroutines.
  Enormous diff on the two files most prone to black-reflow churn, for a workload
  that peaks at 8 concurrent sockets. Rejected as disproportionate.
- **`multiprocessing`** — pointless for I/O-bound work, and would fracture the
  Postgres connection pool and the shared catalog across process boundaries.

### Decision 2: Per-seed is the concurrency grain

Parallelize across seed URLs; each seed's crawl stays internally sequential.

*Why:* it is the only race-free grain available without touching `LinkScraper`.
`crawl_iter` maintains a frontier (`to_visit`), a `seen_urls` set, and a
`pages_visited` counter that must be consistent for the `max_pages` cap and the
same-hostname link filter to mean anything. Parallelizing inside a crawl would
require making all of that thread-safe, in the file we most want to leave alone.
Across seeds, after the pre-loop dedup, the crawls are independent.

*Trade-off:* a source list dominated by one very large crawl (e.g. a deep
`slurm.schedmd.com` seed) sees little speedup, because that one seed is a single
serial unit. Accepted — the dev corpus is 152 seed lines, so there is ample
seed-level parallelism, and the per-host cap would have throttled a single-host
deep crawl anyway.

### Decision 3: One `LinkScraper` per worker — **corrects the issue's premise**

Each concurrent seed crawl gets its own `LinkScraper` instance, created by a
small factory on the manager that mirrors the `__init__` construction args.

*Why this is load-bearing:* issue #136 states that because `crawl_iter` resets its
`seen`/`pages_visited` state per seed, "per-seed parallelism is the natural,
race-free grain." That is half right. Per-seed **is** the right grain, but the
reset is on **instance** state of a **single shared** object:

```
# scraper_manager.py:100   — ONE instance for the whole manager
self.web_scraper = LinkScraper(verify_urls=..., enable_warnings=...)

# scraper.py:194-196       — reset at the TOP of every crawl_iter call
self.visited_urls = set()
self.seen_urls    = set()
self.page_data    = []
```

Handing that shared instance to N threads makes the per-seed reset the *cause* of
the race rather than a defense against it: seed B entering `crawl_iter` wipes
seed A's in-flight `seen_urls`/`visited_urls`, so A re-visits pages it already
took, loses its dedup frontier, and can exceed or misapply `max_pages`. That is a
silent determinism violation — the exact failure this change forbids, and the
kind that would pass a naive "did it get faster" check. Per-worker instances make
the isolation real. `LinkScraper.__init__` is trivial (two flags, two empty
sets), so instantiating one per seed is free.

*Alternative considered:* a `threading.local()` holding one scraper per thread.
Equivalent isolation and marginally fewer allocations, but it keeps state alive
across unrelated seeds on a recycled worker thread and is harder to assert on in
a test. Per-seed construction is simpler and provably clean.

### Decision 4: Per-host cap via a semaphore registry, keyed by seed hostname

A small helper owns a `dict[str, threading.Semaphore]` guarded by a lock; a
worker acquires its host's semaphore for the duration of its seed crawl.

*Why:* the global worker bound alone does not protect any individual host — 8
workers could all land on `docs.rc.fas.harvard.edu`. The semaphore is the minimal
mechanism that makes "at most 4 in flight to this host" a structural guarantee
rather than a statistical hope.

*Keying on the seed's hostname, not each fetched URL's:* `crawl_iter` already
restricts link discovery to the same hostname as the seed
(`get_links_with_same_hostname`), so a seed's fetches are all to one host and
gating at seed granularity is both sufficient and far simpler — it requires zero
changes inside `LinkScraper`. The cost is coarseness: a seed holds its host slot
for its whole crawl, not per request. Given a 4-per-host cap that is the
conservative direction, which is what we want.

*Deadlock safety:* each worker acquires exactly one semaphore and holds no other
lock while blocking on it, so there is no acquisition ordering and no cycle. The
acquire/release is wrapped so a raising crawl always releases.

### Decision 5: New helper module, not inline in `scraper_manager.py`

The pool + semaphore logic lands in a new
`src/data_manager/collectors/scrapers/scrape_pool.py`, exposing a small
seed-runner that takes a callable and a seed list. `scraper_manager.py` changes
only by parsing two config values, adding a scraper factory, and swapping its
`for` loop for a call into the helper.

*Why:* two reasons, both concrete. (a) `scraper_manager.py` and `scraper.py` are
large and reflow under black — a big inline diff there risks the diff-coverage
churn trap (reformatted-but-untested lines counting against the ≥80% gate), which
is exactly the hazard the `black-seam-scout` guidance exists to avoid. (b) A
standalone module with an injected fetch callable is directly unit-testable for
peak-concurrency assertions without constructing a `ScraperManager`, a
`PersistenceService`, or a Postgres connection.

### Decision 6: Two knobs, defaults 8 and 4, not one reused knob

`data_manager.scrape_workers` (default 8) and
`data_manager.scrape_per_host_workers` (default 4), parsed with the same
tolerant-fallback-and-clamp pattern as `parallel_workers`
(`manager.py:148-160`).

*Why not reuse `parallel_workers` (32):* embedding at 32-way talks to our own
GPU/embedding endpoint; scraping at 32-way talks to third parties who will
throttle or ban us. They are different resources with different safe ceilings and
must be tunable independently.

*Why 8:* it stays comfortably under the Postgres pool ceiling of 20 connections
even in the worst case where every worker is mid-upsert, so this change cannot by
itself exhaust the pool. The `base-config.yaml` comment documents that
relationship so an operator raising `scrape_workers` knows to raise the pool max
in tandem. Expected speedup at 8 workers with a 4/host cap on the dev corpus is
roughly 4–6×, i.e. ~19.5 min → ~3–5 min.

### Decision 7: Thread-safe accumulation and preserved fail-open

Collect per-seed counts as futures resolve and sum them on the calling thread
(via `as_completed`), rather than mutating a shared `total_count` from workers.

*Why:* `total_count += count` is a read-modify-write and is not atomic. Summing
on one thread sidesteps the question entirely and needs no lock. Fail-open is
preserved by keeping the existing `try/except` inside `_handle_standard_url` and
additionally guarding future resolution, so a seed that raises contributes 0
without cancelling the batch.

## Risks / Trade-offs

- **Rate-limiting or IP block at a documentation host** → Conservative defaults
  (8 global / 4 per host, vs. the embedding pool's 32), a structural per-host
  semaphore rather than best-effort pacing, and `scrape_workers: 1` as a
  config-only kill switch requiring no code redeploy.
- **Postgres connection pool exhaustion** (max 20) → Default 8 workers cannot
  exceed it; the config comment ties the two knobs together for operators who
  raise it. Catalog upserts already open and return a connection per call.
- **Silent nondeterminism from shared crawler state** → Addressed structurally by
  Decision 3 (per-worker instances) and verified by an explicit
  workers=1-vs-workers=8 identical-URL-set test, not just a timing test.
- **Timing-based tests are flaky on loaded CI** → Assert on *observed peak
  concurrency* (a counter incremented/decremented around an injected fake fetch,
  tracking its max) as the primary signal. Any wall-clock assertion uses a wide
  margin and is secondary, never the sole proof.
- **Little speedup on single-large-crawl source lists** → Accepted (Decision 2).
  Sibling issue #135 (skip unchanged sources) attacks that case from the other
  direction and composes with this change.
- **Interleaved per-seed log lines become harder to read** → The existing
  `Scraped N resources from <url>` line already names its URL, so lines stay
  attributable; the new one-line summary gives the aggregate view.

## Migration Plan

No data migration and no schema change. The new knobs are additive with
defaults, so an existing deployment picks up parallel scraping on its next
re-ingest with no config edit. Rollback is `scrape_workers: 1` in config, which
restores the exact sequential path without redeploying code.

## Open Questions

None blocking. Two items to confirm empirically **after** merge, on the dev
deployment — neither gates this change, and both are observation-only:

1. Actual speedup at the default 8/4 on the real 152-seed source list (predicted
   ~4–6×).
2. Whether `docs.rc.fas.harvard.edu` tolerates 4 concurrent fetches comfortably,
   or whether the per-host default should drop to 2.
