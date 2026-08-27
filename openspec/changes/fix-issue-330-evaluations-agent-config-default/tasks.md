# Tasks — refuse an enabled evaluations console at create time

Every checkbox below is one loop turn and ends **green and committed**. Where a checkbox
says RED, write the failing test, watch it fail, write the smallest fix, run the gate, and
commit — all inside that one checkbox. Never end a task with the suite red, and never use
`--no-verify`.

Run the project gate before every commit — the command is in `CLAUDE.md` under "Gate". On
this host it needs the project interpreter on `PATH`:

```
PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```

Focused runs while working:

```
/home/austin/miniforge3/envs/archi/bin/python -m pytest tests/unit/test_evaluations_config_validation.py -q
/home/austin/miniforge3/envs/archi/bin/python -m pytest tests/unit/test_evaluation_config.py tests/unit/test_evaluation_config_staging.py tests/unit/test_evaluation_console.py -q
```

Four standing notes for every task:

- **Scope.** The files this change edits are `src/utils/evaluations_config.py` (new),
  `src/cli/managers/config_manager.py`, `src/interfaces/chat_app/evaluation_console.py`,
  `src/cli/templates/base-config.yaml`, `docs/docs/configuration.md`, and the test files named
  per task. Do **not** edit `src/interfaces/chat_app/app.py` — the unit suite does not import
  it, so new lines there fail diff-cover. Do **not** edit
  `src/cli/managers/templates_manager.py`; `design.md` D2 records why the check does not go in
  the staging seam.
- **Both edited source files are black-clean at `origin/dev`** (verified 2026-08-27 with
  black 24.10.0). Keep them that way: run black **before** `git add`, then confirm
  `git status` is empty after the commit. The pre-commit hook's black is a writer while CI's
  is an assert, so a reformat that lands after staging is pushed misformatted.
- **`monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", ...)` keeps working**
  after task 1.1 swaps the constant for an import. Ten existing tests in
  `tests/unit/test_evaluation_console.py` use that shape (`:136`, `:164`, `:184`, `:248`,
  `:287`, `:337`, `:388`, `:435`, `:478`, and one more). `_is_live_agent_config` reads the
  module global at call time, so rebinding the name on the module still overrides it. If any
  of those tests needs an edit, stop — the import is wrong, not the test.
- **One existing assertion must change, and only one.**
  `test_generated_evaluation_console_requires_explicit_enablement`
  (`tests/unit/test_evaluation_config.py:44-56`) asserts the rendered `agent_config_path` is
  the live deployment config path. That assertion pins the defect, and task 2.1 changes it to
  `None`. No other existing test may be edited.

## 1. One definition of the refused path, and a validator that runs above the teardown

- [ ] 1.1 RED, then GREEN. Create `src/utils/evaluations_config.py`. It imports `pathlib` and
      `typing` only — no flask, no CLI imports — so both the CLI and the chat app can import it
      (`design.md` D3). It defines `LIVE_AGENT_CONFIG_PATH` holding the live deployment config
      path `/root/archi/configs/config.yaml`, and
      `validate_evaluations_config(chat_app_config: Optional[Dict[str, Any]]) -> None`, which
      returns immediately unless `evaluations.enabled` is exactly `True` (`is True`, so a
      truthy `1` or `"true"` does not arm it — this mirrors the seam at
      `src/interfaces/chat_app/evaluation_console.py:90`), and otherwise raises `ValueError`
      when `agent_config_path` is missing, not a `str`, blank after `.strip()`, or normalizes
      to `LIVE_AGENT_CONFIG_PATH` under `Path(...).resolve()`. Both messages must contain the
      literal `services.chat_app.evaluations.agent_config_path`; the live-config message must
      also state that the live deployment config is refused and that a redacted copy should be
      named instead. Use path normalization only — no `os.path.samefile` (`design.md` D3: on
      the host the live config does not exist, and `samefile` raises there).
      Write `tests/unit/test_evaluations_config_validation.py` first and watch it fail on the
      missing module. Cover: `None` config; `{}`; `enabled` absent; `enabled: False`;
      `enabled: 1`; `enabled: "true"` (all six return `None` and raise nothing); `enabled:
      True` with no key; with `""`; with `"   "`; with a non-string; with the live deployment
      config path; with that same path written through a `..` segment (the normalizing case);
      and with `/root/archi/configs/config.eval.yaml` (accepted, raises nothing). Assert the
      dotted key appears in each raised message with `pytest.raises(ValueError, match=...)`.
      In the same task, replace the constant assignment at
      `src/interfaces/chat_app/evaluation_console.py:35` with
      `from src.utils.evaluations_config import LIVE_AGENT_CONFIG_PATH`, so exactly one
      definition exists. Do not change `_is_live_agent_config` — the container keeps its inode
      check. Run the full `tests/unit/test_evaluation_console.py` to prove the ten monkeypatch
      sites still work. Gate, commit.

- [ ] 1.2 RED, then GREEN. Wire the validator into `archi create` above the teardown. Add a
      test to `tests/unit/test_evaluation_config.py` that calls
      `ConfigManager._validate_chat_app_config` (or `validate_configs`, whichever the existing
      tests already exercise — check `tests/unit/test_cli_create_dev_smoke.py` for the
      established call shape) with `services=["chatbot"]` and a chat_app config that sets
      `evaluations.enabled: true` and no `agent_config_path`; assert `pytest.raises(ValueError)`
      whose message contains `services.chat_app.evaluations.agent_config_path`. Add a second
      case naming the live deployment config path, and a third that passes
      `services=["chatbot"]` with `evaluations.enabled: false` and asserts no raise. Watch them
      fail. Then add one call to `validate_evaluations_config(chat_cfg)` inside
      `_validate_chat_app_config` (`src/cli/managers/config_manager.py:179`), after the
      existing required-field loop at `:192-202`. That method already returns early when
      `"chatbot"` is not in `services` (`:182`), which is the behaviour wanted: a deployment
      without the chatbot cannot have a console to refuse. `cli_main` converts the `ValueError`
      into a non-zero `ClickException` (`src/cli/cli_main.py:355-365`), so no CLI change is
      needed. Gate, commit.

- [ ] 1.3 Prove the ordering, with no production change. Add a test to
      `tests/unit/test_cli_create_dev_smoke.py` in the shape of the existing
      `delete_deployment` tests there: invoke `archi create --force` against an existing
      deployment whose config sets `evaluations.enabled: true` with no `agent_config_path`, and
      assert the command exits non-zero **and** `delete_deployment()` was never called and the
      deployment directory still exists. This is the scenario "The refusal precedes the forced
      teardown". It passes only because `validate_configs()` runs at
      `src/cli/cli_main.py:224`, above `remove_existing_deployment()` at `:295`; it is the
      regression guard against a later move of the check into template staging. Gate, commit.

## 2. Stop rendering the refused path

- [ ] 2.1 GREEN in one step (the failing assertion already exists — see the standing note).
      Change `src/cli/templates/base-config.yaml:126` so `agent_config_path` no longer carries
      a Jinja `default(...)` of the live deployment config path. Use the idiom the sibling
      `mcp_config_path` line at `:127` already uses — pipe the value through
      `default(none, true)` and then `tojson`, so an unset key renders as `null`.
      **Drop the surrounding double quotes.** A quoted `"{{ ... | tojson }}"` renders the
      four-character string `"null"`, which the seam would accept as a filename
      (`design.md` D4). Then change the one expected value in
      `test_generated_evaluation_console_requires_explicit_enablement`
      (`tests/unit/test_evaluation_config.py:55`) to `None`. Add one new test in the same file
      asserting that a config which *does* set `agent_config_path` renders that exact string
      through unchanged (the scenario "An accepted path renders through to the running
      configuration"), and one asserting the rendered value is `None` rather than the string
      `"null"` (`assert rendered[...]["agent_config_path"] is None`). Run
      `tests/unit/test_evaluation_config_staging.py` too: `_stage_evaluation_config` only
      touches `mcp_config_path` and must be unaffected. Gate, commit.

- [ ] 2.2 Align the recorded promise. The function docstring at
      `src/interfaces/chat_app/evaluation_console.py:67` already says "`agent_config_path` has
      no default" — that is now true of the template as well, so add one sentence recording
      that `archi create` refuses the two unacceptable values up front and that the seam's
      check remains the authority because a running configuration can be changed after create
      (`design.md` D3). Keep it to the docstring; no behaviour change in this task. Gate,
      commit.

## 3. Fix the page that recommends the dead config

- [ ] 3.1 Edit `docs/docs/configuration.md`. In the worked example at `:171`, replace the
      live-deployment-config value of `agent_config_path` with a redacted copy under the same
      mounted directory — `agent_config_path: /root/archi/configs/config.eval.yaml`. In the
      prose at `:192-194`, replace the sentence that calls the live path the default with: the
      key is **required** when `enabled` is `true`, it has **no default**, `archi create`
      refuses a config that omits it, and the live deployment config is refused because every
      run copies the named file into the host-mounted run workspace the console serves,
      credential values included. Then verify with a grep for `configs/config.yaml` over
      `docs/docs/configuration.md`: every remaining hit must be prose that says the path is
      *refused* — none may present it as the default or as the example value. Gate, commit.

## 4. Wrap up

- [ ] 4.1 Confirm the acceptance criteria of issue #330 in order, then ship. Run the focused
      files, then the full gate. Confirm with
      `git diff origin/dev -- tests/unit/test_evaluation_console.py` that that file is
      untouched, and with `git diff origin/dev -- tests/unit/test_evaluation_config.py` that
      the only existing-line change is the single expected value from task 2.1. Confirm
      `git status` is empty. Record in the commit message the chosen mechanism — (b), validate
      at create time, plus removing the template default, because (a) alone meets neither
      branch of acceptance criterion 1 (`design.md` D1) — and the gate's patch-coverage
      number. Then `git push -u origin fix/issue-330-evaluations-agent-config-default` and open
      the PR against `fasrc/archi:dev`. The PR body must contain `closes #330` (a closing
      keyword in the title does not link the issue) and must state the mechanism decision, as
      issue #330 plan step 1 requires, plus the note that #320 also edits
      `src/cli/templates/base-config.yaml` in a different block. Do not merge.
