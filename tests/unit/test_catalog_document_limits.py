"""Tests for the server-side catalog document character clamp (issue #260)."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from src.interfaces.uploader_app import app as uploader_app_module
from src.interfaces.uploader_app.document_limits import (
    DEFAULT_CATALOG_DOCUMENT_CHARS,
    MAX_CATALOG_DOCUMENT_CHARS,
    clamp_document_chars,
)

DOC = "".join(chr(ord("a") + i % 26) for i in range(10000))


def test_ceiling_wins_over_a_large_requested_value():
    result = DOC[: clamp_document_chars("999999")]
    assert len(result) <= 6000
    assert len(result) == MAX_CATALOG_DOCUMENT_CHARS
    result = DOC[: clamp_document_chars(999999)]
    assert len(result) <= 6000
    assert len(result) == MAX_CATALOG_DOCUMENT_CHARS


def test_zero_means_the_ceiling_not_the_whole_document():
    result = DOC[: clamp_document_chars("0")]
    assert len(result) <= 6000
    result = DOC[: clamp_document_chars(0)]
    assert len(result) <= 6000


def test_negative_means_the_ceiling_and_never_slices_from_the_tail():
    result = DOC[: clamp_document_chars("-5")]
    assert len(result) <= 6000
    assert result == DOC[: len(result)]
    result = DOC[: clamp_document_chars(-5)]
    assert len(result) <= 6000
    assert result == DOC[: len(result)]


def test_a_valid_smaller_value_is_honoured_exactly():
    result = DOC[: clamp_document_chars("100")]
    assert result == DOC[:100]
    result = DOC[: clamp_document_chars(100)]
    assert result == DOC[:100]


def test_absent_value_falls_back_to_the_4000_default():
    result = DOC[: clamp_document_chars(None)]
    assert result == DOC[:4000]
    assert DEFAULT_CATALOG_DOCUMENT_CHARS == 4000


def test_malformed_values_fall_back_to_the_ceiling():
    assert len(DOC[: clamp_document_chars("abc")]) <= 6000
    assert len(DOC[: clamp_document_chars("")]) <= 6000
    assert len(DOC[: clamp_document_chars(True)]) <= 6000


def test_max_catalog_document_chars_is_6000():
    assert MAX_CATALOG_DOCUMENT_CHARS == 6000


def _api_catalog_document_body():
    app_py = Path("src/interfaces/uploader_app/app.py").read_text()
    start = app_py.index("def api_catalog_document")
    end = app_py.index("\n    def ", start)
    return app_py[start:end]


def test_endpoint_uses_the_clamp_helper_and_drops_the_manual_guard():
    app_py = Path("src/interfaces/uploader_app/app.py").read_text()
    assert (
        "from src.interfaces.uploader_app.document_limits import clamp_document_chars"
        in app_py
    )
    body = _api_catalog_document_body()
    assert 'clamp_document_chars(request.args.get("max_chars"))' in body
    assert "if max_chars and" not in body
    assert "type=int" not in body


class _StubCatalog:
    def __init__(self):
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def get_filepath_for_hash(self, resource_hash):
        return f"/tmp/{resource_hash}.txt"

    def get_metadata_for_hash(self, resource_hash):
        return {"hash": resource_hash}


def _client_for_catalog_document():
    wrapper = object.__new__(uploader_app_module.FlaskAppWrapper)
    wrapper.catalog = _StubCatalog()
    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/catalog/document/<resource_hash>",
        "api_catalog_document",
        wrapper.api_catalog_document,
        methods=["GET"],
    )
    return flask_app.test_client(), wrapper


def test_endpoint_clamps_the_real_response_text():
    client, wrapper = _client_for_catalog_document()
    with patch.object(uploader_app_module, "load_text_from_path", return_value=DOC):
        absent = client.get("/api/catalog/document/abc123")
        ceiling = client.get("/api/catalog/document/abc123?max_chars=0")
        honoured = client.get("/api/catalog/document/abc123?max_chars=100")

    assert wrapper.catalog.refreshed
    assert len(absent.get_json()["text"]) == DEFAULT_CATALOG_DOCUMENT_CHARS
    assert len(ceiling.get_json()["text"]) == MAX_CATALOG_DOCUMENT_CHARS
    assert honoured.get_json()["text"] == DOC[:100]


def test_the_helper_never_loads_the_agent_pipeline_package():
    probe = (
        "import sys\n"
        "from src.interfaces.uploader_app.document_limits import clamp_document_chars\n"
        "assert clamp_document_chars('100') == 100\n"
        "print('src.archi.pipelines' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.stdout.strip().splitlines()[-1] == "False"
