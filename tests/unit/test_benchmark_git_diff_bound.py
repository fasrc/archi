import json
import os
import shutil

import pytest

from src.cli.managers.templates_manager import get_git_information

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    # A git hook exports GIT_DIR and GIT_INDEX_FILE (absolute in a worktree). If they
    # leak into these git calls, `git init`/`add`/`commit` rewrite the repository that
    # runs the hook, not the fixture's checkout.
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
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


def test_a_non_ascii_oversized_change_stays_under_the_serialized_bound(repo):
    from src.cli.managers.git_diff_capture import GIT_DIFF_MAX_BYTES

    # json.dump escapes every non-ASCII character (ensure_ascii), so one emoji is
    # 4 UTF-8 bytes but 12 serialized bytes; the UTF-8 cap alone allows ~3x growth.
    lines = ["x = 1\n"] + [f"s_{i} = '{chr(0x1F600) * 20}'\n" for i in range(20_000)]
    (repo / "src" / "pkg" / "mod.py").write_text("".join(lines), encoding="utf-8")
    info = get_git_information(wd=repo)
    assert info["git_diff"].endswith("\n")
    assert len(info["git_diff"].encode("utf-8")) <= GIT_DIFF_MAX_BYTES
    assert len(json.dumps(info["git_diff"])) - 2 <= GIT_DIFF_MAX_BYTES
    assert info["git_diff_truncated"] is True
    assert info["git_diff_original_bytes"] > 1_000_000
    assert len(json.dumps(info).encode("utf-8")) < 512_000


def test_bound_serialized_text_counts_a_newline_as_its_two_byte_escape():
    from src.cli.managers.git_diff_capture import bound_serialized_text

    # Each "xxxx\\n" line is 5 UTF-8 bytes but 6 serialized bytes.
    text = "aaaa\nbbbb\ncccc\n"
    assert bound_serialized_text(text, 12) == ("aaaa\nbbbb\n", True, 15)
    assert bound_serialized_text(text, 11) == ("aaaa\n", True, 15)
    assert bound_serialized_text(text, 100) == (text, False, 15)
    assert bound_serialized_text("", 10) == ("", False, 0)


def test_bound_serialized_text_cuts_non_ascii_text_by_its_escaped_size():
    from src.cli.managers.git_diff_capture import bound_serialized_text

    # "é" is 2 UTF-8 bytes and 6 escaped bytes ("\\u00e9").
    kept, truncated, original = bound_serialized_text("é\n" * 10, 20)
    assert kept == "é\n" * 2
    assert (truncated, original) == (True, 30)
    assert len(json.dumps(kept)) - 2 <= 20


def test_bound_serialized_text_keeps_a_prefix_of_one_long_non_ascii_line():
    from src.cli.managers.git_diff_capture import bound_serialized_text

    kept, truncated, original = bound_serialized_text("é" * 50, 20)
    assert kept == "é" * 3
    assert (truncated, original) == (True, 100)


def test_bound_serialized_text_cuts_text_under_the_byte_cap_when_escapes_overflow():
    from src.cli.managers.git_diff_capture import bound_serialized_text

    # 18 UTF-8 bytes fit a 20-byte cap, but 8 escapes cost 48 serialized bytes. No
    # whole line fits, and a non-empty diff never becomes empty: keep a prefix.
    kept, truncated, original = bound_serialized_text("éééé\néééé\n", 20)
    assert kept == "ééé"
    assert (truncated, original) == (True, 18)


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


def test_the_stat_stays_bounded_when_the_checkout_prints_paths_verbatim(repo):
    import subprocess

    from src.cli.managers.git_diff_capture import (
        GIT_DIFF_STAT_MAX_FILES,
        GIT_DIFF_STAT_WIDTH,
    )

    # With core.quotePath=false git prints bytes above 0x80 verbatim and shortens a
    # stat name by display columns, so a path of zero-width combining marks takes
    # kilobytes but no columns, and --stat's width stops bounding the bytes.
    subprocess.check_call(["git", "config", "core.quotePath", "false"], cwd=repo)
    segment = "a" + "\u0301" * 120
    deep = repo.joinpath(*[segment] * 6)
    deep.mkdir(parents=True)
    for i in range(250):
        (deep / f"f{i}").write_text("old\n")
    subprocess.check_call(["git", "add", "-A"], cwd=repo)
    subprocess.check_call(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "m"],
        cwd=repo,
    )
    for i in range(250):
        (deep / f"f{i}").write_text("new\n")
    stat = get_git_information(wd=repo)["git_diff_stat"]
    assert "250 files changed" in stat
    lines = stat.splitlines()
    assert len(lines) <= GIT_DIFF_STAT_MAX_FILES + 3
    assert len(json.dumps(stat)) - 2 <= (GIT_DIFF_STAT_MAX_FILES + 3) * (
        GIT_DIFF_STAT_WIDTH + 2
    )
