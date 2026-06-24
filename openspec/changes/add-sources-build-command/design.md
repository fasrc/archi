## Context

Web ingestion is driven by `sources.list` files referenced from
`data_manager.sources.links.input_lists`. Today an operator builds these by hand:
download a sitemap, extract `<loc>` URLs into a per-site `.list`, concatenate the
sites, and hand-feed the result to a redeploy. The list drifts from the source of
truth (the FAS RC docs sitemap publishes 211 KB pages; the hand list carried
~183). This change adds a CLI command that regenerates the list from a declarative
manifest and can trigger the import.

Verified constraints in the current code (file:line):
- CLI commands are flat `@click.command()`s registered via `cli.add_command(...)`
  in `main()` (`src/cli/cli_main.py:39-40`, `:654-660`); no `sources`/`ingest`
  command exists today.
- `create()` (`cli_main.py:58`) **requires exactly one** `--config`
  (`cli_main.py:62`, `:67-68` raise `"Exactly one config file is supported"`), so
  an import trigger cannot call `create()` in-process with only a name+force.
- The `sources.list` line format: one URL per line; `#` and blank lines skipped;
  a per-line `url,depth` is split and the depth **discarded**
  (`scraper_manager.py:528-540`). Downstream prefix classification recognizes
  `git-`, `sso-`, `elog-`, `indico-` (`scraper_manager.py:415-440`).
- Configured `input_lists` absolute paths are staged into a deployment as
  `weblists/<basename>` (`templates_manager.py:708-723`) and read at runtime by
  basename (`scraper_manager.py:395-409`).
- Default output path is resolvable in-process via
  `config_manager.get_input_lists()` (`config_manager.py:279-288`, `:369-370`).
- Existing spec format (`openspec/specs/hierarchical-rerank-retrieval/spec.md`):
  `### Requirement:` + `#### Scenario:` with `- **WHEN**` / `- **THEN**`.

## Goals / Non-Goals

**Goals:**
- A `archi sources build <manifest>` command that expands a typed manifest
  (`sitemap` / `crawl` / `literal`) into a regenerated `sources.list`.
- Reproducible, deterministic output so `--dry-run` diffs are stable and
  reviewable in the (separate) config repo.
- Preserve hand-curated `manual-extras.list` entries, including prefixed lines.
- Optional `--import` that triggers the existing deployment refresh.
- Ship with docs and tests; add **no** new third-party dependency.

**Non-Goals:**
- No native `sitemap` source *type* expanded at ingest time (Alternative A).
- No change to the scraper/data-manager runtime, the source registry, or the
  config schema. The command only produces the existing `input_lists` artifact.
- No honoring of per-line `url,depth` (Alternative B).
- No `lastmod`-driven incremental re-ingest; no new data-manager "re-ingest now"
  endpoint. (Future work.)

## Decisions

**D1 — Build-time expansion that emits the existing artifact.** The command writes
a plain `sources.list`; the scraper consumes it unchanged. Blast radius is one new
helper module (`src/cli/tools/sources_builder.py`, mirroring `config_seed.py`) plus
one registration in `cli_main.py`. *Alternative A (native `sitemap` source type)*
would touch the source registry (`source_registry.py:23-82`), the scraper manager
(`schedule_collect_sitemap` + collect path), the hardcoded schedule map
(`service_data_manager.py:75-84`), `base-config.yaml`, and config validation — ~6–7
files plus a config migration for existing deployments. Rejected for blast radius
and migration burden; noted as future work.

**D2 — stdlib XML + existing `requests`/`bs4`.** Sitemap parsing uses
`xml.etree.ElementTree` (stdlib); fetching uses `requests` (already pinned in
`pyproject.toml` and `requirements/requirements-base.txt`); crawl link extraction
uses `beautifulsoup4` (already a dep, imported in `scraper.py`). **No new
third-party dependency.**

**D3 — Manifest schema (defined here; no repo precedent).** YAML list of entries:
```yaml
- type: sitemap          # fetch XML, emit <loc>; follow one level of <sitemapindex>
  url: https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml
  include: ["*/kb/*"]    # optional URL globs (fnmatch); keep only matches
  exclude: ["*/author/*"]# optional URL globs; drop matches
- type: crawl            # fetch index, extract SAME-HOST links
  url: https://slurm.schedmd.com/archive/slurm-25.11.5/
  depth: 1               # optional, default 1
  include: []
  exclude: []
- type: literal          # emit verbatim, never fetched
  url: https://en.wikipedia.org/wiki/Annie_Jump_Cannon
```
Unknown `type`, missing `url`, or malformed YAML → non-zero exit, no write.

**D4 — Wholesale regeneration + `manual-extras.list`.** Output is regenerated from
scratch every run (reproducible). Generated URLs are deduped preserving first-seen
order across seeds. If a `manual-extras.list` sibling of the output exists, its
non-comment entries are appended verbatim — preserving `git-`/`sso-`/`elog-`/
`indico-` prefixes — and are never fetched/crawled. An extras line duplicating a
generated URL appears once (extras win, kept in the extras position).

**D5 — `--import` shells out.** Because `create()` needs a single `--config`,
`--import` (requires `--name`, forbids `--dry-run`) runs a subprocess equivalent to
`archi create --name <deployment> --config <config> --force` (forwarding
`--env-file` when given), via the existing `CommandRunner.run_simple`
(`command_runner.py:16-34`). The same `-c/--config` resolves the default `--output`
and feeds the import, so the two stay consistent. A non-zero refresh exit propagates.

**D6 — Determinism & normalization.** URLs are normalized before dedupe by stripping
URL fragments and collapsing a single trailing slash on the path (so `…/page` and
`…/page/` are one entry); scheme/host are lowercased. Crawl output is sorted for a
stable order. This keeps `--dry-run` diffs minimal and meaningful.

**D7 — Network-failure policy: fail the build.** Any non-200, timeout, or malformed
XML/HTML on a `sitemap`/`crawl` fetch aborts the whole command with a non-zero exit
and a clear message — a partial list must never silently overwrite a good one.
`--output` is written only after every seed expands successfully. An empty-but-valid
sitemap (zero `<loc>`) is not an error (emits nothing for that seed).

## Risks / Trade-offs

- **Crawl re-implements a slice of scraping** → keep it minimal (one index page,
  same-host links, optional shallow depth) and lean on `bs4` + `urllib.parse`;
  document that deep/JS-rendered sites are out of scope (use a sitemap or `literal`).
- **Coverage gate (diff-cover ≥ 80% on changed lines)** → network is mocked with
  `unittest.mock.patch` on `requests.get` (repo convention; no `responses`/
  `requests-mock` available). Pure functions (parse/expand/glob/render/diff) are
  fully unit-testable; CLI wiring uses `click.testing.CliRunner`.
- **Wholesale regeneration can drop hand edits** → mitigated by `manual-extras.list`
  + `--dry-run` review before writing.
- **`requests` version pin mismatch** (`pyproject.toml` 2.31.0 vs
  `requirements-base.txt` 2.32.5) is pre-existing → note, do **not** "fix" here.
- **`--import` couples to deployment layout** → mitigated by requiring explicit
  `--name` + `--config` rather than inferring them.

## Migration Plan

Additive, no migration. Existing `sources.list` files keep working untouched; the
command is opt-in. Rollback = don't run it (or restore the prior `sources.list`
from the config repo's git history). The hand workflow in `archi-config` (the
separate, non-OpenSpec repo holding the lists) is replaced by writing a
`sources.manifest.yaml` and running the command; the first run should use
`--dry-run` to confirm the diff against the current `sources.list`.

## Open Questions

- **Manifest location convention.** Default to `--manifest` as a required positional
  argument (no implicit search), or also look for a conventional
  `sources.manifest.yaml` next to the output? (Leaning: explicit positional only.)
- **`crawl` depth > 1 semantics.** v1 supports `depth: 1` (index → its same-host
  links). Is multi-level crawl ever needed, or should `depth` be capped at 1 for now
  and the field reserved? (Leaning: honor small depths, document 1 as typical.)
- **Trailing-slash normalization correctness.** Confirm no target site treats
  `…/page` and `…/page/` as distinct content before collapsing them; if any does,
  make normalization opt-out per seed.
