"""Unit tests for config-seed's config-path resolution.

A multi-config benchmarking deployment renders per-variant files (e.g.
`fasrc-cannon-v1-strict.yaml`) instead of a single `config.yaml`, so config-seed
must fall back to the first `*.yaml` in the rendered-config directory rather than
aborting the whole deployment with FileNotFoundError. (Seeding Postgres from any
one config is harmless — the benchmarker reads the YAML files directly and never
consumes the seeded static_config.)
"""

from src.cli.tools.config_seed import resolve_config_path


def test_existing_file_returned_as_is(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("name: x\n")
    assert resolve_config_path(str(cfg)) == str(cfg)


def test_missing_config_yaml_falls_back_to_first_yaml(tmp_path):
    (tmp_path / "fasrc-cannon-v2-lean.yaml").write_text("name: b\n")
    (tmp_path / "fasrc-cannon-v1-strict.yaml").write_text("name: a\n")
    missing = tmp_path / "config.yaml"  # does not exist

    resolved = resolve_config_path(str(missing))

    # first in sorted order
    assert resolved == str(tmp_path / "fasrc-cannon-v1-strict.yaml")


def test_directory_path_resolves_to_first_yaml(tmp_path):
    (tmp_path / "b.yaml").write_text("name: b\n")
    (tmp_path / "a.yaml").write_text("name: a\n")
    assert resolve_config_path(str(tmp_path)) == str(tmp_path / "a.yaml")


def test_no_yaml_returns_original_path(tmp_path):
    missing = tmp_path / "config.yaml"
    # empty dir, nothing to fall back to -> return original so load raises clearly
    assert resolve_config_path(str(missing)) == str(missing)
