"""Regression guards for the hierarchical-rerank A/B example configs.

These tests pin the three reproducibility/footgun fixes from the Codex review of
PR #72 against the *shipped* example configs (not synthetic fixtures), so that a
future edit can't silently reintroduce them:

* F1 - the configs must validate from a clean checkout: ``agent_md_file`` has to
  resolve to a checked-in file, or ``ConfigurationManager`` raises before deploy.
* F3 - ``ragas_settings.embedding_model`` must be a local, key-free embedder; an
  omitted value renders the ``OpenAI`` default and fails with only HUIT creds.
* A/B contract - both arms must be byte-identical except the retrieval treatment
  (``data_manager.chunking`` / ``data_manager.retrievers``) and their ``name`` /
  ``global.DATA_PATH``. Anything else differing would bias the comparison.
"""

import copy
from pathlib import Path

import pytest
import yaml

from src.cli.managers.config_manager import ConfigurationManager

REPO_ROOT = Path(__file__).resolve().parents[2]
AB_DIR = REPO_ROOT / "examples" / "benchmarking" / "hierarchical_rerank_ab"
CONFIG_PATHS = [
    AB_DIR / "baseline_character_hybrid.yaml",
    AB_DIR / "treatment_hierarchical_rerank.yaml",
]

# Keys allowed to differ between the two arms (the retrieval treatment itself
# plus the per-arm identity/output location). Everything else is "held fixed".
_ARM_VARIED_PATHS = [
    ("name",),
    ("global", "DATA_PATH"),
    ("data_manager", "chunking"),
    ("data_manager", "retrievers"),
]


def _load(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    config["_config_path"] = str(path)
    return config


def _strip(config: dict, paths) -> dict:
    pruned = copy.deepcopy(config)
    pruned.pop("_config_path", None)
    for path in paths:
        node = pruned
        for key in path[:-1]:
            node = node.get(key, {})
        node.pop(path[-1], None)
    return pruned


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
def test_ab_config_agent_md_is_checked_in(config_path):
    agent_md = _load(config_path)["services"]["benchmarking"]["agent_md_file"]
    # Must point at the checked-in examples tree, not the gitignored config/
    # tree -- otherwise the config only validates on a host where the operator
    # has staged config/agents/*.md, and fails in a clean checkout / CI.
    assert agent_md.startswith(
        "examples/"
    ), f"agent_md_file must be a checked-in path, got {agent_md!r}"
    assert (REPO_ROOT / agent_md).is_file()


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
def test_ab_config_passes_benchmarking_validation(config_path, monkeypatch):
    # agent_md_file is resolved relative to CWD when the config-relative
    # candidate is absent, so pin CWD to the repo root (as the gate does).
    monkeypatch.chdir(REPO_ROOT)
    manager = ConfigurationManager.__new__(ConfigurationManager)
    # Must not raise: a missing agent_md_file would abort `archi evaluate`
    # before deployment.
    manager._validate_benchmarking_config(_load(config_path), ["benchmarking"])


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
def test_ab_config_ragas_embedding_is_local(config_path):
    ragas = _load(config_path)["services"]["benchmarking"]["mode_settings"][
        "ragas_settings"
    ]
    embedding_model = ragas.get("embedding_model")
    assert embedding_model, "ragas_settings must set embedding_model explicitly"
    # 'openai' (or an unrecognized value falling through to the OpenAI default)
    # needs OPENAI_API_KEY, absent in the benchmark env.
    assert embedding_model.lower() == "huggingface"


@pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda p: p.name)
def test_ab_config_agent_md_parses_as_agent_spec(config_path):
    # The checked-in persona must be a loadable agent spec, not just a .md file:
    # validation only checks the extension, but the harness parses frontmatter.
    from src.archi.pipelines.agents.agent_spec import load_agent_spec

    agent_md = _load(config_path)["services"]["benchmarking"]["agent_md_file"]
    spec = load_agent_spec(REPO_ROOT / agent_md)
    assert spec.name and spec.tools


def test_ab_arms_identical_except_retrieval_treatment():
    baseline = _strip(_load(CONFIG_PATHS[0]), _ARM_VARIED_PATHS)
    treatment = _strip(_load(CONFIG_PATHS[1]), _ARM_VARIED_PATHS)
    assert baseline == treatment, (
        "Baseline and treatment must match except chunking/retrievers/name/"
        "DATA_PATH; a stray difference would confound the A/B."
    )
