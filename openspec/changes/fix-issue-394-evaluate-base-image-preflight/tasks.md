# Tasks — run the base-image preflight on the evaluate path above the teardown

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
tests, watch them fail for the stated reason, write the smallest fix, run
`bash scripts/gate.sh`, commit. Never end a task with the suite red, and never use
`--no-verify`.

Standing notes for every task:

- **Scope.** The only production file to edit is `src/cli/cli_main.py`, and the only edit is
  one call. The only test file to edit is `tests/unit/test_cli_create_dev_smoke.py`. Do not
  touch `src/cli/managers/base_image_preflight.py`, `src/cli/templates/**`, `deploy/**`,
  `config/**`, `.github/workflows/**`, `scripts/gate.sh`, `ralph.conf`, `PROMPT.md`, the
  `Makefile`, or the `Containerfile`.
- **No decision logic in `cli_main.py`.** The call site only. `base_image_preflight.py`'s
  docstring (design D8) keeps the decision logic in that module on purpose.
- **No import change.** `enforce_base_images` is already imported at `cli_main.py:12`. Do
  not add a second import.
- **Coverage.** `cli_main.py` is inside `--cov=src`, so the one new statement reports to
  `diff-cover` and must be covered. It already is: line `:900` does not appear in the
  Missing list when only the `evaluate` tests run. Both files are black 24.10.0 and
  isort 6.0.1 clean today. Run `black` and `isort` on both files **before** `git add`, so the
  pre-commit writer cannot leave content out of the commit, and check `git status` is empty
  after each commit.
- **Run `python -m pytest`, not bare `pytest`**, so an editable install cannot resolve `src`
  to a different checkout.
- **Append, do not insert.** New tests go at the END of the test file under a comment banner
  that names issue #394. The file has 2191 lines today and its current last line,
  `    ), f"the error should name the missing key. output:\n{result.output}\n"`, must appear
  unchanged as trailing context in
  `git diff origin/dev -- tests/unit/test_cli_create_dev_smoke.py`. Nightly runs have
  inserted new tests above the final line and swallowed the previous test's last assertion.
- **Commit messages** are short and lowercase, and contain no `$` and no `${...}`. A shell
  variable in `git commit -m` aborts the commit under `set -u` and leaves the change staged
  while the push reads "up-to-date". No `Co-Authored-By` and no session trailer.
- **Known flake.**
  `tests/unit/evaluation/qa/test_jobs_history.py::test_job_manager_terminates_running_evaluation_process`
  has raced under CPU load and is unrelated to this change. If the gate fails on only that
  test, re-run it once.

## 1. The ordering guarantee

- [ ] 1.1 `model: sonnet` — Add the two evaluate-path ordering tests to
      `tests/unit/test_cli_create_dev_smoke.py`, watch both fail, then insert the one call in
      `src/cli/cli_main.py`.

      **Tests first.** Append to the END of the test file, under the banner
      `# --- issue #394: the evaluate path runs the base-image preflight above the teardown ---`.
      Both use only fixtures that already exist; add no new fixture and no new import.

      Test A, `test_force_evaluate_with_unobtainable_base_image_keeps_existing_deployment`,
      signature `(archi_home, env_file, benchmark_config, monkeypatch)`. Mirror the create
      test at `:1855`. Body, in order: import `cli_main` from `src.cli` and
      `base_image_preflight` from `src.cli.managers`; `existing = _existing_deployment(archi_home)`;
      `teardowns = _record_teardowns(monkeypatch)`; monkeypatch
      `cli_main.check_docker_available` to `lambda: True`; monkeypatch
      `cli_main.preflight_benchmark_configs` to `lambda configs: ([], [])`;
      `_patch_probe(monkeypatch, fetch_error=base_image_preflight.Cause.UNAUTHORIZED)`.
      Then invoke with the shape from `:1285`:
      ```python
      runner = CliRunner()
      result = runner.invoke(
          cli_main.evaluate,
          [
              "--force",
              "-n",
              "smoke",
              "-c",
              str(benchmark_config),
              "-e",
              str(env_file),
          ],
      )
      ```
      Assert all three, in this order:
      `assert result.exit_code != 0, f"expected refusal. output:\n{result.output}"`;
      `assert teardowns == [], f"runtime was torn down before the refusal: {teardowns}"`;
      `assert (existing / "marker.txt").exists(), "existing runtime was destroyed"`.
      The empty-list assertion is the one the acceptance criteria require — do not weaken it
      to "an exception was raised".

      Test B,
      `test_force_evaluate_with_an_uncoverable_service_template_keeps_existing_deployment`,
      signature `(archi_home, env_file, benchmark_config, monkeypatch, tmp_path)`. Mirror the
      create test at `:1897`. Build the template directory first: `templates = tmp_path / "dockerfiles"`,
      `templates.mkdir()`, write `Dockerfile-chat` with
      `"FROM ghcr.io/fasrc/a2rchi-python-base@sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8\n"`
      and `Dockerfile-probe` with `"FROM docker.io/library/python:3.11\n"`, then
      `monkeypatch.setattr(base_image_preflight, "TEMPLATE_DIR", templates)`. Then the same
      four patches as Test A except the probe, which is `record = _patch_probe(monkeypatch)`
      with no `fetch_error`. Same `runner.invoke` shape. Assert, in this order:
      `result.exit_code != 0`; `"Dockerfile-probe" in result.output` with the message
      `"the refusal must name the uncoverable template, or the operator cannot act on it. "`
      plus the output; `teardowns == []`; `(existing / "marker.txt").exists()`;
      `record["pulled"] == []` with the message
      `"the refusal must precede any image work, which is what puts it above the teardown; "`
      plus `record['pulled']`.
      **Do not reorder these assertions** (design D5): on the parent commit Test B exits
      non-zero for the wrong reason — the teardown ran and the post-removal guard raised
      "already exists" — so the message assertion is what makes it a real red.

      **Watch the red.** Run
      `python -m pytest tests/unit/test_cli_create_dev_smoke.py -k "unobtainable_base_image_keeps_existing_deployment or uncoverable_service_template_keeps_existing_deployment" -q`.
      Expect `2 failed, 2 passed, 37 deselected`. Test A must fail on
      `AssertionError: runtime was torn down before the refusal: [{'deployment_name': 'smoke', ...}]`
      and Test B on `AssertionError: the refusal must name the uncoverable template ...`
      whose captured output contains `Benchmarking runtime 'smoke' already exists`. If either
      fails on an import, a fixture, or a config error instead, the test is wrong — fix the
      test, not the production code. Record the failure text; the PR body must quote it.

      **Then the minimum code.** In `src/cli/cli_main.py`, insert directly above the
      `remove_existing_deployment(` call in `evaluate()` (`:900` on the parent commit),
      separated from it by one blank line:
      ```python
      enforce_base_images(
          compose_config,
          use_podman=other_flags.get("podman", False),
          dry=False,
      )
      ```
      Do not assign the result and do not pass `dry=dry` (design D2: `evaluate` has no
      dry-run path, and `:900` already passes a literal `False`). Add nothing else — no
      comment block copied from the create path, no reporting, no try/except.

      **Then green.** Re-run the two tests (expect `4 passed, 37 deselected`), then
      `python -m pytest tests/unit/test_cli_create_dev_smoke.py -q` (all green, including the
      five existing `evaluate` tests and the two create-path ordering tests at `:1855` and
      `:1897`, all unchanged), then `python -m pytest tests/unit/ -q`. The baseline on the
      host with this change is 3897 passed, 2 skipped, 1 xfailed; the loop container collects
      about two fewer tests than the host, which is a collection gap and not a regression.
      `bash scripts/gate.sh` green; commit.

## 2. Close out

- [ ] 2.1 `model: sonnet` — Verify, push, and open the PR. Steps, in order:

      1. `bash scripts/gate.sh` on the finished branch exits 0, and `git status` is empty.
      2. `grep -n "remove_existing_deployment" src/cli/cli_main.py` still shows exactly two
         call sites, and each is preceded **within its own function** by an
         `enforce_base_images` call. `grep -c "enforce_base_images" src/cli/cli_main.py`
         shows 3 — the import plus two call sites.
      3. `git diff origin/dev --stat` lists only `src/cli/cli_main.py`,
         `tests/unit/test_cli_create_dev_smoke.py`, and this change's
         `openspec/changes/fix-issue-394-evaluate-base-image-preflight/` files.
         `git diff origin/dev -- src/cli/managers/base_image_preflight.py` prints nothing.
      4. `git diff origin/dev -- tests/unit/test_cli_create_dev_smoke.py` ends with the
         previous final line as unchanged trailing context, not as a removed line.
      5. Push: `git push -u origin fix/issue-394-evaluate-base-image-preflight`. The `-u` is
         required: a branch created with `git checkout -b ... origin/dev` tracks the trunk
         until the first `-u` push.
      6. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#394): run the base-image preflight on the evaluate path above the teardown"`.
         The body MUST contain `Closes #394` on its own line — a closing keyword in the title
         does not link the issue — and MUST contain these sections:
         **What** (one paragraph: one call inserted between `cli_main.py:890` and `:900`,
         matching `create()`'s shape at `:282`, return value discarded, no import change);
         **Scoping decision** (the whole declared service set, reusing `create`'s call shape,
         settled with the operator on 2026-09-04; no template-subset parameter and no
         narrowing of `_refuse_uncoverable_templates`, which would re-open the fail-open that
         PR #391 closed);
         **Red on the parent commit** — quote the recorded failure text for both tests,
         naming that Test A fails on `teardowns == []` and Test B on the missing
         `Dockerfile-probe` message, and state that both pass on the change;
         **Existing tests** (all five `evaluate` tests and both create-path ordering tests
         at `:1855` and `:1897` pass unchanged; none of the five patches the container probe,
         and the new call is above the teardown, so this was measured rather than assumed —
         design D3);
         **Gate** (exit 0, the pass/skip/xfail counts, and the patch-coverage number).
         Cite this issue and PR #391.
      7. If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop. The nightly
         wrap-up opens it from the host.
      8. Record the PR URL as a line under this task, tick the task, and commit that edit
         with the gate. **Do not merge.**
