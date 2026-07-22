## ADDED Requirements

### Requirement: Sitemap source prefix recognition

The ingestion source-list parser SHALL recognize lines beginning with the `sitemap-` prefix and route the remainder of the line (the sitemap URL) to sitemap expansion. Prefix routing MUST remain pure list-parsing: recognizing a `sitemap-` line SHALL NOT itself perform any network I/O. The explicit `sitemap-` prefix MUST take precedence over the elog/indico URL auto-detection heuristics.

#### Scenario: Sitemap line peeled into the sitemap bucket

- **WHEN** an input list contains the line `sitemap-https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml`
- **THEN** the URL `https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml` (prefix stripped) is routed to sitemap expansion
- **AND** it is not routed to the plain-link, git, sso, elog, or indico buckets
- **AND** no network request is made during list parsing

#### Scenario: Explicit prefix beats auto-detection

- **WHEN** an input list contains a `sitemap-` line whose URL path contains `/elog/`
- **THEN** the URL is routed to sitemap expansion, not to the elog bucket

### Requirement: Non-sitemap lines are unaffected

Source-list lines without the `sitemap-` prefix MUST route exactly as they do today: `git-`, `sso-`, `elog-`, and `indico-` prefixed lines to their respective collectors, auto-detected elog/indico URLs per the existing heuristics, and all remaining lines to the plain web-link path. A deployment whose input lists contain no `sitemap-` lines SHALL observe no behavior change.

#### Scenario: Mixed list routes as before

- **WHEN** an input list contains a plain URL, a `git-` line, and a `sitemap-` line
- **THEN** the plain URL is collected as a web link and the `git-` line is collected by the git scraper, both exactly as before
- **AND** only the `sitemap-` line is routed to sitemap expansion

#### Scenario: No sitemap lines, no change

- **WHEN** no input list contains a `sitemap-` line
- **THEN** no sitemap expansion is attempted and the ingestion run is byte-for-byte identical to current behavior

### Requirement: Urlset expansion to page URLs

For each sitemap URL, the expander SHALL fetch the document and, when its root element is `<urlset>`, emit the text of every `<loc>` child as a page URL. `<loc>` extraction MUST be namespace-agnostic (the sitemap namespace `http://www.sitemaps.org/schemas/sitemap/0.9` may or may not be declared). `<lastmod>` values SHALL be ignored. An otherwise-valid `<urlset>` with zero `<loc>` entries SHALL NOT be treated as a *parse* error at the document level; the source-level consequence of a zero/low result is governed by the minimum-expansion floor requirement below.

#### Scenario: Flat urlset expanded

- **WHEN** the fetched sitemap is a `<urlset>` containing 282 `<loc>` entries, each with a `<lastmod>`
- **THEN** exactly the 282 page URLs are emitted (after normalization and deduplication)
- **AND** the `<lastmod>` values have no effect on the output

#### Scenario: Namespace not declared

- **WHEN** the fetched `<urlset>` does not declare the sitemap XML namespace
- **THEN** its `<loc>` entries are still extracted and emitted

### Requirement: Sitemapindex recursed one level

When the fetched document's root element is `<sitemapindex>`, the expander SHALL fetch each child sitemap referenced by a `<loc>` exactly once and emit the page URLs of children whose root is `<urlset>`. A child that is itself a `<sitemapindex>` SHALL NOT be followed further and SHALL contribute no URLs.

#### Scenario: Index of urlsets expanded

- **WHEN** the fetched sitemap is a `<sitemapindex>` referencing two child sitemaps, each a `<urlset>`
- **THEN** each child sitemap is fetched exactly once
- **AND** the emitted page URLs are the union of both children's `<loc>` entries

#### Scenario: Nested index contributes nothing

- **WHEN** a `<sitemapindex>` child is itself a `<sitemapindex>`
- **THEN** that child is not followed further
- **AND** it contributes no page URLs
- **AND** sibling `<urlset>` children still contribute their page URLs

### Requirement: URL trust policy for emitted and child-sitemap URLs (v1)

Because a `sitemap-` source delegates URL selection to a remote document that no human reviews before ingestion (unlike the committed, human-reviewed output of `archi sources build`), the expander SHALL constrain every URL it acts on — both child-sitemap `<loc>` values it fetches and page `<loc>` values it emits:

1. The URL scheme MUST be `http` or `https`; any other scheme (e.g. `file:`, `ftp:`, `gopher:`, `data:`) is rejected.
2. The URL host MUST equal the configured sitemap's host, OR appear in an explicit allowlist. Cross-host URLs not on the allowlist are rejected. (Exact host match — not registrable-domain — to avoid a public-suffix-list dependency; the allowlist covers legitimate cross-subdomain/CDN cases.)
3. A host that is an IP literal in a loopback, private, or link-local range (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`) is rejected (stdlib `ipaddress`).
4. The fetch MUST NOT follow a redirect to a different host.
5. Every emitted page URL MUST pass rules 1–4 BEFORE it enters the web-link collection, so an off-host or internal-IP-literal page can never be handed to the scraper.

A rejected URL is dropped with a warning and never reaches the scraper. Rejected URLs count as "not emitted" for the minimum-expansion floor.

**v1 scope:** this basic policy is calibrated to a TRUSTED first-party sitemap. Rejecting a hostname that *resolves* (or rebinds) to a private/metadata address, connection pinning, and per-redirect-hop revalidation are NOT in v1 — they are specified in the change's design §Deferred hardening (v2, H1) and MUST be adopted before pointing the prefix at an untrusted/third-party sitemap.

#### Scenario: Non-HTTP scheme rejected

- **WHEN** a `<loc>` value is `file:///etc/passwd` or `gopher://host/`
- **THEN** the URL is dropped with a warning and is never fetched or scraped

#### Scenario: Cross-host page URL rejected by default

- **WHEN** a sitemap served from `docs.rc.fas.harvard.edu` emits a `<loc>` on `evil.example.com` and no allowlist entry covers it
- **THEN** the URL is dropped and not scraped

#### Scenario: Allowlisted cross-host URL permitted

- **WHEN** an allowlist includes `cdn.example.com` and a `<loc>` on that host passes the scheme and IP-literal checks
- **THEN** the URL is emitted

#### Scenario: Internal IP-literal address rejected

- **WHEN** a `<loc>` or child-sitemap URL targets a literal loopback/private/link-local address (e.g. `http://127.0.0.1/` or `http://10.0.0.5/`)
- **THEN** the URL is dropped with a warning

#### Scenario: Cross-host redirect not followed

- **WHEN** fetching a sitemap URL returns a redirect to a different host
- **THEN** the redirect is not followed and the document contributes no URLs

#### Scenario: Emitted page URL validated before entering the collection

- **WHEN** expansion would emit a page URL that is off-host or an internal IP literal
- **THEN** it is rejected at emit time and never enters the web-link collection handed to the scraper

### Requirement: Fetch or parse failure fails open per document

A fetch failure (connection error, timeout, non-200 status) or parse failure (malformed XML, DTD/entity declaration, unrecognized root element) on an individual sitemap document MUST NOT raise an unhandled exception or abort expansion of the other documents. The expander SHALL log a warning identifying the failing sitemap URL, contribute zero URLs from that document, and continue with any remaining sitemap documents. This per-document resilience governs only individual documents within a source; the source's overall success or failure is decided by the minimum-expansion floor requirement below — a below-floor net result is a deliberate, controlled ingest failure, not a crash.

#### Scenario: Unreachable child document skipped, siblings survive

- **WHEN** a `<sitemapindex>` has two children and exactly one child fetch raises a connection error or returns non-200
- **THEN** a warning naming the failing child URL is logged
- **AND** that child contributes an empty URL list while the healthy sibling's URLs are still emitted
- **AND** no unhandled exception propagates from the expander (the source's overall pass/fail is then governed by the minimum-expansion floor)

#### Scenario: Malformed XML skipped

- **WHEN** the fetched body is not well-formed XML, contains a `<!DOCTYPE` or `<!ENTITY` declaration, or has a root element other than `<urlset>`/`<sitemapindex>`
- **THEN** a warning is logged and that document contributes no URLs
- **AND** no exception propagates to the ingestion run

### Requirement: Per-source emitted-page cap (v1)

Each `sitemap-` source SHALL have a configurable maximum number of emitted page URLs, checked across the top-level sitemap plus every child fetched, BEFORE any expanded URL is handed to the scraper. If a source's total emitted count would exceed the maximum, the source fails deterministically — it contributes no URLs and logs an ERROR naming the source and the cap — rather than flooding the crawler. The cap is independent of the per-seed `max_pages` crawl budget (which resets per seed and cannot bound the collection). Bounding the FETCH work of a runaway `<sitemapindex>` (child-document count, bytes, redirects, time) is design §Deferred hardening (v2, H2), acceptable to defer for a trusted first-party sitemap.

#### Scenario: Over-cap source fails deterministically

- **WHEN** a sitemap source expands to more page URLs than its configured maximum
- **THEN** the source contributes zero URLs, an ERROR naming the source and the cap is logged, and no partial or full over-cap set is scraped

#### Scenario: Within-cap source expands normally

- **WHEN** a sitemap source expands to a count at or below its configured maximum
- **THEN** all emitted URLs are scraped as normal

### Requirement: Minimum-expansion floor per sitemap source

Each `sitemap-` source SHALL have a configurable minimum expected page count (floor). On every ingest, if the net emitted count for a sitemap source — after trust filtering, normalization, and deduplication — is below its floor (including the zero produced by a failed fetch, a malformed document, a validly-parsed empty `<urlset>`, or wholesale trust-policy rejection), the expander SHALL treat it as a source-level failure: log an ERROR and fail the ingest, rather than allow a "successful" run to replace real coverage with an empty or near-empty corpus. This guard runs on EVERY ingest — not as a one-time rollout check — so it protects fresh installs, post-`nuke` rebuilds, and automated re-ingests alike.

#### Scenario: Below-floor expansion fails the ingest

- **WHEN** a sitemap source configured with a floor of N expands to fewer than N page URLs (e.g. a transient outage yields zero)
- **THEN** an ERROR is logged and the ingest fails rather than reporting success with a below-floor KB

#### Scenario: Fresh deploy with a failing sitemap does not silently empty the KB

- **WHEN** a first-run or post-`nuke` deploy has no prior corpus and the sitemap fetch fails (zero emitted)
- **THEN** the ingest fails on the floor check rather than completing with an empty KB

#### Scenario: At-or-above floor proceeds

- **WHEN** a sitemap source expands to at least its floor
- **THEN** the ingest proceeds normally

### Requirement: Per-source expansion isolation

When multiple `sitemap-` lines are configured, each SHALL be expanded and validated INDEPENDENTLY: its emitted count is measured against its own floor and page cap, and its trust check (host match / allowlist) uses its own sitemap host. A below-floor or over-cap result for ANY source MUST fail the ingest and MUST NOT be masked by another source's healthy count (no aggregate counting across sources); and one source's host/allowlist MUST NOT authorize another source's URLs.

#### Scenario: One source below floor is not masked by a healthy source

- **WHEN** source A expands to 300 URLs (≥ its floor) and source B expands to 0 (< its floor)
- **THEN** the ingest fails on source B's below-floor result — A's healthy count does not mask it

#### Scenario: Host policy is per source

- **WHEN** source A is served from `a.example.com` and its document emits a `<loc>` on `b.example.com` (which is source B's host)
- **THEN** that `<loc>` is cross-host for source A and rejected — B's host does not authorize URLs inside A's document

### Requirement: Trailing-slash normalization of expanded URLs

Every emitted page URL SHALL be normalized before joining the web-link collection: the fragment dropped, the scheme and host lowercased, the query preserved, and a single trailing path slash collapsed (the root path `/` preserved) — matching the hand-list URL form. Emitted URLs MUST be deduplicated order-preservingly, both within the expansion and against web-link URLs already collected from the input lists.

#### Scenario: Sitemap slash form normalized to hand-list form

- **WHEN** a sitemap `<loc>` reads `https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp/`
- **THEN** the emitted URL is `https://docs.rc.fas.harvard.edu/kb/copying-data-to-and-from-cluster-using-scp` (no trailing slash)

#### Scenario: Slash-variant duplicates collapse

- **WHEN** the expansion would emit both `.../scp` and `.../scp/`, or an expanded URL equals a hand-listed web link already collected
- **THEN** the URL is scraped only once

### Requirement: Expanded URLs flow through the standard web-scraping path

Page URLs produced by sitemap expansion SHALL be appended to the web-link collection before standard link collection runs, and thereafter treated exactly as if each URL had been listed by hand: crawled at the configured `base_source_depth`, fetched, persisted, and processed by the same downstream pipeline with no sitemap-specific handling.

#### Scenario: Expanded URL scraped like a hand-listed URL

- **WHEN** sitemap expansion emits a page URL
- **THEN** the URL is collected by the standard web-link collector in the same run
- **AND** it is crawled at the configured base source depth
- **AND** the persisted resource is indistinguishable from one produced by a hand-listed line for the same URL
