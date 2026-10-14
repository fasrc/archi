## Why

PR #613 (fix #460) made `ConfigurationManager.get_enabled_sources` warn about and skip an input list that cannot be read. The path stays in the config, and two later consumers still raise on it (fasrc/archi#636):

1. **Staging.** `TemplateManager._copy_web_input_lists` (`src/cli/managers/templates_manager.py:1279`) checks only `os.path.isfile()` and then calls `shutil.copyfile`. A mode-000 list raises `PermissionError`. This runs inside `prepare_deployment_files()`, so under `archi create --force` it fails after the old deployment was torn down (the hazard of #287).
2. **Runtime.** `ScraperManager._collect_urls_from_lists` (`src/data_manager/collectors/scrapers/scraper_manager.py:663`) calls `_extract_urls_from_file` → `read_input_list_entries`, which raises `UnicodeDecodeError` on a list that is not valid UTF-8 (such a list copies fine at staging). No caller catches it.

Measured 2026-10-08 on the dev host: `shutil.copyfile` on a mode-000 file raises `PermissionError`; `read_input_list_entries` on `b"\xff\xfe\x00git-https://x\n"` raises `UnicodeDecodeError`.

## What Changes

- `_copy_web_input_lists` catches `OSError` from `shutil.copyfile`, logs a WARNING that names the path, and continues with the next list.
- `_collect_urls_from_lists` catches `(OSError, UnicodeDecodeError)` from `_extract_urls_from_file`, logs a WARNING that names the path, sets `self._input_lists_complete = False` (as the missing-file branch does), and continues.
- The list file format does not change. `read_input_list_entries` does not change.

## Capabilities

### New Capabilities
- `unreadable-input-list-tolerance`: deployment staging and the data-manager runtime skip an unreadable input list with a warning instead of raising.

### Modified Capabilities
None.

## Impact

- `src/cli/managers/templates_manager.py` (`_copy_web_input_lists`)
- `src/data_manager/collectors/scrapers/scraper_manager.py` (`_collect_urls_from_lists`)
- New test file `tests/unit/test_unreadable_input_lists_consumers.py`
