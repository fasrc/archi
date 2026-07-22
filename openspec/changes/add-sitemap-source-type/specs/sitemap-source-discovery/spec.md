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

### Requirement: URL trust policy for emitted and child-sitemap URLs

Because a `sitemap-` source delegates URL selection to a remote document that no human reviews before ingestion (unlike the committed, human-reviewed output of `archi sources build`), the expander SHALL constrain every URL it acts on — both child-sitemap `<loc>` values it fetches and page `<loc>` values it emits:

1. The URL scheme MUST be `http` or `https`; any other scheme (e.g. `file:`, `ftp:`, `gopher:`, `data:`) is rejected.
2. The URL host MUST equal the configured sitemap's host, OR appear in an explicit per-source allowlist. Cross-host URLs not on the allowlist are rejected. (Exact host match — not registrable-domain — is the default to avoid a public-suffix-list dependency; the allowlist covers legitimate cross-subdomain/CDN cases.)
3. The host MUST be resolved before connecting, and EVERY resolved address MUST be global. Any address in a loopback, private, link-local, unique-local, or otherwise reserved/non-global range (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` including the `169.254.169.254` cloud-metadata address, `::1`, `fc00::/7`, `fe80::/10`, …) causes rejection — whether the host is an IP literal OR a DNS name that resolves to such an address. IP-literal checking alone is insufficient. **Private/offline exception:** to preserve archi's private/offline-friendly deployments, an operator MAY permit private-range addresses for a host by placing that host on the allowlist (explicit trust); loopback and the `169.254.169.254` metadata address are rejected UNCONDITIONALLY, allowlist or not.
4. To defeat DNS rebinding (a name that passes the check but resolves elsewhere at connect time), the fetch MUST connect to the exact validated address it checked (pin the resolved IP, preserving the `Host` header) rather than re-resolving, and MUST re-apply rules 1–3 to the target host of every redirect hop.
5. Every emitted page URL MUST pass the scheme/host/resolved-address validation (rules 1–3) BEFORE it enters the web-link collection, so a metadata/internal/off-host page can never be handed to the scraper. (Emitted URLs are later fetched by the shared scraper; closing the residual emit→scrape rebinding window for those page fetches is the shared-scraper hardening follow-on — design D7(d) / Open Questions.)

A rejected URL is dropped with a warning and never reaches the scraper. Rejected URLs count as "not emitted" for the minimum-expansion floor.

#### Scenario: Non-HTTP scheme rejected

- **WHEN** a `<loc>` value is `file:///etc/passwd` or `gopher://host/`
- **THEN** the URL is dropped with a warning and is never fetched or scraped

#### Scenario: Cross-host page URL rejected by default

- **WHEN** a sitemap served from `docs.rc.fas.harvard.edu` emits a `<loc>` on `evil.example.com` and no allowlist entry covers it
- **THEN** the URL is dropped and not scraped

#### Scenario: Cloud-metadata / loopback address rejected unconditionally

- **WHEN** a `<loc>` or child-sitemap URL targets the `169.254.169.254` metadata address or a loopback address
- **THEN** the URL is dropped with a warning even if its host is on the allowlist

#### Scenario: Non-allowlisted private-resolving host rejected

- **WHEN** a `<loc>` host resolves to an RFC1918 address and is NOT on the allowlist
- **THEN** the URL is dropped with a warning

#### Scenario: Allowlisted internal host permits its private address

- **WHEN** an operator allowlists an internal docs host that resolves to an RFC1918 address (a private/offline deployment)
- **THEN** that host's URLs are permitted, while non-allowlisted private-resolving hosts remain rejected

#### Scenario: Cross-host redirect not followed

- **WHEN** fetching a sitemap URL returns a redirect to a different, non-allowlisted host
- **THEN** the redirect is not followed and the document contributes no URLs

#### Scenario: Allowlisted cross-host URL permitted

- **WHEN** a per-source allowlist includes `cdn.example.com` and a `<loc>` on that host passes the scheme and resolved-address checks
- **THEN** the URL is emitted

#### Scenario: DNS name resolving to a private address rejected

- **WHEN** a `<loc>` or child-sitemap host is a DNS name that matches the host/allowlist but resolves to a loopback/RFC1918/link-local/metadata address
- **THEN** the URL is rejected before any connection is made, even though the hostname passed the scheme and host checks

#### Scenario: Connection pinned against rebinding

- **WHEN** a validated host would re-resolve at connect time to a different, private address
- **THEN** the fetch connects to the address that was validated (not a freshly-resolved one), so the rebind cannot redirect the connection

#### Scenario: Redirect hop re-validated

- **WHEN** a fetch is redirected to a host that resolves to a non-global address
- **THEN** the redirect is not followed and the document contributes no URLs

#### Scenario: Emitted page URL validated before entering the collection

- **WHEN** expansion would emit a page URL whose host is off-host or resolves to a non-global address
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

### Requirement: Per-source expansion work budget

Each `sitemap-` source SHALL be bounded by a configurable work budget enforced DURING expansion (not only on the final emitted set), covering at least: (a) maximum emitted page URLs; (b) maximum child-sitemap documents fetched; (c) cumulative fetched bytes across the source's documents; (d) maximum redirect hops per fetch; (e) maximum total expansion wall-clock time for the source. Expansion SHALL stop as soon as any budget is exceeded, and the source SHALL fail deterministically — contribute zero URLs and log an ERROR naming the source and the exceeded budget — rather than keep fetching or ship a partial set. This bounds fetch work even when the emitted-page count alone would stay under the page cap (e.g. a `<sitemapindex>` referencing thousands of empty, slow, or failing children). These budgets are independent of the per-seed `max_pages` crawl budget, which resets per seed and cannot bound the collection.

#### Scenario: Over-page-cap source fails deterministically

- **WHEN** a sitemap source expands to more page URLs than its configured maximum
- **THEN** the source contributes zero URLs, an ERROR naming the source and the exceeded budget is logged, and no partial or full over-budget set is scraped

#### Scenario: Runaway index stopped before exhausting resources

- **WHEN** a `<sitemapindex>` references more child sitemaps than the child-document budget, or the cumulative fetched bytes or total expansion time exceeds their budgets
- **THEN** expansion stops at the first exceeded budget, the remaining children are NOT fetched, the source contributes zero URLs, and an ERROR names the exceeded budget

#### Scenario: Within-budget source expands normally

- **WHEN** a sitemap source stays within every budget
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

When multiple `sitemap-` lines are configured, each SHALL be expanded and validated INDEPENDENTLY: its emitted count is measured against its own floor and work budget, and its trust check (host match / allowlist) uses its own sitemap host. A below-floor or over-budget result for ANY source MUST fail the ingest and MUST NOT be masked by another source's healthy count (no aggregate counting across sources); and one source's host/allowlist MUST NOT authorize another source's URLs.

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
