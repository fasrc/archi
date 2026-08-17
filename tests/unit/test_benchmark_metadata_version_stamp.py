"""``add_metadata`` must stamp the artifact with the code and config it ran.

Before this, ``metadata`` carried ``git_info`` (frozen at ``archi create``, so
identical across every 2026-08-11 to 2026-08-17 run) and ``corpus_snapshot_id``
(a fresh nonce per invocation). Neither can attribute a run to a code version,
and nothing recorded the configuration's identity at all -- which is why
``bench-8192-20260817_170850.json`` attests ``context_window: 32768``.
"""

import pytest

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler
from src.utils.benchmark_provenance import config_fingerprint, config_version

RUNNING = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}
SELECTED = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}

DEPLOY_GIT_INFO = {"last_commit": "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9\n"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Give ``add_metadata`` a deploy git_info file and an empty result set."""
    monkeypatch.setattr(ResultHandler, "metadata", {})
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(
        ResultHandler, "get_corpus_snapshot_id", staticmethod(lambda: "snap")
    )
    monkeypatch.setattr(
        ResultHandler, "get_corpus_fingerprint", staticmethod(lambda: "sha256:corpus")
    )

    git_info = tmp_path / "git_info.yaml"
    git_info.write_text("last_commit: 0a157cdce02de6a3e3455c82a91bfbddfa00a0d9\n")
    monkeypatch.setattr(sb, "EXTRA_METADATA_PATH", str(git_info))


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


def test_stamps_a_code_version_digest_not_only_the_deploy_commit(monkeypatch):
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    code = ResultHandler.metadata["code_version"]
    assert code["digest"].startswith("sha256:")
    assert code["module_count"] > 0


def test_the_frozen_deploy_commit_is_kept_but_labelled(monkeypatch):
    """The value shared by every Aug 11-17 run must not read as the arm's code."""
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    code = ResultHandler.metadata["code_version"]
    assert code["deploy_git_commit"] == "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9"
    assert "archi create" in code["deploy_git_note"]


def test_summarises_the_config_digest_of_every_arm_that_ran(monkeypatch):
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["config_versions"] == [config_fingerprint(RUNNING)]


def test_a_sweep_lists_one_digest_per_arm_not_just_the_last(monkeypatch):
    """bench-sweep-20260610 ran three arms; one label for all three is a lie."""
    ResultHandler.results.append(_record(running=RUNNING))
    ResultHandler.results.append(_record(running=SELECTED))

    ResultHandler.add_metadata()

    assert ResultHandler.metadata["config_versions"] == [
        config_fingerprint(RUNNING),
        config_fingerprint(SELECTED),
    ]


def test_no_single_config_version_is_stamped_on_the_metadata(monkeypatch):
    """A per-file config version cannot describe a multi-arm invocation."""
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    assert "config_version" not in ResultHandler.metadata


def test_the_per_arm_detail_stays_on_the_arm(monkeypatch):
    """The bench-8192 failure, visible on the record that produced it."""
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    stamp = ResultHandler.results[0]["config_version"]
    assert stamp["divergence_from_selected_file"] == [
        "services.chat_app.context_editing.context_window"
    ]
    assert stamp["key_settings"]["services.chat_app.context_editing"] == {
        "context_window": 8192
    }


def test_the_existing_metadata_fields_are_preserved(monkeypatch):
    ResultHandler.results.append(_record())

    ResultHandler.add_metadata()

    meta = ResultHandler.metadata
    assert meta["corpus_snapshot_id"] == "snap"
    assert meta["corpus_fingerprint"] == "sha256:corpus"
    assert meta["git_info"]["last_commit"].strip().startswith("0a157cd")
    assert "time" in meta


def test_a_run_with_no_results_still_produces_metadata(monkeypatch):
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
    assert ResultHandler.metadata["config_versions"] == [config_fingerprint(RUNNING)]
