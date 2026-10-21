"""Regression: the deployed base-config template must carry the hierarchical
chunk-size keys through to the rendered runtime config.

`data_manager.chunking.parent_chunk_size`/`child_chunk_size` are read by
``VectorStoreManager`` at ingestion. The CLI renders ``base-config.yaml`` with
Jinja and only emitted keys survive into ``/root/archi/configs/*.yaml``; if the
template drops these keys the manager silently falls back to its 2048/512
defaults, so a configured chunk-size sweep would secretly test nothing. These
tests pin that the keys render when set and stay absent (defaults preserved)
when unset.
"""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


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


def test_chunk_sizes_rendered_when_set():
    cfg = _render(
        {
            "chunking": {
                "strategy": "sentence",
                "parent_chunk_size": 1024,
                "child_chunk_size": 256,
            }
        }
    )
    chunking = cfg["data_manager"]["chunking"]
    assert chunking["strategy"] == "sentence"
    assert chunking["parent_chunk_size"] == 1024
    assert chunking["child_chunk_size"] == 256


def test_chunk_sizes_absent_when_unset_preserves_defaults():
    cfg = _render({"chunking": {"strategy": "sentence"}})
    chunking = cfg["data_manager"]["chunking"]
    assert chunking["strategy"] == "sentence"
    # Keys omitted → VectorStoreManager applies its built-in 2048/512 defaults.
    assert "parent_chunk_size" not in chunking
    assert "child_chunk_size" not in chunking


def test_hierarchical_rerank_enabled_by_default():
    # ADR 0003: hierarchical-rerank is the recommended default. When a deployment
    # config omits the key, the rendered runtime config must enable it.
    cfg = _render({})
    rerank = cfg["data_manager"]["retrievers"]["hierarchical_rerank"]
    assert rerank["enabled"] is True


def test_hierarchical_rerank_explicit_opt_out_renders_false():
    # Operators must still be able to turn it off. An explicit enabled: false must
    # render false (not be swallowed by a falsy-default), so retrieval falls back
    # to HybridRetriever.
    cfg = _render({"retrievers": {"hierarchical_rerank": {"enabled": False}}})
    rerank = cfg["data_manager"]["retrievers"]["hierarchical_rerank"]
    assert rerank["enabled"] is False


def test_default_chunking_strategy_is_hierarchical():
    # The reranker is default-on (ADR 0003), but it only delivers the benchmarked
    # +19% when ingestion builds parent/child nodes — i.e. a 'sentence'/'markdown'
    # chunking strategy. 'character' produces flat chunks with no parent_id, so the
    # reranker pays its cost with no parent-context benefit. The default chunking
    # strategy must therefore be hierarchical to match the default-on reranker.
    cfg = _render({})
    assert cfg["data_manager"]["chunking"]["strategy"] == "sentence"


def test_default_retrieval_config_is_coherent():
    # Guard the pairing as a unit: out of the box, hierarchical chunking AND the
    # reranker are both on, so the default deployment matches the ADR 0003 package.
    cfg = _render({})
    dm = cfg["data_manager"]
    assert dm["chunking"]["strategy"] == "sentence"
    assert dm["retrievers"]["hierarchical_rerank"]["enabled"] is True


def test_chunk_overlap_zero_renders_as_zero():
    # 0 is a valid configured value (disables overlap); it must not be treated as
    # falsy and swapped for a default.
    cfg = _render({"chunking": {"chunk_overlap": 0}})
    assert cfg["data_manager"]["chunking"]["chunk_overlap"] == 0


def test_chunk_overlap_nonzero_renders():
    cfg = _render({"chunking": {"chunk_overlap": 64}})
    assert cfg["data_manager"]["chunking"]["chunk_overlap"] == 64


def test_chunk_overlap_absent_when_unset():
    # Unset → manager uses CHILD_CHUNK_OVERLAP; key must not appear in config.
    cfg = _render({"chunking": {"strategy": "sentence"}})
    assert "chunk_overlap" not in cfg["data_manager"]["chunking"]


@pytest.mark.parametrize("value", ["20", "null", "true"])
def test_chunk_overlap_string_keeps_its_type(value):
    # A bare interpolation lets YAML retype "20" to 20 and "null" to None, so an
    # invalid string would pass _resolve_chunk_overlap; it must reach the
    # manager as a string and fail there.
    cfg = _render({"chunking": {"chunk_overlap": value}})
    assert cfg["data_manager"]["chunking"]["chunk_overlap"] == value


def test_chunk_overlap_absent_when_none():
    # None (key present but empty in YAML) → same as absent; manager uses default.
    cfg = _render({"chunking": {"chunk_overlap": None}})
    assert "chunk_overlap" not in cfg["data_manager"]["chunking"]
