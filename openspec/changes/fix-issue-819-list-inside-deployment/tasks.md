## 0. Read first (no checkbox)

- **Loop image gap (#776).** The `archi-loop` image has no `slack_sdk` (confirmed again on 2026-11-09). Without it, pytest stops at collection with 3 errors in `tests/unit/test_slack_*.py`, and the pre-commit hook refuses every commit. At the start of each turn, run `python -c "import slack_sdk"`. If it fails, run `pip install slack_sdk==3.45.0` (the pin in `requirements/requirements-base.txt`) before any test or gate run. Do not edit any requirements file for this.
- **Gate.** Run the gate (`bash scripts/gate.sh`) in the foreground, with no pipe and no redirect. Never put it in the background, and never end a turn while it runs. The pre-commit hook runs the same gate; commit only through the hook, never with `--no-verify`.
- **Scope.** The design of record is `design.md` (D1-D10, E1-E5) in this folder. Do not change `get_enabled_sources()`, `_staged_input_lists()`, `_copy_web_input_lists()`, `remove_existing_deployment()`, or `restart`. Do not change how a path resolves (no `~` expansion, no config-directory rule). Do not edit an existing test file. Do not touch `deploy/`, `config/`, `scripts/`, `.github/`, or any requirements file.
- **Copy, do not rewrite.** The Appendix below holds the exact files and edits that were run red and green on a scratch copy of `origin/dev` `f457d3d1` on 2026-11-09. Create the two test files from Appendix A and B byte for byte, and apply Appendix C and D as written. Change them only if a test fails for a reason the design does not predict, and say so in the commit body.
- Each checkbox below is ONE commit with the suite green. The failing tests and the code that makes them pass go in the SAME commit; put the red run (the pytest summary line and one failure's last output line) in the commit body. Tick the box in the same commit as its work.
- Run `black` and `isort` on changed Python files BEFORE `git add` (the Appendix files are already clean).
- Each new test name must be unique: `grep -rn "def <name>" tests/unit/` prints one line.
- Commit messages: short, lowercase, no `Co-Authored-By` trailer. Use `git commit -F <file>` for a multi-line body, with the file OUTSIDE the repository.

## 1. The helper

- [x] 1.1 In ONE commit: create `tests/unit/test_refuse_input_lists_inside_deployment.py` from Appendix A (8 tests, 15 items). Run `python -m pytest -q tests/unit/test_refuse_input_lists_inside_deployment.py` and see it fail at collection: `ImportError: cannot import name 'refuse_input_lists_inside_deployment' from 'src.cli.source_registry'` (`1 error`). Then edit `src/cli/source_registry.py` as Appendix C says: replace the whole of `refuse_missing_input_lists` (from its `def` line through the end of its `raise ValueError(...)`) with the three functions of Appendix C (`_configured_input_list_entries`, the new `refuse_missing_input_lists`, `refuse_input_lists_inside_deployment`). `Path` is already imported at the top of the file. Run the same command and confirm `15 passed`. Then run `python -m pytest -q tests/unit/test_refuse_missing_input_lists.py tests/unit/test_refuse_missing_input_lists_access.py` and confirm it is green with no edit (11 items; one is skipped when the user is root). Gate green, commit (`fix(#819): add refuse_input_lists_inside_deployment helper`).

## 2. The two call sites

- [x] 2.1 In ONE commit: create `tests/unit/test_cli_input_list_inside_deployment.py` from Appendix B (6 tests, 7 items). Run `python -m pytest -q tests/unit/test_cli_input_list_inside_deployment.py` and see `4 failed, 3 passed`: the two `test_forced_create_refuses_a_list_inside_the_deployment_before_the_teardown` cases, `test_forced_create_refuses_a_relative_path_that_resolves_inside_the_deployment`, and `test_forced_evaluate_refuses_a_list_inside_the_runtime_before_the_teardown` fail at `assert str(inside) in result.output`, and each failure's `result.output` ends with `spy reached: remove_existing_deployment` (that line proves the run reached the teardown, design D8). Then edit `src/cli/cli_main.py` as Appendix D says: the import at the top, and the same three lines after `refuse_missing_input_lists(config_manager.get_configs())` in `create` and in `evaluate`. Do not add a call in `restart`. Run the same command and confirm `7 passed`. Then run `python -m pytest -q tests/unit/test_cli_create_dev_smoke.py tests/unit/test_cli_missing_input_lists.py tests/unit/test_render_preflight.py tests/unit/test_render_preflight_review.py tests/unit/test_cli_restart_source_flags.py tests/unit/test_evaluations_config_validation.py` and confirm it is green with no edit. Put the output of `grep -rn "refuse_input_lists_inside_deployment" src/` in the commit body: it must show the definition in `source_registry.py`, the import, and exactly 2 call sites in `cli_main.py` (one in `create`, one in `evaluate`). Gate green, commit (`fix(#819): refuse an input list inside the deployment directory before the teardown`).

## 3. Docs

- [x] 3.1 In ONE commit, make the 2 edits of design E5: the new paragraph in `docs/docs/configuration.md` (`### Sources`, after the #617 paragraph, before the ``The `visible` flag`` line) and the new paragraph in `docs/docs/data_sources.md` (after the #617 paragraph, before `A list that exists but is not a regular file`). Wrap each at the width of its file (about 90 characters) with a blank line on each side. Then confirm: `grep -c "issues/819" docs/docs/configuration.md docs/docs/data_sources.md` prints `1` for each file (it prints `0` for both on `origin/dev`). If `mkdocs` is installed, run `mkdocs build --config-file docs/mkdocs.yml --strict --site-dir /tmp/mkdocs-819` and confirm it prints no WARNING; if it is not installed, say so in the commit body. Gate green, commit (`docs(#819): a list inside the deployment directory stops a forced run`).

## 4. Verify, push, and open the PR

- [x] 4.1 Run the gate on the branch tip and confirm it is green. If `command -v openspec` finds the CLI, run `openspec validate fix-issue-819-list-inside-deployment --strict` and confirm it passes; if not, skip it (Loop 1 validated the change on the host). Run `git diff origin/dev --stat -- docs/` and confirm it lists ONLY `docs/docs/configuration.md` and `docs/docs/data_sources.md`; remove any other file under `docs/` from the branch. Tick this box, commit the tick through the hook, then confirm `git status --porcelain` is empty. Then push with `git push -u origin fix/issue-819-list-inside-deployment`. Write the PR body to a file OUTSIDE the repository (for example `/tmp/pr-body-819.md`; never under `docs/`), then run `gh pr create --repo fasrc/archi --base dev --head fix/issue-819-list-inside-deployment --title "fix(#819): refuse an input_lists path inside the deployment directory before the --force teardown" --body-file /tmp/pr-body-819.md`. In the PR **body** (not the title) put `Closes #819` and a link to PR #781 (the #617 change this one builds on); list the acceptance criteria of #819 with the tests that prove each (design "Test oracle"); state the behavior change (a forced `create` or `evaluate` whose config lists a path inside `~/.archi/archi-<name>/` now exits 1 before the teardown, with the file intact) and what does not change (`restart`, runs without `--force`, lists outside the directory, path resolution, the warn-and-skip at staging); list the two docs edits; and list design "Risks / Trade-offs". No `Co-Authored-By` trailer. Do not merge.

## Appendix (no checkboxes): the measured files and edits

Measured on a scratch copy of `origin/dev` `f457d3d1` on 2026-11-09 with black 24.10.0 and isort 6.0.1 (`profile = "black"`): Appendix A alone → `1 error` (ImportError); A + C → `15 passed`; B with C but without D → `4 failed, 3 passed`; A + B + C + D → `22 passed`; plus the 17 existing unit-test files that use `cli_main` and the 5 input-list and evaluations-config files → green (619 passed; the 4 failures in that run read `scripts/` or `docs/`, which the scratch copy did not hold).

### Appendix A: `tests/unit/test_refuse_input_lists_inside_deployment.py`

```python
"""Tests for refuse_input_lists_inside_deployment (issue #819)."""

import os

import pytest

from src.cli.source_registry import refuse_input_lists_inside_deployment

URL_LINE = "https://example.com/\n"


def _config(input_lists, config_path="cfg/a.yaml"):
    return {
        "_config_path": config_path,
        "data_manager": {"sources": {"links": {"input_lists": input_lists}}},
    }


def _deployment(tmp_path, name="smoke"):
    deployment_dir = tmp_path / "archi-home" / f"archi-{name}"
    (deployment_dir / "weblists").mkdir(parents=True)
    return deployment_dir


def test_absolute_path_inside_names_path_directory_and_config(tmp_path):
    deployment_dir = _deployment(tmp_path)
    inside = deployment_dir / "weblists" / "sources.list"
    inside.write_text(URL_LINE)

    with pytest.raises(ValueError) as excinfo:
        refuse_input_lists_inside_deployment([_config([str(inside)])], deployment_dir)

    message = str(excinfo.value)
    assert f"  - {inside} (config: cfg/a.yaml)" in message
    assert str(deployment_dir.resolve()) in message
    assert "--force" in message


def test_absolute_path_outside_passes(tmp_path):
    deployment_dir = _deployment(tmp_path)
    outside = tmp_path / "lists" / "sources.list"
    outside.parent.mkdir()
    outside.write_text(URL_LINE)

    assert (
        refuse_input_lists_inside_deployment([_config([str(outside)])], deployment_dir)
        is None
    )


def test_sibling_directory_with_the_same_prefix_passes(tmp_path):
    # archi-smoke-2/ is not inside archi-smoke/. A string-prefix comparison
    # would refuse it; a path comparison must not.
    deployment_dir = _deployment(tmp_path)
    sibling = deployment_dir.parent / "archi-smoke-2" / "weblists" / "sources.list"
    sibling.parent.mkdir(parents=True)
    sibling.write_text(URL_LINE)

    assert (
        refuse_input_lists_inside_deployment([_config([str(sibling)])], deployment_dir)
        is None
    )


def test_symlink_outside_that_resolves_inside_is_refused(tmp_path):
    deployment_dir = _deployment(tmp_path)
    target = deployment_dir / "weblists" / "sources.list"
    target.write_text(URL_LINE)
    link = tmp_path / "sources.list"
    link.symlink_to(target)

    with pytest.raises(ValueError) as excinfo:
        refuse_input_lists_inside_deployment([_config([str(link)])], deployment_dir)

    assert f"  - {link} -> {target.resolve()} (config: cfg/a.yaml)" in str(
        excinfo.value
    )


def test_relative_path_with_parent_segments_that_resolves_inside_is_refused(
    tmp_path, monkeypatch
):
    deployment_dir = _deployment(tmp_path)
    inside = deployment_dir / "weblists" / "sources.list"
    inside.write_text(URL_LINE)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    relative = os.path.relpath(inside, os.getcwd())
    assert relative.startswith("..")

    with pytest.raises(ValueError) as excinfo:
        refuse_input_lists_inside_deployment([_config([relative])], deployment_dir)

    assert f"  - {relative} -> {inside.resolve()} (config: cfg/a.yaml)" in str(
        excinfo.value
    )


def test_relative_path_outside_passes(tmp_path, monkeypatch):
    deployment_dir = _deployment(tmp_path)
    cwd = tmp_path / "elsewhere"
    (cwd / "lists").mkdir(parents=True)
    (cwd / "lists" / "ok.list").write_text(URL_LINE)
    monkeypatch.chdir(cwd)

    assert (
        refuse_input_lists_inside_deployment(
            [_config(["lists/ok.list"])], deployment_dir
        )
        is None
    )


def test_every_inside_path_in_every_config_is_named_once(tmp_path):
    deployment_dir = _deployment(tmp_path)
    inside_1 = deployment_dir / "weblists" / "one.list"
    inside_2 = deployment_dir / "two.list"
    outside = tmp_path / "ok.list"
    for path in (inside_1, inside_2, outside):
        path.write_text(URL_LINE)
    configs = [
        _config([str(inside_1), str(outside), str(inside_1)], "a.yaml"),
        _config([str(inside_2)], "b.yaml"),
    ]

    with pytest.raises(ValueError) as excinfo:
        refuse_input_lists_inside_deployment(configs, deployment_dir)

    message = str(excinfo.value)
    lines = [line for line in message.splitlines() if line.startswith("  - ")]
    assert lines == [
        f"  - {inside_1} (config: a.yaml)",
        f"  - {inside_2} (config: b.yaml)",
    ]
    assert str(outside) not in message


@pytest.mark.parametrize(
    "cfg_body",
    [
        {},
        {"data_manager": None},
        {"data_manager": {"sources": None}},
        {"data_manager": {"sources": {"links": True}}},
        {"data_manager": {"sources": {"links": {"input_lists": None}}}},
        {"data_manager": {"sources": {"links": {"input_lists": []}}}},
        {"data_manager": {"sources": {"links": {"input_lists": "a-string.list"}}}},
        {"data_manager": {"sources": {"links": {"input_lists": [None, 987654]}}}},
    ],
)
def test_entries_and_shapes_that_are_not_paths_are_ignored(tmp_path, cfg_body):
    deployment_dir = _deployment(tmp_path)
    cfg = {"_config_path": "a.yaml", **cfg_body}

    assert refuse_input_lists_inside_deployment([cfg], deployment_dir) is None
```

### Appendix B: `tests/unit/test_cli_input_list_inside_deployment.py`

```python
"""Tests for refuse_input_lists_inside_deployment at the create and evaluate call sites (#819)."""

import os

import pytest
import yaml
from click.testing import CliRunner

from src.cli import cli_main
from src.cli.managers.templates_manager import TemplateManager
from tests.unit.test_cli_create_dev_smoke import (
    EXAMPLE_CONFIG,
    REPO_ROOT,
    _existing_deployment,
    _record_teardowns,
    archi_home,
    benchmark_config,
    env_file,
    satisfied_base_images,
)

SENTINEL = "passed-the-inside-deployment-check"
URL_LINE = "https://example.com/\n"
CREATE_ARGS = ["-n", "smoke", "--services", "chatbot", "--hostmode"]


def _setup_spies(monkeypatch):
    """Record the teardown and stop there.

    A run without the check must reach remove_existing_deployment, so the
    preflight render is stubbed out (as test_force_create_still_tears_down_once_
    validation_passes does) and the teardown spy raises after it records.
    """
    calls = []

    def _spy(name):
        def _inner(*a, **kw):
            calls.append(name)
            raise RuntimeError(f"spy reached: {name}")

        return _inner

    monkeypatch.setattr(
        cli_main, "remove_existing_deployment", _spy("remove_existing_deployment")
    )
    monkeypatch.setattr(
        TemplateManager, "preflight_render", lambda self, *a, **kw: None
    )
    return calls


def _list_inside(existing):
    weblists = existing / "weblists"
    weblists.mkdir()
    inside = weblists / "x.list"
    inside.write_text(URL_LINE)
    return inside


def _write_config(tmp_path, base, input_lists):
    config = yaml.safe_load(base.read_text())
    config["data_manager"]["sources"]["links"]["input_lists"] = input_lists
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(config))
    return cfg


def _assert_refused_before_the_teardown(
    result, inside, existing, cfg, calls, teardowns
):
    assert result.exit_code != 0
    assert str(inside) in result.output
    assert str(existing.resolve()) in result.output
    assert str(cfg) in result.output
    assert calls == []
    assert teardowns == []
    assert inside.exists()
    assert (existing / "marker.txt").exists()


@pytest.mark.parametrize("flags", [["--force"], ["--force", "--dry"]])
def test_forced_create_refuses_a_list_inside_the_deployment_before_the_teardown(
    flags, archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [str(inside)])

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file)] + flags
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)


def test_forced_create_refuses_a_relative_path_that_resolves_inside_the_deployment(
    archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    relative = os.path.relpath(inside, REPO_ROOT)
    assert relative.startswith("..")
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [relative])

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file), "--force"]
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)
    assert f"{relative} -> {inside.resolve()}" in result.output


def test_forced_evaluate_refuses_a_list_inside_the_runtime_before_the_teardown(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, benchmark_config, [str(inside)])

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    _assert_refused_before_the_teardown(result, inside, existing, cfg, calls, teardowns)


def test_evaluate_without_force_reports_the_existing_runtime_not_the_list(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    """Without --force there is no teardown, so the check does not run.

    The existing-runtime refusal keeps its precedence (it comes after the
    input-list checks in evaluate).
    """
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    monkeypatch.setattr(
        cli_main, "preflight_benchmark_configs", lambda configs: ([], [])
    )
    existing = _existing_deployment(archi_home)
    inside = _list_inside(existing)
    teardowns = _record_teardowns(monkeypatch)
    calls = _setup_spies(monkeypatch)
    cfg = _write_config(tmp_path, benchmark_config, [str(inside)])

    result = CliRunner().invoke(
        cli_main.evaluate, ["-n", "smoke", "-c", str(cfg), "-e", str(env_file)]
    )

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "inside the deployment directory" not in result.output
    assert calls == []
    assert teardowns == []
    assert inside.exists()


def test_forced_create_passes_the_check_with_a_list_outside_the_deployment(
    archi_home, env_file, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _existing_deployment(archi_home)
    outside = tmp_path / "ok.list"
    outside.write_text(URL_LINE)
    cfg = _write_config(tmp_path, EXAMPLE_CONFIG, [str(outside)])

    def _sentinel_sm(*a, **kw):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "SecretsManager", _sentinel_sm)

    result = CliRunner().invoke(
        cli_main.create, CREATE_ARGS + ["-c", str(cfg), "-e", str(env_file), "--force"]
    )

    assert SENTINEL in result.output


def test_forced_evaluate_passes_the_check_with_a_list_outside_the_runtime(
    archi_home, env_file, benchmark_config, monkeypatch, tmp_path
):
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(cli_main, "check_docker_available", lambda: True)
    _existing_deployment(archi_home)
    outside = tmp_path / "ok.list"
    outside.write_text(URL_LINE)
    cfg = _write_config(tmp_path, benchmark_config, [str(outside)])

    def _sentinel_preflight(configs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli_main, "preflight_benchmark_configs", _sentinel_preflight)

    result = CliRunner().invoke(
        cli_main.evaluate,
        ["--force", "-n", "smoke", "-c", str(cfg), "-e", str(env_file)],
    )

    assert SENTINEL in result.output
```

### Appendix C: `src/cli/source_registry.py`

Replace the whole of today's `refuse_missing_input_lists` (its `def` line through the closing `)` of its `raise ValueError(...)`, lines 59-105 on `origin/dev`) with these three functions. Nothing else in the file changes; `_input_list_is_missing` stays as it is above them, and `Path` is already imported.

```python
def _configured_input_list_entries(configs):
    """Yield ``(path, config file)`` for each usable ``input_lists`` entry.

    The shape rules match get_enabled_sources(): a config whose ``data_manager``,
    ``sources``, or ``links`` is not a mapping, or whose ``input_lists`` is not a
    list, yields nothing, and an entry that is not a str or an os.PathLike is
    skipped (it keeps the WARNING that get_enabled_sources() logs for it). The
    config file is the ``_config_path`` the operator gave on the command line.
    """
    for cfg in configs:
        config_path = cfg.get("_config_path", "<unknown config>")
        dm = cfg.get("data_manager")
        if not isinstance(dm, dict):
            continue
        sources = dm.get("sources")
        if not isinstance(sources, dict):
            continue
        links = sources.get("links")
        if not isinstance(links, dict):
            continue
        input_lists = links.get("input_lists")
        if not isinstance(input_lists, list):
            continue
        for entry in input_lists:
            if not isinstance(entry, (str, os.PathLike)):
                continue
            yield os.fspath(entry), config_path


def refuse_missing_input_lists(configs) -> None:
    """Raise ValueError naming every configured input list that does not exist.

    A relative path resolves from the current directory, as get_enabled_sources()
    and staging resolve it (fasrc/archi#617). A path that exists but cannot be
    read is not refused here: those callers warn about it and skip it (#636).
    That includes a list under a directory the user cannot traverse: os.stat
    raises PermissionError there, which is not "does not exist".
    """
    missing_pairs = []
    seen = set()
    for key in _configured_input_list_entries(configs):
        if key not in seen and _input_list_is_missing(key[0]):
            seen.add(key)
            missing_pairs.append(key)

    if not missing_pairs:
        return None

    cwd = os.getcwd()
    lines = "\n".join(
        f"  - {path} (config: {cfg_path})" for path, cfg_path in missing_pairs
    )
    raise ValueError(
        f"input_lists: these configured list files do not exist. A relative path"
        f" resolves from the current directory ({cwd}), not from the config"
        f" file's directory.\n{lines}\n"
        f"Correct each path, or use an absolute path, then run the command again."
    )


def refuse_input_lists_inside_deployment(configs, deployment_dir) -> None:
    """Raise ValueError naming every configured input list inside *deployment_dir*.

    A ``--force`` run of ``archi create`` or ``archi evaluate`` removes the whole
    deployment directory (remove_existing_deployment() ->
    DeploymentManager.delete_deployment()) before it stages the lists into
    ``<deployment dir>/weblists/``. A list that lives inside that directory, for
    example a ``weblists/`` copy that an earlier run staged, passes
    refuse_missing_input_lists() and is then deleted by the teardown; staging
    warns and skips it, and the new deployment starts without it
    (fasrc/archi#819).

    Both sides are resolved with Path.resolve(): symbolic links are followed,
    ``..`` is collapsed, and a relative path is taken from the current directory,
    as refuse_missing_input_lists() takes it. So a link or a relative path that
    lands inside the directory is refused too. ``~`` is not expanded, as nowhere
    else in the input-list path handling expands it. A sibling directory with the
    same prefix (``archi-smoke-2`` next to ``archi-smoke``) is outside.
    """
    resolved_dir = Path(deployment_dir).resolve()
    inside = []
    seen = set()
    for path_str, config_path in _configured_input_list_entries(configs):
        key = (path_str, config_path)
        if key in seen:
            continue
        seen.add(key)
        resolved = Path(path_str).resolve()
        if resolved.is_relative_to(resolved_dir):
            inside.append((path_str, str(resolved), config_path))

    if not inside:
        return None

    lines = []
    for path_str, resolved_str, config_path in inside:
        where = (
            path_str if resolved_str == path_str else f"{path_str} -> {resolved_str}"
        )
        lines.append(f"  - {where} (config: {config_path})")
    raise ValueError(
        f"input_lists: these configured list files are inside the deployment"
        f" directory {resolved_dir}. A --force run deletes that directory before"
        f" archi stages the lists, so they would be lost.\n" + "\n".join(lines) + "\n"
        f"Move each file outside that directory, point the config at the new"
        f" location, then run the command again."
    )
```

### Appendix D: `src/cli/cli_main.py`

Three hunks. Find each by its context text, not by line number.

1. The import (line 26 on `origin/dev`):

```diff
-from src.cli.source_registry import refuse_missing_input_lists, source_registry
+from src.cli.source_registry import (
+    refuse_input_lists_inside_deployment,
+    refuse_missing_input_lists,
+    source_registry,
+)
```

2. In `create`, directly after the `refuse_missing_input_lists` call (line 192 on `origin/dev`):

```diff
         # Initialize managers
         config_manager = ConfigurationManager(config_files, env)
         refuse_missing_input_lists(config_manager.get_configs())
+        if force:
+            # The --force teardown removes base_dir before the lists are staged (#819).
+            refuse_input_lists_inside_deployment(config_manager.get_configs(), base_dir)
         try:
             secrets_manager = SecretsManager(env_file, config_manager)
```

3. In `evaluate`, directly after the `refuse_missing_input_lists` call (line 859 on `origin/dev`):

```diff
         config_manager = ConfigurationManager(config_files, env)
         refuse_missing_input_lists(config_manager.get_configs())
+        if force:
+            # The --force teardown removes base_dir before the lists are staged (#819).
+            refuse_input_lists_inside_deployment(config_manager.get_configs(), base_dir)
         bank_errors, bank_warnings = preflight_benchmark_configs(config_manager.configs)
```

`restart` (`refuse_missing_input_lists` at line 612) gets no call: it has no teardown.
