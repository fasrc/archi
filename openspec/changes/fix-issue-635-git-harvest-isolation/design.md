## Context

`GitScraper.collect` has three per-URL steps: parse (line 111–116, `except ValueError`), clone
(line 118–123, `except Exception`), and harvest (line 125–128, `try`/`finally` only). The first two
steps record `(repo_name_or_None, str(exc))` in `self.last_failures` and `continue`. The harvest
step does not catch anything.

## Goals / Non-Goals

**Goals:**
- One repository's harvest exception does not stop the loop.
- The resources of the other repositories are returned.
- The failure is logged at ERROR and recorded in `last_failures`.
- The clone directory of the failed repository is still removed.

**Non-Goals:**
- Keep the partial resources of the failed repository. `_harvest_repository` builds one list
  and returns it; an exception loses that list. The recorded failure makes the reconcile pass
  skip that repository's scope, so no row of that repository is falsely reported as uncollected.
- Change `_harvest_mkdocs` or `_harvest_code` internal error handling.
- Catch `BaseException` (`KeyboardInterrupt`, `SystemExit`). These must still stop the run.

## Decisions

**D1. Catch `Exception` at the harvest step, same as the clone step.** The harvest step can raise
`OSError`, YAML errors, `UnicodeDecodeError`, and errors from git objects. A narrow list would
miss one, and the issue is exactly an unexpected error type. The clone step already uses
`except Exception`, so this matches the existing idiom.

**D2. Order: `except` before `finally`.** Python runs the `except` body, then the `finally`
body, then the `continue` takes effect. As a result the cleanup still runs for the failed
repository. The `continue` is not strictly necessary (the `try` is the last statement in the loop
body) but it states the intent; either form is acceptable.

**D3. Log message.** `logger.error(f"Failed to harvest {url}: {exc}")`, the same shape as the
clone message `Failed to clone {url}: {exc}`.

**D4. `last_failures` entry.** `(repo_name, str(exc))`, where `repo_name` is the value already
parsed at line 112. This is the same tuple shape as the clone failure.

## Risks / Trade-offs

- A bug in harvest code that used to fail loudly now only logs an ERROR and records a failure.
  Mitigation: the reconcile pass logs a WARNING for each failed scope (#534 spec), and the ERROR
  log names the URL.

## Test plan

In `tests/unit/test_git_failure_recording.py`, reuse `_make_git_scraper` and `_fake_repo_info`.
Patch `_prepare_repository` to return a `repo_info` whose `repo_path` is a real directory under
`tmp_path` (one directory for each URL), and patch `_harvest_repository` to raise
`OSError("boom")` for the first repository and return one fake resource for the second.
Assert: no exception; the return value is the second repository's resource; an ERROR record names
the first URL; `last_failures == [(<first repo name>, "boom")]`; both clone directories are gone.
Use real URLs that `_parse_url` accepts (for example `https://github.com/org/repo-a` and
`https://github.com/org/repo-b`), or patch `_parse_url` too.
