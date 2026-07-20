## 1. Red test — dry run must not need a runtime

- [ ] 1.1 In `tests/unit/test_cli_create_dev_smoke.py`, add
  `test_dry_run_succeeds_without_docker`: monkeypatch
  `src.cli.cli_main.check_docker_available` to return `False` (patch the name as bound in
  `cli_main`, not in `helpers` — see design decision 3), invoke `create` with
  `--dry -n smoke -c <EXAMPLE_CONFIG> -e <env_file>` via `CliRunner`, assert
  `result.exit_code == 0` and that "Docker is not available" is absent from the output.
- [ ] 1.2 Run `python -m pytest tests/unit/test_cli_create_dev_smoke.py -v` in a shell with
  a runtime present and confirm the new test FAILS on the `ClickException`. Record the
  failure output. Do not write implementation before seeing red.

## 2. Move the check off the dry path

- [ ] 2.1 In `src/cli/cli_main.py`, delete the Docker-availability block at lines 140-146
  (the `if not other_flags.get("podman", False) and not check_docker_available():` raise).
- [ ] 2.2 Re-insert the identical block immediately after the `if dry:` early-return branch
  (~line 214-215, after `print_dry_run_summary(...)` returns) and before the first
  container operation, with the message and `--podman` short-circuit byte-for-byte
  unchanged.
- [ ] 2.3 Re-run the test file; confirm `test_dry_run_succeeds_without_docker` and
  `test_dev_flag_prints_warning_in_dry_run` are both green.

## 3. Pin the non-dry contract

- [ ] 3.1 Add `test_non_dry_create_requires_docker`: same monkeypatch of
  `check_docker_available` → `False`, invoke `create` WITHOUT `--dry` and without
  `--podman`, assert non-zero exit and that the output contains "Docker is not available on
  this system".
- [ ] 3.2 Add `test_non_dry_create_with_podman_skips_docker_check`: monkeypatch
  `check_docker_available` → `False`, invoke `create` without `--dry` but with `--podman`,
  assert the run does NOT fail on the Docker message (it may fail later for unrelated
  reasons — assert on the absence of that message, not on exit code).

## 4. Un-skip the dry-run smoke coverage

- [ ] 4.1 Audit every test in `tests/unit/test_cli_create_dev_smoke.py` and determine which,
  if any, genuinely need `docker`/`podman` on PATH.
- [ ] 4.2 Remove the module-level `pytestmark = pytest.mark.skipif(...)` and its now-false
  explanatory comment. If step 4.1 found tests that truly need a runtime, give those a
  per-test `skipif` with a precise reason instead of a blanket module skip.
- [ ] 4.3 Verify in a runtime-less shell (`PATH` without docker/podman, e.g.
  `env PATH=/usr/bin:/bin python -m pytest tests/unit/test_cli_create_dev_smoke.py -v`
  after confirming neither binary resolves) that the dry-run tests RUN and pass rather
  than skip.
- [ ] 4.4 If un-skipping surfaces a failure unrelated to this change, do NOT fix it here:
  restore a narrow per-test skip with a precise reason and note the finding in the PR body.

## 5. Confirm scope boundary

- [ ] 5.1 Confirm `restart` (`src/cli/cli_main.py:436`) and `evaluate`
  (`src/cli/cli_main.py:713`) expose no `--dry` option (`grep -n '"--dry"'
  src/cli/cli_main.py` should show only the `create` option at line 90) and leave both
  checks untouched.

## 6. Gate and PR

- [ ] 6.1 Run `bash scripts/gate.sh` and confirm exit 0, including ≥80% diff coverage on
  the changed lines.
- [ ] 6.2 Commit with a short lowercase message and no `Co-Authored-By`/session trailers.
  Never use `--no-verify`.
- [ ] 6.3 Open the PR: `gh pr create --repo fasrc/archi --base dev`. Body must say
  `closes #112` and link both contrasting CI runs
  (https://github.com/fasrc/archi/actions/runs/29549066080 — failed;
  https://github.com/fasrc/archi/actions/runs/29549067994 — passed on the same commit).
  Do NOT merge.
