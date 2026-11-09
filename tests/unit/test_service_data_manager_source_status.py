"""The data-manager main() writes the per-source status through one module (#700).

``main()`` has three status writers: the start-up seed, ``set_source_status``
(called by a scheduled refresh through ``run_source_refresh``) and the
``/api/reload-schedules`` route. This drives ``main()`` with fakes for the
database, the collectors, the scheduler and the uploader, and checks what each
writer leaves in ``ingestion_status.json``.
"""

import json
import time

import pytest

import src.bin.service_data_manager as service


class _FakeFactory:
    @classmethod
    def from_env(cls, password_override=None):
        return cls()

    @classmethod
    def set_instance(cls, factory):
        pass


class _FakeScraperManager:
    def __init__(self, calls):
        self.calls = calls

    def schedule_collect_git(self, persistence, last_run=None):
        self.calls.append(("collect_git", last_run))


class _FakeDataManager:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.persistence = object()
        self.scraper_manager = _FakeScraperManager(self.calls)
        self.update_result = None

    def run_ingestion(self, progress_callback=None, embedding_progress=None):
        return None

    def update_vectorstore(self, force=False):
        return self.update_result


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.reload_result = {}

    def set_config_loader(self, loader, job_factory):
        pass

    def add_job(self, name, cron, callback):
        self.jobs[name] = callback

    def start(self):
        pass

    def reload_schedules(self):
        return self.reload_result

    def get_job_status(self):
        return [{"name": name} for name in self.jobs]


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Run main() to its end; return the fakes, the Flask app and the file."""
    made = {}
    status_file = tmp_path / "ingestion_status.json"
    status_file.write_text(
        json.dumps({"git": {"state": "idle", "last_run": "t0"}, "web": {"state": "x"}})
    )

    def data_manager(*args, **kwargs):
        made["data_manager"] = _FakeDataManager()
        return made["data_manager"]

    def scheduler():
        made["scheduler"] = _FakeScheduler()
        return made["scheduler"]

    class _FakeUploader:
        def __init__(
            self, app, *, post_update_hook=None, status_file=None, track_upload=None
        ):
            made["app"] = app
            made["status_file"] = status_file
            made["track_upload"] = track_upload

        def run(self, **kwargs):
            pass

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.setenv(key, "unchanged")
    sources = {"git": {"schedule": "0 3 * * *"}, "jira": {"schedule": "0 4 * * *"}}
    monkeypatch.setattr(service, "setup_logging", lambda: None)
    monkeypatch.setattr(service, "read_secret", lambda name: "secret")
    monkeypatch.setattr(service, "PostgresServiceFactory", _FakeFactory)
    monkeypatch.setattr(service, "build_parent_id_index_at_startup", lambda f: True)
    monkeypatch.setattr(
        service, "get_full_config", lambda: {"data_manager": {"sources": sources}}
    )
    monkeypatch.setattr(
        service,
        "get_services_config",
        lambda: {"data_manager": {"flask_debug_mode": False, "port": 0, "host": "x"}},
    )
    monkeypatch.setattr(
        service, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    monkeypatch.setattr(service, "DataManager", data_manager)
    monkeypatch.setattr(service, "CronScheduler", scheduler)
    monkeypatch.setattr(service, "instrument_flask_app", lambda app: None)
    monkeypatch.setattr(service, "FlaskAppWrapper", _FakeUploader)

    service.main()

    assert made["status_file"] == status_file
    return made, status_file


def _read(status_file):
    return json.loads(status_file.read_text())


def test_start_seeds_scheduled_sources_and_keeps_existing_entries(started):
    _, status_file = started

    assert _read(status_file) == {
        "git": {"state": "idle", "last_run": "t0", "schedule": "0 3 * * *"},
        "web": {"state": "x"},
        "jira": {"schedule": "0 4 * * *", "state": "idle"},
    }


def test_scheduled_refresh_writes_error_then_idle_through_the_file(started):
    made, status_file = started
    data_manager = made["data_manager"]
    refresh_git = made["scheduler"].jobs["git"]

    data_manager.update_result = "failed"
    with pytest.raises(RuntimeError):
        refresh_git()

    assert data_manager.calls == [("collect_git", "t0")]
    assert _read(status_file)["git"] == {
        "state": "error",
        "last_run": "t0",
        "schedule": "0 3 * * *",
    }

    data_manager.update_result = None
    refresh_git()

    git = _read(status_file)["git"]
    assert git["state"] == "idle"
    assert git["last_run"] not in (None, "t0")
    assert git["schedule"] == "0 3 * * *"


def test_reload_sets_schedules_only_on_existing_entries(started):
    made, status_file = started
    made["scheduler"].reload_result = {"git": "0 5 * * *", "links": "0 6 * * *"}

    response = made["app"].test_client().post("/api/reload-schedules")

    assert response.status_code == 200
    status = _read(status_file)
    assert status["git"]["schedule"] == "0 5 * * *"
    assert "links" not in status


def test_schedules_route_reads_the_state_from_the_file(started):
    made, _ = started

    response = made["app"].test_client().get("/api/schedules")

    jobs = {job["name"]: job for job in response.get_json()["jobs"]}
    assert jobs["git"]["state"] == "idle"
    assert jobs["git"]["last_run"] == "t0"
    assert jobs["jira"]["state"] == "idle"
    assert jobs["jira"]["last_run"] is None


def _wait_for_the_initial_ingest(client):
    deadline = time.monotonic() + 5
    while client.get("/api/ingestion/status").get_json()["state"] != "completed":
        assert time.monotonic() < deadline, "the initial ingest did not finish"
        time.sleep(0.01)


def test_the_uploader_gets_the_data_managers_upload_tracker(started):
    """The tracker main() hands to the uploader publishes on the status endpoint (#648)."""
    made, _ = started
    client = made["app"].test_client()
    _wait_for_the_initial_ingest(client)

    with made["track_upload"]():
        during = client.get("/api/ingestion/status").get_json()
    after = client.get("/api/ingestion/status").get_json()

    assert during["state"] == "running"
    assert during["step"] == "upload"
    assert after["state"] == "completed"
