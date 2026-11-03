"""A manual ingest records its source status after its vector update (issue #699).

Each uploader handler that collects or removes documents runs the vectorstore update
through ``post_update_hook``. The per-source status in ``ingestion_status.json`` must
be written only after that update returns: ``idle`` with a new ``last_run`` when it
succeeds, and ``error`` with the old ``last_run`` and a 500 answer when it fails.
"""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module

OLD_RUN = "2026-01-01T00:00:00+00:00"
SOURCE_KEYS = ("git", "jira", "local_files", "web")

# (handler name, form data, source key, 200 answer body)
HANDLERS = [
    (
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
        "git",
        {"status": "ok"},
    ),
    (
        "remove_git_repo",
        {"repo": "repo"},
        "git",
        {"status": "ok", "repo": "repo", "deleted": 1},
    ),
    ("add_jira_project", {"project_key": "PROJ"}, "jira", {"status": "ok"}),
    ("upload", None, "local_files", {"status": "ok", "path": "/data/a.txt"}),
    (
        "upload_url",
        {"url": "https://example.org/page"},
        "web",
        {"status": "ok", "resources_scraped": 3},
    ),
]
HANDLER_IDS = [handler[0] for handler in HANDLERS]


class _Collectors:
    """Stand-in for the scraper, ticket and local-file managers."""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.jira_client = object()

    def _collect(self):
        if self.fail_with is not None:
            raise self.fail_with

    def collect_git(self, persistence, urls):
        self._collect()

    def collect_jira(self, persistence, keys):
        self._collect()

    def collect_links(self, persistence, link_urls, max_depth):
        self._collect()
        return 3

    def ingest_uploaded_file(self, upload, persistence):
        self._collect()
        return Path("/data/a.txt")


class _Persistence:
    def flush_index(self):
        pass

    def delete_resource(self, resource_hash, flush=True):
        pass


class _Catalog:
    metadata_index = {"h1": None}

    def refresh(self):
        pass

    def get_metadata_for_hash(self, resource_hash):
        return {"source_type": "git", "parent": "repo"}


def _read_status(tmp_path):
    return json.loads((tmp_path / "ingestion_status.json").read_text())


def _recording_hook(tmp_path, seen, error=None):
    def hook():
        seen.append(_read_status(tmp_path))
        if error is not None:
            raise error

    return hook


def _client(tmp_path, hook, collectors=None):
    status_file = tmp_path / "ingestion_status.json"
    status_file.write_text(
        json.dumps({key: {"state": "idle", "last_run": OLD_RUN} for key in SOURCE_KEYS})
    )
    collectors = collectors or _Collectors()
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.status_file = status_file
    wrapper.post_update_hook = hook
    wrapper.global_config = {"ACCEPTED_FILES": [".txt"]}
    wrapper.persistence = _Persistence()
    wrapper.catalog = _Catalog()
    wrapper.scraper_manager = collectors
    wrapper.ticket_manager = collectors
    wrapper.localfile_manager = collectors
    flask_app = Flask(__name__)
    for name in HANDLER_IDS:
        flask_app.add_url_rule(
            f"/document_index/{name}", name, getattr(wrapper, name), methods=["POST"]
        )
    return flask_app.test_client()


def _post(client, name, form):
    if name == "upload":
        return client.post(
            "/document_index/upload",
            data={"file": (io.BytesIO(b"hello"), "a.txt")},
            content_type="multipart/form-data",
        )
    return client.post(f"/document_index/{name}", data=form)


@pytest.mark.parametrize("name, form, key, ok_body", HANDLERS, ids=HANDLER_IDS)
def test_failed_update_sets_error_keeps_last_run_and_answers_500(
    tmp_path, name, form, key, ok_body
):
    seen = []
    hook = _recording_hook(tmp_path, seen, RuntimeError("sync down"))

    resp = _post(_client(tmp_path, hook), name, form)

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "update_failed", "detail": "sync down"}
    assert len(seen) == 1
    assert _read_status(tmp_path)[key] == {"state": "error", "last_run": OLD_RUN}


@pytest.mark.parametrize("name, form, key, ok_body", HANDLERS, ids=HANDLER_IDS)
def test_successful_update_writes_idle_only_after_the_update_ran(
    tmp_path, name, form, key, ok_body
):
    seen = []
    before = datetime.now(timezone.utc)

    resp = _post(_client(tmp_path, _recording_hook(tmp_path, seen)), name, form)

    assert resp.status_code == 200
    assert resp.get_json() == ok_body
    assert len(seen) == 1
    assert seen[0][key] == {"state": "idle", "last_run": OLD_RUN}
    entry = _read_status(tmp_path)[key]
    assert entry["state"] == "idle"
    assert datetime.fromisoformat(entry["last_run"]) >= before


def test_manual_ingest_without_a_hook_writes_idle_and_answers_ok(tmp_path):
    before = datetime.now(timezone.utc)

    resp = _post(
        _client(tmp_path, None),
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    entry = _read_status(tmp_path)["git"]
    assert entry["state"] == "idle"
    assert datetime.fromisoformat(entry["last_run"]) >= before


def test_failed_collection_skips_the_update_and_keeps_the_status(tmp_path):
    seen = []
    collectors = _Collectors(fail_with=RuntimeError("clone down"))

    resp = _post(
        _client(tmp_path, _recording_hook(tmp_path, seen), collectors),
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
    )

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "ingest_failed", "detail": "clone down"}
    assert seen == []
    assert _read_status(tmp_path)["git"] == {"state": "idle", "last_run": OLD_RUN}
