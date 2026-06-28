"""Unit test: benchmarking sweeps must stage every config's agent .md file.

In a multi-config `archi evaluate --config-dir` run, each config names its own
`services.benchmarking.agent_md_file` (the prompt variant under test). The
deployment must copy ALL of them into data/agents/ so the benchmarker can load
each variant; previously it staged only the first config's agent, so later
variants crashed with FileNotFoundError inside the container.
"""

from pathlib import Path
from types import SimpleNamespace

from src.cli.managers.templates_manager import TemplateManager


def _config(tmp_path: Path, agent_rel: str):
    cfg = {
        "name": "ragas-bench",
        "services": {"benchmarking": {"agent_md_file": agent_rel}},
        "_config_path": str(tmp_path / "sweep" / "variant.yaml"),
    }
    return cfg


def test_stages_all_configs_agent_files(tmp_path, monkeypatch):
    # three prompt variants on disk (cwd-relative, like config/agents/archive/*.md)
    agents_src = tmp_path / "agents"
    agents_src.mkdir()
    names = ["v1-strict.md", "v2-lean.md", "v3-cited.md"]
    for n in names:
        (agents_src / n).write_text(f"# {n}\n")

    # run from tmp_path so the cwd-relative agent_md_file resolves
    monkeypatch.chdir(tmp_path)
    rels = [f"agents/{n}" for n in names]
    configs = [_config(tmp_path, r) for r in rels]

    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    class _CM:
        config = configs[0]

        def get_configs(self):
            return configs

    context = SimpleNamespace(
        config_manager=_CM(),
        base_dir=base_dir,
        benchmarking=True,
    )

    mgr = object.__new__(TemplateManager)
    mgr._stage_agents(context)

    staged = sorted(p.name for p in (base_dir / "data" / "agents").iterdir())
    assert staged == sorted(names)


def test_same_basename_different_files_is_rejected(tmp_path, monkeypatch):
    """Two configs whose agent files share a basename would overwrite each
    other when staged (and when referenced by the rendered config), so the
    deployment must reject it instead of silently dropping one variant."""
    import pytest

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "prompt.md").write_text("# a\n")
    (tmp_path / "b" / "prompt.md").write_text("# b\n")
    monkeypatch.chdir(tmp_path)

    configs = [_config(tmp_path, "a/prompt.md"), _config(tmp_path, "b/prompt.md")]
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    class _CM:
        config = configs[0]

        def get_configs(self):
            return configs

    context = SimpleNamespace(
        config_manager=_CM(), base_dir=base_dir, benchmarking=True
    )
    mgr = object.__new__(TemplateManager)
    with pytest.raises(ValueError, match="same basename"):
        mgr._stage_agents(context)
