"""Unit tests for arm-config divergence comparison and fallback-candidate discovery (#523)."""

import pytest
import yaml

from src.cli.tools import config_seed
from src.utils import benchmark_provenance

_BASE = {
    "name": "a",
    "services": {
        "chat_app": {"force_initial_retrieval": True, "agents_dir": "/x"},
        "benchmarking": {"agent_md_file": "a.md"},
    },
    "data_manager": {"chunk_size": 1000},
}


def _write(path, data):
    path.write_text(yaml.safe_dump(data))
    return str(path)


# (a) Ignored paths produce no divergence; identity check ---


def test_benchmarking_diff_ignored(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(
        tmp_path / "b.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {"force_initial_retrieval": True, "agents_dir": "/x"},
                "benchmarking": {"agent_md_file": "b.md"},
            },
        },
    )
    assert config_seed.arm_config_divergence([a, b]) == {}


def test_name_diff_ignored(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "name": "b"})
    assert config_seed.arm_config_divergence([a, b]) == {}


def test_agents_dir_diff_ignored(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(
        tmp_path / "b.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {"force_initial_retrieval": True, "agents_dir": "/y"},
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )
    assert config_seed.arm_config_divergence([a, b]) == {}


def test_divergence_ignored_paths_is_imported_not_copied():
    assert (
        config_seed.DIVERGENCE_IGNORED_PATHS
        is benchmark_provenance.DIVERGENCE_IGNORED_PATHS
    )


# (b) Real difference is reported ---


def test_force_initial_retrieval_diff_reported(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(
        tmp_path / "b.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {"force_initial_retrieval": False, "agents_dir": "/x"},
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )
    assert config_seed.arm_config_divergence([a, b]) == {
        b: ["services.chat_app.force_initial_retrieval"]
    }


# (c) Key present in only one file (non-null) is reported for the second file ---


def test_extra_key_in_first_reported_for_second(tmp_path):
    a = _write(
        tmp_path / "a.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {
                    "force_initial_retrieval": True,
                    "agents_dir": "/x",
                    "recursion_limit": 50,
                },
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )
    b = _write(tmp_path / "b.yaml", _BASE)
    result = config_seed.arm_config_divergence([a, b])
    assert b in result
    assert "services.chat_app.recursion_limit" in result[b]


def test_extra_key_in_second_reported_for_second(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(
        tmp_path / "b.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {
                    "force_initial_retrieval": True,
                    "agents_dir": "/x",
                    "recursion_limit": 50,
                },
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )
    result = config_seed.arm_config_divergence([a, b])
    assert b in result
    assert "services.chat_app.recursion_limit" in result[b]


# (d) data_manager difference is reported ---


def test_data_manager_chunk_size_diff_reported(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "data_manager": {"chunk_size": 500}})
    result = config_seed.arm_config_divergence([a, b])
    assert b in result
    assert "data_manager.chunk_size" in result[b]


# (e) Edge cases: three files, single path, empty list ---


def test_three_files_only_third_differs(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "name": "b"})
    c = _write(
        tmp_path / "c.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {"force_initial_retrieval": False, "agents_dir": "/x"},
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )
    result = config_seed.arm_config_divergence([a, b, c])
    assert list(result.keys()) == [c]


def test_single_path_returns_empty(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    assert config_seed.arm_config_divergence([a]) == {}


def test_empty_list_returns_empty():
    assert config_seed.arm_config_divergence([]) == {}


# (f) fallback_candidates ---


def test_fallback_candidates_config_yaml_present(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("name: x\n")
    assert config_seed.fallback_candidates(str(cfg)) == []


def test_fallback_candidates_absent_returns_sorted_pair(tmp_path):
    (tmp_path / "b.yaml").write_text("name: b\n")
    (tmp_path / "a.yaml").write_text("name: a\n")
    result = config_seed.fallback_candidates(str(tmp_path / "config.yaml"))
    assert result == [str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")]


def test_fallback_candidates_empty_dir_returns_empty(tmp_path):
    assert config_seed.fallback_candidates(str(tmp_path / "config.yaml")) == []


# --- seed_entry divergence guard (task 2.1) ---


def _make_fake_pg(factory_log):
    class FakeCS:
        pass

    class FakeFactory:
        config_service = FakeCS()

        @classmethod
        def from_env(cls, **kwargs):
            factory_log.append(True)
            return cls()

        @staticmethod
        def set_instance(f):
            pass

    return FakeFactory


def test_seed_entry_diverging_arms_exits_nonzero(tmp_path, monkeypatch, capsys):
    b_cfg = {
        **_BASE,
        "services": {
            "chat_app": {"force_initial_retrieval": False, "agents_dir": "/x"},
            "benchmarking": {"agent_md_file": "a.md"},
        },
    }
    _write(tmp_path / "a.yaml", _BASE)
    _write(tmp_path / "b.yaml", b_cfg)

    factory_log, seed_log, record_log = [], [], []
    monkeypatch.setattr(
        config_seed, "PostgresServiceFactory", _make_fake_pg(factory_log)
    )
    monkeypatch.setattr(config_seed, "seed", lambda cfg, cs: seed_log.append(cfg))
    monkeypatch.setattr(
        config_seed, "record_deployment", lambda cs, env: record_log.append(True)
    )

    with pytest.raises(SystemExit) as exc_info:
        config_seed.seed_entry(str(tmp_path / "config.yaml"), {})

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "services.chat_app.force_initial_retrieval" in err
    assert "a.yaml" in err
    assert "b.yaml" in err
    assert not factory_log
    assert not seed_log
    assert not record_log


def test_seed_entry_benchmarking_only_diff_seeds_normally(tmp_path, monkeypatch):
    b_cfg = {
        **_BASE,
        "services": {
            "chat_app": {"force_initial_retrieval": True, "agents_dir": "/x"},
            "benchmarking": {"agent_md_file": "b.md"},
        },
    }
    _write(tmp_path / "a.yaml", _BASE)
    _write(tmp_path / "b.yaml", b_cfg)

    factory_log, seed_log, record_log = [], [], []
    monkeypatch.setattr(
        config_seed, "PostgresServiceFactory", _make_fake_pg(factory_log)
    )
    monkeypatch.setattr(config_seed, "seed", lambda cfg, cs: seed_log.append(cfg))
    monkeypatch.setattr(
        config_seed, "record_deployment", lambda cs, env: record_log.append(True)
    )

    config_seed.seed_entry(str(tmp_path / "config.yaml"), {})

    assert len(seed_log) == 1
    assert seed_log[0]["name"] == "a"
    assert len(record_log) == 1


def test_seed_entry_config_yaml_present_skips_divergence_check(tmp_path, monkeypatch):
    _write(tmp_path / "config.yaml", _BASE)
    _write(
        tmp_path / "x.yaml",
        {
            **_BASE,
            "services": {
                "chat_app": {"force_initial_retrieval": False, "agents_dir": "/x"},
                "benchmarking": {"agent_md_file": "a.md"},
            },
        },
    )

    factory_log, seed_log, record_log = [], [], []
    monkeypatch.setattr(
        config_seed, "PostgresServiceFactory", _make_fake_pg(factory_log)
    )
    monkeypatch.setattr(config_seed, "seed", lambda cfg, cs: seed_log.append(cfg))
    monkeypatch.setattr(
        config_seed, "record_deployment", lambda cs, env: record_log.append(True)
    )

    config_seed.seed_entry(str(tmp_path / "config.yaml"), {})

    assert len(seed_log) == 1
    assert seed_log[0]["name"] == "a"


def test_seed_entry_single_arm_seeds_normally(tmp_path, monkeypatch):
    _write(tmp_path / "a.yaml", _BASE)

    factory_log, seed_log, record_log = [], [], []
    monkeypatch.setattr(
        config_seed, "PostgresServiceFactory", _make_fake_pg(factory_log)
    )
    monkeypatch.setattr(config_seed, "seed", lambda cfg, cs: seed_log.append(cfg))
    monkeypatch.setattr(
        config_seed, "record_deployment", lambda cs, env: record_log.append(True)
    )

    config_seed.seed_entry(str(tmp_path / "config.yaml"), {})

    assert len(seed_log) == 1
    assert len(record_log) == 1


# --- key presence is a setting between arms (PR #628 review) ---


def _git_source(**git):
    return {**_BASE, "data_manager": {"chunk_size": 1000, "sources": {"git": git}}}


@pytest.mark.parametrize("value", [[], None, {}])
def test_absent_versus_present_empty_reported(tmp_path, value):
    # GitScraper reads code_suffixes with a default: absent means the built-in
    # suffix list, [] means no suffixes, null means a TypeError.
    a = _write(tmp_path / "a.yaml", _git_source())
    b = _write(tmp_path / "b.yaml", _git_source(code_suffixes=value))
    result = config_seed.arm_config_divergence([a, b])
    assert result == {b: ["data_manager.sources.git.code_suffixes"]}


def test_present_empty_versus_absent_reported(tmp_path):
    a = _write(tmp_path / "a.yaml", _git_source(code_suffixes=[]))
    b = _write(tmp_path / "b.yaml", _git_source())
    result = config_seed.arm_config_divergence([a, b])
    assert result == {b: ["data_manager.sources.git.code_suffixes"]}


def test_empty_list_versus_empty_mapping_reported(tmp_path):
    a = _write(tmp_path / "a.yaml", _git_source(code_suffixes=[]))
    b = _write(tmp_path / "b.yaml", _git_source(code_suffixes={}))
    result = config_seed.arm_config_divergence([a, b])
    assert result == {b: ["data_manager.sources.git.code_suffixes"]}


def test_zero_versus_false_reported(tmp_path):
    a = _write(tmp_path / "a.yaml", {**_BASE, "data_manager": {"chunk_size": 0}})
    b = _write(tmp_path / "b.yaml", {**_BASE, "data_manager": {"chunk_size": False}})
    result = config_seed.arm_config_divergence([a, b])
    assert result == {b: ["data_manager.chunk_size"]}


def test_identical_empty_values_agree(tmp_path):
    a = _write(tmp_path / "a.yaml", _git_source(code_suffixes=[], branches=None))
    b = _write(tmp_path / "b.yaml", _git_source(code_suffixes=[], branches=None))
    assert config_seed.arm_config_divergence([a, b]) == {}


# --- evaluate-time refusal over the operator's own arm files ---


def test_arm_divergence_refusal_names_reference_and_paths(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "data_manager": {"chunk_size": 500}})
    message = config_seed.arm_divergence_refusal([a, b])
    assert f"reference: {a}" in message
    assert f"{b}: data_manager.chunk_size" in message
    assert "separate deployments" in message


def test_arm_divergence_refusal_none_when_arms_agree(tmp_path):
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "name": "b"})
    assert config_seed.arm_divergence_refusal([a, b]) is None


def test_arm_divergence_refusal_skips_files_that_are_not_configs(tmp_path):
    # ConfigurationManager logs and skips these; the refusal must not read a
    # README or a broken file as a divergent arm.
    a = _write(tmp_path / "a.yaml", _BASE)
    b = _write(tmp_path / "b.yaml", {**_BASE, "name": "b"})
    readme = tmp_path / "README.md"
    readme.write_text("These are the sweep arms.\n")
    broken = tmp_path / "broken.yaml"
    broken.write_text("key: [unclosed\n")
    missing = str(tmp_path / "missing.yaml")
    paths = [a, str(readme), str(broken), missing, b]
    assert config_seed.arm_divergence_refusal(paths) is None
