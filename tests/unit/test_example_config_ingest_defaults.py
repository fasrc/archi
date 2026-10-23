"""The shipped FASRC example config must not enable per-document LLM categorization.

``deploy/fasrc-dev/config.yaml`` is git-excluded, so
``deploy/fasrc-dev/config.example.yaml`` is the only version of these settings a
fresh checkout or a new host ever sees: ``deploy/scripts/README.md`` tells an
operator to copy it. An ``enabled: true`` there hands every new deployment one
LLM call per document at ingest.

The 2026-09 feature-matrix campaign measured that call at about 19 minutes per
1091-document ingest, and resolved no retrieval effect (issue #496, campaign arm
03). No default retrieval path reads ``metadata.llm_category``. This module is the
regression guard for that decision: the example must ship the feature off, and must
keep the tuning keys so opting back in stays a one-line change.
"""

import pathlib

import pytest

EXAMPLE = pathlib.Path("deploy/fasrc-dev/config.example.yaml")


def _categorization():
    yaml = pytest.importorskip("yaml")
    payload = yaml.safe_load(EXAMPLE.read_text())
    return payload["data_manager"]["processing"]["categorization"]


def test_the_example_ships_categorization_off():
    """A copied example must not cost 19 minutes per ingest by default."""
    assert _categorization()["enabled"] is False


def test_the_example_keeps_the_tuning_keys():
    """Opting back in stays a one-line change to `enabled`.

    Deleting the block would also disable the feature, but then re-enabling it
    means reconstructing the provider, model and label set from documentation.
    """
    block = _categorization()

    for key in ("provider", "model", "max_chars", "categories"):
        assert key in block, f"the example dropped `{key}`"

    assert block["categories"], "the example dropped the label set"
