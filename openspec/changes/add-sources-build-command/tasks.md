## 1. Manifest schema (red → green)

- [ ] 1.1 Write `tests/unit/test_sources_build.py` cases: valid mixed manifest loads; unknown `type` rejected; missing `url` rejected; non-YAML rejected (all assert non-zero / no write).
- [ ] 1.2 Create `src/cli/tools/sources_builder.py` with `load_manifest(path)` + validation (pattern mirrors `src/cli/tools/config_seed.py`).

## 2. Sitemap expansion

- [ ] 2.1 Tests (mock `requests.get`): flat `<urlset>` (3 locs); one-level `<sitemapindex>` (2 children fetched once); nested index NOT followed; empty `<urlset>` → no URLs, no error; HTTP 503 → non-zero abort.
- [ ] 2.2 Implement `expand_sitemap(url)` with `xml.etree.ElementTree` (namespace-aware `<loc>`, one level of `<sitemapindex>`).

## 3. Glob filtering

- [ ] 3.1 Tests: include gate drops non-matches; exclude wins over include; no-filters passthrough; literals never filtered.
- [ ] 3.2 Implement `apply_globs(urls, include, exclude)` using `fnmatch`.

## 4. Deterministic same-host crawl

- [ ] 4.1 Tests (mock `requests.get` + bs4 HTML fixtures): same-host kept / off-host dropped; relative `href` resolved via `urljoin`; depth honored; repeated run → identical ordered output; fetch failure → abort.
- [ ] 4.2 Implement `crawl_same_host(url, depth, include, exclude)` (bs4 anchor extraction + `urllib.parse.urljoin`/`urlparse` host compare; sorted output).

## 5. Literal passthrough

- [ ] 5.1 Test: literal emitted verbatim; assert no HTTP request issued for it.
- [ ] 5.2 Implement the `literal` branch in the seed dispatcher.

## 6. List render, normalize, dedupe, extras

- [ ] 6.1 Tests: URL normalization (fragment stripped, scheme/host lowercased, single trailing slash collapsed); cross-seed dedupe preserves first-seen order; `manual-extras.list` appended; `git-`/`sso-`/`elog-`/`indico-` prefixes preserved; extras de-duplicated against generated URLs.
- [ ] 6.2 Implement `normalize_url()`, `render_list(seed_urls)`, and `append_manual_extras(output_path)`.

## 7. Output path resolution

- [ ] 7.1 Tests: default output resolved from `config_manager.get_input_lists()` via `-c/--config`; `--output` override wins.
- [ ] 7.2 Implement output-path resolution (load config with `ConfigurationManager`, read `data_manager.sources.links.input_lists`).

## 8. Dry-run diff

- [ ] 8.1 Tests: `--dry-run` prints a unified diff and writes nothing (assert file content/mtime unchanged); nonexistent target diffs against empty.
- [ ] 8.2 Implement the `difflib.unified_diff` path gated by `--dry-run`.

## 9. CLI wiring

- [ ] 9.1 Tests (`click.testing.CliRunner`): `archi sources build <manifest> -c <config>` happy path writes the list; `--dry-run` writes nothing; malformed manifest → non-zero exit.
- [ ] 9.2 Add `@click.group() sources()` + nested `@click.command() build()` (`@click.argument('manifest')`, `--config/-c`, `--output`, `--name`, `--env-file`, `--import`, `--dry-run`) in `src/cli/cli_main.py`; register via `cli.add_command(sources)` beside the others (`~:654-660`); delegate to `sources_builder.sources_build_entry(...)`.

## 10. Import trigger

- [ ] 10.1 Tests: `--import --name dev -c <config>` shells `archi create --name dev --config <config> --force` exactly once (mock `CommandRunner.run_simple`); `--import` without `--name` → non-zero before write; `--import` + `--dry-run` → non-zero, no refresh; refresh failure → non-zero exit.
- [ ] 10.2 Implement the shell-out via `src/cli/utils/command_runner.py` (`CommandRunner.run_simple`), forwarding `--env-file` when present.

## 11. Docs

- [ ] 11.1 Document the command in `docs/docs/cli_reference.md` (usage, all flags).
- [ ] 11.2 Document the manifest format + the build→import workflow in `docs/docs/data_sources.md`; add an example `sources.manifest.yaml`.
- [ ] 11.3 Update `docs/mkdocs.yml` nav if a new page is added.

## 12. Validate & gate

- [ ] 12.1 `openspec validate add-sources-build-command --strict` passes.
- [ ] 12.2 Run `scripts/gate.sh` locally; confirm tests pass and diff-cover ≥ 80% on changed lines (network paths covered via mocks; note hard-to-cover lines).
