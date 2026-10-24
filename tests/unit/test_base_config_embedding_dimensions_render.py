"""Regression: an explicit per-embedder ``dimensions`` must survive the
base-config render into the runtime config.

``init.sql`` sizes the vector column from the source config, but the ingest
snapshot, ``VectorStoreManager`` and the config seed read the rendered runtime
config. If the template drops ``dimensions``, the runtime config describes the
model default and the ``embedding_dimensions_explicit`` marker (#654) can never
be true on the standard ``archi create`` path.
"""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape

from src.utils.ingest_provenance import build_ingest_config_snapshot


def _render(data_manager):
    # Mirror the env in src/cli/cli_main.py (PackageLoader + ChainableUndefined).
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, data_manager=data_manager)
    return yaml.safe_load(rendered)


@pytest.mark.parametrize("embedder", ["OpenAIEmbeddings", "HuggingFaceEmbeddings"])
def test_explicit_dimensions_render_as_an_integer(embedder):
    cfg = _render({"embedding_class_map": {embedder: {"dimensions": 384}}})
    assert cfg["data_manager"]["embedding_class_map"][embedder]["dimensions"] == 384


@pytest.mark.parametrize("embedder", ["OpenAIEmbeddings", "HuggingFaceEmbeddings"])
def test_dimensions_absent_when_unset(embedder):
    cfg = _render({})
    assert "dimensions" not in cfg["data_manager"]["embedding_class_map"][embedder]


def test_rendered_explicit_dimensions_reach_the_ingest_snapshot():
    cfg = _render(
        {
            "embedding_name": "OpenAIEmbeddings",
            "embedding_class_map": {"OpenAIEmbeddings": {"dimensions": 384}},
        }
    )
    snapshot = build_ingest_config_snapshot(cfg["data_manager"])
    assert snapshot["embedding_dimensions"] == 384
    assert snapshot["embedding_dimensions_explicit"] is True


def test_rendered_default_dimensions_are_not_marked_explicit():
    cfg = _render({"embedding_name": "OpenAIEmbeddings"})
    snapshot = build_ingest_config_snapshot(cfg["data_manager"])
    assert snapshot["embedding_dimensions_explicit"] is False
