## Why

A `git-` or `sso-` entry in `input_lists` silently turns a disabled source back on
(`scraper_manager.py:190-194`), and `elog-` and `indico-` entries are collected whatever
the flag says (`collect_elog` appends `extra_urls` before it checks `elog_enabled`). The
CLI never reads list entries, so it never validates that source's secrets, and the
symptom is an authentication failure inside the data manager (fasrc/archi#460).

The operator resolved the precedence rule on 2026-09-26: **the CLI infers a source from
the list, and an explicit `enabled: false` wins.**

## What Changes

- One shared definition of the input-list prefix → source map (`git-`, `sso-`, `elog-`,
  `indico-`), the ELOG path heuristic, and the list-file line parser, in
  `src/cli/source_registry.py` (no flask import). The scraper, `sources_builder.py`, and
  `goldenset_maintenance.py` use it. `grep -rn '"git-"' src/` returns one site.
- `elog` joins the CLI source registry (no secrets, depends on `links`). Without this the
  CLI cannot write `elog.enabled: true`, and the template renders an absent
  `elog.enabled` as `false` (`base-config.yaml:504`), so the shipped
  `basic-ollama-fnal` `elog-` entry stops being collected under the new rule.
- Scraper: replace the unconditional overrides with a three-state rule per source
  (git, sso, elog, indico): explicit `false` → skip that source's list entries with one
  WARNING (source name and entry count); key absent → the entries enable the source,
  with one INFO line; `true` → collect. `schedule_collect_elog` obeys explicit `false`.
- CLI: `ConfigurationManager.get_enabled_sources` reads the entries of each configured
  list file. A prefixed (or ELOG-heuristic) entry adds its source when that source's
  `enabled` key is absent. Explicit `false` keeps it out and logs a warning with the
  entry count. A missing list file logs a warning and is skipped.
- `docs/docs/configuration.md:126-133`: replace the CAUTION with the new rule.

## Capabilities

### New Capabilities
- `input-list-source-precedence`: how a prefixed `input_lists` entry and a source's
  `enabled` key combine, in the CLI and in the scraper.

### Modified Capabilities
- None.

## Impact

- `src/cli/source_registry.py`, `src/cli/managers/config_manager.py`,
  `src/cli/tools/sources_builder.py`, `src/utils/goldenset_maintenance.py`,
  `src/data_manager/collectors/scrapers/scraper_manager.py`, `docs/docs/configuration.md`.
- Behavior change: a deployment with `enabled: false` and list entries for that source
  stops collecting those entries. A CLI deploy whose list has a `git-`/`sso-`/`elog-`/
  `indico-` entry and no `enabled` key now requires that source's secrets and required
  config fields (for `sso`: `links.selenium_scraper.selenium_class`). The shipped
  `basic-openai` example now needs `GIT_USERNAME`/`GIT_TOKEN`.
- A boolean section (`git: false`, `sso: false`) is honored: today `sso: false` crashes
  the scraper constructor and `git: false` leaves git enabled.
- A hand-written config with `indico.enabled` absent and `indico-` entries now collects
  them (today `indico_enabled` defaults to false and skips them).
- Out of scope: `links_enabled` (`scraper_manager.py:126`), `sitemap-` entries, the
  restart path's own normalization (#461).
