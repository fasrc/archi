## Context

`get_enabled_sources` already tolerates an unreadable list (`src/cli/managers/config_manager.py`, the `except (OSError, UnicodeDecodeError)` block near line 522). Its tests are in `tests/unit/test_config_manager_unreadable_input_lists.py`. Staging and the runtime reader are the two remaining consumers that raise.

## Goals / Non-Goals

**Goals:** a warning-and-skip at each of the two consumers; a good list next to a bad one is still staged and still read.

**Non-Goals:** no change to the list file format, to `read_input_list_entries`, or to `get_enabled_sources`. No attempt to decode non-UTF-8 lists with another codec.

## Decisions

1. **Staging catches `OSError` only.** `shutil.copyfile` does not decode, so a non-UTF-8 list stages normally; only file-system errors (`PermissionError`, and any other `OSError`) can occur. The existing `os.path.isfile` guard stays: it keeps its own warning for a missing path or a directory.
2. **Runtime catches `(OSError, UnicodeDecodeError)`**, the same pair as `get_enabled_sources`. The catch wraps only the `self._extract_urls_from_file(list_path)` call, so a bug elsewhere in the loop still raises.
3. **Runtime marks the read incomplete.** An unreadable list is no more usable than a missing one. `schedule_collect_links` reads `_input_lists_complete` to decide whether an empty sitemap result means "sources removed" or "read failed" (`scraper_manager.py:434`). Setting it to `False` keeps the current sitemap map, the same as the missing-file branch.
4. **Warnings name the path.** Staging logs the configured path (`input_list`); runtime logs the staged path (`list_path`). Tests assert that the path string is in `caplog.text`.
5. **New test file** `tests/unit/test_unreadable_input_lists_consumers.py`, not an append to an existing file. This avoids the trap where an appended test swallows the prior test's last assertion.

## Risks / Trade-offs

- A permanently unreadable list is now silent apart from a WARNING at each deploy and each scrape cycle. That matches `get_enabled_sources` and the missing-file behavior, which the operator already accepts.
- The permission tests are skipped under root (`os.geteuid() == 0`), as in the existing #460 tests. The non-UTF-8 test runs everywhere.
