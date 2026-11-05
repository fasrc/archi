## Context

`data_manager.sources.links.input_lists` names files of URLs. Some entries enable a source by their prefix (`git-`, `sso-`, `elog-`, `indico-`), or by an ELOG path in the URL (#460). `get_enabled_sources()` (`src/cli/managers/config_manager.py:467`) reads each list to find those sources. A path that is not a file gets one WARNING and is skipped (`:509`). The source of that list then stays out of `enabled_sources`, and `set_sources_enabled()` (`:580`) writes `enabled: false` for it. The collector is off, and the deploy reports success.

The operator decision of 2026-10-20 (issue #617, "Decision"): keep the rule that a relative path resolves from the current directory (cwd), and fail closed when a path does not exist.

## Facts the issue body does not state

### D1. The anchors moved

The issue gives line numbers on `82850b3b`. On `origin/dev` `1ca97e24` they are:

| What | Line |
|---|---|
| `create`: `config_manager = ConfigurationManager(config_files, env)` | `src/cli/cli_main.py:191` |
| `create`: `config_manager.set_sources_enabled(enabled_sources)` | `:253` |
| `create`: `remove_existing_deployment(` | `:319` |
| `restart`: `config_manager = ConfigurationManager(list(config_files), env)` | `:609` |
| `restart`: `config_manager.set_sources_enabled(enabled_sources)` | `:618` |
| `evaluate`: `config_manager = ConfigurationManager(config_files, env)` | `:853` |
| `evaluate`: `config_manager.set_sources_enabled(enabled_sources)` | `:901` |
| `evaluate`: `remove_existing_deployment(` | `:944` |
| `get_enabled_sources` / its `os.path.isfile(list_path)` check | `config_manager.py:467` / `:509` |
| "Input list could not be read, skipping" | `config_manager.py:527` |
| `_copy_web_input_lists` (warn and skip, PR #689) | `src/cli/managers/templates_manager.py:1306` |
| `read_input_list_entries` | `src/cli/source_registry.py:28` |

Find each line by its text, not by its number.

### D2. What runs before the new check, and why that is safe

- `create`: `handle_existing_deployment()` (`:188`) runs before `ConfigurationManager`. It only refuses an existing deployment without `--force` (`src/cli/utils/helpers.py:299`, "Non-destructive"). It deletes nothing.
- `evaluate`: `arm_divergence_refusal()` runs before `ConfigurationManager`. It reads the config files and writes nothing.
- `restart`: it reads `compose.yaml` and the rendered configs. It writes nothing before `ConfigurationManager`.

So a check on the line after `ConfigurationManager(...)` runs before every write in all three commands.

### D3. `restart` has no outer exception handler

`create` (`:378`) and `evaluate` (`:968`) catch every exception and raise `click.ClickException`, so a `ValueError` from the helper prints as `Error: ...` and exits 1. `restart` has no such handler around its config block. A raw `ValueError` there prints a Python traceback. So the `restart` call site catches `ValueError` and raises `click.ClickException(str(exc))`. The tests check the message in `result.output`, and that is true only with this wrap.

### D4. Which entries the helper checks

The helper obeys the same shape rules as `get_enabled_sources()`, so it refuses only what that function would skip as "not found":

- It reads `data_manager.sources.links.input_lists` from each config. A config with no `data_manager`, a `data_manager`, `sources`, or `links` value that is not a mapping, or an `input_lists` value that is not a list gives no paths. (`get_enabled_sources()` already ignores a `links` or `input_lists` value of the wrong type, at `config_manager.py:487-496`.)
- It checks only an entry that is a `str` or an `os.PathLike`. Other entries (`None`, `5`) keep the WARNING of `config_manager.py:503-508`. Do the type check BEFORE `os.path.exists`: `os.path.exists(5)` tests file descriptor 5, not a path.
- A path that exists but is a directory or cannot be read passes the check. The warn-and-skip of #636 and PR #689 does not change (issue: "Do not change that"). A directory still gets the "not found, skipping" WARNING from `get_enabled_sources()`.
- `os.path.exists` returns False for a broken symbolic link, so a broken link is refused. That is correct, because nothing can read it.
- Do not expand `~` or environment variables, and do not resolve against the config directory. `get_enabled_sources()` and staging do neither (issue: "Do not change how a path resolves").

### D5. The config file name

`ConfigurationManager._load_config()` stores the path of each config file as `config["_config_path"]` (`config_manager.py:81`), in the form the operator gave on the command line. The message uses that value. A config dict without the key (only a test builds one) shows `<unknown config>`.

### D6. Existing tests stay green

The CLI tests that load configs with `input_lists` give an absolute path (`tests/unit/test_cli_create_dev_smoke.py:150`, `test_render_preflight.py:833` and `:901`, `test_render_preflight_review.py:151`). Or they use `examples/deployments/basic-openai/config.yaml`, whose list `examples/deployments/basic-openai/miscellanea.list` is checked in and resolves from the repository root, where the gate runs pytest. No CLI test changes the cwd (`grep -rln chdir tests/unit | xargs grep -ln cli_main` prints nothing). `test_config_manager_input_list_inference.py::test_missing_list_path_warns_no_exception` calls `get_enabled_sources()` directly. That function does not change, so the test stays green with no edit.

### D7. Docs and examples that become false

- `docs/docs/data_sources.md:33-35` says that a missing list "does not stop `archi create`".
- `examples/benchmarking/hierarchical_rerank_ab/README.md:57-59` says "If it is missing, the scraper logs a warning and skips it".
- The comment blocks at `examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml:34-40` and `treatment_hierarchical_rerank.yaml:23-29` say "If it is missing the scraper logs a warning and SKIPS it".
- `docs/docs/configuration.md:107` cites the three `set_sources_enabled()` calls as `cli_main.py:249`, `:894` and `:614`. They are already stale (`:253`, `:901`, `:618`), and this change moves them again.

`tests/unit/test_hierarchical_rerank_ab_configs.py` compares the loaded YAML of the two arms, not the comments, so the comment edit does not change it.

### D8. The FASRC dev redeploy

`archi_deploy` runs `cd "$REPO_ROOT"` before `archi create` (`deploy/scripts/lib.sh:388`), so dev's relative `config/lists/sources.list` resolves. `ensure_config` already stops when the config checkout has no `lists/sources.list` (`lib.sh:370`). This change does not touch either file.

## Goals / Non-Goals

Goals: a missing `input_lists` path stops `archi create`, `archi restart --config`, and `archi evaluate` before `set_sources_enabled()` and before any teardown. The error names each missing path, its config file, and the cwd. An existing list, relative or absolute, works as before. An existing list that cannot be read still warns and is skipped.

Non-goals: path resolution (D4); the unreadable-list behavior (D4); a refusal for a directory (D4); the data-manager runtime; `archi sources build`; `restart` with no `--config` (it loads no config, so it reads no list).

## Decisions

### E1. The helper (`src/cli/source_registry.py`)

Add `import os` at the top. Add this function after `read_input_list_entries`:

```python
def refuse_missing_input_lists(configs) -> None:
    """Raise ValueError naming every configured input list that does not exist.

    A relative path resolves from the current directory, as get_enabled_sources()
    and staging resolve it (fasrc/archi#617). A path that exists but cannot be
    read is not refused here: those callers warn about it and skip it (#636).
    """
```

The contract:

- Input: the list from `config_manager.get_configs()`. This is the same argument shape as `refuse_agent_config_inside_deployment(config_manager.get_configs(), ...)` in `cli_main.py`, and a test can call the helper with plain dicts.
- For each config in order, and for each entry of its `input_lists` in order (D4 shape rules), record `(os.fspath(entry), config.get("_config_path", "<unknown config>"))` when `not os.path.exists(entry)`. Keep each pair once (`dict.fromkeys`), in first-seen order.
- When there is no pair, return `None`.
- Else raise ONE `ValueError` with this text (E3).

### E2. The call sites (`src/cli/cli_main.py`)

Change the import at `cli_main.py:26` to `from src.cli.source_registry import refuse_missing_input_lists, source_registry`.

- `create`: on the line after `config_manager = ConfigurationManager(config_files, env)`, before the `try:` that builds `SecretsManager`: `refuse_missing_input_lists(config_manager.get_configs())`.
- `evaluate`: on the line after `config_manager = ConfigurationManager(config_files, env)`, before `preflight_benchmark_configs(...)`: the same one line.
- `restart`: on the line after `config_manager = ConfigurationManager(list(config_files), env)`, before `get_enabled_sources()`:

  ```python
        try:
            refuse_missing_input_lists(config_manager.get_configs())
        except ValueError as exc:
            raise click.ClickException(str(exc))
  ```

Add no comment block longer than 2 lines at a call site. The helper docstring holds the reason.

### E3. The message

```
input_lists: these configured list files do not exist. A relative path resolves from the current directory (<cwd>), not from the config file's directory.
  - <path> (config: <config file>)
  - <path> (config: <config file>)
Correct each path, or use an absolute path, then run the command again.
```

`<cwd>` is `os.getcwd()`. There is one `  - ` line for each pair of E1. The tests check the parts (each path, each config file, the cwd, the `  - ` line count), not the full text.

### E4. Tests

Two new files. Each test name is new: `grep -rn "def <name>" tests/unit/` prints one line. Do not edit an existing test file.

`tests/unit/test_refuse_missing_input_lists.py` (the helper; build configs as plain dicts with `_config_path` set):

1. `test_missing_relative_path_names_path_config_and_cwd`: `monkeypatch.chdir(tmp_path)`; one config (`_config_path` = `"cfg/a.yaml"`) lists `"lists/missing.list"`. `pytest.raises(ValueError)`. The message holds `lists/missing.list`, `cfg/a.yaml`, and `os.getcwd()` (read it after the chdir; do not use `str(tmp_path)`, because `/tmp` can be a symbolic link).
2. `test_every_missing_path_in_every_config_is_named`: cwd = `tmp_path`, and `tmp_path / "ok.list"` exists. Config `a.yaml` lists `["missing-1.list", "ok.list", "missing-1.list"]`. Config `b.yaml` lists `["missing-2.list", str(tmp_path / "nope" / "missing-3.list")]`. The message has exactly 3 lines that start with `"  - "`: `missing-1.list` with `a.yaml`, then `missing-2.list` and `missing-3.list` with `b.yaml`, in that order. `ok.list` is not in the message.
3. `test_existing_relative_path_passes`: cwd = `tmp_path`; `tmp_path / "lists" / "ok.list"` exists; the config lists `"lists/ok.list"`. The helper returns `None`.
4. `test_existing_absolute_path_passes_from_any_cwd`: the list is at `tmp_path / "ok.list"`; the cwd is a different new directory `tmp_path / "elsewhere"`; the config lists the absolute path. The helper returns `None`.
5. `test_relative_path_resolves_from_cwd_not_from_config_dir`: `tmp_path / "cfgdir" / "x.list"` exists; `_config_path` = `str(tmp_path / "cfgdir" / "a.yaml")`; the cwd is `tmp_path / "other"` (new, empty); the config lists `"x.list"`. The helper raises, and the message names `x.list`.
6. `test_undecodable_list_passes_and_is_still_skipped_with_warning`: write `tmp_path / "binary.list"` with the bytes `b"\xff\xfe\x00git-https://x\n"` and a good list with `"git-https://github.com/org/repo\n"`. The helper returns `None` for both absolute paths. Then build a manager as `tests/unit/test_config_manager_unreadable_input_lists.py::_manager` does (`object.__new__(ConfigurationManager)` with `mgr.configs` set), and call `get_enabled_sources()` under `caplog.at_level(logging.WARNING)`. It returns `["git"]`, and `caplog.text` names the binary list.
7. `test_permission_denied_list_passes_and_is_still_skipped_with_warning`: `@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")`. The same as test 6 with a `chmod(0)` file holding `"sso-https://example.org/\n"`. Restore the mode in a `finally`.
8. `test_entries_and_shapes_that_are_not_paths_are_ignored`, parametrized with these configs (each with `_config_path`): no `data_manager` key; `data_manager: None`; `sources: None`; `links: True`; `input_lists: None`; `input_lists: []`; `input_lists: "a-string.list"`; `input_lists: [None, 987654]`. The helper returns `None` for each. (987654 is a closed file descriptor, so `os.path.exists(987654)` is False: without the E1 type check the last case raises.)

`tests/unit/test_cli_missing_input_lists.py` (the three commands). Import the fixtures `archi_home`, `env_file`, and `benchmark_config`, the constants `REPO_ROOT` and `EXAMPLE_CONFIG`, and the helpers `_existing_deployment` and `_record_teardowns` from `tests.unit.test_cli_create_dev_smoke`, as `tests/unit/test_render_preflight.py:11-21` does.

Common setup for each test in this file:

- `monkeypatch.chdir(REPO_ROOT)`, so the cwd is known and the relative paths in `EXAMPLE_CONFIG` resolve. Do not chdir to `tmp_path`: `EXAMPLE_CONFIG` has other relative paths, and validation would then stop the run before `set_sources_enabled()`, which makes the spy assertions empty for the wrong reason.
- `monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)`.
- The missing path is `MISSING = "does-not-exist-617/missing.list"` (relative; no such path is in the repository).
- Write each config to `tmp_path / "cfg.yaml"` with `yaml.safe_dump`. Start from a VALID config, so that a run without the check reaches `set_sources_enabled()`: for `create` and `restart`, `yaml.safe_load(EXAMPLE_CONFIG.read_text())`; for `evaluate`, `yaml.safe_load(benchmark_config.read_text())`. Replace only `data_manager.sources.links.input_lists`.
- Spies: replace `cli_main.remove_existing_deployment`, `ConfigurationManager.set_sources_enabled`, `TemplateManager.prepare_deployment_files`, and `DeploymentManager.restart_service` with functions that append their name to one list `calls` and then raise `RuntimeError("spy reached: <name>")`. The raise stops a run without the check at the first spy, so the red run never reaches a real container or volume call. Also record `delete_deployment` with `_record_teardowns(monkeypatch)`.

9. `test_create_refuses_missing_list_before_any_change`, parametrized over the flag lists `["--force"]` and `["--force", "--dry"]`: `_existing_deployment(archi_home)`; the config lists `[MISSING]`; invoke `cli_main.create` with `-n smoke -c <cfg> -e <env> --services chatbot --hostmode` plus the flags. Assert: `exit_code != 0`; `result.output` holds `MISSING`, `str(cfg)`, and `os.getcwd()`; `calls == []`; the teardown list is empty; `marker.txt` still exists.
10. `test_restart_refuses_missing_list_before_any_change`: write `archi_home / "archi-smoke" / "compose.yaml"` as `{"services": {"chatbot": {}}}` and `archi_home / "archi-smoke" / "configs" / "smoke.yaml"` as `{"name": "smoke"}`. The config lists `[MISSING]`. Invoke `cli_main.restart` with `--name smoke --config <cfg>`. Assert: `exit_code != 0`; `result.output` (not only `result.exception`) holds `MISSING`, `str(cfg)`, and `os.getcwd()`; `calls == []`.
11. `test_evaluate_refuses_missing_list_before_any_change`: `_existing_deployment(archi_home)`; replace `cli_main.preflight_benchmark_configs` with `lambda configs: ([], [])`, as `test_render_preflight.py` does, so that a run without the check gets as far as `set_sources_enabled()`. The config lists `[MISSING]`. Invoke `cli_main.evaluate` with `--force -n smoke -c <cfg> -e <env>`. Assert as in test 9.
12. `test_create_passes_the_check_with_an_existing_list`, parametrized over 3 cases: `"relative"` (`"examples/deployments/basic-openai/miscellanea.list"`, checked in, resolves from `REPO_ROOT`), `"absolute"` (`str(tmp_path / "ok.list")`, a file with one URL line), and `"undecodable"` (`str(tmp_path / "binary.list")` with the bytes of test 6). Replace `cli_main.SecretsManager` with a callable that raises `RuntimeError(SENTINEL)` (`SENTINEL = "passed-the-input-list-check"`). Invoke `create` as in test 9 with `["--force"]`. Assert `SENTINEL in result.output`. `SecretsManager` is the next call after the check, so the sentinel proves that the check passed.
13. `test_restart_passes_the_check_with_an_existing_list`, the same 3 cases and the deployment files of test 10. Replace `ConfigurationManager.get_enabled_sources` with a function that raises `RuntimeError(SENTINEL)`. `restart` does not catch it, so assert `SENTINEL in str(result.exception)`.
14. `test_evaluate_passes_the_check_with_an_existing_list`, the same 3 cases. Replace `cli_main.preflight_benchmark_configs` with a function that raises `RuntimeError(SENTINEL)`. Assert `SENTINEL in result.output`.

Red before the call sites: tests 9, 10, and 11 fail, because the command reaches the `set_sources_enabled` spy (`calls` is not empty) and the message is absent. Tests 12, 13, and 14 pass before and after the change; they guard against a check that refuses too much.

### E5. Docs and examples (exact text)

1. `docs/docs/configuration.md`, in the `### Sources` section: insert this paragraph after the closing fence of the YAML block and before the line that starts ``The `visible` flag on any source``:

   > A relative `input_lists` path resolves from the directory where you run `archi` (the current directory), not from the directory of the config file. Use an absolute path when you can. If a listed path does not exist, `archi create`, `archi restart --config`, and `archi evaluate` stop before any `--force` teardown and before they write any `enabled` key. The error names each missing path, the config file that lists it, and the current directory. A path that exists but is not a readable file does not stop the command: archi logs a WARNING and skips that list (see [Data Sources](data_sources.md)). (Changed in [#617](https://github.com/fasrc/archi/issues/617).)

2. `docs/docs/configuration.md:107`: replace the three line citations `` (`src/cli/cli_main.py:249`, `:894` and `:614`) `` with the line numbers of the three `config_manager.set_sources_enabled(` calls AFTER the code change, in the same order (create, evaluate, restart). Find them with `grep -n "config_manager.set_sources_enabled(" src/cli/cli_main.py`.

3. `docs/docs/data_sources.md:33-39`: replace the paragraph that starts "A list that is missing, is not a regular file, or cannot be read" with these two paragraphs:

   > A list path that does not exist stops `archi create`, `archi restart --config`, and `archi evaluate` before any teardown. The error names each missing path, the config file that lists it, and the current directory. A relative path resolves from the directory where you run `archi`. (Changed in [#617](https://github.com/fasrc/archi/issues/617).)
   >
   > A list that exists but is not a regular file, or cannot be read (for example, permission denied, or bytes that are not UTF-8), does not stop `archi create`. A list problem does not stop a scrape either. Archi logs a WARNING that names the list, skips that list, and continues with the other lists. A successful deploy therefore does not prove that every configured list was ingested: look for `could not be copied`, `could not be read`, `not a readable file`, or `not found` in the deploy and data-manager logs. While a list is skipped, a scheduled refresh or a later full ingest keeps the sitemap `<lastmod>` timestamps it already has instead of discarding them.

   Keep the line width of the file (about 85 characters).

4. `examples/benchmarking/hierarchical_rerank_ab/README.md:57-60`: replace the bold sentence that starts "**If it is missing, the scraper logs a warning and" with:

   > **If it is missing, `archi evaluate` stops before any teardown and names the path (#617). If it exists but is empty or cannot be read, the benchmark runs against an empty corpus — producing meaningless RAGAS scores rather than failing loudly.**

5. `examples/benchmarking/hierarchical_rerank_ab/baseline_character_hybrid.yaml:34-40` and `treatment_hierarchical_rerank.yaml:23-29`: in each comment block, replace the words from "If it is" to "meaningless RAGAS scores." with "If it is missing, `archi evaluate` refuses to start (#617); if it is empty or unreadable, the benchmark runs against an EMPTY corpus and yields meaningless RAGAS scores." Keep the two blocks identical, keep `#` comments only, and keep the YAML values unchanged.

## Test oracle (issue #617 acceptance criteria)

| Criterion | Proof |
|---|---|
| A missing path makes `create`, `restart`, and `evaluate` raise before `remove_existing_deployment` and before `set_sources_enabled` | E4 tests 9, 10, 11 |
| The message names the missing path, its config file, and the cwd | E4 tests 1, 2, 9, 10, 11 |
| Existing relative and absolute paths still work | E4 tests 3, 4, 5, 12, 13, 14 |
| An unreadable existing list still warns and skips | E4 tests 6, 7, and the `"undecodable"` cases of 12-14 |
| `docs/docs/configuration.md` states the cwd rule and the fail-closed behavior | E5 item 1 |
| The gate exits 0 and the PR targets `fasrc/archi:dev` | task 3.1 |

## Risks / Trade-offs

- An operator who ran `archi` from a directory where a list did not resolve, and accepted the lost sources, now gets an error. That is the purpose of the change. The error says how to fix it.
- A deployment can list a path that is missing on purpose (a list for later use). It now must remove the entry or create an empty file. An empty file passes the check and enables nothing.
- The check runs once, at command start. A list that is deleted between the check and staging still gets the warn-and-skip of `_copy_web_input_lists`. That window is small and is not in scope.
