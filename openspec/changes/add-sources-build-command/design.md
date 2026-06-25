## Context

Web ingestion is driven by `sources.list` files referenced from
`data_manager.sources.links.input_lists`. Today an operator builds these by hand:
download a sitemap, extract `<loc>` URLs into a per-site `.list`, concatenate the
sites, and hand-feed the result to a redeploy. The list drifts from the source of
truth (the FAS RC docs sitemap publishes 211 knowledge-base pages; the hand list
carried ~183). This change adds a CLI command that regenerates the list from a
declarative manifest and can trigger the import.

Verified constraints in the current code (file:line):
- CLI commands are flat `@click.command()`s registered via `cli.add_command(...)`
  in `main()` (`src/cli/cli_main.py:39-40`, `:654-660`); no `sources`/`ingest`
  command exists today.
- `create()` (`cli_main.py:58`) **requires exactly one** `--config`
  (`cli_main.py:62`, `:67-68` raise `"Exactly one config file is supported"`) **and a
  non-empty `--services`**: it calls `validate_services_selection(services)`
  (`cli_main.py:93`) which raises `"No services selected…"` on an empty list
  (`helpers.py:255-263`). So an import trigger must supply both a single `--config`
  and at least one service; a name+force alone exits before refreshing.
- The `sources.list` line format: one URL per line; `#` and blank lines skipped;
  a per-line `url,depth` is split and the depth **discarded**
  (`scraper_manager.py:528-540`). Downstream prefix classification recognizes
  `git-`, `sso-`, `elog-`, `indico-` (`scraper_manager.py:415-440`).
- Configured `input_lists` absolute paths are staged into a deployment as
  `weblists/<basename>` (`templates_manager.py:708-723`) and read at runtime by
  basename (`scraper_manager.py:395-409`).
- `config_manager.get_input_lists()` returns a **list**, not a single path:
  `_collect_input_lists()` aggregates `data_manager.sources.links.input_lists` across
  every config and stores `sorted(set(collected))` (`config_manager.py:279-288`,
  `:369-370`); `TemplateManager._copy_web_input_lists` iterates every entry
  (`templates_manager.py:714-718`), and example configs declare several (e.g.
  `examples/deployments/basic-ollama-fnal/config.yaml:47-50` has two). Default output
  resolution therefore must handle 0/1/N: the command resolves a default **only when
  exactly one** `input_lists` entry is configured; with zero or several it exits
  non-zero and requires `--output`.
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
- Ship with docs and tests; introduce **no genuinely new** third-party package
  (only declare the already-vendored `beautifulsoup4` in `pyproject.toml`).

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

**D2 — stdlib XML + `requests` + `beautifulsoup4` (declare bs4 in pyproject).**
Sitemap parsing uses `xml.etree.ElementTree` (stdlib); fetching uses `requests`
(already pinned in `pyproject.toml:19` and `requirements/requirements-base.txt`);
crawl link extraction uses `beautifulsoup4` (imported in `scraper.py`). **`bs4` is
declared only in `requirements/requirements-base.txt:4`, not in `pyproject.toml`** —
so a fresh `pip install .`/editable install and the deployment images (which
`pip install .` per the `pyproject.toml:31-35` comment) would lack it, and the crawl
path would `ImportError`. This change therefore **adds `beautifulsoup4==4.12.3` to
`pyproject.toml` dependencies** (version-matched, same precedent as
`llama-index-core`/`flashrank`). No genuinely new third-party package is introduced —
this only closes the existing pyproject/requirements declaration gap.

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
`indico-` prefixes — and are never fetched/crawled. **Precedence (explicit): the
generated block comes first and wins position; an extras line whose value duplicates
an already-emitted generated URL is dropped from the extras section so the URL
appears exactly once, in its generated position.** (Prefixed extras like `git-…`
have no generated counterpart, so they always survive.) This is the single rule the
spec and tasks both reference.

**D5 — `--import` shells out.** Because `create()` needs a single `--config` **and a
non-empty `--services`** (`validate_services_selection`, `cli_main.py:93`), `--import`
(requires `--name` and `-c/--config`, forbids `--dry-run`) runs a subprocess
equivalent to `archi create --name <deployment> --config <config> --services
<services> --force` (forwarding `--env-file` when given), via the existing
`CommandRunner.run_simple` (`command_runner.py:16-34`). `--services` defaults to
`chatbot` (a non-empty set so the refresh passes validation) and is overridable. The
same `-c/--config` resolves the default `--output` and feeds the import, so the two
stay consistent. A non-zero refresh exit propagates.

**D6 — Determinism & normalization (applies to ALL emitted URLs, including
`literal`).** URLs are normalized before dedupe by stripping URL fragments and
collapsing a single trailing slash on the path (so `…/page` and `…/page/` are one
entry); scheme/host are lowercased. **Normalization is uniform across `sitemap`,
`crawl`, and `literal` seeds** — "literal" means "not fetched/crawled/glob-filtered",
not "byte-for-byte preserved". This resolves the prior conflict where the literal
requirement promised verbatim output while the regeneration requirement normalized
all URLs: a literal whose URL has an uppercase host, a fragment, or a trailing slash
is emitted in normalized form, deterministically. (`manual-extras.list` lines remain
the one verbatim exception — they are appended as-is and never normalized, since they
may carry `git-`/`sso-` prefixes that are not URLs.) Crawl output is sorted for a
stable order. This keeps `--dry-run` diffs minimal and meaningful.

**D7 — Network-failure policy: fail the build.** Any non-200, timeout, or malformed
XML/HTML on a `sitemap`/`crawl` fetch aborts the whole command with a non-zero exit
and a clear message — a partial list must never silently overwrite a good one.
`--output` is written only after every seed expands successfully. An empty-but-valid
sitemap (zero `<loc>`) is not an error (emits nothing for that seed).

**D8 — Sitemap-index `<loc>` values are sitemap documents, not page URLs.** In a
`<sitemapindex>`, each `<loc>` points at a *child sitemap document*, not a page. The
command treats a top-level index's `<loc>`s as fetch targets (one level), fetches
each child once, and emits the `<loc>`s from the children's `<urlset>`s as page URLs.
A child that is itself a `<sitemapindex>` is **not** followed **and contributes no
URLs** — its `<loc>`s are sitemap-XML URLs, and emitting them would point the scraper
at index files rather than pages. (Whether to instead fail loudly on a too-deep index
is noted as an open question; v1 skips-and-warns to stay non-fatal.) This makes the
"one level of nesting" rule concrete: pages come only from a `<urlset>`, never from
an index's `<loc>`.

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
