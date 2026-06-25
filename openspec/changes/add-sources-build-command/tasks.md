## 1. Manifest schema (red → green)

- [x] 1.1 Write `tests/unit/test_sources_build.py` cases: valid mixed manifest loads; unknown `type` rejected; missing `url` rejected; non-YAML rejected (all assert non-zero / no write, and assert the error message names the offending entry per the spec).
- [x] 1.2 Create `src/cli/tools/sources_builder.py` with `load_manifest(path)` + validation (pattern mirrors `src/cli/tools/config_seed.py`).

## 2. Sitemap expansion

- [x] 2.1 Tests (mock `requests.get`): flat `<urlset>` (3 locs); one-level `<sitemapindex>` (2 children fetched once); nested `<sitemapindex>` child NOT followed and contributes no page URLs; empty `<urlset>` → no URLs, no error; HTTP 503 → non-zero abort; malformed/non-XML body → non-zero abort.
- [x] 2.2 Implement `expand_sitemap(url)` with `xml.etree.ElementTree` (namespace-aware `<loc>`, one level of `<sitemapindex>`).

## 3. Glob filtering

- [x] 3.1 Tests: include gate drops non-matches; exclude wins over include; no-filters passthrough; literals never filtered.
- [x] 3.2 Implement `apply_globs(urls, include, exclude)` using `fnmatch`.

## 4. Deterministic same-host crawl

- [x] 4.1 Tests (mock `requests.get` + bs4 HTML fixtures): same-host kept / off-host dropped; relative `href` resolved via `urljoin`; depth honored; repeated run → identical ordered output; fetch failure → abort; malformed/non-HTML body → abort.
- [x] 4.2 Implement `crawl_same_host(url, depth, include, exclude)` (bs4 anchor extraction + `urllib.parse.urljoin`/`urlparse` host compare; sorted output).

## 5. Literal passthrough

- [x] 5.1 Tests: literal emitted (assert no HTTP request issued for it); a literal with uppercase host / fragment / trailing slash is emitted in normalized form (not byte-for-byte).
- [x] 5.2 Implement the `literal` branch in the seed dispatcher.

## 6. List render, normalize, dedupe, extras

- [x] 6.1 Tests: URL normalization (fragment stripped, scheme/host lowercased, single trailing slash collapsed); cross-seed dedupe preserves first-seen order; `manual-extras.list` appended after the generated block; `git-`/`sso-`/`elog-`/`indico-` prefixes preserved; an extras line duplicating a generated URL is dropped from the extras section (generated block wins position, URL appears once); a prefixed extras line is always retained.
- [x] 6.2 Implement `normalize_url()`, `render_list(seed_urls)`, and `append_manual_extras(output_path)`.

## 7. Output path resolution

- [x] 7.1 Tests: `config_manager.get_input_lists()` returns a **list** — default output resolved only when it has exactly one entry; zero entries → non-zero exit demanding `--output`; two-or-more entries → non-zero exit demanding `--output`; `--output` override wins regardless of list length.
- [x] 7.2 Implement output-path resolution (load config with `ConfigurationManager`, read `data_manager.sources.links.input_lists`; require a singleton list for the default, else error requiring `--output`).

## 8. Dry-run diff

- [x] 8.1 Tests: `--dry-run` prints a unified diff and writes nothing (assert file content/mtime unchanged); nonexistent target diffs against empty.
- [x] 8.2 Implement the `difflib.unified_diff` path gated by `--dry-run`.

## 9. CLI wiring

- [x] 9.1 Tests (`click.testing.CliRunner`): `archi sources build <manifest> -c <config>` happy path writes the list; `--dry-run` writes nothing; malformed manifest → non-zero exit.
- [x] 9.2 Add `@click.group() sources()` + nested `@click.command() build()` (`@click.argument('manifest')`, `--config/-c`, `--output`, `--name`, `--services` (default `chatbot`), `--env-file`, `--import`, `--dry-run`) in `src/cli/cli_main.py`; register via `cli.add_command(sources)` beside the others (`~:654-660`); delegate to `sources_builder.sources_build_entry(...)`.

## 10. Import trigger

- [x] 10.1 Tests: `--import --name dev -c <config>` shells `archi create --name dev --config <config> --services chatbot --force` exactly once (mock `CommandRunner.run_simple`); assert a non-empty `--services` is in the argv (so the refresh passes `validate_services_selection`); `--import` without `--name` → non-zero before write; `--import` without `-c/--config` → non-zero before write; `--import` + `--dry-run` → non-zero, no refresh; refresh failure → non-zero exit.
- [x] 10.2 Implement the shell-out via `src/cli/utils/command_runner.py` (`CommandRunner.run_simple`), including a non-empty `--services` (default `chatbot`) and forwarding `--env-file` when present.

## 11. Dependency declaration

- [x] 11.0 Add `beautifulsoup4==4.12.3` to `pyproject.toml` `dependencies` (version-matched to `requirements/requirements-base.txt:4`, following the `pyproject.toml:31-35` comment) so the crawl path imports under a fresh `pip install .`/editable install and in the deployment images.

## 12. Docs

- [x] 12.1 Document the command in `docs/docs/cli_reference.md` (usage, all flags).
- [x] 12.2 Document the manifest format + the build→import workflow in `docs/docs/data_sources.md`; add an example `sources.manifest.yaml`.
- [x] 12.3 Update `docs/mkdocs.yml` nav if a new page is added (n/a — both pages already in nav; no new page).

## 13. Validate & gate

- [x] 13.1 `openspec validate add-sources-build-command --strict` passes.
- [x] 13.2 CI `gate` is the verification oracle (no full local conda toolchain): PR #37 `gate` is GREEN — full unit suite passes and diff-cover reports 89% on changed lines (sources_builder.py 97.6%), above the 80% floor. Remaining uncovered lines are defensive guards (unexpected sitemap root, malformed-HTML guard, visited-page short-circuit, unreachable dispatcher branch, non-list input_lists).
