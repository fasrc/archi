## ADDED Requirements

### Requirement: Typed manifest parsing and validation
The command SHALL read a YAML manifest whose entries each declare a `type` of
`sitemap`, `crawl`, or `literal` and a `url`. The command SHALL reject, with a
non-zero exit and a message naming the offending entry, any manifest that is not
valid YAML or that contains an entry with a missing/unknown `type` or a missing
`url`, and SHALL NOT write any output in that case.

#### Scenario: Valid mixed manifest parses
- **WHEN** a manifest containing one `sitemap`, one `crawl`, and one `literal`
  entry is supplied
- **THEN** each seed is loaded with its type-specific fields and no error is raised

#### Scenario: Unknown type rejected
- **WHEN** a seed declares `type: rss`
- **THEN** the command exits non-zero, names the offending entry, and writes nothing

#### Scenario: Missing url rejected
- **WHEN** a seed omits its `url` field
- **THEN** the command exits non-zero and writes nothing

### Requirement: Sitemap expansion with one level of sitemapindex nesting
For a `sitemap` seed the command SHALL fetch the sitemap XML and emit every `<loc>`
URL. When the fetched document is a `<sitemapindex>`, the command SHALL fetch each
referenced child sitemap exactly once and emit their `<loc>` URLs, and SHALL NOT
follow index nesting beyond one level. An otherwise-valid sitemap containing zero
`<loc>` entries SHALL contribute no URLs and SHALL NOT be treated as an error.

#### Scenario: Flat urlset
- **WHEN** the fetched sitemap is a `<urlset>` with three `<loc>` entries
- **THEN** all three URLs are collected

#### Scenario: One-level index
- **WHEN** the fetched document is a `<sitemapindex>` referencing two child sitemaps
- **THEN** each child is fetched once and their `<loc>` URLs are collected, and a
  `<sitemapindex>` nested inside a child is not followed

#### Scenario: Empty sitemap
- **WHEN** a fetched `<urlset>` contains no `<loc>` entries
- **THEN** the seed contributes no URLs and the command does not error

### Requirement: Include/exclude glob filtering
The command SHALL filter `sitemap` and `crawl` URLs by the seed's optional
`include`/`exclude` glob lists, keeping a URL only if it matches at least one
`include` (when any are given) and matches no `exclude`. A `literal` seed's URL
SHALL NOT be glob-filtered.

#### Scenario: Exclude wins
- **WHEN** a URL matches both an `include` and an `exclude` glob
- **THEN** the URL is dropped

#### Scenario: Include gate
- **WHEN** `include: ["*/docs/*"]` is set and a candidate URL lacks `/docs/`
- **THEN** the URL is dropped

### Requirement: Deterministic same-host crawl
For a `crawl` seed the command SHALL fetch the index page, extract its anchor links,
resolve relative links against the seed URL, and keep only links whose host matches
the seed's host. The crawl SHALL honor an optional `depth` (default one level) and
the seed's include/exclude globs. The set of emitted URLs SHALL be identical and in
identical order across repeated runs on identical input.

#### Scenario: Off-host link dropped
- **WHEN** the index page links to both same-host and external URLs
- **THEN** only same-host URLs are emitted

#### Scenario: Relative link resolved
- **WHEN** the index page contains a relative `href` such as `man/srun.html`
- **THEN** it is resolved against the seed URL and emitted as an absolute same-host URL

#### Scenario: Deterministic order
- **WHEN** the same crawl seed is expanded twice on identical input
- **THEN** the emitted URL order is identical

### Requirement: Literal passthrough
For a `literal` seed the command SHALL emit the given URL verbatim without fetching,
crawling, or glob-filtering it, and SHALL NOT issue any HTTP request for it.

#### Scenario: Literal emitted as-is
- **WHEN** a `literal` seed lists a single URL
- **THEN** that exact URL appears in the output and no HTTP request is made for it

### Requirement: Wholesale list regeneration with deterministic dedupe
The command SHALL regenerate the target list wholesale, writing one URL per line.
The default output path SHALL be resolved by loading the deployment config supplied
with `-c/--config` and reading where `data_manager.sources.links.input_lists`
points; `--output` SHALL override it. URLs SHALL be normalized (drop fragment,
lowercase scheme/host, collapse a single trailing path slash) and deduplicated,
preserving first-seen order across seeds.

#### Scenario: Default output resolution
- **WHEN** `--output` is omitted and `-c/--config` points at a deployment config
- **THEN** the command writes to the path that config's `input_lists` resolves to

#### Scenario: Cross-seed dedupe
- **WHEN** two seeds yield the same URL (modulo trailing slash / fragment)
- **THEN** it appears exactly once in the output, in first-seen position

### Requirement: Manual-extras append with prefix preservation
The command SHALL append a sibling `manual-extras.list`, when present, to the
regenerated list verbatim — preserving its non-comment, non-blank entries including
lines bearing `git-`, `sso-`, `elog-`, or `indico-` prefixes — and SHALL NOT fetch
or crawl those entries. An extras entry that duplicates a generated URL SHALL appear
exactly once.

#### Scenario: Prefixed extras preserved
- **WHEN** `manual-extras.list` contains a `git-…` line and an `sso-…` line
- **THEN** both appear unchanged in the regenerated list

#### Scenario: No extras file
- **WHEN** no `manual-extras.list` exists beside the output
- **THEN** regeneration proceeds with only manifest-derived URLs

### Requirement: Dry-run diff without side effects
With `--dry-run` the command SHALL print a unified diff of the proposed list against
the existing output file and SHALL NOT write or create the output, and SHALL NOT
trigger an import. When the output file does not yet exist, the diff SHALL be
computed against an empty file.

#### Scenario: Dry-run writes nothing
- **WHEN** `--dry-run` is passed
- **THEN** the existing output file's content is unchanged and a diff is printed to stdout

### Requirement: Fetch-error policy fails the build atomically
The command SHALL abort with a non-zero exit, leaving the output file unchanged, on
any non-200 response, connection/timeout failure, or malformed XML/HTML while
expanding a `sitemap` or `crawl` seed. The output file SHALL be written only after
every seed has expanded successfully.

#### Scenario: Sitemap fetch fails
- **WHEN** a `sitemap` seed's URL returns HTTP 503
- **THEN** the command exits non-zero and the existing output file is not modified

### Requirement: Optional import trigger
With `--import` the command SHALL require `--name <deployment>` and SHALL be
incompatible with `--dry-run`. After a successful write, it SHALL trigger a
deployment refresh equivalent to `archi create --name <deployment> --config
<config> --force` (forwarding `--env-file` when given) and SHALL exit non-zero if
that refresh fails.

#### Scenario: Import after write
- **WHEN** `--import --name dev -c <config>` is passed and the write succeeds
- **THEN** the refresh command is invoked once for deployment `dev`

#### Scenario: Import requires a name
- **WHEN** `--import` is passed without `--name`
- **THEN** the command exits non-zero before writing or refreshing

#### Scenario: No import on dry-run
- **WHEN** both `--import` and `--dry-run` are passed
- **THEN** the command exits non-zero and no refresh is invoked
