"""Unit tests for rendered-config filename derivation in the deployment manager.

A multi-config `archi evaluate --config-dir` run must render one distinct file
per config so the benchmarker iterates every variant. Previously all configs
collided onto `{top_level_name}.yaml` (e.g. three `ragas-bench` sweep variants
all overwrote `ragas-bench.yaml`), so only the last survived.
"""

from src.cli.managers.templates_manager import _render_config_target_name


def test_single_mode_is_config_yaml():
    assert _render_config_target_name(True, "ragas-bench", None, 0, set()) == "config.yaml"


def test_multi_uses_benchmarking_name():
    used = set()
    assert (
        _render_config_target_name(False, "ragas-bench", "fasrc-cannon-v1-strict", 0, used)
        == "fasrc-cannon-v1-strict.yaml"
    )


def test_multi_distinct_files_for_distinct_variants():
    used = set()
    names = [
        _render_config_target_name(False, "ragas-bench", "v1", 0, used),
        _render_config_target_name(False, "ragas-bench", "v2", 1, used),
        _render_config_target_name(False, "ragas-bench", "v3", 2, used),
    ]
    assert names == ["v1.yaml", "v2.yaml", "v3.yaml"]
    assert len(set(names)) == 3


def test_collision_disambiguated_by_index():
    used = set()
    first = _render_config_target_name(False, "ragas-bench", "dup", 0, used)
    second = _render_config_target_name(False, "ragas-bench", "dup", 1, used)
    assert first == "dup.yaml"
    assert second == "dup_1.yaml"
    assert first != second


def test_repeated_collisions_stay_unique():
    """Disambiguation must keep bumping until unique, not collide a second time."""
    used = set()
    names = [_render_config_target_name(False, "rb", "dup", i, used) for i in range(4)]
    assert len(set(names)) == 4  # all distinct, none overwrites another
    assert names[0] == "dup.yaml"


def test_multi_falls_back_to_top_level_name_when_no_benchmarking_name():
    used = set()
    assert (
        _render_config_target_name(False, "ragas-bench", None, 0, used) == "ragas-bench.yaml"
    )
