"""``GET /api/config/static`` reports the chunking the ingest path applies (#489).

The top-level ``chunk_size``/``chunk_overlap`` pair feeds only the character
splitter, so the endpoint also carries ``effective_chunking``, derived from the
stored ``data_manager_config`` through the shared helper.
"""

import pytest
from flask import Flask

from src.interfaces.chat_app.api import api
from src.utils.config_service import StaticConfig
from src.utils.ingest_provenance import effective_chunking


class _ConfigService:
    def __init__(self, config):
        self._config = config

    def get_static_config(self):
        return self._config


class _Services:
    def __init__(self, config):
        self.config_service = _ConfigService(config)


def _get_static_config(data_manager_config):
    app = Flask(__name__)
    app.register_blueprint(api)
    app.pg_services = _Services(
        StaticConfig(
            deployment_name="dev",
            config_version="1",
            data_path="/data",
            embedding_model="model-a",
            embedding_dimensions=384,
            chunk_size=1000,
            chunk_overlap=150,
            distance_metric="cosine",
            data_manager_config=data_manager_config,
        )
    )
    return app.test_client().get("/api/config/static")


@pytest.mark.parametrize(
    "data_manager_config",
    [
        {},
        {"chunking": {"strategy": "character"}, "chunk_size": 800},
        {"chunking": {"parent_chunk_size": 4096, "child_chunk_size": 256}},
    ],
)
def test_static_config_reports_effective_chunking(data_manager_config):
    response = _get_static_config(data_manager_config)

    assert response.status_code == 200
    body = response.get_json()
    assert body["effective_chunking"] == effective_chunking(data_manager_config)
    assert body["chunk_size"] == 1000
    assert body["chunk_overlap"] == 150


def test_static_config_reports_the_hierarchical_defaults_with_no_chunking_section():
    body = _get_static_config({}).get_json()

    assert body["effective_chunking"]["path"] == "hierarchical"
    assert body["effective_chunking"]["parent_chunk_size"] == 2048
    assert body["effective_chunking"]["child_chunk_size"] == 512
