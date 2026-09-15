# sources-build Specification

## Purpose
TBD - created by archiving change add-sources-build-command. Update Purpose after archive.
## Requirements
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
For a `sitemap` seed the command SHALL emit page URLs only from `<urlset>` documents, fetching one level of `<sitemapindex>` nesting. When the fetched document is a `<urlset>`, the command SHALL emit every `<loc>`. When it is a `<sitemapindex>`, the command SHALL fetch each referenced child sitemap exactly once and emit the `<loc>` URLs of children that are `<urlset>` documents. A child that is itself a `<sitemapindex>` SHALL NOT be followed and SHALL contribute no URLs (its `<loc>` values are sitemap documents, not pages). An otherwise-valid sitemap containing zero `<loc>` entries SHALL contribute no URLs and SHALL NOT be treated as an error.

#### Scenario: Flat urlset
- **WHEN** the fetched sitemap is a `<urlset>` with three `<loc>` entries
- **THEN** all three URLs are collected

#### Scenario: One-level index
- **WHEN** the fetched document is a `<sitemapindex>` referencing two child sitemaps
- **THEN** each child is fetched once and the `<loc>` URLs from its `<urlset>` are collected

#### Scenario: Nested index contributes no page URLs
- **WHEN** a fetched child sitemap is itself a `<sitemapindex>`
- **THEN** it is not followed and none of its `<loc>` values are emitted as page URLs

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
For a `literal` seed the command SHALL emit the given URL without fetching, crawling, or glob-filtering it, and SHALL NOT issue any HTTP request for it. The URL SHALL still be normalized and deduplicated on the same terms as every other emitted URL (drop fragment, lowercase scheme/host, collapse a single trailing path slash); "literal" governs that the URL is not fetched/crawled/filtered, not that its bytes are preserved.

#### Scenario: Literal not fetched
- **WHEN** a `literal` seed lists a single URL
- **THEN** the URL appears in the output and no HTTP request is made for it

#### Scenario: Literal is normalized
- **WHEN** a `literal` seed's URL has an uppercase host, a fragment, or a trailing slash
- **THEN** the emitted URL is the normalized form (lowercased host, no fragment, no trailing slash)

### Requirement: Wholesale list regeneration with deterministic dedupe
The command SHALL regenerate the target list wholesale, writing one URL per line, with every URL normalized (drop fragment, lowercase scheme/host, collapse a single trailing path slash) and deduplicated preserving first-seen order across seeds. When `--output` is given it SHALL be the target. When `--output` is omitted, the target SHALL be resolved from the `-c/--config` deployment config's `data_manager.sources.links.input_lists`, which is a **list**; the command SHALL use it as the default output **only when it contains exactly one entry**, and SHALL exit non-zero (requiring `--output`) when the config declares zero or more than one `input_lists` entry.

#### Scenario: Default output resolution with a single input list
- **WHEN** `--output` is omitted and `-c/--config` points at a config whose `input_lists` has exactly one entry
- **THEN** the command writes to that single resolved path

#### Scenario: Ambiguous default output rejected
- **WHEN** `--output` is omitted and the config's `input_lists` has zero or two-or-more entries
- **THEN** the command exits non-zero, writes nothing, and the message instructs the operator to pass `--output`

#### Scenario: Cross-seed dedupe
- **WHEN** two seeds yield the same URL (modulo trailing slash / fragment)
- **THEN** it appears exactly once in the output, in first-seen position

### Requirement: Manual-extras append with prefix preservation
The command SHALL append a sibling `manual-extras.list`, when present, after the generated block — preserving its non-comment, non-blank entries verbatim, including lines bearing `git-`, `sso-`, `elog-`, or `indico-` prefixes — and SHALL NOT fetch or crawl those entries. The generated block SHALL take position precedence: an extras entry whose value duplicates an already-emitted generated URL SHALL be dropped from the extras section so the URL appears exactly once in its generated position; a prefixed extras line, which has no generated counterpart, SHALL always be retained.

#### Scenario: Prefixed extras preserved
- **WHEN** `manual-extras.list` contains a `git-…` line and an `sso-…` line
- **THEN** both appear unchanged in the regenerated list

#### Scenario: Duplicate extras dropped in favor of generated
- **WHEN** a `manual-extras.list` entry duplicates a URL already emitted by a seed
- **THEN** the URL appears exactly once, in its generated position, and not again in the extras section

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

### Requirement: Advisory import hint
With `--import` the command SHALL require `--name <deployment>`, SHALL be incompatible with `--dry-run`, and after a successful write SHALL PRINT (to stdout) a copy-pasteable redeploy command of the form `archi create --name <deployment> [--config <config>] [--env-file <env>] --force` together with a note instructing the operator to append their usual flags (`--services …`, `--podman`, host/gpu/tag). The command SHALL execute nothing — it triggers no subprocess and no deployment refresh — because a forced recreate is destructive (it removes the deployment directory and re-renders compose for only the named services, dropping others, while ignoring runtime flags). When `--import` is combined with an explicit `--output` that is not one of the resolved config's `data_manager.sources.links.input_lists`, the command SHALL print a warning that the regenerated file is not referenced by that config and will not be ingested by the printed redeploy.

#### Scenario: Import prints a redeploy command and runs nothing
- **WHEN** `--import --name dev -c <config>` is passed and the write succeeds
- **THEN** a redeploy command naming deployment `dev` is printed to stdout and no subprocess is executed

#### Scenario: Import requires a name
- **WHEN** `--import` is passed without `--name`
- **THEN** the command exits non-zero before writing

#### Scenario: No import on dry-run
- **WHEN** both `--import` and `--dry-run` are passed
- **THEN** the command exits non-zero and nothing is written or printed as a redeploy command

#### Scenario: Out-of-config output warns
- **WHEN** `--import` is passed with an explicit `--output` that is not in the config's `input_lists`
- **THEN** the build succeeds and a warning is printed that the file will not be ingested by the printed redeploy

