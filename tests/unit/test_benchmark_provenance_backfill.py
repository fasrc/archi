"""Version blocks for artifacts written before version stamping existed.

The 18 reports already in ``bench_out/`` cannot be re-run, and what they did not
record cannot be recovered. A backfill therefore has one hard rule: give the
artifact the *identity* it lacked, and refuse to claim the facts it never held.

Concretely, for a 2026-08-11 or 2026-08-17 artifact:

* the code version is unrecoverable -- ``git_info.last_commit`` is the deploy's
  commit, shared by every run in both windows, so it cannot be promoted to
  "the code this run used";
* the configuration *can* be fingerprinted, because the file was recorded -- but
  it is the selected file, and ``bench-8192-20260817_170850.json`` proves the
  file can disagree with what the agent ran. The digest is real; the claim that
  it describes the run is not.
"""

from src.utils.benchmark_provenance import config_fingerprint, reconstruct_version_stamp

GIT_INFO = {"last_commit": "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9\n", "git_diff": ""}
RECORDED_CONFIG = {
    "services": {"chat_app": {"context_editing": {"context_window": 32768, "keep": 1}}}
}


def _stamp(metadata=None, config=RECORDED_CONFIG, path="/root/archi/config.yaml"):
    return reconstruct_version_stamp(
        metadata if metadata is not None else {"git_info": GIT_INFO},
        recorded_config=config,
        configuration_file=path,
    )


class TestReconstructedCodeVersion:
    def test_the_code_digest_is_absent_because_it_was_never_recorded(self):
        assert _stamp()["code_version"]["digest"] is None

    def test_the_source_says_it_was_not_recorded(self):
        assert "not recorded" in _stamp()["code_version"]["source"].lower()

    def test_the_deploy_commit_is_carried_over_with_its_caveat(self):
        code = _stamp()["code_version"]

        assert code["deploy_git_commit"] == "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9"
        assert "archi create" in code["deploy_git_note"]

    def test_the_deploy_commit_is_never_promoted_to_the_digest(self):
        """The whole defect: this value is identical across every run."""
        code = _stamp()["code_version"]

        assert code["digest"] != code["deploy_git_commit"]
        assert code["digest"] is None

    def test_a_recorded_diff_marks_the_deploy_tree_dirty(self):
        stamp = _stamp(metadata={"git_info": dict(GIT_INFO, git_diff="--- a\n+++ b\n")})

        assert stamp["code_version"]["deploy_git_dirty"] is True

    def test_missing_git_info_yields_no_commit(self):
        assert _stamp(metadata={})["code_version"]["deploy_git_commit"] is None


class TestReconstructedConfigVersion:
    def test_the_recorded_configuration_is_fingerprinted(self):
        config = _stamp()["config_version"]

        assert config["digest"] == config_fingerprint(RECORDED_CONFIG)

    def test_the_digest_is_labelled_as_the_selected_file_not_the_run(self):
        """bench-8192 recorded 32768; the label must not claim otherwise."""
        source = _stamp()["config_version"]["source"].lower()

        assert "reconstructed" in source
        assert "may not describe the run" in source

    def test_divergence_is_unknown_rather_than_empty(self):
        """An empty list would assert the file and the run agreed. Nobody knows."""
        assert _stamp()["config_version"]["divergence_from_selected_file"] is None

    def test_the_selected_file_path_is_carried_over(self):
        assert _stamp()["config_version"]["selected_file"] == "/root/archi/config.yaml"

    def test_the_arm_settings_are_surfaced_from_what_was_recorded(self):
        key = _stamp()["config_version"]["key_settings"]

        assert key["services.chat_app.context_editing"] == {
            "context_window": 32768,
            "keep": 1,
        }

    def test_the_two_context_window_arms_reconstruct_to_different_digests(self):
        arm_a = _stamp(
            config={
                "services": {"chat_app": {"context_editing": {"context_window": 8192}}}
            }
        )
        arm_b = _stamp(
            config={
                "services": {"chat_app": {"context_editing": {"context_window": 32768}}}
            }
        )

        assert arm_a["config_version"]["digest"] != arm_b["config_version"]["digest"]

    def test_an_artifact_with_no_recorded_config_gets_no_digest(self):
        config = _stamp(config=None)["config_version"]

        assert config["digest"] is None
        assert "not recorded" in config["source"].lower()


class TestBackfillIsAdditive:
    def test_existing_metadata_keys_are_not_part_of_the_stamp(self):
        """The caller merges; the stamp must not carry keys that would overwrite."""
        assert set(_stamp()) == {"code_version", "config_version"}
