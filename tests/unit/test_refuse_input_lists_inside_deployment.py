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
