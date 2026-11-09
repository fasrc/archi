"""Each manual ingest handler runs inside the data manager's upload tracker (issue #648).

``FlaskAppWrapper`` enters ``track_upload`` before a handler's collection (for a git
removal, before its deletes) and leaves it after ``_finish_manual_ingest`` returned,
so the ingestion status reports the whole handler as one running upload. A wrapper
without a tracker runs the handlers as before.
"""

import io
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module

SOURCE_KEYS = ("git", "jira", "local_files", "web")

# (handler name, form data, 200 answer body, error code of a failed collection)
HANDLERS = [
    (
        "add_git_repo",
        {"repo_url": "https://github.com/org/repo"},
        {"status": "ok"},
        "ingest_failed",
    ),
    (
        "remove_git_repo",
        {"repo": "repo"},
        {"status": "ok", "repo": "repo", "deleted": 1},
        "delete_failed",
    ),
    ("add_jira_project", {"project_key": "PROJ"}, {"status": "ok"}, "ingest_failed"),
    ("upload", None, {"status": "ok", "path": "/data/a.txt"}, "upload_failed"),
    (
        "upload_url",
        {"url": "https://example.org/page"},
        {"status": "ok", "resources_scraped": 3},
        "upload_failed",
    ),
]
HANDLER_IDS = [handler[0] for handler in HANDLERS]


class _Stubs:
    """Stand-in for the collectors, the persistence and the catalog.

    Every store change appends "collect" to ``events``; with ``fail_with`` set, it
    raises instead.
    """

    metadata_index = {"h1": None}

    def __init__(self, events, fail_with=None):
        self.events = events
        self.fail_with = fail_with
        self.jira_client = object()

    def _collect(self):
        self.events.append("collect")
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

    def delete_resource(self, resource_hash, flush=True):
        self._collect()

    def flush_index(self):
        pass

    def refresh(self):
        pass

    def get_metadata_for_hash(self, resource_hash):
        return {"source_type": "git", "parent": "repo"}


def _recording_tracker(events):
    @contextmanager
    def tracker():
        events.append("enter")
        try:
            yield
        except Exception as exc:
            events.append(f"exit:{exc}")
            raise
        events.append("exit")

    return tracker


def _client(tmp_path, events, *, tracker, hook_error=None, fail_with=None, stubs=None):
    status_file = tmp_path / "ingestion_status.json"
    status_file.write_text(
        json.dumps({key: {"state": "idle", "last_run": "t0"} for key in SOURCE_KEYS})
    )
    stubs = stubs or _Stubs(events, fail_with)

    def hook():
        events.append("hook")
        if hook_error is not None:
            raise hook_error

    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.status_file = status_file
    wrapper.post_update_hook = hook
    wrapper.global_config = {"ACCEPTED_FILES": [".txt"]}
    wrapper.persistence = stubs
    wrapper.catalog = stubs
    wrapper.scraper_manager = stubs
    wrapper.ticket_manager = stubs
    wrapper.localfile_manager = stubs
    if tracker is not None:
        wrapper.track_upload = tracker
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


@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
def test_handler_collects_and_updates_inside_the_tracker(
    tmp_path, name, form, ok_body, error_code
):
    events = []
    client = _client(tmp_path, events, tracker=_recording_tracker(events))

    resp = _post(client, name, form)

    assert resp.status_code == 200
    assert resp.get_json() == ok_body
    assert events == ["enter", "collect", "hook", "exit"]


@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
def test_a_failed_collection_leaves_the_tracker_with_the_error(
    tmp_path, name, form, ok_body, error_code
):
    events = []
    client = _client(
        tmp_path,
        events,
        tracker=_recording_tracker(events),
        fail_with=RuntimeError("crawl down"),
    )

    resp = _post(client, name, form)

    assert resp.status_code == 500
    assert resp.get_json() == {"error": error_code, "detail": "crawl down"}
    assert events == ["enter", "collect", "exit:crawl down"]


@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
def test_a_failed_update_leaves_the_tracker_normally(
    tmp_path, name, form, ok_body, error_code
):
    events = []
    client = _client(
        tmp_path,
        events,
        tracker=_recording_tracker(events),
        hook_error=RuntimeError("sync down"),
    )

    resp = _post(client, name, form)

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "update_failed", "detail": "sync down"}
    assert events == ["enter", "collect", "hook", "exit"]


@pytest.mark.parametrize("name, form, ok_body, error_code", HANDLERS, ids=HANDLER_IDS)
def test_handler_without_a_tracker_runs_as_before(
    tmp_path, name, form, ok_body, error_code
):
    events = []
    client = _client(tmp_path, events, tracker=None)

    resp = _post(client, name, form)

    assert resp.status_code == 200
    assert resp.get_json() == ok_body
    assert events == ["collect", "hook"]


def test_remove_git_repo_not_found_does_not_enter_the_tracker(tmp_path):
    events = []
    stubs = _Stubs(events)
    stubs.metadata_index = {}
    client = _client(tmp_path, events, tracker=_recording_tracker(events), stubs=stubs)

    resp = client.post("/document_index/remove_git_repo", data={"repo": "repo"})

    assert resp.status_code == 404
    assert events == []


def test_the_constructed_wrapper_keeps_the_tracker(tmp_path):
    config = {
        "global": {"DATA_PATH": str(tmp_path)},
        "services": {"postgres": {}, "data_manager": {"auth": {"enabled": False}}},
        "data_manager": {},
    }
    tracker = object()
    m = uploader_app_module
    with (
        patch.object(m, "get_full_config", return_value=config),
        patch.object(m, "read_secret", return_value=""),
        patch.object(m, "build_persistence"),
        patch.object(m, "PostgresCatalogService"),
        patch.object(m, "ScraperManager"),
        patch.object(m, "TicketManager"),
        patch.object(m, "LocalFileManager"),
    ):
        wrapper = m.FlaskAppWrapper(
            Flask(__name__), post_update_hook=None, track_upload=tracker
        )

    assert wrapper.track_upload is tracker
    assert uploader_app_module.FlaskAppWrapper.track_upload is None
