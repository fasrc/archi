## Why

The `sources.list` that drives web ingestion is maintained by hand: an operator
downloads sitemaps, extracts URLs into per-site `.list` files, and concatenates
them. The result drifts — the FAS RC docs site publishes a sitemap with 211 KB
pages, but the hand-curated list carries only ~183, so the corpus is already
stale and incomplete the moment it is built. There is no archi command to turn a
documentation site (sitemap-backed or not) into an importable source list, and
nothing ties list regeneration to triggering an import. `archi sources build`
makes that workflow a first-class, repeatable command.

## What Changes

- Add a new CLI command group `sources` with a `build` subcommand:
  `archi sources build <manifest> [-c/--config PATH] [--output PATH]
  [--name DEPLOYMENT] [--env-file PATH] [--import] [--dry-run]`.
- Read a typed YAML **manifest** of seed entries, each with a `type`:
  - `sitemap` — fetch the sitemap XML and emit every `<loc>`; follow one level
    of `<sitemapindex>` nesting; apply optional include/exclude URL globs.
  - `crawl` — fetch a non-sitemap index page and extract its same-host links
    (deterministic, script-side crawl with an optional depth and include/exclude
    globs) for sites without a sitemap (e.g. a Slurm release archive index).
  - `literal` — pass a URL through verbatim (e.g. a single canary page).
- Regenerate the target `sources.list` wholesale. The default output path is
  resolved by loading the deployment config given with `-c/--config` and reading
  where `data_manager.sources.links.input_lists` points; `--output` overrides it.
  If a sibling `manual-extras.list` exists, append its entries so hand-added /
  prefixed lines (`git-`, `sso-`, `elog-`, `indico-`) are preserved verbatim.
- `--dry-run` prints the diff against the existing list instead of writing.
- `--import` (requires `--name <deployment>`, and not `--dry-run`) re-ingests
  after the write by invoking the existing deployment refresh — a shell-out
  equivalent to `archi create --name <deployment> --config <config> --force`
  (the in-process `create()` requires a single `--config`, so import passes the
  same `-c/--config` and forwards `--env-file`).
- Update `docs/` to document the command and manifest format (project convention:
  CLI/behavior changes ship with docs).

This is additive: it produces the **existing** `input_lists` artifact and does
not modify the data-manager, the scraper runtime, or the config schema.

## Capabilities

### New Capabilities
- `sources-build`: generating an importable web `sources.list` from a typed
  manifest of seeds (sitemap expansion, deterministic same-host crawl, literal
  passthrough), with wholesale regeneration plus preserved manual extras, a
  dry-run diff mode, and an optional post-write import trigger.

### Modified Capabilities
<!-- None. The command emits the existing input_lists artifact and does not change
     the behavior of any existing capability (links ingestion, scraper, config). -->

## Impact

- **New code (archi repo):** a `sources` click command group + a sources-build
  helper module under `src/cli/`; registration in `src/cli/cli_main.py`.
- **Reads, does not change:** `data_manager.sources.links.input_lists` resolution
  (to find the default output path) and the existing `sources.list` line format
  (one URL per line, `#` comments, `git-`/`sso-`/`elog-`/`indico-` prefixes).
- **Dependencies:** uses the Python stdlib XML parser (`xml.etree.ElementTree`)
  plus `requests` and `beautifulsoup4`, all already present in the project —
  **no new third-party dependency**.
- **Docs:** update `docs/docs/cli_reference.md` (command) and
  `docs/docs/data_sources.md` (manifest format / workflow); add a mkdocs nav
  entry if a new page is introduced.
- **Out of scope (parked as design alternatives):** a native `sitemap` source
  *type* expanded at ingest time; honoring the currently-ignored per-line
  `url,depth` so archi's own scraper crawls seeds; `lastmod`-driven incremental
  re-ingest; a lighter "re-ingest now" data-manager endpoint.
