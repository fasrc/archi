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
