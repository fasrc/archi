## 1. Isolate the harvest failure (test first, one green commit)

- [x] 1.1 In `tests/unit/test_git_failure_recording.py`, append tests at the END of the file (after the last existing test, keep its last assertion intact; use new, unique test names — grep the file first). Reuse `_make_git_scraper` and `_fake_repo_info`. Patch `GitScraper._prepare_repository` to return a `repo_info` whose `repo_path` is a real directory under `tmp_path` (one per URL, created with `mkdir`), and patch `GitScraper._harvest_repository` to raise `OSError("boom")` for repository A and return `[<one fake resource>]` for repository B. Use URLs `https://github.com/org/repo-a` and `https://github.com/org/repo-b` (check that `_parse_url` gives repo names `repo-a`/`repo-b`; if not, patch `_parse_url`). Tests: (a) `collect([url_a, url_b])` does not raise and returns exactly B's resource; (b) an ERROR record (use `caplog`) contains `url_a`; (c) `scraper.last_failures == [("repo-a", "boom")]`; (d) both clone directories do not exist after `collect`; (e) a `KeyboardInterrupt` raised from `_harvest_repository` still goes out of `collect` and the clone directory is removed. Run `python -m pytest tests/unit/test_git_failure_recording.py -q` and watch (a)–(d) FAIL with the `OSError` (e passes already). Then in `src/data_manager/collectors/scrapers/integrations/git_scraper.py` `collect()`, add before the harvest `finally:` (about line 127):
  ```python
  except Exception as exc:
      logger.error(f"Failed to harvest {url}: {exc}")
      self.last_failures.append((repo_name, str(exc)))
      continue
  ```
  Re-run the module and see all tests pass. Run black and isort on the two files BEFORE `git add`, then run `bash scripts/gate.sh` (bare, no pipe) and commit both files together: `git commit -m "git: isolate a per-repository harvest failure (#635)"`. Do not change any other file under `src/`.

## 2. Verify, push, and open the PR

- [ ] 2.1 Run `bash scripts/gate.sh` on the branch tip and confirm it is green. Confirm `git diff origin/dev --stat -- src tests` lists only `git_scraper.py` and `tests/unit/test_git_failure_recording.py`. Push with `git push -u origin fix/issue-635-git-harvest-isolation`. Open the PR with `gh pr create --repo fasrc/archi --base dev --title "git: isolate a per-repository harvest failure"`, with `Closes #635` in the PR **body** (not the title). The body states the before (tests a–d fail with `OSError: boom`) and after (all pass) results, and notes that PR #633 is merged so the failure is recorded in `last_failures`. No `Co-Authored-By` trailer. Do not merge.
