"""Codex review of fasrc/archi#793 on judge_trial.sbatch: a TRIAL_BUNDLES pattern must be
matched under the bundles folder only, never expanded in the submit directory."""

from __future__ import annotations

from tests.unit.test_judge_trial_sbatch_dryrun import _dry_run, _package, _step4


def test_a_pattern_is_not_expanded_against_the_package_root(tmp_path):
    pkg = _package(tmp_path)
    (pkg / "reference").mkdir()  # "r*" would match this in the package root
    proc = _dry_run(pkg, TRIAL_BUNDLES="r*")
    assert proc.returncode == 0, proc.stderr
    step4 = _step4(proc)
    assert "2 bundle(s)" in step4 and "r1-arm0 r2-arm0" in step4


def test_a_star_selects_every_complete_bundle(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES="*")
    assert proc.returncode == 0, proc.stderr
    assert "3 bundle(s)" in _step4(proc)


def test_commas_and_spaces_both_separate_patterns(tmp_path):
    pkg = _package(tmp_path)
    proc = _dry_run(pkg, TRIAL_BUNDLES="r1-arm0,control-*  r2-arm0")
    assert proc.returncode == 0, proc.stderr
    assert "3 bundle(s)" in _step4(proc)
