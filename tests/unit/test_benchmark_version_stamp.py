"""A benchmark artifact must identify the code and the config that produced it.

Divergence detection (``config_divergence``) only works at write time, when both
the selected file and the running config are in hand. It cannot answer the
question a reader of a finished artifact actually asks: *was this the same code
and the same settings as that other run?*

Two symptoms motivated these helpers, both observed in ``bench_out/``:

* Every run from 2026-08-11 through 2026-08-17 records the same
  ``git_info.last_commit`` (``0a157cdce0``) with an empty diff, because
  ``git_info.yaml`` is written once by ``archi create`` and then frozen. The
  commit identifies the deploy, not the image, so it cannot distinguish arms.
* ``bench-8192-20260817_170850.json`` -- the 8192 arm -- records
  ``context_window: 32768``. The artifact does not merely omit the arm's config,
  it attests to the wrong one.

A content digest fixes both: equal digests mean equal inputs, and the property
survives in the artifact long after the live sources are gone.
"""

import pytest

from src.utils.benchmark_provenance import (
    KEY_SETTING_PATHS,
    code_fingerprint,
    code_version,
    collect_code_version,
    config_fingerprint,
    config_version,
    loaded_module_files,
    read_module_sources,
    settings_at_paths,
)


class TestConfigFingerprint:
    """Identity of a configuration, derived from its content."""

    def test_the_same_config_fingerprints_the_same(self):
        cfg = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}

        assert config_fingerprint(cfg) == config_fingerprint(dict(cfg))

    def test_key_order_does_not_change_the_fingerprint(self):
        """A dict round-tripped through YAML or JSONB may reorder its keys."""
        left = {"a": 1, "b": {"x": 1, "y": 2}}
        right = {"b": {"y": 2, "x": 1}, "a": 1}

        assert config_fingerprint(left) == config_fingerprint(right)

    def test_the_8192_and_32768_arms_fingerprint_differently(self):
        """The distinction the bench-8192 artifact failed to record."""
        arm_8192 = {
            "services": {"chat_app": {"context_editing": {"context_window": 8192}}}
        }
        arm_32768 = {
            "services": {"chat_app": {"context_editing": {"context_window": 32768}}}
        }

        assert config_fingerprint(arm_8192) != config_fingerprint(arm_32768)

    def test_the_fingerprint_is_labelled_with_its_algorithm(self):
        assert config_fingerprint({"a": 1}).startswith("sha256:")

    def test_a_numeric_setting_is_not_a_boolean_one(self):
        """``0 == False`` in Python; they are different settings."""
        assert config_fingerprint({"keep": 0}) != config_fingerprint({"keep": False})

    def test_a_value_json_cannot_encode_still_yields_a_fingerprint(self):
        """Provenance must never be the reason a finished run loses its scores."""
        digest = config_fingerprint({"path": object()})

        assert digest.startswith("sha256:")


class TestCodeFingerprint:
    """Identity of the code that was actually loaded, not the deploy's commit."""

    def test_the_same_sources_fingerprint_the_same(self):
        sources = [("src.archi.archi", b"print(1)"), ("src.utils.x", b"print(2)")]

        assert code_fingerprint(sources) == code_fingerprint(list(sources))

    def test_order_does_not_change_the_fingerprint(self):
        """``sys.modules`` iteration order is an implementation detail."""
        a = [("src.a", b"one"), ("src.b", b"two")]
        b = [("src.b", b"two"), ("src.a", b"one")]

        assert code_fingerprint(a) == code_fingerprint(b)

    def test_changed_source_changes_the_fingerprint(self):
        before = [("src.archi.archi", b"context_window = 32768")]
        after = [("src.archi.archi", b"context_window = 8192")]

        assert code_fingerprint(before) != code_fingerprint(after)

    def test_a_renamed_module_changes_the_fingerprint(self):
        """The module set is part of the code's identity, not just the bytes."""
        before = [("src.old_name", b"same body")]
        after = [("src.new_name", b"same body")]

        assert code_fingerprint(before) != code_fingerprint(after)

    def test_which_module_holds_which_body_is_part_of_the_identity(self):
        """Hashing the bodies as an unordered bag would miss a swap."""
        before = [("src.a", b"one"), ("src.b", b"two")]
        after = [("src.a", b"two"), ("src.b", b"one")]

        assert code_fingerprint(before) != code_fingerprint(after)

    def test_a_module_name_cannot_forge_two_records(self):
        forged = [("src.a\nsrc.b", b"")]
        genuine = [("src.a", b""), ("src.b", b"")]

        assert code_fingerprint(forged) != code_fingerprint(genuine)

    def test_no_loaded_modules_is_reported_rather_than_hashed(self):
        """An empty digest would silently claim two unknown images matched."""
        with pytest.raises(ValueError):
            code_fingerprint([])


class _FakeModule:
    def __init__(self, file):
        self.__file__ = file


class TestLoadedModuleFiles:
    """Which loaded modules make up the code under test."""

    def test_selects_only_modules_under_the_package(self):
        modules = {
            "src.archi.archi": _FakeModule("/app/src/archi/archi.py"),
            "yaml": _FakeModule("/site-packages/yaml/__init__.py"),
            "srcfoo.bar": _FakeModule("/app/srcfoo/bar.py"),
        }

        assert loaded_module_files(modules, package="src") == [
            ("src.archi.archi", "/app/src/archi/archi.py")
        ]

    def test_includes_the_package_root_itself(self):
        modules = {"src": _FakeModule("/app/src/__init__.py")}

        assert loaded_module_files(modules, package="src") == [
            ("src", "/app/src/__init__.py")
        ]

    def test_result_is_sorted_by_module_name(self):
        modules = {
            "src.b": _FakeModule("/app/src/b.py"),
            "src.a": _FakeModule("/app/src/a.py"),
        }

        assert [name for name, _ in loaded_module_files(modules, package="src")] == [
            "src.a",
            "src.b",
        ]

    def test_skips_modules_with_no_file(self):
        """Namespace packages and C extensions have no source to hash."""
        modules = {
            "src.real": _FakeModule("/app/src/real.py"),
            "src.namespace": _FakeModule(None),
            "src.builtin": object(),
        }

        assert loaded_module_files(modules, package="src") == [
            ("src.real", "/app/src/real.py")
        ]


class TestReadModuleSources:
    """Turning module paths into the bytes that get digested."""

    def test_reads_each_module_body(self, tmp_path):
        module = tmp_path / "a.py"
        module.write_bytes(b"context_window = 8192")

        assert read_module_sources([("src.a", str(module))]) == [
            ("src.a", b"context_window = 8192")
        ]

    def test_an_unreadable_file_is_skipped_not_fatal(self, tmp_path):
        """A scored benchmark must not be lost because one source went missing."""
        present = tmp_path / "present.py"
        present.write_bytes(b"body")

        sources = read_module_sources(
            [
                ("src.gone", str(tmp_path / "absent.py")),
                ("src.present", str(present)),
            ]
        )

        assert sources == [("src.present", b"body")]

    def test_collect_uses_the_live_module_map(self, tmp_path):
        module = tmp_path / "live.py"
        module.write_bytes(b"body")
        modules = {"src.live": _FakeModule(str(module))}

        stamp = collect_code_version(modules, {"last_commit": "abc\n"})

        assert stamp["digest"] == code_fingerprint([("src.live", b"body")])
        assert stamp["module_count"] == 1

    def test_collect_reports_unavailable_when_nothing_is_loaded(self):
        stamp = collect_code_version({}, {})

        assert stamp["digest"] is None
        assert "unavailable" in stamp["source"]


class TestSettingsAtPaths:
    """A compact view of the settings a campaign varies."""

    def test_extracts_a_nested_path(self):
        cfg = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}

        assert settings_at_paths(cfg, ["services.chat_app.context_editing"]) == {
            "services.chat_app.context_editing": {"context_window": 8192}
        }

    def test_absent_paths_are_omitted_rather_than_recorded_as_null(self):
        """A missing key and a key set to null are different facts."""
        cfg = {"services": {"chat_app": {}}}

        assert settings_at_paths(cfg, ["services.chat_app.context_editing"]) == {}

    def test_a_path_through_a_non_mapping_is_omitted(self):
        cfg = {"services": "not-a-mapping"}

        assert settings_at_paths(cfg, ["services.chat_app.agent_class"]) == {}

    def test_the_default_paths_cover_the_context_window_arm(self):
        """The setting the bench-8192 artifact misattributed."""
        assert "services.chat_app.context_editing" in KEY_SETTING_PATHS

    def test_a_non_mapping_config_yields_no_settings(self):
        assert settings_at_paths(None, ["services.chat_app"]) == {}


class TestCodeVersion:
    """The ``code_version`` block written into a report's metadata."""

    def test_records_the_digest_of_the_loaded_code(self):
        stamp = code_version(
            sources=[("src.a", b"body")], deploy_git_info={"last_commit": "abc123\n"}
        )

        assert stamp["digest"] == code_fingerprint([("src.a", b"body")])

    def test_records_how_many_modules_the_digest_covers(self):
        stamp = code_version(
            sources=[("src.a", b"x"), ("src.b", b"y")], deploy_git_info={}
        )

        assert stamp["module_count"] == 2

    def test_carries_the_deploy_commit_but_marks_it_as_deploy_scoped(self):
        """Every Aug 11-17 run shares 0a157cdce0; the field must say why."""
        stamp = code_version(
            sources=[("src.a", b"x")],
            deploy_git_info={"last_commit": "0a157cdce0\n", "git_diff": ""},
        )

        assert stamp["deploy_git_commit"] == "0a157cdce0"
        assert stamp["deploy_git_dirty"] is False
        assert "archi create" in stamp["deploy_git_note"]

    def test_a_deploy_diff_marks_the_deploy_tree_dirty(self):
        stamp = code_version(
            sources=[("src.a", b"x")],
            deploy_git_info={"last_commit": "abc\n", "git_diff": "--- a/x\n+++ b/x\n"},
        )

        assert stamp["deploy_git_dirty"] is True

    def test_a_missing_deploy_commit_is_recorded_as_unknown(self):
        stamp = code_version(sources=[("src.a", b"x")], deploy_git_info=None)

        assert stamp["deploy_git_commit"] is None

    def test_unavailable_sources_do_not_raise(self):
        """A finished benchmark must not lose its scores over provenance."""
        stamp = code_version(sources=[], deploy_git_info={})

        assert stamp["digest"] is None
        assert "unavailable" in stamp["source"]


class TestConfigVersion:
    """The ``config_version`` block written into a report's metadata."""

    RUNNING = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}
    SELECTED = {
        "services": {"chat_app": {"context_editing": {"context_window": 32768}}}
    }

    def test_the_digest_identifies_the_config_the_agent_read(self):
        stamp = config_version(
            running=self.RUNNING, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert stamp["digest"] == config_fingerprint(self.RUNNING)

    def test_the_selected_file_is_fingerprinted_separately(self):
        stamp = config_version(
            running=self.RUNNING, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert stamp["selected_file"] == "/c.yaml"
        assert stamp["selected_file_digest"] == config_fingerprint(self.SELECTED)

    def test_names_the_setting_the_artifact_would_have_misattributed(self):
        stamp = config_version(
            running=self.RUNNING, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert stamp["divergence_from_selected_file"] == [
            "services.chat_app.context_editing.context_window"
        ]

    def test_surfaces_the_arm_defining_settings_from_the_running_config(self):
        """A reader must see the arm without parsing a 500-key blob."""
        stamp = config_version(
            running=self.RUNNING, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert stamp["key_settings"] == {
            "services.chat_app.context_editing": {"context_window": 8192}
        }

    def test_falls_back_to_the_selected_file_when_postgres_is_unreadable(self):
        """Degraded, and labelled as such -- not silently mislabelled."""
        stamp = config_version(
            running=None, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert stamp["digest"] == config_fingerprint(self.SELECTED)
        assert "selected file" in stamp["source"]

    def test_the_running_config_is_the_named_source_when_available(self):
        stamp = config_version(
            running=self.RUNNING, selected=self.SELECTED, selected_file="/c.yaml"
        )

        assert "running" in stamp["source"]
