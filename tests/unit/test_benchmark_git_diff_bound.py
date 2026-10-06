import json
import os
import shutil

import pytest

from src.cli.managers.templates_manager import get_git_information

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "src").mkdir()
    (checkout / "src" / "pkg").mkdir()
    (checkout / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (checkout / "README.md").write_text("readme\n")
    bench_out = checkout / "bench_out"
    bench_out.mkdir()
    (bench_out / "art.json").write_text(json.dumps({"k": list(range(20000))}))

    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    import subprocess

    subprocess.check_call(["git", "init", "-q"], cwd=checkout, env=env)
    subprocess.check_call(["git", "add", "-A"], cwd=checkout, env=env)
    subprocess.check_call(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        cwd=checkout,
        env=env,
    )
    return checkout


def test_a_clean_tree_records_empty_fields(repo):
    info = get_git_information(wd=repo)
    assert info["git_diff"] == ""
    assert info["git_diff_stat"] == ""
    assert info["git_diff_truncated"] is False
    assert info["git_diff_original_bytes"] == 0


def test_a_small_change_is_kept_whole(repo):
    (repo / "src" / "pkg" / "mod.py").write_text("x = 1\ny = 2\n")
    info = get_git_information(wd=repo)
    assert "+" in info["git_diff"]
    assert info["git_diff_truncated"] is False
    assert info["git_diff_original_bytes"] == len(info["git_diff"].encode("utf-8"))
    assert "1 file changed" in info["git_diff_stat"]


def test_an_oversized_change_is_cut_and_marked(repo):
    from src.cli.managers.git_diff_capture import GIT_DIFF_MAX_BYTES

    lines = ["x = 1\n"] + [f"line_{i} = {i}\n" for i in range(90_000)]
    (repo / "src" / "pkg" / "mod.py").write_text("".join(lines))
    info = get_git_information(wd=repo)
    assert len(info["git_diff"].encode("utf-8")) <= GIT_DIFF_MAX_BYTES
    assert info["git_diff"].endswith("\n")
    assert info["git_diff_truncated"] is True
    assert info["git_diff_original_bytes"] > 1_000_000
    assert len(json.dumps(info).encode("utf-8")) < 512_000


def test_a_bench_out_only_deletion_records_a_clean_tree(repo):
    (repo / "bench_out" / "art.json").unlink()
    info = get_git_information(wd=repo)
    assert info["git_diff"] == ""
    assert info["git_diff_stat"] == ""
    assert info["git_diff_original_bytes"] == 0


def test_a_mixed_change_records_only_the_code_path(repo):
    (repo / "src" / "pkg" / "mod.py").write_text("x = 1\ny = 2\n")
    (repo / "bench_out" / "art.json").unlink()
    info = get_git_information(wd=repo)
    assert "src/pkg/mod.py" in info["git_diff"]
    assert "bench_out" not in info["git_diff"]
    assert "bench_out" not in info["git_diff_stat"]


def test_capture_from_a_subdirectory_covers_the_whole_tree(repo):
    (repo / "src" / "pkg" / "mod.py").write_text("x = 1\ny = 2\n")
    (repo / "README.md").write_text("readme\nmore\n")
    (repo / "bench_out" / "art.json").unlink()
    info = get_git_information(wd=repo / "src" / "pkg")
    assert "src/pkg/mod.py" in info["git_diff"]
    assert "README.md" in info["git_diff"]
    assert "bench_out" not in info["git_diff"]
    assert "bench_out" not in info["git_diff_stat"]
