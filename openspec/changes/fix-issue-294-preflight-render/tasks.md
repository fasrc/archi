## 0. Already done by Loop 1 — do NOT redo

- [x] 0.1 The branch `fix/issue-294-preflight-render` exists, cut from `origin/dev` at
      `ac3e7d1a`. You are on it. Do not re-branch and do not rebase.
- [x] 0.2 `openspec validate fix-issue-294-preflight-render --strict` passed on the host. The
      `openspec` CLI is **not usable in this container** — do not run it, and do not add a
      task that does. It is already green.
- [x] 0.3 The approach is decided (issue #294, operator, 2026-09-26/27) and every design
      question is answered in `design.md` (D1–D8). You are not searching for an approach.
      Where the issue body and the design differ, the design wins.
- [x] 0.4 Measured at `ac3e7d1a`: a real render of the smoke tests' `EXAMPLE_CONFIG`
      (`examples/deployments/basic-openai/config.yaml`) fails in `_check_ports_available` on
      this host because a real deployment holds the ports, and renders the whole tree when
      `TemplateManager._probe_port` is patched to return `None`. So every new test that runs
      a real render MUST patch `_probe_port` to `lambda self, port: None`, or it will pass on
      one machine and fail on another.

## Rules that apply to EVERY task below

- Code changes go in `src/cli/managers/templates_manager.py` (the method) and
  `src/cli/cli_main.py` (**call sites only** — `cli_main.py` new lines must be one-line calls
  or moves; the logic lives in `templates_manager.py`). New tests go in a new file
  `tests/unit/test_render_preflight.py`. The only edits to
  `tests/unit/test_cli_create_dev_smoke.py` are the three tests named in design D6.
- **Red and green live in the SAME task.** `bash scripts/gate.sh` runs before every commit,
  so a task that ends with the suite red can never be committed and the loop halts there.
  Watch the red fail, then make it green, then commit — all inside the one task.
- Do not implement a later task's behaviour early. Task 1 does not touch `cli_main.py`.
- Import the smoke-test fixtures you need (`env_file`, `archi_home`, `benchmark_config`,
  `fake_repo_root`) and helpers (`EXAMPLE_CONFIG`, `SENTINEL`, `_existing_deployment`,
  `_record_teardowns`) from `tests.unit.test_cli_create_dev_smoke`; do not copy them.
- Before you add a test, `grep -n 'def test_'` the target file and pick an unused name. The
  gate runs no linter, so a duplicate `def test_...` silently replaces the older test.
- Never delete a test. After each commit,
  `git diff origin/dev -- tests/unit/ | grep -c '^-def test_'` must print `0`.
- Run `black` and `isort` on every changed `.py` file **before** `git add`. After each
  commit, `git status --porcelain` must be empty.
- Never `git commit --no-verify`. Never add a `Co-Authored-By` or session trailer.
- Do not write any file under `docs/` except the edit in task 4. The PR body goes in
  `/tmp`, never in the repo. Do not add an entry to `docs/questions.md` unless you halt.

## 1. `TemplateManager.preflight_render` — render into a temp directory and discard it

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** In the new `tests/unit/test_render_preflight.py`, build a real
      `TemplateManager` and its inputs the way `create()` does for `EXAMPLE_CONFIG`
      (`--services chatbot`, host mode) — or drive `cli_main.create` with
      `DeploymentManager.start_deployment` patched to raise and
      `VolumeManager.create_required_volumes` patched to a no-op, and capture the
      `compose_config`, `config_manager`, `secrets_manager` and options by wrapping
      `TemplateManager.prepare_deployment_files`. Pick whichever is shorter; patch
      `_probe_port` to `None` either way (task 0.4). Then assert:
      (a) `preflight_render(plan, config_manager, secrets_manager, **options)` returns `None`,
      writes nothing under `plan.base_dir` (it does not exist afterwards, or is unchanged if
      it existed), and the directory it rendered into is gone after it returns — record it by
      wrapping `_run_workflow` or by patching `tempfile.TemporaryDirectory` with a recorder
      that delegates to the real one;
      (b) it ran the full stage list: patch `_build_workflow` with a wrapper that records
      each real stage's `__name__` as it runs it, and assert the recorded list equals the real
      list minus nothing — `_stage_source_copy` is called but copies nothing:
      `copy_source_code` is never called even when the caller passed `build=True`;
      (c) `_probe_port` is never called (patch it with a recorder), while an invalid port
      value still raises `ValueError` matching `Invalid port value` (reuse the
      `"notaport"` config shape from `test_force_create_with_invalid_port_keeps_existing_deployment`);
      (d) a stage that raises propagates the **same exception object** (patch `_build_workflow`
      to return `[raising_stage]`, catch it, compare with `is`), and the temp directory is gone
      afterwards;
      (e) the caller's options dict is not mutated (pass a dict, compare a copy after);
      (f) idempotence (design D8): render into directory A with `prepare_deployment_files`;
      then run `preflight_render` and `prepare_deployment_files` on the **same** objects into
      directory B (point each plan at its own `base_dir`); assert every file under A and B,
      excluding `archi_code/**` and `SOURCE_COMMIT`, has identical relative paths and bytes.
      Confirm (a)–(e) fail with `AttributeError: ... 'preflight_render'` (not a fixture error).
      (f) may fail the same way.

      **Then green** (design D1–D4): extract `_run_workflow(self, context)` from
      `prepare_deployment_files`; add `preflight_render` as design D1 says, with the options
      override `{**options, "build": False, "allow_port_reuse": True}`, a
      `tempfile.TemporaryDirectory(prefix="archi-preflight-")`, `context.base_dir` set after
      construction, and one `logger.error` naming the failed stage before re-raising unchanged.
      `_run_workflow` must expose the failing stage's name to that log line — either log it
      inside `_run_workflow` on `except` and re-raise, or let `preflight_render` do it; do not
      wrap the exception. Give the method a docstring that says what it skips and why (D2, D3).
      Update the `_check_ports_available` comment to mention the preflight skips the probe.

      Then: format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `fix(#294): add TemplateManager.preflight_render, a discard-only full render`.

## 2. `create()`: preflight before the teardown, volumes above it, `--dry` renders

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add to `tests/unit/test_render_preflight.py`:
      (a) `create --force` on an existing deployment (`_existing_deployment`,
      `_record_teardowns`, `check_docker_available` → `True`, `EXAMPLE_CONFIG`,
      `--services chatbot --hostmode`) with `TemplateManager._build_workflow` patched to
      return one stage raising `ValueError("late-render-sentinel")` (design D7) →
      `exit_code != 0`, `late-render-sentinel` in the output, `teardowns == []`, and
      `marker.txt` still exists;
      (b) the same with `--dry` added → the same four assertions, plus
      `cli_main.remove_existing_deployment` (patched with a recorder — under `--dry` it
      never calls `delete_deployment`, so `teardowns == []` alone is vacuous here) was never
      called, and `VolumeManager.create_required_volumes` (patched with a recorder) was never
      called;
      (c) a valid `create --force` (no raising stage, `_probe_port` → `None`) records, in one
      shared `events` list, `preflight` (wrap `preflight_render` so it still runs for real),
      `volumes` (patched no-op recorder), `teardown` (the `_record_teardowns` pattern, appending
      to `events`), and `render` (patch `prepare_deployment_files` to append and then raise
      `RuntimeError(SENTINEL)`) → `events == ["preflight", "volumes", "teardown", "render"]`;
      (d) a valid `create --dry` (no `--force`, no existing deployment) runs the preflight
      (recorder called once), creates no volume, and creates no
      `archi_home / "archi-smoke"` directory.
      Confirm (a)–(d) fail on their assertions.

      **Then green** (design D5): in `create()`, move `template_manager = TemplateManager(env, verbosity)`
      to directly after `enforce_base_images(...)`, then call
      `template_manager.preflight_render(compose_config, config_manager, secrets_manager, **other_flags)`,
      then (only `if not dry:`) the two `VolumeManager` lines, then the existing
      `remove_existing_deployment(...)` and `if dry:` block unchanged. Below the dry return,
      delete the old `TemplateManager(...)` and `VolumeManager` lines and reuse
      `template_manager`. Update the comment block above `remove_existing_deployment` in
      `create()` to say the full render now runs above it (#294).

      **Then fix the two `create` tests design D6 names** —
      `test_force_create_still_tears_down_once_validation_passes` and
      `test_force_create_continues_when_teardown_fails` — exactly as D6 says. Do not touch the
      other six `_stop_before_host_mutation` tests; run them and confirm they still pass.
      Run `python -m pytest tests/unit/test_cli_create_dev_smoke.py tests/unit/test_render_preflight.py -q --no-header`
      and confirm 0 failed.

      Then: format, `git add`, gate green, commit
      `fix(#294): create renders the replacement before the --force teardown`.

## 3. `evaluate()`: the same order, and the two agent_md_file routes

- [x] 3.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Add to `tests/unit/test_render_preflight.py`, modelled on
      `test_force_evaluate_still_removes_existing_runtime` (patch
      `preflight_benchmark_configs` to `([], [])`, `check_docker_available` → `True`,
      `_probe_port` → `None`):
      (a) `evaluate --force` on an existing runtime with `_build_workflow` patched to one stage
      raising `ValueError("late-render-sentinel")` → non-zero exit, the sentinel text in the
      output, no teardown, `marker.txt` exists;
      (b) `evaluate --force --config-dir <dir>` with two copies of the `benchmark_config`
      fixture's text whose `services.benchmarking.agent_md_file` point at two different files
      with the same basename (write `tmp_path/a/agent.md` and `tmp_path/b/agent.md`; give each
      config its own `name` and benchmarking `name`) → non-zero exit, the output contains the
      basename-collision message raised in `_stage_agents` (`templates_manager.py:566-569`:
      assert on `same basename 'agent.md'`), no teardown;
      (c) `evaluate --force` whose `agent_md_file` does not exist → non-zero exit, the output
      contains `agent_md_file not found`, no teardown. `config_manager.py:347-348` already
      refuses this before the teardown on `origin/dev`, so (c) is green at the start — keep
      it as a regression test and say so in the commit body; do not invent a red for it;
      (d) a valid `evaluate --force` records `events == ["preflight", "volumes", "teardown", "render"]`
      the way task 2(c) does.
      Confirm (a), (b) and (d) fail on their assertions.

      **Then green** (design D5): in `evaluate()`, directly after `enforce_base_images(...)`,
      construct `template_manager`, call `preflight_render(...)` with the same arguments the
      real `prepare_deployment_files` call passes, then the two `VolumeManager` lines, then the
      unchanged `remove_existing_deployment(...)` and `base_dir.exists()` refusal. Below,
      delete the old constructor and `VolumeManager` lines and reuse `template_manager`.
      Rewrite the `:835-849` comment so it says #294 closed the class for deterministic render
      failures: the full render now runs above the teardown, and only non-deterministic
      failures (a port held by another process, a full disk, `compose up`) remain after it.

      **Then fix** `test_force_evaluate_still_removes_existing_runtime` exactly as design D6
      says. Run both test files and confirm 0 failed.

      Then: format, `git add`, gate green, commit
      `fix(#294): evaluate renders the replacement before the --force teardown`.

## 4. Docs

- [x] 4.1 Edit `docs/docs/fasrc_archi.md`, the `--force` note (grep
      `This does not make \`--force\` safe in general`). Replace the sentences that say
      closing the class "means rendering the replacement … ([#294](...))" with: the whole
      replacement is now rendered into a temporary directory and discarded before anything
      is destroyed, for both `archi create --force` and `archi evaluate --force`, and a
      `--dry` run performs the same render check. Keep the rest of the paragraph: failures
      while *starting* the deployment still leave you without one, and a port held by
      another process is still found only after the teardown. Keep the `#294` link. Add no
      heading and no anchor. Then `bash scripts/gate.sh` exits 0, `git status --porcelain`
      empty after the commit. Commit `docs(#294): the --force note describes the render preflight`.

## 5. Publish

- [ ] 5.1 Push the branch and open the PR. The branch has no upstream — push with
      `git push -u origin fix/issue-294-preflight-render`. Confirm the push landed on
      **fasrc/archi**, not a fork: `git ls-remote --heads origin fix/issue-294-preflight-render`
      must print the same SHA as `git rev-parse HEAD`. If it prints nothing, the push went
      elsewhere — stop and write the halt reason to `STATUS.md`.
      Write the PR body to `/tmp/pr-body-294.md` — **never** under `docs/` — with the literal
      line `Closes #294`, the new call order in both commands, the three smoke tests whose stop
      point moved and why (design D6), the early checks kept (none removed — they give better
      messages), the test count before and after, and the residual non-deterministic failures.
      If no PR exists for the branch
      (`gh pr list --repo fasrc/archi --head fix/issue-294-preflight-render` is empty), run
      `gh pr create --repo fasrc/archi --base dev --title "fix(#294): render the replacement deployment before the --force teardown" --body-file /tmp/pr-body-294.md`.
      If the review gate already opened one, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-294.md`
      instead. Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 294. If it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
# the two affected suites — 0 failed after every task
python -m pytest tests/unit/test_cli_create_dev_smoke.py tests/unit/test_render_preflight.py -q --no-header

# no test was deleted — must print 0
git diff origin/dev -- tests/unit/ | grep -c '^-def test_'

# the gate, before every commit
bash scripts/gate.sh
```
