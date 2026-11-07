## Why

`GitScraper.collect` (`src/data_manager/collectors/scrapers/integrations/git_scraper.py:101`)
loops over the configured git URLs. The harvest step at line 125–128 is a `try` with only a
`finally:` that removes the clone. It has no `except`. An exception that `_harvest_repository`
(line 150, which calls `_harvest_mkdocs` and `_harvest_code`) does not catch itself goes out of
`collect()`.

`ScraperManager._collect_git_resources` (`src/data_manager/collectors/scrapers/scraper_manager.py:953`)
persists resources only after `collect()` returns. As a result, one harvest exception in
repository N:

1. stops the harvest of repositories N+1 and later,
2. discards the resources already harvested from repositories 1 to N-1,
3. makes the whole git ingestion step fail.

A Codex adversarial review of PR #633 found this on 2026-10-08. It is pre-existing on `dev`
(confirmed again at `origin/dev` `0ed50a23` on 2026-10-12). The clone step (line 118–123) already
isolates a per-repository failure and records it in `last_failures`; the harvest step must do the
same.

## What Changes

- Add `except Exception as exc:` to the harvest `try` in `GitScraper.collect`, before the
  existing `finally:`. The handler logs an ERROR that names the URL and the error, appends
  `(repo_name, str(exc))` to `self.last_failures`, and continues with the next URL.
- The `finally:` clone cleanup stays and still runs for the failed repository.
- PR #633 (#534) merged on 2026-10-08, so `last_failures` exists on `dev`.
  `_collect_git_resources` already forwards each entry as a `("git", repo_name)` failure, so the
  reconcile report skips the failed repository's scope. No change to `scraper_manager.py`.
- No change to the URL-parse or clone failure paths.

## Capabilities

### New Capabilities
- `git-harvest-isolation`: a harvest exception in one git repository does not stop or discard
  the collection of the other configured repositories, and the failure is recorded.

### Modified Capabilities
None. (`catalog-reconcile-report` from #534 is not archived into `openspec/specs/` yet, so
this change does not modify it. Its existing contract — a scope with a recorded failure yields
zero candidates — already covers the new failure entry.)

## Impact

- `src/data_manager/collectors/scrapers/integrations/git_scraper.py`: about 4 lines in `collect()`.
- `tests/unit/test_git_failure_recording.py`: new tests.
- No config, deploy, or schema change.
