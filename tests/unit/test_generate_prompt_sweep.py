"""Unit tests for the prompt-sweep config generator.

Covers scripts/benchmarking/generate_prompt_sweep.py: one config per prompt,
each differing from the base ONLY in services.benchmarking.agent_md_file and
.name (+ primary_metric), name = prompt stem, and atomic failure (a missing
prompt aborts before any config is written).
"""

import copy

import pytest
import yaml

from scripts.benchmarking.generate_prompt_sweep import generate_sweep_configs

BASE_CONFIG = {
    "name": "ragas-bench",
    "global": {"DATA_PATH": "/root/data/"},
    "services": {
        "benchmarking": {
            "agent_class": "CMSCompOpsAgent",
            "agent_md_file": "config/agents/fasrc-cannon-v1-strict.md",
            "provider": "openai",
            "model": "Qwen/Qwen3.5-35B-A3B-GPTQ-Int4",
            "queries_path": "config/benchmarking/queries.json",
            "modes": ["RAGAS"],
        },
    },
}


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _setup(tmp_path, prompt_names, *, primary_metric=None, base=None):
    """Create a base config, prompt files, and a manifest under tmp_path."""
    base_path = tmp_path / "base.yaml"
    _write_yaml(base_path, base if base is not None else BASE_CONFIG)

    prompt_paths = []
    for name in prompt_names:
        p = tmp_path / name
        p.write_text(f"# prompt {name}\n")
        prompt_paths.append(p)

    manifest = {
        "base_config": str(base_path),
        "out_dir": str(tmp_path / "sweep_configs"),
        "prompts": [str(p) for p in prompt_paths],
    }
    if primary_metric is not None:
        manifest["primary_metric"] = primary_metric
    manifest_path = tmp_path / "manifest.yaml"
    _write_yaml(manifest_path, manifest)
    return manifest_path


def test_one_config_per_prompt_only_prompt_fields_differ(tmp_path):
    manifest = _setup(tmp_path, ["v1-strict.md", "v2-lean.md", "v3-cited.md"])
    written = generate_sweep_configs(manifest)
    assert len(written) == 3

    for path in written:
        with open(path) as f:
            cfg = yaml.safe_load(f)
        bench = cfg["services"]["benchmarking"]
        # agent_md_file points at one of the prompts
        assert bench["agent_md_file"].endswith(".md")
        # Everything else equals the base, ignoring the three swept fields.
        stripped = copy.deepcopy(cfg)
        sb = stripped["services"]["benchmarking"]
        for k in ("agent_md_file", "name", "primary_metric"):
            sb.pop(k, None)
        expected = copy.deepcopy(BASE_CONFIG)
        expected["services"]["benchmarking"].pop("agent_md_file", None)
        assert stripped == expected


def test_name_is_prompt_stem(tmp_path):
    manifest = _setup(tmp_path, ["fasrc-cannon-v2-lean.md"])
    written = generate_sweep_configs(manifest)
    with open(written[0]) as f:
        cfg = yaml.safe_load(f)
    assert cfg["services"]["benchmarking"]["name"] == "fasrc-cannon-v2-lean"
    # output file is named for the stem too
    assert written[0].name == "fasrc-cannon-v2-lean.yaml"


def test_primary_metric_threaded_into_each_config(tmp_path):
    manifest = _setup(tmp_path, ["a.md", "b.md"], primary_metric="answer_relevancy")
    written = generate_sweep_configs(manifest)
    for path in written:
        with open(path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["services"]["benchmarking"]["primary_metric"] == "answer_relevancy"


def test_missing_prompt_aborts_atomically(tmp_path):
    # One real prompt, one bogus path that does not exist.
    base_path = tmp_path / "base.yaml"
    _write_yaml(base_path, BASE_CONFIG)
    real = tmp_path / "real.md"
    real.write_text("# real\n")
    out_dir = tmp_path / "sweep_configs"
    manifest = {
        "base_config": str(base_path),
        "out_dir": str(out_dir),
        "prompts": [str(real), str(tmp_path / "does-not-exist.md")],
    }
    manifest_path = tmp_path / "manifest.yaml"
    _write_yaml(manifest_path, manifest)

    with pytest.raises(ValueError, match="does-not-exist.md"):
        generate_sweep_configs(manifest_path)

    # Atomic: no configs written.
    assert not out_dir.exists() or list(out_dir.glob("*.yaml")) == []


def test_invalid_primary_metric_rejected(tmp_path):
    manifest = _setup(tmp_path, ["a.md"], primary_metric="bogus_metric")
    with pytest.raises(ValueError, match="bogus_metric"):
        generate_sweep_configs(manifest)
