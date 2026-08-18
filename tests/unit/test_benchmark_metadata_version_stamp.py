"""``add_metadata`` must stamp the artifact with the code that produced it.

``metadata`` carried ``git_info`` (frozen at ``archi create``, so identical across
every 2026-08-11 to 2026-08-17 run) and ``corpus_snapshot_id`` (a fresh nonce per
invocation). Neither can attribute a run to a code version.

The code version is per invocation -- one image runs every arm of a sweep -- so it
lives here. The config version is per arm and lives on each result record; this
block only summarises their digests.
"""

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import (
    code_fingerprint,
    config_fingerprint,
    config_version,
    effective_config,
)

RUNNING = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}
SELECTED = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}

COMMIT = "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Empty result set, a deploy git_info file, and a tiny stand-in package."""
    monkeypatch.setattr(ResultHandler, "metadata", {})
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(
        ResultHandler, "get_corpus_snapshot_id", staticmethod(lambda: "snap")
    )
    monkeypatch.setattr(
        ResultHandler, "get_corpus_fingerprint", staticmethod(lambda: "sha256:corpus")
    )

    git_info = tmp_path / "git_info.yaml"
    git_info.write_text(f"last_commit: {COMMIT}\n")
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(git_info))

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_bytes(b"body")
    monkeypatch.setattr(sb, "PACKAGE_DIR", str(package))


def _record(running=RUNNING, selected=SELECTED):
    """Stand in for the record ``handle_results`` appends."""
    return {
        "configuration": selected,
        "running_configuration": running,
        "configuration_file": "/root/archi/config.yaml",
        "config_version": config_version(
            running=running, selected=selected, selected_file="/root/archi/config.yaml"
        ),
    }


def test_stamps_a_code_digest_of_the_package_files():
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    code = ResultHandler.metadata["code_version"]
    assert code["digest"] == code_fingerprint([("mod.py", b"body")])
    assert code["file_count"] == 1


def test_the_frozen_deploy_commit_is_kept_but_labelled():
    """The value shared by every Aug 11-17 run must not read as the arm's code."""
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    code = ResultHandler.metadata["code_version"]
    assert code["deploy_git_commit"] == COMMIT
    assert "archi create" in code["deploy_git_note"]
    assert code["digest"] != code["deploy_git_commit"]


def test_the_code_digest_does_not_depend_on_which_modules_were_imported(monkeypatch):
    """Two runs of one image must agree, whichever code paths they took.

    A sys.modules-based digest did not: `src.utils.rbac.registry` loads only when
    a decorated agent tool runs, so the digest moved with the model's choices.
    """
    ResultHandler.results.append(_record())
    ResultHandler.add_metadata()
    first = ResultHandler.metadata["code_version"]["digest"]

    monkeypatch.setitem(__import__("sys").modules, "src.fake_lazy_module", object())
    ResultHandler.metadata.clear()
    ResultHandler.add_metadata()

    assert ResultHandler.metadata["code_version"]["digest"] == first


def test_summarises_the_config_digest_of_every_arm_that_ran():
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["config_versions"] == [
        config_fingerprint(effective_config(RUNNING, SELECTED))
    ]


def test_a_sweep_lists_one_digest_per_arm_not_just_the_last():
    """bench-sweep-20260610 ran three arms; one label for all three is a lie."""
    ResultHandler.results.append(_record(running=RUNNING))
    ResultHandler.results.append(_record(running=SELECTED))

    ResultHandler.add_metadata()

    digests = ResultHandler.metadata["config_versions"]
    assert len(digests) == 2
    assert digests[0] != digests[1]


def test_no_single_config_version_is_stamped_on_the_metadata():
    """A per-file config version cannot describe a multi-arm invocation."""
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    assert "config_version" not in ResultHandler.metadata


def test_the_existing_metadata_fields_are_preserved():
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    meta = ResultHandler.metadata
    assert meta["corpus_snapshot_id"] == "snap"
    assert meta["corpus_fingerprint"] == "sha256:corpus"
    assert meta["git_info"]["last_commit"].strip() == COMMIT
    assert "time" in meta


def test_a_run_with_no_results_still_produces_metadata():
    """A benchmark that scored nothing must not crash on the way to its report."""
    ResultHandler.add_metadata()

    assert ResultHandler.metadata["config_versions"] == []
    assert ResultHandler.metadata["code_version"]["digest"].startswith("sha256:")


def test_an_unreadable_deploy_git_info_does_not_lose_the_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(tmp_path / "absent.yaml"))
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["code_version"]["deploy_git_commit"] is None
    assert ResultHandler.metadata["code_version"]["digest"].startswith("sha256:")


def test_an_unreadable_package_dir_does_not_lose_the_metadata(monkeypatch):
    """Provenance must never be the reason a finished run loses its scores."""
    monkeypatch.setattr(sb, "PACKAGE_DIR", "/nonexistent/package")
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    code = ResultHandler.metadata["code_version"]
    assert code["digest"] is None
    assert code["deploy_git_commit"] == COMMIT
