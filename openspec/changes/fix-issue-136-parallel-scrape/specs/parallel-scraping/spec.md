## ADDED Requirements

### Requirement: Seed-level concurrency in the standard link scrape phase

The system SHALL fetch standard (non-selenium) link seeds concurrently using a
bounded worker pool sized by `data_manager.scrape_workers`, rather than one seed
at a time. Concurrency SHALL be at the granularity of a **seed URL**; the link
discovery *within* a single seed's crawl SHALL remain sequential.

#### Scenario: Independent seeds are fetched in parallel

- **WHEN** `scrape_workers` is 8 and 8 independent seeds are scraped, each backed by an injected fake fetch that blocks for a fixed interval
- **THEN** the maximum number of fetches observed in flight at one time is greater than 1, and total elapsed time is materially less than the sequential sum of the per-seed intervals

#### Scenario: Worker pool is bounded

- **WHEN** `scrape_workers` is 2 and 8 seeds are scraped with an injected blocking fake fetch
- **THEN** the maximum number of seed crawls observed in flight at one time never exceeds 2

#### Scenario: A single seed's crawl is not internally parallelized

- **WHEN** one seed expands to multiple discovered links during its crawl
- **THEN** those links are visited sequentially within that seed's crawl, and the peak in-flight fetch count attributable to that single seed is 1

### Requirement: Per-host concurrency cap

The system SHALL cap the number of concurrent in-flight requests to any single
host at `data_manager.scrape_per_host_workers`, independently of and in addition
to the global `scrape_workers` bound. Hosts SHALL be identified by the network
location (hostname) of the seed URL. The cap SHALL be enforced even when the
global worker pool has idle capacity.

#### Scenario: Many seeds on one host respect the cap

- **WHEN** `scrape_workers` is 8, `scrape_per_host_workers` is 4, and 8 seeds all targeting `docs.example.edu` are scraped with an injected blocking fake fetch
- **THEN** the peak number of concurrent fetches observed for `docs.example.edu` is at most 4

#### Scenario: Distinct hosts do not contend with each other

- **WHEN** `scrape_workers` is 8, `scrape_per_host_workers` is 1, and 4 seeds each targeting a distinct host are scraped
- **THEN** all 4 seeds are in flight concurrently, because the per-host cap constrains each host separately

#### Scenario: Per-host cap does not deadlock a saturated host

- **WHEN** more seeds target a single host than the per-host cap allows
- **THEN** every seed is still eventually scraped, and the returned resource count includes all of them

### Requirement: Each worker uses its own crawler instance

The system SHALL provide each concurrent seed crawl with its own `LinkScraper`
instance. A single `LinkScraper` SHALL NOT be shared across concurrent crawls,
because `crawl_iter` resets and mutates per-instance state (`visited_urls`,
`seen_urls`, `page_data`) for the duration of a crawl. Each instance SHALL be
constructed with the same configuration (`verify_urls`, `enable_warnings`) that
the shared sequential scraper uses today.

#### Scenario: Concurrent crawls do not share mutable crawler state

- **WHEN** two seeds are crawled concurrently and each visits a distinct set of URLs
- **THEN** each crawl yields exactly its own URLs, and neither crawl's visited/seen state is reset or observed by the other

#### Scenario: Per-worker scrapers inherit configuration

- **WHEN** the manager is configured with `verify_urls` false and `enable_warnings` false
- **THEN** every per-worker `LinkScraper` created for the pool is constructed with those same values

### Requirement: The selenium/SSO path remains sequential

The system SHALL NOT parallelize scrape paths that use a selenium authenticator.
The shared browser session is not thread-safe, so SSO collection and any
selenium-backed standard collection SHALL continue to run one URL at a time
against a single authenticator instance.

#### Scenario: SSO collection never uses the pool

- **WHEN** SSO URLs are collected via a single shared authenticator
- **THEN** the authenticator is never invoked from two threads at once, and the maximum observed concurrent use of the authenticator is 1

#### Scenario: Authenticator lifecycle is preserved

- **WHEN** a selenium authenticator is created for a collection run
- **THEN** it is closed exactly once when the run finishes, including when a seed raises

### Requirement: Determinism with respect to the sequential path

The system SHALL produce an identical set of scraped resources regardless of
concurrency settings. For the same seed inputs, the set of scraped resource URLs
and the returned total count SHALL be identical between a sequential run
(`scrape_workers: 1`) and a parallel run (`scrape_workers: 8`). No resource SHALL
be dropped or duplicated under concurrency.

#### Scenario: Parallel and sequential runs agree

- **WHEN** the same fixture seeds are scraped once with `scrape_workers` 1 and once with `scrape_workers` 8
- **THEN** the set of persisted resource URLs is identical between the two runs and both return the same total count

#### Scenario: A worker count of 1 reproduces the sequential path

- **WHEN** `scrape_workers` is 1
- **THEN** seeds are scraped one at a time in their input order, and the result is identical to the pre-change sequential implementation

#### Scenario: Total count is accumulated without loss

- **WHEN** many seeds complete concurrently, each contributing a nonzero resource count
- **THEN** the returned total equals the exact sum of the per-seed counts

### Requirement: Per-seed fault isolation

The system SHALL isolate failures to the seed that caused them. An exception
raised while scraping one seed SHALL NOT abort the batch, SHALL be logged, and
SHALL contribute zero to the total count while every other seed is still scraped
and counted.

#### Scenario: One failing seed does not abort the batch

- **WHEN** 4 seeds are scraped concurrently and one raises an exception
- **THEN** the other 3 are scraped successfully, the failure is logged, and the returned total equals the sum of the 3 successful seeds' counts

#### Scenario: Every seed failing still returns cleanly

- **WHEN** every seed raises an exception
- **THEN** the call returns 0 rather than propagating, and each failure is logged

### Requirement: Scrape concurrency configuration

The system SHALL read `data_manager.scrape_workers` and
`data_manager.scrape_per_host_workers` from configuration, defaulting to 8 and 4
respectively when unset. Both SHALL be coerced to integers, SHALL fall back to
their defaults with a logged warning when the configured value is not a valid
integer, and SHALL be clamped to a minimum of 1. These knobs SHALL be documented
in `src/cli/templates/base-config.yaml` and SHALL be independent of the existing
embedding-phase `data_manager.parallel_workers` knob.

#### Scenario: Defaults apply when unset

- **WHEN** neither knob is present in configuration
- **THEN** `scrape_workers` resolves to 8 and `scrape_per_host_workers` resolves to 4

#### Scenario: Invalid values fall back with a warning

- **WHEN** `scrape_workers` is configured as the string `"many"`
- **THEN** a warning is logged and the effective value is the default of 8

#### Scenario: Values are clamped to at least one

- **WHEN** `scrape_workers` is configured as 0 or a negative number
- **THEN** the effective value is 1

#### Scenario: Embedding concurrency is unaffected

- **WHEN** `scrape_workers` is set to any value
- **THEN** the embedding phase's `parallel_workers` setting and behavior are unchanged

### Requirement: Scrape phase completion summary

The system SHALL log a single summary line when the parallel link scrape phase
completes, reporting the number of seeds processed, the effective worker count,
the effective per-host cap, and the elapsed wall-clock time.

#### Scenario: Summary is emitted after the pool drains

- **WHEN** a link scrape run over N seeds finishes
- **THEN** exactly one summary line is logged containing the seed count, worker count, per-host cap, and elapsed time
