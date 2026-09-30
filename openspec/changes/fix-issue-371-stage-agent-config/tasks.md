Work order: issue #371 (milestone v2026.11.0, P2, default tier). Branch
`fix/issue-371-stage-agent-config`, cut from `origin/dev` `e58a7ada`. Read `proposal.md` and
`design.md` in this directory first. Every task ends with a green gate and a commit — no task
ends red. Run `black` and `isort` on every file you touch **before** `git add`.

## 1. The host resolver: red, green, gate, commit

- [x] 1.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Append to `tests/unit/test_evaluations_config_validation.py` (after the
      last existing test; check that the last test keeps all of its assertions) tests for
      `resolve_agent_config_source` and `AGENT_CONFIG_STAGED_FILENAME`, imported from
      `src.utils.evaluations_config`. Build each config as
      `{"_config_path": str(tmp_path / "archi.yaml"), "services": {"chat_app": {"evaluations": {...}}}}`
      and write `archi.yaml` to disk. Cover:
      (a) `enabled` absent, `False`, or `"true"` → returns `None` even for a missing path;
      (b) an existing absolute file → returns that path resolved;
      (c) a relative `"eval/agent.yaml"` that exists under `tmp_path/eval/` → returns
          `tmp_path / "eval" / "agent.yaml"` resolved (it resolves against the YAML's
          directory, never the process cwd — `monkeypatch.chdir` to another dir to prove it);
      (d) a missing file → `ValueError` whose message contains
          `services.chat_app.evaluations.agent_config_path`, `not found`, and the resolved
          path string;
      (e) a directory → `ValueError` containing `must be a file`;
      (f) a file with mode `0o000` → `ValueError` containing `not readable` (skip when
          `os.geteuid() == 0`);
      (g) the value names `archi.yaml` itself (relative `"archi.yaml"` and its absolute path)
          → `ValueError` containing `live deployment config`;
      (h) `"/root/archi/configs/config.yaml"` → the existing live-config refusal (message
          contains `live deployment config`);
      (i) a relative value with no `_config_path` key → `ValueError` naming the key;
      (j) blank or missing value → the existing `is required` refusal;
      (k) `AGENT_CONFIG_STAGED_FILENAME == "qa_agent_config.yaml"`.
      Run `python -m pytest tests/unit/test_evaluations_config_validation.py -q --no-header`
      and confirm the new tests fail with `ImportError` for `resolve_agent_config_source`
      (not a fixture or syntax error).

      **Then green.** In `src/utils/evaluations_config.py` add
      `AGENT_CONFIG_STAGED_FILENAME` and `resolve_agent_config_source(config)` exactly as in
      design D1. Update the `validate_evaluations_config` docstring as D1 says. Do not change
      its behavior. Run the file's tests (all pass), format, `git add`, `bash scripts/gate.sh`
      exits 0, commit `feat(#371): resolve the evaluations agent config on the host`.

## 2. Refuse at validation time, above the teardown

- [x] 2.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Append a test to `tests/unit/test_cli_create_dev_smoke.py`, modelled on
      `test_force_create_with_enabled_evaluations_and_no_agent_config_path_keeps_existing_deployment`
      (reuse `_existing_deployment`, `_record_teardowns`, and the same `create --force`
      invocation). Set `evaluations = {"enabled": True, "agent_config_path": str(tmp_path / "absent.yaml")}`.
      Assert `teardowns == []`, the marker file still exists, `exit_code != 0`, and the
      output contains `services.chat_app.evaluations.agent_config_path` and `not found`.
      Append to `tests/unit/test_evaluation_config.py` one test that
      `_validate_chat_app_config` raises for an enabled config whose `agent_config_path`
      names a missing absolute `tmp_path` file. Confirm both new tests fail on their
      assertions.

      **Then green.** In `ConfigurationManager._validate_chat_app_config`
      (`src/cli/managers/config_manager.py`), call `resolve_agent_config_source(config)`
      right after the evaluations root check (design D2). Then run
      `python -m pytest tests/unit/test_evaluations_root_validation.py tests/unit/test_evaluation_config.py tests/unit/test_cli_create_dev_smoke.py -q --no-header`.
      Fix every test that now fails only because its enabled config names a nonexistent
      path: give it a real `tmp_path` file (for
      `tests/unit/test_evaluations_root_validation.py`, make `_chat_app_config` take the path
      and create the file in each test). Never weaken the check to make a test pass.
      Confirm `git diff origin/dev -- tests/unit | grep -c '^-.*def test_'` prints `0`.
      Format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `feat(#371): refuse a missing evaluations agent config before teardown`.

## 3. Stage, rewrite, and mount

- [ ] 3.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Append to `tests/unit/test_evaluation_config_staging.py` a class
      `TestEvaluationAgentConfigStaging`, reusing `_template_manager`, `_context` and
      `_FakeConfigManager`. Cover:
      (a) enabled, relative `agent_config_path` to an existing file beside the YAML →
          after `_stage_agent_config(context)` then `_render_config_files(context)`, the
          file `base_dir/evaluation_config/qa_agent_config.yaml` has the source bytes,
          `context.evaluation_agent_config_staged is True`, and the rendered
          `configs/config.yaml` has
          `services.chat_app.evaluations.agent_config_path == "/root/archi/evaluation_config/qa_agent_config.yaml"`;
      (b) enabled with `mcp_config_path` unset → the rendered `mcp_config_path` is `None`
          and `agent_config_path` is the fixed path;
      (c) disabled → the flag is `False`, no staged file, and a stale staged file placed
          before the call is removed;
      (d) a compose render (follow `_render_compose` in `tests/unit/test_evaluation_config.py`)
          with `evaluation_agent_config_staged=True` and `evaluation_mcp_configured=False`
          contains the chatbot volume `./evaluation_config:/root/archi/evaluation_config:ro`;
          with both `False` it does not;
      (e) `TemplateManager._build_workflow` lists `_stage_agent_config` directly after
          `_stage_evaluation_config` (inspect the returned stage list).
      Confirm the new tests fail on `AttributeError`/assertions, not on fixture errors.

      **Then green.** Implement design D4 in `src/cli/managers/templates_manager.py` and
      `src/cli/templates/base-compose.yaml`. Import `AGENT_CONFIG_STAGED_FILENAME` and
      `resolve_agent_config_source` from `src.utils.evaluations_config`. Run
      `python -m pytest tests/unit/test_evaluation_config_staging.py tests/unit/test_evaluation_config.py -q --no-header`
      (all pass, including the existing MCP staging tests). Format, `git add`,
      `bash scripts/gate.sh` exits 0, commit
      `feat(#371): stage the evaluations agent config into evaluation_config/`.

## 4. Refuse a source inside the deployment directory

- [ ] 4.1 Write the red, make it green, gate, commit — all in this one task.

      **Red first.** Append to `tests/unit/test_evaluations_config_validation.py` tests for
      `refuse_agent_config_inside_deployment(configs, base_dir)`: a source under `base_dir`
      (for example `base_dir/configs/config.eval.yaml`, file created) → `ValueError` naming
      the key, the source path, and containing `outside`; a source outside `base_dir` →
      no raise; a disabled config with a path under `base_dir` → no raise; a list of two
      configs where only the second is inside → raises.
      Append to `tests/unit/test_cli_create_dev_smoke.py` one `create --force` test (same
      pattern as task 2.1) whose `agent_config_path` names a file created inside the
      existing deployment directory `existing / "configs" / "config.eval.yaml"`: assert
      `teardowns == []`, the marker still exists, `exit_code != 0`, the output names the
      key. Confirm the new tests fail.

      **Then green.** Add `refuse_agent_config_inside_deployment` to
      `src/utils/evaluations_config.py` (design D3). In `src/cli/cli_main.py`, in `create`
      and in `evaluate`, add exactly one call
      `refuse_agent_config_inside_deployment(config_manager.get_configs(), base_dir)`
      directly above `remove_existing_deployment(`, plus the import. Change nothing else in
      `cli_main.py`; `git diff origin/dev -- src/cli/cli_main.py` shows at most 6 added
      lines. Format, `git add`, `bash scripts/gate.sh` exits 0, commit
      `feat(#371): refuse an agent config inside the deployment directory`.

## 5. Docs and validate

- [ ] 5.1 Edit `docs/docs/configuration.md`, the `evaluations.agent_config_path` bullet
      (about lines 407-421). Replace the in-container description and the "place the
      redacted file in the deployment's own `configs/` directory" workaround with: the value
      is an absolute host path or a path relative to this deployment YAML (the same rule as
      `mcp_config_path`); it is required when `enabled` is `true`; `archi create` refuses a
      missing file, the deployment YAML itself, `/root/archi/configs/config.yaml`, and a
      file inside the deployment directory (which `archi create --force` deletes); Archi
      copies the file to `evaluation_config/qa_agent_config.yaml` on every create and
      points the running config at `/root/archi/evaluation_config/qa_agent_config.yaml`;
      Archi does not generate or redact it — the operator still names a redacted copy,
      because every run copies it into the run workspace the console serves. Add no heading
      and no anchor. Confirm no other line in `docs/` still tells the operator to place the
      agent config by hand: `grep -rn "agent_config_path" docs/docs`.
      If `openspec` is on PATH, run
      `openspec validate fix-issue-371-stage-agent-config --strict` and fix any error. If it
      is not on PATH (the loop container has none), note that in the PR body — Loop 1
      validated the change on the host. `git add`, `bash scripts/gate.sh` exits 0, commit
      `docs(#371): the evaluations agent config is a staged host file`.

## 6. Publish

- [ ] 6.1 Push the branch and open the PR. Push with
      `git push -u origin fix/issue-371-stage-agent-config`. Confirm the push landed on
      **fasrc/archi**, not a fork:
      `git ls-remote --heads origin fix/issue-371-stage-agent-config` must print the same SHA
      as `git rev-parse HEAD`. If it prints nothing, stop and write the halt reason to
      `STATUS.md`.
      Write the PR body to `/tmp/pr-body-371.md` — **never** under `docs/` — and include the
      literal line `Closes #371`, the new refusals (missing, not a file, unreadable, the
      deployment YAML, inside the deployment directory), the staged and runtime paths, the
      behavior change for relative values, and a note that `mcp_config_path` inside the
      deployment directory has the same teardown flaw and is out of scope. If no PR exists
      for the branch
      (`gh pr list --repo fasrc/archi --head fix/issue-371-stage-agent-config` is empty), run
      `gh pr create --repo fasrc/archi --base dev --title "feat(#371): stage the evaluations agent config at create time" --body-file /tmp/pr-body-371.md`.
      If one already exists, `gh pr edit <pr> --repo fasrc/archi --body-file /tmp/pr-body-371.md`.
      Verify the link: `gh pr view <pr> --repo fasrc/archi --json closingIssuesReferences`
      must list 371. If it does not, edit the body and re-verify.
      Then STOP. Do not merge this PR. A human merges, in daylight.

## Commands

```bash
python -m pytest tests/unit/test_evaluations_config_validation.py tests/unit/test_evaluation_config_staging.py tests/unit/test_evaluation_config.py tests/unit/test_evaluations_root_validation.py -q --no-header

# no test was deleted — must print 0
git diff origin/dev -- tests/unit | grep -c '^-.*def test_'

# the cli_main.py diff stays thin
git diff --stat origin/dev -- src/cli/cli_main.py

# the gate, before every commit
bash scripts/gate.sh
```
