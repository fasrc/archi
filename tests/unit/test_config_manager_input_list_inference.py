"""Unit tests for get_enabled_sources input-list inference (design D5)."""

import logging

import pytest

from src.cli.managers.config_manager import ConfigurationManager
from src.cli.source_registry import source_registry


def _manager(configs):
    """Build a ConfigurationManager shell without running its file-loading __init__."""
    mgr = object.__new__(ConfigurationManager)
    mgr.configs = list(configs)
    return mgr


def _config_with_list(list_path, sources_section=None):
    """Return a minimal config dict with a single input list path."""
    cfg = {
        "name": "test",
        "data_manager": {
            "sources": {
                "links": {
                    "input_lists": [str(list_path)],
                },
            },
        },
    }
    if sources_section:
        cfg["data_manager"]["sources"].update(sources_section)
    return cfg


# (a) git- entry, git key absent → git in get_enabled_sources; required_secrets contains GIT creds
def test_git_prefix_absent_key_infers_git(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("git-https://github.com/org/repo.git\n")
    mgr = _manager([_config_with_list(lst)])
    enabled = mgr.get_enabled_sources()
    assert "git" in enabled
    secrets = source_registry.required_secrets(enabled)
    assert "GIT_USERNAME" in secrets
    assert "GIT_TOKEN" in secrets


# (b) git- entry, git.enabled: false → git not included; one WARNING with name and count
def test_git_prefix_explicit_false_skips_and_warns(tmp_path, caplog):
    lst = tmp_path / "urls.list"
    lst.write_text("git-https://github.com/org/repo.git\n")
    cfg = _config_with_list(lst, {"git": {"enabled": False}})
    mgr = _manager([cfg])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert "git" not in enabled
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("git" in w and "1" in w for w in warnings), warnings


# (c) git- entries, git section is bool False → git not included; WARNING with name and count
def test_git_prefix_bool_false_section_skips_and_warns(tmp_path, caplog):
    lst = tmp_path / "urls.list"
    lst.write_text(
        "git-https://github.com/org/repo.git\ngit-https://github.com/org/r2.git\n"
    )
    cfg = _config_with_list(lst, {"git": False})
    mgr = _manager([cfg])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert "git" not in enabled
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("git" in w and "2" in w for w in warnings), warnings


# (d) missing list path → no exception; WARNING names the path
def test_missing_list_path_warns_no_exception(tmp_path, caplog):
    missing = tmp_path / "does_not_exist.list"
    cfg = _config_with_list(missing)
    mgr = _manager([cfg])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(missing) in w for w in warnings), warnings


# (e) elog- entry and unprefixed elog URL with elog absent → elog included;
#     set_sources_enabled writes elog.enabled: True
def test_elog_prefix_and_url_heuristic_infers_elog(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("elog-https://h/elog/logbook\nhttps://h/elog/x\n")
    cfg = _config_with_list(lst)
    mgr = _manager([cfg])
    enabled = mgr.get_enabled_sources()
    assert "elog" in enabled
    mgr.set_sources_enabled(enabled)
    assert mgr.configs[0]["data_manager"]["sources"]["elog"]["enabled"] is True


# (f) sitemap- entry alone adds no new source
def test_sitemap_entry_alone_adds_nothing(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("sitemap-https://x/sitemap.xml\n")
    mgr = _manager([_config_with_list(lst)])
    enabled = mgr.get_enabled_sources()
    assert not any(s in enabled for s in ["git", "sso", "elog", "indico"])


# (f2) sitemap- entry whose path contains /elog/ → no elog inference; the
#      runtime classifier peels sitemap- before the ELOG heuristic
def test_sitemap_entry_with_elog_path_does_not_infer_elog(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("sitemap-https://h/elog/sitemap.xml\n")
    mgr = _manager([_config_with_list(lst)])
    assert "elog" not in mgr.get_enabled_sources()


# (g) git.enabled: true with entries → included; no WARNING about git
def test_git_enabled_true_with_entries_no_warning(tmp_path, caplog):
    lst = tmp_path / "urls.list"
    lst.write_text("git-https://github.com/org/repo.git\n")
    cfg = _config_with_list(lst, {"git": {"enabled": True}})
    mgr = _manager([cfg])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert "git" in enabled
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("git" in w for w in warnings), warnings


# (h) sso- entry with sso absent and no selenium_scraper → sso in enabled;
#     validate_configs raises ValueError naming selenium_class
def test_sso_prefix_infers_sso_fails_validation(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("sso-https://example.com/page\n")
    cfg = _config_with_list(lst)
    cfg["name"] = "test-sso"
    mgr = _manager([cfg])
    enabled = mgr.get_enabled_sources()
    assert "sso" in enabled
    with pytest.raises(ValueError, match="selenium_class"):
        mgr.validate_configs([], source_registry.resolve_dependencies(["links", "sso"]))


# (i) indico- entry with /elog/ in path → indico included; elog NOT included
def test_indico_prefix_with_elog_path_goes_to_indico_not_elog(tmp_path):
    lst = tmp_path / "urls.list"
    lst.write_text("indico-https://indico.h/elog/x\n")
    mgr = _manager([_config_with_list(lst)])
    enabled = mgr.get_enabled_sources()
    assert "indico" in enabled
    assert "elog" not in enabled


def _two_lists_same_basename(tmp_path):
    """Two lists named urls.list in different directories; only the first has git-."""
    first = tmp_path / "a" / "urls.list"
    second = tmp_path / "b" / "urls.list"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("git-https://github.com/org/repo.git\n")
    second.write_text("https://example.org/page\n")
    return first, second


def _config_with_lists(paths):
    cfg = _config_with_list(paths[0])
    cfg["data_manager"]["sources"]["links"]["input_lists"] = [str(p) for p in paths]
    return cfg


# Staging copies each list to weblists/<basename>, so a later list with the same
# basename overwrites an earlier one; inference reads only the file staging keeps.
def test_basename_collision_infers_from_the_staged_file_only(tmp_path, caplog):
    first, second = _two_lists_same_basename(tmp_path)
    mgr = _manager([_config_with_lists([first, second])])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert "git" not in enabled
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(first) in w and str(second) in w for w in warnings), warnings


# Staging copies the lists in sorted path order across every config
# (_collect_input_lists), so the lexically last path wins, not the last YAML entry.
def test_basename_collision_follows_staging_order_not_yaml_order(tmp_path):
    first, second = _two_lists_same_basename(tmp_path)
    mgr = _manager([_config_with_lists([second, first])])
    assert "git" not in mgr.get_enabled_sources()


def test_basename_collision_lexically_last_list_wins(tmp_path):
    first, second = _two_lists_same_basename(tmp_path)
    first.write_text("https://example.org/page\n")
    second.write_text("git-https://github.com/org/repo.git\n")
    mgr = _manager([_config_with_lists([second, first])])
    assert "git" in mgr.get_enabled_sources()


# All configs stage into one weblists/ directory, so a list in another config can
# replace this config's list of the same basename.
def test_basename_collision_across_configs_uses_the_staged_file(tmp_path, caplog):
    first, second = _two_lists_same_basename(tmp_path)
    mgr = _manager([_config_with_list(first), _config_with_list(second)])
    with caplog.at_level(logging.WARNING):
        enabled = mgr.get_enabled_sources()
    assert "git" not in enabled
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(first) in w and str(second) in w for w in warnings), warnings
