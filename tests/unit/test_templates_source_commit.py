"""Provenance is tied to the source-copy/build path.

Covers the "Deployment records the source commit" spec requirement. The write lives
in ``copy_source_code`` (the single point that materialises the source the image is
built from) rather than in ``prepare_deployment_files``, so:

* a rebuild that copies source always refreshes ``SOURCE_COMMIT`` (``restart`` with the
  default rebuild), and
* a run that does not rebuild (``restart --no-build``) leaves it untouched — the recorded
  commit reflects the code actually running in the image.

The commit resolver is patched so assertions do not depend on the runner's git state.
"""

from pathlib import Path
from unittest.mock import MagicMock

from jinja2 import Environment

from src.cli.managers import source_version, templates_manager
from src.cli.managers.templates_manager import TemplateContext, TemplateManager
from src.cli.utils.service_builder import ServiceBuilder


def _manager():
    return TemplateManager(Environment(), verbosity=0)


def _plan(base_dir):
    return ServiceBuilder.build_compose_config(
        name="t",
        verbosity=0,
        base_dir=base_dir,
        enabled_services=["chatbot"],
    )


def _context(base_dir, **options):
    return TemplateContext(
        plan=_plan(base_dir),
        config_manager=None,
        secrets_manager=None,
        options=dict(options),
    )


def _fake_repo(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "archi.py").write_text("print('hi')\n")
    (repo / "pyproject.toml").write_text("[project]\nname='archi'\n")
    (repo / "LICENSE").write_text("MIT\n")
    return repo


def test_copy_source_code_writes_source_commit(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    monkeypatch.setattr("src.cli.utils._repository_info.REPO_PATH", str(repo))
    monkeypatch.setattr(
        source_version,
        "resolve_source_commit",
        lambda repo_root=None: "936a52f8-dirty",
    )

    _manager().copy_source_code(base_dir)

    assert (base_dir / "archi_code").is_dir()
    assert (base_dir / "SOURCE_COMMIT").read_text().strip() == "936a52f8-dirty"


def test_copy_source_code_ties_commit_to_copied_repo_root(monkeypatch, tmp_path):
    repo = _fake_repo(tmp_path)
    base_dir = tmp_path / "deploy"
    base_dir.mkdir()

    monkeypatch.setattr("src.cli.utils._repository_info.REPO_PATH", str(repo))
    spy = MagicMock(return_value="deadbeef")
    monkeypatch.setattr(templates_manager, "write_source_commit", spy)

    _manager().copy_source_code(base_dir)

    spy.assert_called_once()
    args, kwargs = spy.call_args
    # Provenance is resolved from the same checkout the source was copied from.
    assert Path(kwargs.get("repo_root", args[1] if len(args) > 1 else None)) == repo


def test_stage_source_copy_runs_when_build_enabled(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        TemplateManager,
        "copy_source_code",
        lambda self, base_dir: called.setdefault("base_dir", base_dir),
    )

    ctx = _context(tmp_path)  # build defaults to True
    _manager()._stage_source_copy(ctx)

    assert called.get("base_dir") == tmp_path


def test_stage_source_copy_skips_when_build_disabled(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        TemplateManager,
        "copy_source_code",
        lambda self, base_dir: called.setdefault("base_dir", base_dir),
    )

    ctx = _context(tmp_path, build=False)
    _manager()._stage_source_copy(ctx)

    assert "base_dir" not in called
    assert not (tmp_path / "SOURCE_COMMIT").exists()


def test_context_build_defaults_true_and_reads_option(tmp_path):
    assert _context(tmp_path).build is True
    assert _context(tmp_path, build=False).build is False
