## Context

`archi create --force` and `archi evaluate --force` delete the deployment directory `<ARCHI_DIR>/archi-<name>/` (default `~/.archi/archi-<name>/`) and then stage each configured input list into `<deployment dir>/weblists/`. The #617 check (`refuse_missing_input_lists`, `src/cli/source_registry.py:59`) refuses a list that does not exist when the command starts. A list that exists inside the deployment directory passes that check, is deleted by the teardown, and is then warned about and skipped at staging (`templates_manager.py:1328-1339`). The replacement deployment starts without it (issue #819).

The code already has the same guard for one other input: `refuse_agent_config_inside_deployment` (`src/utils/evaluations_config.py:104`) refuses an `agent_config_path` inside the deployment directory with `Path.resolve()` and `is_relative_to()`. `create` calls it at `cli_main.py:310` and `evaluate` at `:947`. This change adds the same guard for input lists, placed with the other input-list check.

## Facts the issue body does not state

### D1. The anchors moved

The issue cites `786edbc8`, the head of PR #781. On `origin/dev` `f457d3d1` (PR #781 and PR #823 merged on 2026-11-08):

| What | Line |
|---|---|
| `create`: `base_dir = Path(ARCHI_DIR) / f"archi-{name}"` | `src/cli/cli_main.py:187` |
| `create`: `handle_existing_deployment(base_dir, name, force)` | `:188` |
| `create`: `refuse_missing_input_lists(config_manager.get_configs())` | `:192` |
| `create`: `refuse_agent_config_inside_deployment(` | `:310` |
| `create`: `remove_existing_deployment(` | `:320` |
| `create`: outer `except Exception as e:` → `click.ClickException(str(e))` | `:379-390` |
| `restart`: `refuse_missing_input_lists(` (no teardown; unchanged) | `:612` |
| `evaluate`: `base_dir = Path(ARCHI_DIR) / f"archi-{name}"` | `:847` |
| `evaluate`: `refuse_missing_input_lists(config_manager.get_configs())` | `:859` |
| `evaluate`: `handle_existing_deployment(` (after the input-list checks) | `:871` |
| `evaluate`: `remove_existing_deployment(` | `:950` |
| `evaluate`: outer `except Exception as e:` → `"Failed due to the following exception: {e}"` | `:974-981` |
| the import of `refuse_missing_input_lists` | `:26` |
| `handle_existing_deployment` / `remove_existing_deployment` | `src/cli/utils/helpers.py:299` / `:316` |
| `delete_deployment`, `deployment_dir`, `shutil.rmtree` | `src/cli/managers/deployment_manager.py:154` / `:170` / `:201` |
| `_copy_web_input_lists`, the `isfile` skip | `src/cli/managers/templates_manager.py:1306` / `:1339` |
| `_input_list_is_missing`, `refuse_missing_input_lists` | `src/cli/source_registry.py:41` / `:59` |
| `refuse_agent_config_inside_deployment` (the sibling idiom) | `src/utils/evaluations_config.py:104-126` |

Find each line by its text, not by its number.

### D2. Remedy (a), not (b)

The issue offers (a) refuse the list, or (b) copy every list to a staging location outside the deployment directory before the teardown and render from that copy. Decision: (a).

- (a) is one check with no state. It fails closed, and the error says what to do.
- (b) needs a new temporary location, cleanup on every exit path of `create` and `evaluate`, and a change to the source that `_copy_web_input_lists()` reads. The config would still name a path that the deploy deletes, so the next forced run hits the same defect unless the copy is kept forever.

### D3. The check runs only under `--force`

The teardown runs only under `--force` (`remove_existing_deployment`, `helpers.py:331`). In `create` the gate is redundant but states intent: without `--force`, `handle_existing_deployment()` at `:188` already refuses an existing directory, and a directory that does not exist cannot hold an existing list (`refuse_missing_input_lists` fires first). In `evaluate` the gate changes behavior: `handle_existing_deployment()` runs at `:871`, AFTER the input-list checks at `:859`, so without the gate a non-forced `evaluate` against an existing runtime would print the new error instead of "already exists". The gate keeps that precedence. Test 4 of E4 file B is the mutation guard for the `if force:` line.

### D4. `--dry` still refuses

`remove_existing_deployment(..., dry, ...)` logs `[DRY RUN] Would remove` and returns under `--dry`. The check still fires: a real run with the same inputs refuses, and the spec requirement "Dry runs report the teardown they would perform, and only when they would" says the dry run must then fail the same way and not print the notice. Test 1 of E4 file B covers `["--force", "--dry"]`.

### D5. Resolution rule

Both sides go through `Path.resolve()` (non-strict; it is `os.path.realpath`), the idiom of `refuse_agent_config_inside_deployment`. So a symbolic link outside the directory whose target is inside, and a relative path with `..` segments that lands inside, are refused. A relative path is taken from the current directory, the #617 rule. `~` is not expanded: `refuse_missing_input_lists`, `get_enabled_sources()`, and staging do not expand it (#617 design D4). Containment is `is_relative_to()`, not a string prefix, so `archi-smoke-2/` is outside `archi-smoke/` (E4 file A test 3). After `refuse_missing_input_lists` the path exists, but the helper does not depend on that: `resolve()` on a path that does not exist still collapses it lexically. `os.path.realpath` swallows an `OSError` on a component it cannot read (non-strict), so a list under an untraversable directory does not raise here.

### D6. `ValueError` becomes an error message

`create` wraps any exception in `click.ClickException(str(e))` (`:379-390`), and `evaluate` in `click.ClickException(f"Failed due to the following exception: {e}")` (`:974-981`). So the message lands in `result.output` with exit status 1, and the tests check `result.output`. No call-site `try` is needed (unlike `restart` in #617, which does not change here).

### D7. Existing tests stay green

The CLI tests load configs whose lists are outside `ARCHI_DIR`: `EXAMPLE_CONFIG` lists `examples/deployments/basic-openai/miscellanea.list` (relative from the repository root), the `benchmark_config` fixture lists the absolute path of that file, `test_render_preflight*.py` use absolute `tmp_path` lists, and the one test that changes the cwd (`test_cli_create_dev_smoke.py::test_create_without_env_file_names_the_flag_when_the_fallback_is_missing`) writes an absolute list path since #617. The `archi_home` fixture patches `cli_main.ARCHI_DIR` to `tmp_path / "archi-home"`, so `base_dir` resolves there, and no existing test puts a list under it.

Measured on a scratch copy of `origin/dev` `f457d3d1` (2026-11-09) with the E1 and E2 code applied: the 17 unit-test files that use `cli_main` plus `test_refuse_missing_input_lists.py`, `test_refuse_missing_input_lists_access.py`, `test_evaluations_config_validation.py`, `test_config_manager_input_list_inference.py`, and `test_config_manager_unreadable_input_lists.py` gave 619 passed, 1 skipped. The 4 failures in that run (`test_base_config_benchmark_render.py`, `test_base_config_scrape_render.py`, `test_base_image_preflight.py` x2) read `scripts/` or `docs/`, which the scratch copy did not hold; they are not related to this change. The gate runs the whole suite in the real tree.

### D8. The red run reaches the teardown

A refusal test proves ordering only if a run WITHOUT the check reaches `remove_existing_deployment`. Measured on the scratch copy before the code change: each of the 4 refusal items of E4 file B fails with `Error: spy reached: remove_existing_deployment` (create) or `Error: Failed due to the following exception: spy reached: remove_existing_deployment` (evaluate) in `result.output`. Three stubs make that true, and the test file must keep them: `satisfied_base_images` imported from `test_cli_create_dev_smoke.py` (an autouse fixture that stubs `ContainerProbe` and `VolumeManager.create_required_volumes`), `TemplateManager.preflight_render` replaced with a no-op, and for `evaluate` `cli_main.preflight_benchmark_configs` replaced with `lambda configs: ([], [])`. The teardown spy on `cli_main.remove_existing_deployment` records its name and raises, so no run reaches a container or volume call, and `_record_teardowns` records `DeploymentManager.delete_deployment` as a second witness.

### D9. One shared walker

`refuse_missing_input_lists` and the new helper read the same `input_lists` entries with the same shape rules. The loop moves into a private generator `_configured_input_list_entries(configs)` that yields `(os.fspath(entry), config_path)`. `refuse_missing_input_lists` keeps its contract: the same pairs in the same order, deduplicated, and `_input_list_is_missing(path_str)` instead of `_input_list_is_missing(entry)` (`os.stat` accepts both). Its 11 existing tests pass with no edit (D7 measurement).

### D10. Docs that are incomplete

`docs/docs/configuration.md:746-753` (`### Sources`) and `docs/docs/data_sources.md:33-36` describe the #617 check and say nothing about a list inside the deployment directory. `configuration.md:447-456` already documents the same rule for `agent_config_path` ("any file inside the deployment directory (which `archi create --force` deletes on every run)"); E5 models its sentences on it. No other page under `docs/docs/` describes the input-list checks (`grep -rn "input_lists" docs/docs/` hits only those two pages, a `sources build` example in `cli_reference.md`, and benchmarking pages that do not describe the check). `grep -c "#819"` prints 0 for both pages on `origin/dev`, so the E5 criterion is not vacuous.

## Goals / Non-Goals

Goals: a forced `create` or `evaluate` whose config lists a path that resolves inside the deployment directory exits 1 before `remove_existing_deployment()`, with the file intact, and the error names the list, the resolved directory, and the config file. Lists outside the directory, `restart`, and runs without `--force` do not change.

Non-goals: option (b) (D2); `restart` (no teardown); `archi sources build`; `_copy_web_input_lists()` and the warn-and-skip of #636 and PR #689; the dry-run notice; `~` expansion or any change to how a list path resolves; a check against another deployment's directory (a forced run of `<name>` deletes only `archi-<name>`).

## Decisions

### E1. The helper (`src/cli/source_registry.py`)

Three edits, verbatim in tasks.md Appendix C (black 24.10.0 and isort 6.0.1 clean):

1. After `_input_list_is_missing`, add `_configured_input_list_entries(configs)`: the loop body of today's `refuse_missing_input_lists` (`:70-87`), ending in `yield os.fspath(entry), config_path`.
2. `refuse_missing_input_lists` reads `for key in _configured_input_list_entries(configs):` and keeps `seen` (now a set) and `missing_pairs`; the message does not change.
3. After `refuse_missing_input_lists`, add `refuse_input_lists_inside_deployment(configs, deployment_dir) -> None`: `resolved_dir = Path(deployment_dir).resolve()`; for each `(path_str, config_path)` pair once, `resolved = Path(path_str).resolve()`; collect `(path_str, str(resolved), config_path)` when `resolved.is_relative_to(resolved_dir)`; return `None` when nothing is collected; else raise one `ValueError` (E3).

`Path` is already imported at `source_registry.py:3`.

### E2. The call sites (`src/cli/cli_main.py`)

Verbatim in tasks.md Appendix D. The import at `:26` becomes a parenthesized three-name import (`refuse_input_lists_inside_deployment`, `refuse_missing_input_lists`, `source_registry`), which is how isort with `profile = "black"` writes it. In `create` and in `evaluate`, directly after `refuse_missing_input_lists(config_manager.get_configs())`:

```python
        if force:
            # The --force teardown removes base_dir before the lists are staged (#819).
            refuse_input_lists_inside_deployment(config_manager.get_configs(), base_dir)
```

`base_dir` is defined before both sites (`:187`, `:847`). `restart` does not change.

### E3. The message

```
input_lists: these configured list files are inside the deployment directory <resolved dir>. A --force run deletes that directory before archi stages the lists, so they would be lost.
  - <path> (config: <config file>)
  - <path> -> <resolved path> (config: <config file>)
Move each file outside that directory, point the config at the new location, then run the command again.
```

One `  - ` line per `(path, config file)` pair, in configuration order. The arrow part appears only when the resolved path differs from the configured one (a relative path or a symbolic link). The tests check the whole `  - ` line, the resolved directory, and the words `--force`.

### E4. Tests

Two new files, verbatim in tasks.md Appendix A and B. Each test name is new (`grep -rn "def <name>" tests/unit/` prints one line). No existing test file changes.

`tests/unit/test_refuse_input_lists_inside_deployment.py` (the helper; configs are plain dicts with `_config_path`; 15 items):

1. `test_absolute_path_inside_names_path_directory_and_config`: the message holds `  - <inside> (config: cfg/a.yaml)`, the resolved directory, and `--force`.
2. `test_absolute_path_outside_passes`: returns `None`.
3. `test_sibling_directory_with_the_same_prefix_passes`: `archi-smoke-2/weblists/sources.list` next to `archi-smoke/` returns `None`.
4. `test_symlink_outside_that_resolves_inside_is_refused`: the message holds `  - <link> -> <target> (config: cfg/a.yaml)`.
5. `test_relative_path_with_parent_segments_that_resolves_inside_is_refused`: cwd is `tmp_path / "elsewhere"`, the entry is `os.path.relpath(inside, cwd)` (starts with `..`), the message holds `  - <relative> -> <resolved> (config: cfg/a.yaml)`.
6. `test_relative_path_outside_passes`: `lists/ok.list` from that cwd returns `None`.
7. `test_every_inside_path_in_every_config_is_named_once`: two configs, two inside paths (one listed twice) and one outside; exactly two `  - ` lines, in order, each with its own config file; the outside path is absent.
8. `test_entries_and_shapes_that_are_not_paths_are_ignored`, parametrized over 8 config shapes (no `data_manager`; `data_manager: None`; `sources: None`; `links: True`; `input_lists: None`; `[]`; a string; `[None, 987654]`): returns `None`.

Red before E1: the file fails at collection with `ImportError: cannot import name 'refuse_input_lists_inside_deployment'` (1 error). Green after E1: `15 passed`.

`tests/unit/test_cli_input_list_inside_deployment.py` (the two commands; 7 items). It imports `EXAMPLE_CONFIG`, `REPO_ROOT`, `_existing_deployment`, `_record_teardowns`, `archi_home`, `benchmark_config`, `env_file`, and `satisfied_base_images` from `tests.unit.test_cli_create_dev_smoke` (D8 says why the last one is required). Common setup: `monkeypatch.chdir(REPO_ROOT)`, `check_docker_available` → `True`, `_existing_deployment(archi_home)` with `weblists/x.list` written inside it, a copy of the base config with `input_lists` replaced, written to `tmp_path / "cfg.yaml"`.

1. `test_forced_create_refuses_a_list_inside_the_deployment_before_the_teardown`, parametrized over `["--force"]` and `["--force", "--dry"]`: exit non-zero; `result.output` holds the list path, the resolved deployment directory, and the config path; the teardown spy list and `_record_teardowns` are empty; `x.list` and `marker.txt` still exist.
2. `test_forced_create_refuses_a_relative_path_that_resolves_inside_the_deployment`: the entry is `os.path.relpath(inside, REPO_ROOT)` (starts with `..`); the same assertions, and `result.output` holds `<relative> -> <resolved>`.
3. `test_forced_evaluate_refuses_a_list_inside_the_runtime_before_the_teardown`: `benchmark_config` as the base, `preflight_benchmark_configs` → `([], [])`; the same assertions.
4. `test_evaluate_without_force_reports_the_existing_runtime_not_the_list`: no `--force`; exit non-zero; `already exists` in the output; `inside the deployment directory` NOT in the output; spies and teardowns empty; `x.list` still exists. Passes before and after the change (it guards the `if force:` gate, D3).
5. `test_forced_create_passes_the_check_with_a_list_outside_the_deployment`: `cli_main.SecretsManager` raises `RuntimeError(SENTINEL)`; `SENTINEL in result.output`. `SecretsManager` is the next call after the check.
6. `test_forced_evaluate_passes_the_check_with_a_list_outside_the_runtime`: `cli_main.preflight_benchmark_configs` raises `RuntimeError(SENTINEL)`; `SENTINEL in result.output`.

Red before E2 (with E1 applied): `4 failed, 3 passed`; the 4 failures are tests 1 (both cases), 2, and 3, each with `spy reached: remove_existing_deployment` in `result.output` (D8). Tests 4, 5, 6 pass before and after. Green after E2: `7 passed`.

### E5. Docs (exact text)

1. `docs/docs/configuration.md`, in `### Sources`: insert this paragraph after the paragraph that ends `(Changed in [#617](https://github.com/fasrc/archi/issues/617).)` (`:746-753`) and before the line that starts ``The `visible` flag on any source``:

   > A list inside the deployment directory (`${ARCHI_DIR:-$HOME/.archi}/archi-<name>/`, for example a `weblists/` copy that an earlier run staged) stops `archi create --force` and `archi evaluate --force` before the teardown. The `--force` teardown deletes that directory before archi stages the lists, so the list would be lost. The error names the list, the directory, and the config file: move the file outside the directory and point the config at the moved copy. (Changed in [#819](https://github.com/fasrc/archi/issues/819).)

2. `docs/docs/data_sources.md`: insert this paragraph after the paragraph that ends `(Changed in [#617](https://github.com/fasrc/archi/issues/617).)` (`:33-36`) and before the paragraph that starts `A list that exists but is not a regular file`:

   > A list inside the deployment directory (`~/.archi/archi-<name>/`) stops `archi create --force` and `archi evaluate --force` before the teardown, because the teardown deletes that directory before archi stages the lists. Move the file outside the directory. (Changed in [#819](https://github.com/fasrc/archi/issues/819).)

   Wrap both at the width of the file (about 90 characters). Keep a blank line on each side.

## Test oracle (issue #819 acceptance criteria)

| Criterion | Proof |
|---|---|
| A forced `create` or `evaluate` with a list inside `~/.archi/archi-<name>/` exits non-zero before `remove_existing_deployment` is called, and the message names the path and the deployment directory | E4 file B tests 1, 2, 3 (spies); file A tests 1, 4, 5 (message) |
| A forced `create` whose lists are outside the directory still passes the check | E4 file B tests 5, 6; file A tests 2, 3, 6 |
| `restart` behavior is unchanged | `tests/unit/test_cli_missing_input_lists.py` green with no edit (D7); `restart` has no call site (E2) |
| Runs without `--force` keep the "already exists" refusal | E4 file B test 4 (D3) |
| Gate exits 0; `openspec validate --strict` passes; `mkdocs build --strict` prints no WARNING | task 4.1 |
| The PR body has `Closes #819` and links PR #781 | task 4.1 |

## Risks / Trade-offs

- An operator who pointed a config at a staged `weblists/` copy on purpose now gets an error on the next forced run. That is the purpose of the change; the message says to move the file.
- The check reads the configured path once, at command start. A list moved into the directory between the check and the teardown is still deleted and skipped at staging. That window is small and is not in scope.
- `Path.resolve()` follows symbolic links. A list that is a link INTO the deployment directory is refused (correct: the target is deleted). A list inside the directory that is a link OUT of it resolves outside and passes; the teardown deletes the link, and staging then warns and skips it. That shape is not in the issue and is not in scope.
