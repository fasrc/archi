"""POST /document_index/embed runs the tracked upload update (issue #652)."""

import threading
from unittest.mock import patch

from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module
from src.utils.ingestion_status import build_ingestion_helpers


def _tracked_hook(update_vectorstore):
    ing = build_ingestion_helpers(lambda: None, threading.RLock())
    return ing, (lambda: ing["run_upload_update"](update_vectorstore))


def _embed_client(hook):
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.post_update_hook = hook
    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/document_index/embed", "embed", wrapper.embed, methods=["POST"]
    )
    return flask_app.test_client()


def test_embed_endpoint_reports_upload_while_the_sync_runs():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def update_vectorstore(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return "updated"

    ing, hook = _tracked_hook(update_vectorstore)
    client = _embed_client(hook)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(resp=client.post("/document_index/embed"))
    )
    worker.start()
    assert entered.wait(5)
    during = ing["get_ingestion_status"]()
    release.set()
    worker.join(5)

    assert during["state"] == "running"
    assert during["step"] == "upload"
    assert result["resp"].status_code == 200
    assert result["resp"].get_json() == {"status": "ok"}
    assert calls == [{"force": True}]
    assert ing["get_ingestion_status"]()["state"] == "completed"


def test_embed_endpoint_answers_non_2xx_when_the_sync_failed():
    ing, hook = _tracked_hook(lambda **kwargs: "failed")

    resp = _embed_client(hook).post("/document_index/embed")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error"] == "embed_failed"
    assert "documents not added" in body["detail"]
    status = ing["get_ingestion_status"]()
    assert status["state"] == "error"
    assert status["step"] == "failed"


def test_embed_endpoint_answers_non_2xx_when_the_sync_raises():
    def update_vectorstore(**kwargs):
        raise RuntimeError("boom")

    ing, hook = _tracked_hook(update_vectorstore)

    resp = _embed_client(hook).post("/document_index/embed")

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "embed_failed", "detail": "boom"}
    assert ing["get_ingestion_status"]()["error"] == "boom"


def test_embed_endpoint_without_a_hook_is_not_a_success():
    resp = _embed_client(None).post("/document_index/embed")

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "embed_unavailable"


def _constructed_client(tmp_path, hook):
    config = {
        "global": {"DATA_PATH": str(tmp_path)},
        "services": {"postgres": {}, "data_manager": {"auth": {"enabled": True}}},
        "data_manager": {},
    }
    secret_values = {"DM_API_TOKEN": "dm-token"}
    m = uploader_app_module
    with (
        patch.object(m, "get_full_config", return_value=config),
        patch.object(m, "read_secret", side_effect=lambda k: secret_values.get(k, "")),
        patch.object(m, "build_persistence"),
        patch.object(m, "PostgresCatalogService"),
        patch.object(m, "ScraperManager"),
        patch.object(m, "TicketManager"),
        patch.object(m, "LocalFileManager"),
    ):
        flask_app = Flask(__name__)
        m.FlaskAppWrapper(flask_app, post_update_hook=hook)
    return flask_app.test_client()


def test_embed_route_is_registered_post_only_and_admin_protected(tmp_path):
    calls = []
    client = _constructed_client(tmp_path, lambda: calls.append("hook"))

    anonymous = client.post("/document_index/embed")
    wrong_method = client.get(
        "/document_index/embed", headers={"Authorization": "Bearer dm-token"}
    )
    service = client.post(
        "/document_index/embed", headers={"Authorization": "Bearer dm-token"}
    )

    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith("/login")
    assert wrong_method.status_code == 405
    assert service.status_code == 200
    assert calls == ["hook"]
