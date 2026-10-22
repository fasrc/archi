## Context

Anchors re-verified on `origin/dev` `db701852`.

- Scraper `src/data_manager/collectors/scrapers/scraper_manager.py`: flags read at
  `:126-152` (`git_enabled` `:127-129` is `True` when the `git` section is not a dict;
  `indico_enabled` `:131-135`; `sso_enabled` `:141`; `elog_enabled` `:149-151` needs
  `enabled` AND `url`). Overrides at `:190-194`. Collect calls at `:214-218`.
  `schedule_collect_elog` `:407-416` passes catalog URLs as `extra_urls`; `collect_elog`
  `:418` appends `extra_urls` before it checks `elog_enabled` (`:436-438`).
  `_collect_urls_from_lists_by_type` `:605-642` (prefix branches `:616-638`; `sitemap-`
  is peeled before the ELOG heuristic). `_is_elog_url` `:763` (static, path contains
  `/elog/` or `/elogs/`); `_is_indico_url` `:772` (returns False unless `indico_enabled`,
  and reads `indico.base_url`). `_extract_urls_from_file` `:864-876`.
- CLI: `_collect_input_lists` `config_manager.py:403-416`; `get_enabled_sources`
  `:418-434` (filters by `source_registry.names()`); `get_disabled_sources` `:436-452`;
  `set_sources_enabled` `:454-470` (writes `false` for every managed source whose
  `enabled` key is absent). `cli_main.py:206-212` drops any source that a config
  explicitly disables. `templates_manager.py:1291-1306` uses list paths as given and warns
  on a non-file.
- Other prefix sites: `src/cli/tools/sources_builder.py:43` (`_EXTRA_PREFIXES`),
  `src/utils/goldenset_maintenance.py:581-591` (`SITEMAP_PREFIX`, `SSO_PREFIX`,
  `FANOUT_PREFIXES`).
- Template `src/cli/templates/base-config.yaml`: git and sso render `true` when absent
  (`:426`, `:435`); indico renders `default(false, true)` (`:440`); elog renders
  `default(false, true)` (`:504`).
- `src/cli/source_registry.py` registers links, sso, git, jira, indico. It does **not**
  register `elog` (or redmine). It imports only `dataclasses` and `typing`;
  `src/cli/__init__.py` is empty, and the data-manager image copies all of `src/` and
  runs `pip install .`, so the scraper can import it.

## Goals / Non-Goals

**Goals:** one precedence rule, enforced the same way by the CLI and the scraper; one
definition of the prefix map; the operator's explicit `false` is final.

**Non-Goals:** `links_enabled`, `sitemap-` entries, redmine/jira (no list prefix), the
restart path (#461), the multi-config `set_sources_enabled` overwrite behavior.

## Decisions

### D1. Shared definitions live in `src/cli/source_registry.py`

Add, at module level:
- `INPUT_LIST_PREFIXES: Dict[str, str] = {"git-": "git", "sso-": "sso", "elog-": "elog",
  "indico-": "indico"}` — the only literal `"git-"` in `src/`.
- `SITEMAP_PREFIX = "sitemap-"` is NOT moved (out of scope); the scraper keeps it.
- `split_prefixed_entry(entry: str) -> Optional[Tuple[str, str]]` → `(source, url)` for a
  prefixed entry, else `None`.
- `is_elog_url(url: str) -> bool` — moved from `ScraperManager._is_elog_url` (the static
  method stays as a one-line delegate so its callers and tests keep working).
- `read_input_list_entries(path) -> List[str]` — the parse from `_extract_urls_from_file`
  (strip, skip blank and `#` lines, keep the text before the first `,`).
  `_extract_urls_from_file` delegates to it.

`sources_builder._EXTRA_PREFIXES = tuple(INPUT_LIST_PREFIXES)`.
`goldenset_maintenance.SSO_PREFIX` and `FANOUT_PREFIXES` derive from the map (the keys
whose source is `sso`, and the keys whose source is git/elog/indico, in map order).
Why here: the issue names it; it has no flask dependency (#468), and both sides can
import it.

### D2. `elog` joins the source registry

`SourceDefinition(name="elog", description="ELOG logbook scraping",
depends_on=["links"])` — no secrets, no required config fields (a list-only ELOG has no
`elog.url`). Why: `get_enabled_sources` filters by registry names and
`set_sources_enabled` writes only registry names. Without this, the CLI cannot honor
"the source joins `enabled_sources` and `set_sources_enabled` writes `enabled: true`" for
`elog-` entries. The template then renders `false`, and the new scraper rule would skip
the shipped `examples/deployments/basic-ollama-fnal/dcache-elog.list` entry.
Side effect: `set_sources_enabled` now writes `elog.enabled: false` for an unselected
elog with no key. The template already renders `false` for that case, so the rendered
config does not change. `archi list-services` (`cli_main.py:684`, sources loop at `:707`) lists elog.

### D3. The ELOG path heuristic counts as an `elog` entry in the CLI

Unprefixed entries with `/elog/` or `/elogs/` in the path land in `elog_urls` today and
are collected whatever the flag says. The CLI writes `elog.enabled: false` for them
(D2), so without inference they would stop being collected. The CLI therefore uses the
same classifier as the scraper: `sitemap-` entries are ignored, prefixed entries map via
`split_prefixed_entry`, and other entries count as `elog` when `is_elog_url` is true.
Classification order, for both sides: `sitemap-`, then all four prefixes, then the ELOG
heuristic on unprefixed entries, then (scraper only) the Indico heuristic. This is a
deliberate small fix: today `indico-https://h/elog/x` hits the ELOG heuristic before the
`indico-` check and lands in `elog_urls` with the prefix still attached; now it is an
`indico` entry. The
Indico host heuristic is NOT used by the CLI: the scraper applies it only when
`indico_enabled` is already true, so it never enables a source.

### D4. Scraper three-state rule

Add `_input_list_flag(sources_config, name) -> Optional[bool]`: if the section is a
`bool` → that value; if a dict whose `enabled` is not `None` → `bool(enabled)`; else
`None` (absent). Read the raw config, not the coerced attributes.

In `collect_all_from_config`, replace `:190-194` with a loop over git, sso, elog,
indico. For a source with N > 0 list URLs:
- flag `False` → set that list to `[]`; one `logger.warning` that names the source and N,
  e.g. `"Skipping 2 input-list entries for source 'git': data_manager.sources.git.enabled
  is false"`. Do not change the source's own enabled attribute.
- flag `None` → set the enabled attribute to `True` (`git_enabled`, `sso_enabled` plus
  `_ensure_sso_defaults()`, `indico_enabled`); one `logger.info` that names the source
  and N. For elog, collecting `extra_urls` needs no attribute change.
- flag `True` → collect (sso still calls `_ensure_sso_defaults()`, as today).

`elog_enabled` keeps its `enabled AND url` meaning for the dedicated `elog.url`.
`schedule_collect_elog`: when the elog flag is `False`, log one WARNING and return
without collecting catalog URLs.

Constructor fixes for a boolean section (`git: false`, `sso: false`):
- `sso` (`:91-93`, `:141`): today a bool section raises `AttributeError` at
  `sso_config.get`. Normalize as git and indico do:
  `self.sso_enabled = sso_config if isinstance(sso_config, bool) else
  bool((sso_config or {}).get("enabled", False))`, and keep a dict for later reads.
- `git` (`:127-129`): today any non-dict section gives `git_enabled = True`, so
  `git: false` still lets `schedule_collect_git` re-collect catalog URLs. Map a bool
  section to its value; keep `True` for a missing or `None` section.
With these, the git, sso, and indico scheduled paths are gated by their enabled
attributes inside `collect_*` (`collect_git` `:246`, `collect_indico`, `collect_sso`).

### D5. CLI inference in `get_enabled_sources`

Per config: compute the explicit flag per source (same three states as D4, from
`data_manager.sources.<name>`). Read every path in that config's
`data_manager.sources.links.input_lists` with `os.path.isfile` as the test (same as
`templates_manager`); a non-file → `logger.warning` naming the path, skip. Count entries
per source with the D3 classifier. For each source with a count > 0 that is a registry
name: flag `None` → add it to `enabled`; flag `False` → `logger.warning("N input-list
entries for source 'X' will be skipped: data_manager.sources.X.enabled is false")`;
flag `True` → already enabled. Keep the existing `enabled`-key logic unchanged.
`get_disabled_sources` is unchanged, so `cli_main.py:209-211` still removes a source that
any config disables explicitly.

## Risks / Trade-offs

- Paths resolve relative to the CLI's working directory, as `templates_manager` does.
  A list path that `templates_manager` cannot copy is also skipped here, with a warning.
- `get_enabled_sources` has three callers: create (`cli_main.py:206`), restart
  (`:607`, feeds `get_secrets`/`validate_configs`, no `set_sources_enabled`), and the
  evaluate create path (`:872`, then `set_sources_enabled` at `:892`). Inference applies
  to all three, so restart now also validates inferred sources' secrets, with list paths
  resolved against restart's working directory. This overlaps #461 (restart
  normalization); no restart code changes here. The warning fires once per call.
- An inferred source gets the full validation of `enabled: true`: its secrets AND its
  `required_config_fields`. For `sso` that is
  `data_manager.sources.links.selenium_scraper.selenium_class`
  (`source_registry.py:38-41`, checked by `_validate_source_fields`). A config with
  `sso-` entries and no `sso` key that deploys today will now fail `archi create` until
  that field is set. This follows from the issue's rule; relaxing it is a separate
  decision.
- The shipped `examples/deployments/basic-openai` config has a `git-` entry and no
  `git` key, so `archi create` on it now needs `GIT_USERNAME`/`GIT_TOKEN`. The
  `env_file` fixture in `tests/unit/test_cli_create_dev_smoke.py:81-89` (also used by
  `test_render_preflight*.py`) must gain those two keys.
- Multi-config: if config A disables git and config B infers it, `cli_main` drops git and
  `set_sources_enabled` writes `false` into B, so B's entries are skipped with a scraper
  WARNING. That is "explicit false wins" across the deployment; documented, not changed.
