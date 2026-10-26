"""The chat app's embed proxy to the data manager (issue #652)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from flask import Flask

import src.interfaces.chat_app.app as chat_app_module
from src.interfaces.chat_app import embed_proxy

BASE = "http://data-manager:7871"
HEADERS = {"Authorization": "Bearer dm-token"}


def _response(status_code, body=None, *, is_redirect=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_redirect = is_redirect
    if body is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = body
    return resp


def _call(resp=None, *, raises=None):
    with patch.object(embed_proxy.requests, "post") as post:
        if raises is not None:
            post.side_effect = raises
        else:
            post.return_value = resp
        result = embed_proxy.request_embed(BASE, HEADERS)
    return result, post


def test_success_returns_none_and_posts_to_the_embed_endpoint():
    result, post = _call(_response(200, {"status": "ok"}))

    assert result is None
    post.assert_called_once_with(
        f"{BASE}/document_index/embed",
        headers=HEADERS,
        timeout=(10, 3600),
        allow_redirects=False,
    )


def test_auth_redirect_maps_to_502():
    result, _ = _call(_response(302, None, is_redirect=True))

    assert result == ({"error": "Data manager authentication failed"}, 502)


def test_unreachable_data_manager_maps_to_503():
    result, _ = _call(raises=requests.exceptions.ConnectionError("refused"))

    assert result == ({"error": "data_manager_unavailable"}, 503)


def test_connect_timeout_maps_to_503():
    result, _ = _call(raises=requests.exceptions.ConnectTimeout("slow connect"))

    assert result == ({"error": "data_manager_unavailable"}, 503)


def test_read_timeout_maps_to_504():
    result, _ = _call(raises=requests.exceptions.ReadTimeout("slow answer"))

    body, status = result
    assert status == 504
    assert "still be running" in body["error"]


def test_data_manager_error_forwards_its_message_as_500():
    resp = _response(500, {"error": "embed_failed", "detail": "boom"})

    result, _ = _call(resp)

    assert result == ({"error": "boom"}, 500)


def test_data_manager_error_without_detail_forwards_its_error_code():
    result, _ = _call(_response(503, {"error": "embed_unavailable"}))

    assert result == ({"error": "embed_unavailable"}, 500)


def test_non_json_answer_maps_to_502():
    result, _ = _call(_response(404, None))

    assert result == ({"error": "Data manager error (HTTP 404)"}, 502)


def test_2xx_without_status_ok_is_not_a_success():
    result, _ = _call(_response(200, {"status": "queued"}))

    assert result == ({"error": "Data manager error (HTTP 200)"}, 502)


def test_chat_app_has_no_direct_vectorstore_write():
    source = Path("src/interfaces/chat_app/app.py").read_text()

    assert "update_vectorstore" not in source
    assert "from src.interfaces.chat_app.embed_proxy import request_embed" in source


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def cursor(self):
        return _Cursor(self.rows)

    def close(self):
        self.closed = True


def _trigger_embedding(proxy_result, rows=()):
    wrapper = object.__new__(chat_app_module.FlaskAppWrapper)
    wrapper.data_manager_url = BASE
    wrapper._dm_headers = HEADERS
    wrapper.chat = SimpleNamespace(pg_config={"host": "pg"})
    conn = _Connection(list(rows))
    with (
        patch.object(
            chat_app_module, "request_embed", return_value=proxy_result
        ) as proxy,
        patch.object(chat_app_module.psycopg2, "connect", return_value=conn) as connect,
        Flask(__name__).test_request_context(),
    ):
        resp, status = wrapper.trigger_embedding()
        body = resp.get_json()
    return body, status, proxy, connect


def test_trigger_embedding_answers_with_the_proxy_failure():
    body, status, proxy, connect = _trigger_embedding(({"error": "boom"}, 500))

    assert (body, status) == ({"error": "boom"}, 500)
    proxy.assert_called_once_with(BASE, HEADERS)
    connect.assert_not_called()


def test_trigger_embedding_keeps_the_success_answer():
    body, status, proxy, _ = _trigger_embedding(None)

    assert status == 200
    assert body == {
        "success": True,
        "message": "Embedding complete. Documents are now searchable.",
    }
    proxy.assert_called_once_with(BASE, HEADERS)


def test_trigger_embedding_keeps_the_partial_answer():
    rows = [("a.pdf", "bad pdf"), ("b.txt", None)]

    body, status, _, connect = _trigger_embedding(None, rows)

    assert status == 200
    assert body["success"] is True
    assert body["partial"] is True
    assert body["failed"] == [
        {"file": "a.pdf", "error": "bad pdf"},
        {"file": "b.txt", "error": "Unknown error"},
    ]
    connect.assert_called_once_with(host="pg")
