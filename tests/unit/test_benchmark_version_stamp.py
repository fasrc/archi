"""A benchmark artifact must identify the code and the config that produced it.

``config_divergence`` works only at write time, while the selected file and the
config the chain held are both in hand. It cannot answer the question a reader of
a finished artifact asks: *was this the same code and the same settings as that
other run?*

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
    effective_config,
    package_module_files,
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
        assert config_fingerprint({"path": object()}).startswith("sha256:")


class TestEffectiveConfig:
    """What actually determined the run, not just what Postgres held.

    ``load_new_configuration`` passes ``agent_class``, ``provider``, ``model`` and
    ``agent_md_file`` from the selected file straight to ``archi()``, so they
    never reach Postgres. A digest built from the running config alone cannot
    tell two sweep arms apart when that is all that differs -- which is the
    common case.
    """

    RUNNING = {
        "services": {
            "chat_app": {"context_editing": {"context_window": 8192}},
            "benchmarking": {"agent_md_file": "/stale/from-postgres.md"},
        }
    }

    def test_the_running_config_supplies_the_base(self):
        selected = {"services": {"benchmarking": {}}}

        merged = effective_config(self.RUNNING, selected)

        assert merged["services"]["chat_app"]["context_editing"] == {
            "context_window": 8192
        }

    def test_the_selected_benchmarking_subtree_wins(self):
        selected = {"services": {"benchmarking": {"agent_md_file": "/arm/v2-lean.md"}}}

        merged = effective_config(self.RUNNING, selected)

        assert merged["services"]["benchmarking"]["agent_md_file"] == "/arm/v2-lean.md"

    def test_two_sweep_arms_differing_only_in_the_overlay_are_distinguishable(self):
        """The fasrc-cannon arms differ only in agent_md_file and name."""
        arm_a = {"services": {"benchmarking": {"agent_md_file": "/v1-strict.md"}}}
        arm_b = {"services": {"benchmarking": {"agent_md_file": "/v3-cited.md"}}}

        digest_a = config_fingerprint(effective_config(self.RUNNING, arm_a))
        digest_b = config_fingerprint(effective_config(self.RUNNING, arm_b))

        assert digest_a != digest_b

    def test_the_running_config_is_not_mutated(self):
        """The chain still holds this object; overlaying must not rewrite it."""
        selected = {"services": {"benchmarking": {"agent_md_file": "/arm.md"}}}

        effective_config(self.RUNNING, selected)

        assert (
            self.RUNNING["services"]["benchmarking"]["agent_md_file"]
            == "/stale/from-postgres.md"
        )

    def test_an_absent_overlay_path_leaves_the_base_alone(self):
        merged = effective_config(self.RUNNING, {"services": {}})

        assert (
            merged["services"]["benchmarking"]["agent_md_file"]
            == "/stale/from-postgres.md"
        )

    def test_falls_back_to_the_selected_file_when_running_is_unavailable(self):
        selected = {"services": {"benchmarking": {"agent_md_file": "/arm.md"}}}

        assert effective_config(None, selected) == selected


class TestCodeFingerprint:
    """Identity of the code under test, not the deploy's commit."""

    def test_the_same_sources_fingerprint_the_same(self):
        sources = [("archi/archi.py", b"print(1)"), ("utils/x.py", b"print(2)")]

        assert code_fingerprint(sources) == code_fingerprint(list(sources))

    def test_order_does_not_change_the_fingerprint(self):
        a = [("a.py", b"one"), ("b.py", b"two")]
        b = [("b.py", b"two"), ("a.py", b"one")]

        assert code_fingerprint(a) == code_fingerprint(b)

    def test_changed_source_changes_the_fingerprint(self):
        before = [("archi/archi.py", b"context_window = 32768")]
        after = [("archi/archi.py", b"context_window = 8192")]

        assert code_fingerprint(before) != code_fingerprint(after)

    def test_a_renamed_file_changes_the_fingerprint(self):
        """The file set is part of the code's identity, not just the bytes."""
        assert code_fingerprint([("old.py", b"same")]) != code_fingerprint(
            [("new.py", b"same")]
        )

    def test_which_file_holds_which_body_is_part_of_the_identity(self):
        """Hashing the bodies as an unordered bag would miss a swap."""
        before = [("a.py", b"one"), ("b.py", b"two")]
        after = [("a.py", b"two"), ("b.py", b"one")]

        assert code_fingerprint(before) != code_fingerprint(after)

    def test_a_path_cannot_forge_two_records(self):
        forged = [("a.py\nb.py", b"")]
        genuine = [("a.py", b""), ("b.py", b"")]

        assert code_fingerprint(forged) != code_fingerprint(genuine)

    def test_no_sources_is_reported_rather_than_hashed(self):
        """An empty digest would silently claim two unknown images matched."""
        with pytest.raises(ValueError):
            code_fingerprint([])


class TestPackageModuleFiles:
    """The manifest is the files on disk, not the modules that happened to load."""

    def test_finds_python_files_recursively(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "pkg" / "b.py").write_text("b")

        found = [rel for rel, _ in package_module_files(str(tmp_path))]

        assert found == ["a.py", "pkg/b.py"]

    def test_ignores_non_python_files(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "notes.md").write_text("m")
        (tmp_path / "data.json").write_text("{}")

        assert [rel for rel, _ in package_module_files(str(tmp_path))] == ["a.py"]

    def test_ignores_pycache(self, tmp_path):
        """Compiled artifacts vary without the source having changed."""
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "a.cpython-311.py").write_text("x")
        (tmp_path / "a.py").write_text("a")

        assert [rel for rel, _ in package_module_files(str(tmp_path))] == ["a.py"]

    def test_the_manifest_does_not_depend_on_what_was_imported(self, tmp_path):
        """The defect this replaces: a lazily-imported module changed the digest.

        ``src.utils.rbac.registry`` loads only when a decorated agent tool runs,
        so a sys.modules-based digest differed between two runs of one image.
        """
        (tmp_path / "eager.py").write_text("e")
        (tmp_path / "lazy.py").write_text("l")

        first = read_module_sources(package_module_files(str(tmp_path)))
        second = read_module_sources(package_module_files(str(tmp_path)))

        assert code_fingerprint(first) == code_fingerprint(second)
        assert len(first) == 2

    def test_relative_paths_are_reported_not_absolute_ones(self, tmp_path):
        """An absolute path would change the digest when the image layout moves."""
        (tmp_path / "a.py").write_text("a")

        rel, absolute = package_module_files(str(tmp_path))[0]

        assert rel == "a.py"
        assert absolute.endswith("a.py")
        assert absolute.startswith(str(tmp_path))


class TestReadModuleSources:
    def test_reads_each_body(self, tmp_path):
        module = tmp_path / "a.py"
        module.write_bytes(b"context_window = 8192")

        assert read_module_sources([("a.py", str(module))]) == [
            ("a.py", b"context_window = 8192")
        ]

    def test_an_unreadable_file_is_skipped_not_fatal(self, tmp_path):
        """A scored benchmark must not be lost because one source went missing."""
        present = tmp_path / "present.py"
        present.write_bytes(b"body")

        sources = read_module_sources(
            [("gone.py", str(tmp_path / "absent.py")), ("present.py", str(present))]
        )

        assert sources == [("present.py", b"body")]


class TestSettingsAtPaths:
    """A compact view of the settings a campaign varies."""

    def test_extracts_a_nested_path(self):
        cfg = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}

        assert settings_at_paths(cfg, ["services.chat_app.context_editing"]) == {
            "services.chat_app.context_editing": {"context_window": 8192}
        }

    def test_absent_paths_are_omitted_rather_than_recorded_as_null(self):
        """A missing key and a key set to null are different facts."""
        assert (
            settings_at_paths({"services": {"chat_app": {}}}, ["services.chat_app.x"])
            == {}
        )

    def test_a_path_through_a_non_mapping_is_omitted(self):
        assert settings_at_paths({"services": "nope"}, ["services.chat_app"]) == {}

    def test_the_default_paths_cover_the_context_window_arm(self):
        """The setting the bench-8192 artifact misattributed."""
        assert "services.chat_app.context_editing" in KEY_SETTING_PATHS

    def test_the_default_paths_cover_the_sweep_arm(self):
        """What the fasrc-cannon arms actually varied."""
        assert "services.benchmarking.agent_md_file" in KEY_SETTING_PATHS

    def test_a_non_mapping_config_yields_no_settings(self):
        assert settings_at_paths(None, ["services.chat_app"]) == {}


class TestCodeVersion:
    """The ``code_version`` block written into a report's metadata."""

    def test_records_the_digest_of_the_package(self):
        stamp = code_version(
            sources=[("a.py", b"body")], deploy_git_info={"last_commit": "abc123\n"}
        )

        assert stamp["digest"] == code_fingerprint([("a.py", b"body")])

    def test_records_how_many_files_the_digest_covers(self):
        stamp = code_version(
            sources=[("a.py", b"x"), ("b.py", b"y")], deploy_git_info={}
        )

        assert stamp["file_count"] == 2

    def test_carries_the_deploy_commit_but_marks_it_as_deploy_scoped(self):
        """Every Aug 11-17 run shares 0a157cdce0; the field must say why."""
        stamp = code_version(
            sources=[("a.py", b"x")],
            deploy_git_info={"last_commit": "0a157cdce0\n", "git_diff": ""},
        )

        assert stamp["deploy_git_commit"] == "0a157cdce0"
        assert stamp["deploy_git_dirty"] is False
        assert "archi create" in stamp["deploy_git_note"]

    def test_a_deploy_diff_marks_the_deploy_tree_dirty(self):
        stamp = code_version(
            sources=[("a.py", b"x")],
            deploy_git_info={"last_commit": "abc\n", "git_diff": "--- a/x\n"},
        )

        assert stamp["deploy_git_dirty"] is True

    def test_a_missing_deploy_commit_is_recorded_as_unknown(self):
        assert (
            code_version(sources=[("a.py", b"x")], deploy_git_info=None)[
                "deploy_git_commit"
            ]
            is None
        )

    def test_unavailable_sources_do_not_raise(self):
        """A finished benchmark must not lose its scores over provenance."""
        stamp = code_version(sources=[], deploy_git_info={})

        assert stamp["digest"] is None
        assert "unavailable" in stamp["source"]


class TestCollectCodeVersion:
    def test_digests_the_package_on_disk(self, tmp_path):
        (tmp_path / "a.py").write_text("body")

        stamp = collect_code_version(str(tmp_path), {"last_commit": "abc\n"})

        assert stamp["digest"] == code_fingerprint([("a.py", b"body")])
        assert stamp["file_count"] == 1

    def test_an_empty_package_reports_unavailable(self, tmp_path):
        stamp = collect_code_version(str(tmp_path), {})

        assert stamp["digest"] is None
        assert "unavailable" in stamp["source"]

    def test_a_missing_directory_reports_unavailable_and_keeps_the_commit(self):
        stamp = collect_code_version("/nonexistent/pkg", {"last_commit": "abc\n"})

        assert stamp["digest"] is None
        assert stamp["deploy_git_commit"] == "abc"


class TestConfigVersion:
    """The ``config_version`` block written onto each arm."""

    RUNNING = {
        "services": {
            "chat_app": {"context_editing": {"context_window": 8192}},
            "benchmarking": {"agent_md_file": "/postgres.md"},
        }
    }
    SELECTED = {
        "services": {
            "chat_app": {"context_editing": {"context_window": 32768}},
            "benchmarking": {"agent_md_file": "/arm-v1.md"},
        }
    }

    _DEFAULT = object()

    def _stamp(self, running=_DEFAULT, selected=_DEFAULT):
        """``running=None`` must mean "unavailable", not "use the default"."""
        return config_version(
            running=self.RUNNING if running is self._DEFAULT else running,
            selected=self.SELECTED if selected is self._DEFAULT else selected,
            selected_file="/c.yaml",
        )

    def test_the_digest_covers_the_effective_configuration(self):
        stamp = self._stamp()

        assert stamp["digest"] == config_fingerprint(
            effective_config(self.RUNNING, self.SELECTED)
        )

    def test_the_digest_is_not_the_running_config_alone(self):
        """The finding this fixes: a sweep's arms would collapse to one digest."""
        assert self._stamp()["digest"] != config_fingerprint(self.RUNNING)

    def test_arms_differing_only_in_the_harness_subtree_get_distinct_digests(self):
        arm_a = {"services": {"benchmarking": {"agent_md_file": "/v1.md"}}}
        arm_b = {"services": {"benchmarking": {"agent_md_file": "/v3.md"}}}

        assert (
            self._stamp(selected=arm_a)["digest"]
            != self._stamp(selected=arm_b)["digest"]
        )

    def test_the_selected_file_is_fingerprinted_separately(self):
        stamp = self._stamp()

        assert stamp["selected_file"] == "/c.yaml"
        assert stamp["selected_file_digest"] == config_fingerprint(self.SELECTED)

    def test_names_the_setting_the_artifact_would_have_misattributed(self):
        assert (
            "services.chat_app.context_editing.context_window"
            in self._stamp()["divergence_from_selected_file"]
        )

    def test_surfaces_the_arm_defining_settings(self):
        """A reader must see the arm without parsing a 500-key blob."""
        key = self._stamp()["key_settings"]

        assert key["services.chat_app.context_editing"] == {"context_window": 8192}
        assert key["services.benchmarking.agent_md_file"] == "/arm-v1.md"

    def test_falls_back_to_the_selected_file_when_the_chain_config_is_unavailable(self):
        stamp = self._stamp(running=None)

        assert stamp["digest"] == config_fingerprint(self.SELECTED)
        assert "unavailable" in stamp["source"]

    def test_divergence_is_unknown_when_the_chain_config_is_unavailable(self):
        """Not [] -- an empty list would assert the two agreed."""
        assert self._stamp(running=None)["divergence_from_selected_file"] is None

    def test_the_effective_configuration_is_the_named_source(self):
        assert "effective" in self._stamp()["source"]
