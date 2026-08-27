import json
import logging
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from src.interfaces.chat_app import evaluation_console
from src.interfaces.chat_app.evaluation_console import (
    build_authorize_request,
    build_evaluation_service,
    can_view_evaluations,
)
from src.utils.rbac.permission_enum import Permission


def _flask_app():
    # A secret key is required before a test request context accepts session writes.
    app = Flask(__name__)
    app.secret_key = "evaluation-console-test"
    return app


@pytest.mark.parametrize(
    "chat_app_config",
    [
        {},
        {"evaluations": {}},
        {"evaluations": None},
        {"evaluations": {"enabled": False}},
        {"evaluations": {"enabled": 1}},
        {"evaluations": {"enabled": "true"}},
    ],
)
def test_evaluation_service_requires_strictly_true_enablement(chat_app_config):
    assert build_evaluation_service(chat_app_config) is None


def test_evaluation_service_uses_deployment_defaults():
    # The defaults are container paths, so the constructor is recorded rather
    # than run: a real build would mkdir /root/archi/evaluations on the host.
    # agent_config_path has no default — it is required, and named here.
    # The write probe is recorded for the same reason: with construction mocked
    # nothing creates that tree, so a real probe would report the container path
    # as unusable on a host that has no /root/archi.
    with patch.object(evaluation_console, "EvaluationConsoleService") as factory:
        with patch.object(evaluation_console.tempfile, "NamedTemporaryFile"):
            service = build_evaluation_service(
                {
                    "evaluations": {
                        "enabled": True,
                        "agent_config_path": "/root/archi/configs/evaluation.yaml",
                    }
                }
            )

    assert service is factory.return_value
    assert factory.call_args.args == (Path("/root/archi/evaluations"),)
    assert factory.call_args.kwargs == {
        "agent_config_path": Path("/root/archi/configs/evaluation.yaml"),
        "agents_dir": Path("/root/archi/agents"),
        "mcp_config_path": None,
    }


@pytest.mark.parametrize(
    "evaluations_config",
    [
        {"enabled": True},
        {"enabled": True, "agent_config_path": None},
        {"enabled": True, "agent_config_path": ""},
        {"enabled": True, "agent_config_path": "   "},
        {"enabled": True, "agent_config_path": 7},
    ],
)
def test_evaluation_service_requires_a_named_agent_config(evaluations_config, caplog):
    """An enabled console without an explicit config path stays off.

    Each run copies the named file into its own run directory, so there is no
    safe default: the deployment must name a redacted copy.
    """
    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service({"evaluations": evaluations_config})

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "evaluations.agent_config_path is required" in caplog.text


@pytest.mark.parametrize(
    "configured_path",
    [
        "/root/archi/configs/config.yaml",
        "/root/archi/configs//config.yaml",
        "/root/archi/configs/./config.yaml",
        "/root/archi/configs/../configs/config.yaml",
        "/root/archi/../archi/configs/config.yaml",
    ],
)
def test_evaluation_service_refuses_the_live_deployment_config(configured_path, caplog):
    """The live config is the one path the console must never snapshot.

    Every spelling of it counts: a ``..`` segment names the same file, so the
    guard compares canonical targets, not the text it was handed.
    """
    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": configured_path}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "/root/archi/configs/config.yaml" in caplog.text
    assert "live deployment config" in caplog.text


def test_evaluation_service_refuses_a_symlink_to_the_live_deployment_config(
    monkeypatch, tmp_path, caplog
):
    """A symlink pointing at the live config resolves to it, so it is refused.

    The refused location moves into ``tmp_path``: the case needs a real symlink
    and a real target, and the gate cannot write under ``/root``.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(live)
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": str(alias)}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "live deployment config" in caplog.text


def test_evaluation_service_refuses_a_hard_link_to_the_live_deployment_config(
    monkeypatch, tmp_path, caplog
):
    """A hard link is a second name for one file, so it is refused as well.

    Path canonicalization cannot see it — both names are already canonical — so
    the guard asks the filesystem for identity instead. A bind mount of the live
    config into the container reads the same way. The refused location moves into
    ``tmp_path``: the case needs two real names for one real file.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    alias = tmp_path / "alias.yaml"
    os.link(live, alias)
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {"evaluations": {"enabled": True, "agent_config_path": str(alias)}}
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert "live deployment config" in caplog.text


def test_evaluation_service_accepts_a_distinct_existing_config(monkeypatch, tmp_path):
    """The identity check must not refuse a real file that is a different file."""
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    redacted = tmp_path / "evaluation.yaml"
    redacted.write_text("redacted: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    service = build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": str(tmp_path / "evaluations"),
                "agent_config_path": str(redacted),
            }
        }
    )

    assert service is not None
    assert service.agent_config_path == redacted


def test_evaluation_service_honours_configured_paths(tmp_path):
    service = build_evaluation_service(
        {
            "agents_dir": str(tmp_path / "agents"),
            "evaluations": {
                "enabled": True,
                "root": str(tmp_path / "evaluations"),
                "agent_config_path": str(tmp_path / "configs" / "config.yaml"),
                "mcp_config_path": str(tmp_path / "qa_evaluation_mcp.yaml"),
            },
        }
    )

    assert service is not None
    assert service.catalog.root == tmp_path / "evaluations"
    assert service.agent_config_path == tmp_path / "configs" / "config.yaml"
    assert service.agents_dir == tmp_path / "agents"
    assert service.mcp_config_path == tmp_path / "qa_evaluation_mcp.yaml"


def test_evaluation_service_creates_the_catalog_tree(tmp_path):
    root = tmp_path / "evaluations"

    build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": str(root),
                "agent_config_path": str(tmp_path / "evaluation.yaml"),
            }
        }
    )

    assert (root / "datasets").is_dir()
    assert (root / "runs").is_dir()
    assert (root / "jobs").is_dir()


def test_evaluation_service_disables_on_an_unwritable_root(
    monkeypatch, tmp_path, caplog
):
    """A root whose parent is a regular file cannot be mkdir'd, and must not crash chat.

    The live-config refusal is neutralized via ``LIVE_AGENT_CONFIG_PATH`` so this
    test proves the storage guard, not the earlier config guard.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    root = blocker / "evaluations"

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert str(root) in caplog.text


def test_evaluation_service_disables_when_the_stale_job_sweep_cannot_write(
    monkeypatch, tmp_path, caplog
):
    """The storage guard covers the whole construction, not just the first mkdir.

    The root itself is writable here, so ``EvaluationCatalog`` and the jobs-dir
    mkdir both succeed. The failure comes from ``EvaluationJobManager``'s stale-job
    sweep (``_interrupt_stale_jobs``), which only writes when it finds a job file to
    interrupt, so one queued job is seeded first.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    jobs_dir = root / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / f"{uuid.uuid4()}.json"
    job_path.write_text(
        json.dumps({"id": job_path.stem, "status": "queued"}), encoding="utf-8"
    )

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    def _raise(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("src.evaluation.qa.jobs.write_json", _raise)

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]


def test_evaluation_service_disables_on_a_prepopulated_read_only_root(
    monkeypatch, tmp_path, caplog
):
    """The guard has to catch the root that raises nothing during construction.

    Construction only calls ``mkdir(parents=True, exist_ok=True)`` five times
    (``src/evaluation/qa/catalog.py:261-268``) and sweeps stale jobs, which
    writes only when it finds a job to interrupt. A read-only mount that already
    holds the five directories and no active job therefore raises nothing: the
    console registers, and the first dataset import 500s on a temporary file.
    That is the pre-populated case an operator restoring a snapshot onto a
    read-only volume actually hits.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    for name in ("datasets", "profiles", "drafts", "runs", "jobs"):
        (root / name).mkdir(parents=True)

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    def _read_only(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(evaluation_console.tempfile, "NamedTemporaryFile", _read_only)

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )

    assert service is None
    assert [record.levelname for record in caplog.records] == ["ERROR"]
    assert str(root) in caplog.text


CATALOG_DIRECTORIES = ("datasets", "profiles", "drafts", "runs", "jobs")


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores the directory modes this test relies on"
)
def test_evaluation_service_disables_on_a_real_read_only_tree(
    monkeypatch, tmp_path, caplog
):
    """The same case without a patched probe: a genuinely unwritable mode.

    A read-only mount is read-only throughout, so the modes are set on the
    catalog directories as well as the root. The root alone is not the surface
    under test: nothing is written directly there once the tree exists, so a
    read-only root whose catalog directories are writable — separate mounts
    inside it — is serviceable and must not be refused.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    for name in CATALOG_DIRECTORIES:
        (root / name).mkdir(parents=True)

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    for name in CATALOG_DIRECTORIES:
        (root / name).chmod(0o555)
    root.chmod(0o555)
    try:
        with caplog.at_level(
            logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
        ):
            service = build_evaluation_service(
                {
                    "evaluations": {
                        "enabled": True,
                        "root": str(root),
                        "agent_config_path": str(agent_config_path),
                    }
                }
            )
    finally:
        root.chmod(0o755)
        for name in CATALOG_DIRECTORIES:
            (root / name).chmod(0o755)

    assert service is None
    assert str(root) in caplog.text


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores the directory mode this test relies on"
)
def test_evaluation_service_builds_when_only_the_root_itself_is_read_only(
    monkeypatch, tmp_path
):
    """The other side of the same contract: the root is not a write surface.

    Refusing this deployment would disable a console that works — the catalog
    directories are where every write lands.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    for name in CATALOG_DIRECTORIES:
        (root / name).mkdir(parents=True)

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    root.chmod(0o555)
    try:
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )
    finally:
        root.chmod(0o755)

    assert service is not None


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores the directory mode this test relies on"
)
def test_evaluation_service_disables_when_one_catalog_directory_is_unwritable(
    monkeypatch, tmp_path, caplog
):
    """The probe has to cover the directories the console actually writes to.

    Nothing is written at the root: dataset, profile and draft creation stage
    into `tempfile.TemporaryDirectory(dir=...)` under `datasets/`, `profiles/`
    and `drafts/` (`src/evaluation/qa/catalog.py:468-472`, `:532-536`,
    `:775-779`), runs are workspaces under `runs/`, and job records are written
    under `jobs/`. A writable root with one tighter-permissioned or separately
    mounted child therefore passes a root-only probe and fails on first use.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    for name in ("datasets", "profiles", "drafts", "runs", "jobs"):
        (root / name).mkdir(parents=True)

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    (root / "datasets").chmod(0o555)
    try:
        with caplog.at_level(
            logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
        ):
            service = build_evaluation_service(
                {
                    "evaluations": {
                        "enabled": True,
                        "root": str(root),
                        "agent_config_path": str(agent_config_path),
                    }
                }
            )
    finally:
        (root / "datasets").chmod(0o755)

    assert service is None
    assert "datasets" in caplog.text, "name the directory the operator must fix"


def test_evaluation_service_leaves_no_probe_file_behind(tmp_path):
    """A writable root still builds, and the probe cleans up after itself."""
    root = tmp_path / "evaluations"

    service = build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": str(root),
                "agent_config_path": str(tmp_path / "evaluation.yaml"),
            }
        }
    )

    assert service is not None
    assert sorted(entry.name for entry in root.iterdir()) == [
        "datasets",
        "drafts",
        "jobs",
        "profiles",
        "runs",
    ]


def test_evaluation_service_survives_a_corrupt_job_file(monkeypatch, tmp_path, caplog):
    """A corrupt job file needs no net of its own.

    ``_interrupt_stale_jobs`` already turns an unreadable job file into a caught
    ``ValueError`` and continues (``src/evaluation/qa/jobs.py:62-64``), because
    ``read_json`` wraps the read failure as ``ValueError``
    (``src/evaluation/qa/artifacts.py:176-177``). This pins that as the answer to
    plan item 3 of issue #328: no production change here.
    """
    live = tmp_path / "config.yaml"
    live.write_text("live: true\n", encoding="utf-8")
    monkeypatch.setattr(evaluation_console, "LIVE_AGENT_CONFIG_PATH", str(live))

    root = tmp_path / "evaluations"
    jobs_dir = root / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / f"{uuid.uuid4()}.json").write_text("not json at all", encoding="utf-8")

    agent_config_path = tmp_path / "evaluation.yaml"
    agent_config_path.write_text("redacted: true\n", encoding="utf-8")

    with caplog.at_level(
        logging.ERROR, logger="src.interfaces.chat_app.evaluation_console"
    ):
        service = build_evaluation_service(
            {
                "evaluations": {
                    "enabled": True,
                    "root": str(root),
                    "agent_config_path": str(agent_config_path),
                }
            }
        )

    assert service is not None
    assert [record.levelname for record in caplog.records] == []


def test_evaluation_service_does_not_swallow_a_non_storage_error():
    """A programming error inside the constructor must not read as a disabled console.

    The ``except OSError`` boundary in ``build_evaluation_service`` is deliberately
    narrow: only a storage failure disables the console. Anything else — here a
    ``TypeError`` standing in for a real defect — must propagate, so a later
    widening to ``except Exception`` fails this test instead of quietly hiding a bug.
    """
    with patch.object(
        evaluation_console,
        "EvaluationConsoleService",
        side_effect=TypeError("unexpected keyword argument"),
    ):
        with pytest.raises(TypeError):
            build_evaluation_service(
                {
                    "evaluations": {
                        "enabled": True,
                        "agent_config_path": "/root/archi/configs/evaluation.yaml",
                    }
                }
            )


def test_authorize_request_allows_every_permission_when_auth_is_off():
    authorize_request = build_authorize_request(False)

    with _flask_app().test_request_context("/api/evaluations/catalog"):
        assert authorize_request(Permission.Evaluations.VIEW) is None
        assert authorize_request(Permission.Evaluations.MANAGE) is None


def test_authorize_request_rejects_anonymous_callers_with_401():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog"):
        response, status = authorize_request(Permission.Evaluations.VIEW)

    assert status == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_authorize_request_rejects_missing_permission_with_403():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog") as ctx:
        ctx.session["logged_in"] = True
        ctx.session["roles"] = ["viewer"]
        with patch.object(
            evaluation_console, "has_permission", return_value=False
        ) as has_permission:
            response, status = authorize_request(Permission.Evaluations.RUN)

    assert status == 403
    assert response.get_json()["error"] == "Forbidden"
    assert response.get_json()["required_permission"] == Permission.Evaluations.RUN
    has_permission.assert_called_once_with(Permission.Evaluations.RUN, ["viewer"])


def test_authorize_request_allows_a_permitted_session():
    authorize_request = build_authorize_request(True)

    with _flask_app().test_request_context("/api/evaluations/catalog") as ctx:
        ctx.session["logged_in"] = True
        ctx.session["roles"] = ["admin"]
        with patch.object(evaluation_console, "has_permission", return_value=True):
            assert authorize_request(Permission.Evaluations.MANAGE) is None


@pytest.mark.parametrize(
    ("evaluations_enabled", "auth_enabled", "has_view_permission", "expected_visible"),
    [
        (False, False, True, False),
        (False, True, True, False),
        (True, False, False, True),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
def test_navigation_visibility_matches_route_access(
    evaluations_enabled, auth_enabled, has_view_permission, expected_visible
):
    with _flask_app().test_request_context():
        with patch.object(
            evaluation_console, "has_permission", return_value=has_view_permission
        ) as has_permission:
            visible = can_view_evaluations(evaluations_enabled, auth_enabled)

    assert visible is expected_visible
    assert has_permission.call_count == int(evaluations_enabled and auth_enabled)


def test_explicit_null_root_falls_back_to_the_default(monkeypatch, tmp_path):
    """``root: null`` must disable or default, never crash app init.

    ``dict.get("root", DEFAULT)`` returns the default only when the key is
    ABSENT; an explicit YAML null returns ``None``, and ``Path(None)`` raises
    ``TypeError``. That escapes the ``except OSError`` fail-closed path, so a
    shape the create-time validator accepts as inert would take chat down with
    it instead of just switching the console off.
    """
    redacted = tmp_path / "evaluation.yaml"
    redacted.write_text("redacted: true\n", encoding="utf-8")
    monkeypatch.setattr(
        evaluation_console, "DEFAULT_EVALUATION_ROOT", str(tmp_path / "evaluations")
    )

    service = build_evaluation_service(
        {
            "evaluations": {
                "enabled": True,
                "root": None,
                "agent_config_path": str(redacted),
            }
        }
    )

    assert service is not None
    assert service.catalog.root == tmp_path / "evaluations"
