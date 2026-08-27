# Tasks — refuse a relocated evaluations root

Every checkbox below is one loop turn and ends **green and committed**. Each one says RED
then GREEN: write the failing test, watch it fail, write the smallest code that passes it,
run the gate, commit — all inside that single checkbox. Never end a task with the suite red,
and never use `--no-verify`.

Run the project gate before every commit — the command is in `CLAUDE.md` under "Gate", and
the loop's own prompt already runs it. Run it bare from the repository root: do not pipe or
redirect it, and do not run it from another directory. On this host it needs the project
interpreter on `PATH`:

```
PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```

Focused run while working:

```
/home/austin/miniforge3/envs/archi/bin/python -m pytest tests/unit/test_evaluations_root_validation.py tests/unit/test_evaluation_config.py -q
```

Four standing notes for every task:

- **Scope.** The only files this change edits are `src/utils/evaluations_root.py` (new),
  `src/cli/managers/config_manager.py`, `tests/unit/test_evaluations_root_validation.py`
  (new), `tests/unit/test_evaluation_config.py` and `docs/docs/configuration.md`. Do **not**
  edit `src/cli/templates/base-compose.yaml` or `src/cli/templates/base-config.yaml` — the
  template contract is preserved on purpose (design D1). Do **not** edit
  `src/interfaces/chat_app/evaluation_console.py` (the runtime cannot detect an overlay
  root, design D5) or `src/cli/managers/templates_manager.py` (it runs after the teardown,
  design D2).
- **Write the minimum each turn.** The module gains one branch per task, with the test that
  demands it. Writing every branch in task 1.1 leaves untested lines in that commit's diff
  and the patch-coverage gate fails on your own code.
- **Do not modify existing tests.** If an existing test needs an edit to pass, stop — the
  change is wrong, not the test. The only existing file that gains anything is
  `tests/unit/test_evaluation_config.py`, and it gains a new test, not an edit.
- **The class is `ConfigurationManager`.** `tests/unit/test_config_manager_benchmarking_variation.py:16`
  builds one with `object.__new__(ConfigurationManager)` to skip the file-loading
  `__init__`. Copy that shape rather than writing a config file to disk.

## 1. The validator

- [x] 1.1 RED, then GREEN — refuse a root outside the mount. Create
      `tests/unit/test_evaluations_root_validation.py` with two tests against a new
      `src/utils/evaluations_root.py`: `validate_evaluations_root({"evaluations": {"enabled":
      True, "root": "/data/evaluations"}})` raises `ValueError` whose message contains both
      `/data/evaluations` and `/root/archi/evaluations`, and the same call with `"root":
      "/root/archi/evaluations"` returns `None`. Watch both fail on the missing module. Then
      write the module: a module docstring saying why the check is lexical and runs before
      the teardown (design D2, D3), `EVALUATIONS_MOUNT_PATH = "/root/archi/evaluations"`, and
      `validate_evaluations_root(chat_app_config)` that reads `evaluations.root`, normalizes
      it with `posixpath.normpath`, wraps it in `PurePosixPath`, and raises unless the result
      equals the mount. Do not add the `parents` branch, the enabled gate, or the type checks
      yet — later tasks bring them with their tests. Do not import anything from
      `src/interfaces/**` (design D7). Gate, commit.

- [x] 1.2 RED, then GREEN — accept a root beneath the mount, and refuse a prefix sibling.
      Add two tests: `"/root/archi/evaluations/trial-a"` returns `None`, and
      `"/root/archi/evaluations-backup"` raises. Confirm the first fails and the second
      already passes, then make the first pass by also accepting when
      `PurePosixPath(EVALUATIONS_MOUNT_PATH)` is in the candidate's `.parents`. Re-run and
      confirm the sibling test still raises: the `.parents` of
      `/root/archi/evaluations-backup` are `/root/archi`, `/root` and `/`, and the mount is
      not among them (design D3). Add a comment at that comparison saying a `startswith` test
      would accept the sibling. Gate, commit.

- [x] 1.3 RED, then GREEN — refuse traversal and relative roots. Add tests for
      `"/root/archi/evaluations/../elsewhere"` and for `"evaluations"`, both raising
      `ValueError`. The traversal case passes already if `normpath` is in place from 1.1 —
      verify that by reading the test output, and if it passes, say so in the commit message
      rather than adding code it does not need. The relative case needs an explicit
      `is_absolute()` refusal with its own message: name the value and say an absolute
      container path under the mount is required, because no working directory is pinned for
      the container (design D3). Gate, commit.

- [x] 1.4 RED, then GREEN — the enabled gate and the non-string cases. Add tests: a config
      with `"enabled": False` (and also one omitting `enabled`, and one with the string
      `"true"`) and a root outside the mount returns `None`; a config with no `evaluations`
      block at all returns `None`; and with the console enabled, a `root` that is `None`
      returns `None` (the template default applies), while a `root` that is `123` or `""`
      raises `ValueError` naming the field path
      `services.chat_app.evaluations.root`. Then add the `enabled is not True` early return
      and the type checks. Gate, commit.

## 2. Wire it into the pre-teardown validator

- [x] 2.1 RED, then GREEN — `_validate_chat_app_config` refuses. Add tests to
      `tests/unit/test_evaluations_root_validation.py` that build a manager with
      `object.__new__(ConfigurationManager)` and call
      `mgr._validate_chat_app_config(config, ["chatbot"])` directly, with a config carrying
      the three fields that validator already requires (`agent_class`, `default_provider`,
      `default_model`) plus an enabled evaluations block. Assert three things: an outside
      root raises `ValueError` naming both paths; the mounted root does not raise; and
      passing `services=["data_manager"]` with an outside root does not raise, because the
      validator returns early without `chatbot`. Watch the first fail. Then add
      `from src.utils.evaluations_root import validate_evaluations_root` to
      `src/cli/managers/config_manager.py` and call it with `chat_cfg` at the end of
      `_validate_chat_app_config`. That is the whole call site — no logic in this file.
      Gate, commit.

## 3. Hold the constant and the default render

- [x] 3.1 RED, then GREEN — the mount constant tracks the template. Add
      `test_evaluations_mount_constant_matches_the_compose_template` to
      `tests/unit/test_evaluation_config.py`, next to
      `test_chatbot_deployments_persist_the_evaluation_root`
      (`tests/unit/test_evaluation_config.py:128`). Read
      `src/cli/templates/base-compose.yaml`, find the chatbot service's evaluations volume
      line, split it on `:`, and assert the container side equals `EVALUATIONS_MOUNT_PATH`.
      Prove it can fail before you trust it: temporarily change the constant, watch the test
      fail, change it back. This test is the reason a fourth copy of the path is acceptable
      (design D7). Gate, commit.

- [ ] 3.2 RED, then GREEN — a default configuration is accepted and renders unchanged. Add
      `test_default_evaluations_config_is_accepted_and_renders_unchanged` to
      `tests/unit/test_evaluation_config.py`: render `base-config.yaml` for a chat app with
      no `evaluations` block and for one with `enabled: true` and no `root`, assert both
      render `root: /root/archi/evaluations` as they do today, and assert
      `validate_evaluations_root` accepts the rendered `services.chat_app` block in both
      cases. This is the acceptance criterion "the default configuration renders unchanged",
      and it must pass with no production change — if it does not, the change altered a
      default and is wrong. Gate, commit.

## 4. Document the constraint

- [ ] 4.1 Document it where the knob is documented. In `docs/docs/configuration.md`, at the
      `services.chat_app.evaluations` entry, state that `root` must be
      `/root/archi/evaluations` or a path beneath it, that the compose bind mount is fixed at
      that path, and that a root outside it is stored in the container and lost on the next
      `archi create --force`. If the file has no evaluations entry yet, add one that covers
      `enabled`, `root` and `agent_config_path` in the style of its neighbours. Docs only, so
      the gate reports no lines with coverage information — that is expected, not a failure.
      Gate, commit.

## 5. Open the pull request

- [ ] 5.1 Push the branch to `origin` (`fasrc/archi`, **not** the `fork` remote) with
      `git push -u origin fix/issue-331-evaluations-root-escapes-compose-mount`, then open a
      PR against `fasrc/archi:dev`. The body must contain the closing keyword for issue 331
      on its own line — a keyword in the title does not link the issue — and must record the
      chosen mechanism (validate, do not move the mount; design D1) as issue #331 plan item 1
      asks. Also carry the upstream-parity finding from design D7 into the body: upstream at
      pin `bebfbe56` shares the split in both its compose and helm templates, the fork has no
      helm templates, and reporting it on archi-physics/archi PR #608 is a human follow-up.
      Do **not** merge.
