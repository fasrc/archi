"""The judge queue: match rule, claims, stale-claim recovery, budget (#732).

Tasks 3.0, 3.1, 3.1a and the budget half of 3.2 of
openspec/changes/offload-ragas-judge-to-slurm. The module runs on a login node
with the standard library only, so the tests load it by path, as the submitter
does, and replace ``squeue`` and ``sacct`` with stub executables on ``PATH``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
QUEUE_PATH = REPO / "scripts" / "benchmarking" / "judge" / "judge_queue.py"


def load_queue():
    spec = importlib.util.spec_from_file_location("judge_queue_under_test", QUEUE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def jq():
    return load_queue()


_SQUEUE = """#!/bin/sh
id=""; fmt=""
while [ $# -gt 0 ]; do
  case "$1" in
    -j) id="$2"; shift 2 ;;
    -o) fmt="$2"; shift 2 ;;
    *) shift ;;
  esac
done
f="$STUB_SLURM/squeue.$id"
if [ ! -f "$f" ]; then
  echo "slurm_load_jobs error: Invalid job id specified" >&2
  exit 1
fi
if [ "$fmt" = "%L" ]; then cat "$STUB_SLURM/timeleft.$id"; else echo "$id"; fi
"""

_SACCT = """#!/bin/sh
id=""
while [ $# -gt 0 ]; do
  case "$1" in
    -j) id="$2"; shift 2 ;;
    *) shift ;;
  esac
done
f="$STUB_SLURM/sacct.$id"
[ -f "$f" ] && cat "$f"
exit 0
"""


class Slurm:
    """Stub ``squeue`` and ``sacct`` state, one file per job id."""

    def __init__(self, folder: Path):
        self.folder = folder

    def running(self, job_id, time_left="2:00:00"):
        (self.folder / f"squeue.{job_id}").write_text(f"{job_id}\n")
        (self.folder / f"timeleft.{job_id}").write_text(f"{time_left}\n")
        (self.folder / f"sacct.{job_id}").write_text("RUNNING\n")

    def ended(self, job_id, state="TIMEOUT"):
        (self.folder / f"squeue.{job_id}").unlink(missing_ok=True)
        (self.folder / f"sacct.{job_id}").write_text(f"{state}\n")


@pytest.fixture
def slurm(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    state = tmp_path / "slurm"
    bindir.mkdir()
    state.mkdir()
    for name, body in (("squeue", _SQUEUE), ("sacct", _SACCT)):
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_SLURM", str(state))
    return Slurm(state)


def make_bundle(
    queue: Path,
    name="run-20261023_120000-arm0",
    *,
    model="meta-llama/Llama-3.3-70B-Instruct",
    revision="rev1",
    code="sha256:code",
    package="sha256:pkg",
    rows=3,
    ready=True,
    mtime=None,
):
    folder = queue / name
    folder.mkdir(parents=True)
    manifest = {
        "result_basename": name.rsplit("-arm", 1)[0],
        "arm_index": int(name.rsplit("-arm", 1)[1]),
        "arm_count": 1,
        "row_count": rows,
        "judge_model": {"id": model, "revision": revision},
        "code_version": {"digest": code},
        "package_digest": package,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    if ready:
        (folder / "READY").write_bytes(b"")
        if mtime is not None:
            os.utime(folder / "READY", (mtime, mtime))
    return folder


def write_judge_env(folder: Path, **overrides):
    values = {
        "JUDGE_MODEL_ID": "meta-llama/Llama-3.3-70B-Instruct",
        "JUDGE_MODEL_REVISION": "rev1",
        "JUDGE_PRECISION": "bf16",
        "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE": "2",
    }
    values.update(overrides)
    path = folder / "judge.env"
    path.write_text(
        "# judge settings\n"
        + "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
    )
    return path


def write_sidecar(folder: Path, code="sha256:code", package="sha256:pkg", name=None):
    path = folder / (name or "scorer-identity.json")
    path.write_text(
        json.dumps({"code_digest": code, "package_digest": package}, sort_keys=True)
    )
    return path


@pytest.fixture
def setup(tmp_path, jq):
    queue = tmp_path / "queue"
    queue.mkdir()
    env = jq.read_judge_env(write_judge_env(tmp_path))
    sidecar = jq.read_sidecar(write_sidecar(tmp_path))
    return queue, env, sidecar


# --- judge.env --------------------------------------------------------------


def test_judge_env_reads_key_value_lines(tmp_path, jq):
    path = tmp_path / "judge.env"
    path.write_text(
        "# comment\n\n"
        "JUDGE_MODEL_ID=meta-llama/Llama-3.3-70B-Instruct\n"
        'JUDGE_MODEL_REVISION="abc123"\n'
        "JUDGE_PRECISION='bf16'\n"
        "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE=1.5\n"
        "JUDGE_SERVER_ARGS=--max-model-len 32768\n"
    )
    env = jq.read_judge_env(path)
    assert env["JUDGE_MODEL_ID"] == "meta-llama/Llama-3.3-70B-Instruct"
    assert env["JUDGE_MODEL_REVISION"] == "abc123"
    assert env["JUDGE_PRECISION"] == "bf16"
    assert env["JUDGE_BOOTSTRAP_ROWS_PER_MINUTE"] == "1.5"
    assert env["JUDGE_SERVER_ARGS"] == "--max-model-len 32768"
    assert env["JUDGE_IMAGE_DIGEST"] is None


def test_judge_env_names_a_missing_required_key(tmp_path, jq):
    path = write_judge_env(tmp_path, JUDGE_PRECISION=None)
    with pytest.raises(ValueError, match="JUDGE_PRECISION"):
        jq.read_judge_env(path)


@pytest.mark.parametrize("rate", ["0", "-1", "fast", "nan"])
def test_judge_env_refuses_a_bootstrap_rate_that_is_not_positive(tmp_path, jq, rate):
    path = write_judge_env(tmp_path, JUDGE_BOOTSTRAP_ROWS_PER_MINUTE=rate)
    with pytest.raises(ValueError, match="JUDGE_BOOTSTRAP_ROWS_PER_MINUTE"):
        jq.read_judge_env(path)


def test_judge_env_names_a_line_without_an_equals_sign(tmp_path, jq):
    path = tmp_path / "judge.env"
    path.write_text("JUDGE_MODEL_ID=m\nnot a setting\n")
    with pytest.raises(ValueError, match="line 2"):
        jq.read_judge_env(path)


# --- scorer sidecar -----------------------------------------------------------


def test_the_sidecar_digest_is_the_sha256_of_its_bytes(tmp_path, jq):
    path = write_sidecar(tmp_path)
    sidecar = jq.read_sidecar(path)
    hexdigest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sidecar.digest == f"sha256:{hexdigest}"
    assert sidecar.token == hexdigest
    assert sidecar.identity["code_digest"] == "sha256:code"


def test_a_sidecar_without_both_digests_is_refused(tmp_path, jq):
    path = tmp_path / "scorer-identity.json"
    path.write_text(json.dumps({"code_digest": "sha256:code"}))
    with pytest.raises(ValueError, match="package_digest"):
        jq.read_sidecar(path)


# --- 3.0 match rule -----------------------------------------------------------


def test_a_bundle_for_this_judge_and_scorer_matches(setup, jq):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    assert jq.match_reason(bundle, env, sidecar, queue) is None


@pytest.mark.parametrize(
    "overrides",
    [{"model": "deepseek-ai/R1-Distill-Llama-70B"}, {"revision": "rev2"}],
)
def test_a_bundle_for_another_judge_names_the_judge_it_waits_for(setup, jq, overrides):
    queue, env, sidecar = setup
    bundle = make_bundle(queue, **overrides)
    reason = jq.match_reason(bundle, env, sidecar, queue)
    model = overrides.get("model", "meta-llama/Llama-3.3-70B-Instruct")
    assert f"{model}@{overrides.get('revision', 'rev1')}" in reason
    assert "meta-llama/Llama-3.3-70B-Instruct@rev1" in reason


@pytest.mark.parametrize(
    "overrides", [{"code": "sha256:other"}, {"package": "sha256:otherpkg"}]
)
def test_a_scorer_mismatch_names_both_scorer_values(setup, jq, overrides):
    queue, env, sidecar = setup
    bundle = make_bundle(queue, **overrides)
    reason = jq.match_reason(bundle, env, sidecar, queue)
    for value in (
        overrides.get("code", "sha256:code"),
        overrides.get("package", "sha256:pkg"),
        "sha256:code",
        "sha256:pkg",
    ):
        assert value in reason


def test_a_stale_sidecar_excludes_every_bundle_until_the_sidecar_changes(
    tmp_path, setup, jq
):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    (queue / f"SCORER_STALE.{sidecar.token}").write_text("live differs")
    assert "SCORER_STALE" in jq.match_reason(bundle, env, sidecar, queue)
    # A rebuilt scorer with the same digests writes a sidecar with other bytes
    # (here, one more newline), so its digest differs.
    path = write_sidecar(tmp_path, name="rebuilt.json")
    path.write_bytes(path.read_bytes() + b"\n")
    rebuilt = jq.read_sidecar(path)
    assert rebuilt.token != sidecar.token
    assert jq.match_reason(bundle, env, rebuilt, queue) is None


def test_an_incompatible_record_excludes_only_that_bundle_for_that_sidecar(setup, jq):
    queue, env, sidecar = setup
    bad = make_bundle(queue, "run-20261023_120000-arm0")
    good = make_bundle(queue, "run-20261023_130000-arm0")
    (bad / f"INCOMPATIBLE.{sidecar.token}").write_text("live differs")
    assert "INCOMPATIBLE" in jq.match_reason(bad, env, sidecar, queue)
    assert jq.match_reason(good, env, sidecar, queue) is None


def test_an_unreadable_manifest_does_not_match(setup, jq):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    (bundle / "manifest.json").write_text("{not json")
    assert "manifest.json" in jq.match_reason(bundle, env, sidecar, queue)


# --- 3.1 claims ---------------------------------------------------------------


def test_available_takes_ready_matching_unclaimed_bundles_oldest_first(
    setup, jq, slurm
):
    queue, env, sidecar = setup
    newer = make_bundle(queue, "b-20261023_120000-arm0", mtime=2_000)
    older = make_bundle(queue, "a-20261023_120000-arm0", mtime=1_000)
    make_bundle(queue, "c-20261023_120000-arm0", revision="other")
    make_bundle(queue, "d-20261023_120000-arm0", ready=False)
    scored = make_bundle(queue, "e-20261023_120000-arm0")
    (scored / "SCORED").write_bytes(b"")
    failed = make_bundle(queue, "f-20261023_120000-arm0")
    (failed / "FAILED").write_text("digest mismatch")
    assert jq.available(queue, env, sidecar) == [older, newer]


def test_a_claim_holds_the_job_id(setup, jq, slurm):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    assert jq.try_claim(bundle, "1001") is True
    assert (bundle / "claim.1").read_text() == "1001\n"
    slurm.running("1001")
    assert jq.available(queue, env, sidecar) == []


def test_exactly_one_of_two_racing_claimants_wins(setup, jq, monkeypatch):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    monkeypatch.setattr(jq, "current_claim", lambda folder: None)
    results = [jq.try_claim(bundle, "1001"), jq.try_claim(bundle, "1002")]
    assert results == [True, False]
    assert (bundle / "claim.1").read_text() == "1001\n"


def test_the_claim_name_never_exists_without_the_job_id(setup, jq, monkeypatch):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    seen = []
    real_link = os.link

    def spy_link(src, dst):
        seen.append(Path(src).read_text())
        real_link(src, dst)

    monkeypatch.setattr(os, "link", spy_link)
    jq.try_claim(bundle, "1001")
    assert seen == ["1001\n"]


# --- 3.1a stale-claim recovery ------------------------------------------------


def test_a_claim_of_a_timed_out_job_is_taken_over(setup, jq, slurm):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.ended("1001", "TIMEOUT")
    assert jq.available(queue, env, sidecar) == [bundle]
    assert jq.try_claim(bundle, "2002") is True
    assert (bundle / "claim.2").read_text() == "2002\n"
    assert (bundle / "claim.1").read_text() == "1001\n"


def test_a_claim_of_a_running_job_is_not_taken(setup, jq, slurm):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.running("1001")
    assert jq.try_claim(bundle, "2002") is False
    assert not (bundle / "claim.2").exists()


def test_a_job_missing_from_squeue_but_not_terminal_in_sacct_is_not_stale(
    setup, jq, slurm
):
    """A short squeue gap: the job is gone from squeue, sacct still says RUNNING."""
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.ended("1001", "RUNNING")
    assert jq.try_claim(bundle, "2002") is False


def test_two_jobs_racing_for_one_stale_claim_make_one_takeover(
    setup, jq, slurm, monkeypatch
):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.ended("1001", "CANCELLED by 42")
    stale = jq.current_claim(bundle)
    monkeypatch.setattr(jq, "current_claim", lambda folder: stale)
    results = [jq.try_claim(bundle, "2002"), jq.try_claim(bundle, "3003")]
    assert results == [True, False]
    assert (bundle / "claim.2").read_text() == "2002\n"


def test_a_slow_job_cannot_replace_a_newer_claim(setup, jq, slurm, monkeypatch):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.ended("1001")
    read_by_slow_job = jq.current_claim(bundle)
    assert jq.try_claim(bundle, "2002") is True
    slurm.running("2002")
    monkeypatch.setattr(jq, "current_claim", lambda folder: read_by_slow_job)
    assert jq.try_claim(bundle, "3003") is False
    assert (bundle / "claim.2").read_text() == "2002\n"
    assert sorted(p.name for p in bundle.glob("claim.*")) == ["claim.1", "claim.2"]


def test_no_takeover_removes_or_renames_a_claim_file(setup, jq, slurm):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.ended("1001")
    jq.try_claim(bundle, "2002")
    slurm.ended("2002", "NODE_FAIL")
    jq.try_claim(bundle, "3003")
    claims = {p.name: p.read_text() for p in bundle.glob("claim.*")}
    assert claims == {"claim.1": "1001\n", "claim.2": "2002\n", "claim.3": "3003\n"}
    assert jq.current_claim(bundle) == (3, "3003")


def test_a_requeued_job_keeps_and_resumes_its_claim(setup, jq, slurm):
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    slurm.running("1001")
    assert jq.own_work(queue, "1001") == [bundle]
    assert jq.available(queue, env, sidecar) == []
    assert jq.try_claim(bundle, "1001") is True
    assert sorted(p.name for p in bundle.glob("claim.*")) == ["claim.1"]


def test_own_work_skips_a_bundle_with_a_terminal_marker(setup, jq, slurm):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    (bundle / "SCORED").write_bytes(b"")
    assert jq.own_work(queue, "1001") == []


def test_without_slurm_tools_a_claim_is_never_stale(setup, jq, monkeypatch, tmp_path):
    queue, _, _ = setup
    bundle = make_bundle(queue)
    jq.try_claim(bundle, "1001")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    assert jq.try_claim(bundle, "2002") is False


def test_a_claim_with_no_job_id_is_stale(setup, jq, slurm):
    """Only a manual edit makes one; no owner can be checked, so it is taken."""
    queue, env, sidecar = setup
    bundle = make_bundle(queue)
    (bundle / "claim.1").write_text("\n")
    assert jq.available(queue, env, sidecar) == [bundle]
    assert jq.try_claim(bundle, "2002") is True


# --- status -------------------------------------------------------------------


def test_status_names_every_state(tmp_path, setup, jq, slurm, capsys):
    queue, env, sidecar = setup
    make_bundle(queue, "a-20261023_120000-arm0")
    claimed = make_bundle(queue, "b-20261023_120000-arm0")
    jq.try_claim(claimed, "1001")
    slurm.running("1001")
    stale = make_bundle(queue, "c-20261023_120000-arm0")
    jq.try_claim(stale, "1002")
    slurm.ended("1002")
    make_bundle(queue, "d-20261023_120000-arm0", package="sha256:otherpkg")
    incompatible = make_bundle(queue, "e-20261023_120000-arm0")
    (incompatible / f"INCOMPATIBLE.{sidecar.token}").write_text("live has pkg X")
    (queue / "SCORER_STALE.0123").write_text("sidecar old")

    lines = jq.status_lines(queue, env, sidecar)
    text = "\n".join(lines)
    assert "a-20261023_120000-arm0: available" in text
    assert "b-20261023_120000-arm0: claimed by job 1001" in text
    assert "c-20261023_120000-arm0: stale claim of job 1002" in text
    assert "sha256:otherpkg" in text and "sha256:pkg" in text
    assert "INCOMPATIBLE" in text and "live has pkg X" in text
    assert "SCORER_STALE.0123: sidecar old" in text

    argv = [
        "--queue",
        str(queue),
        "--judge-env",
        str(tmp_path / "judge.env"),
        "--sidecar",
        str(tmp_path / "scorer-identity.json"),
    ]
    assert jq.main(argv + ["--status"]) == 0
    assert "a-20261023_120000-arm0: available" in capsys.readouterr().out
    assert jq.main(argv + ["--count"]) == 0
    assert capsys.readouterr().out.strip() == "2"


# --- 3.2 time budget ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("1-02:03:04", 93784),
        ("02:03:04", 7384),
        ("03:04", 184),
        ("UNLIMITED", math.inf),
        ("NOT_SET", None),
        ("INVALID", None),
        ("", None),
    ],
)
def test_time_left_parses_the_squeue_format(jq, text, seconds):
    """An unlimited allocation is infinite time; anything else unreadable is unknown."""
    assert jq.parse_time_left(text) == seconds


def test_time_left_asks_squeue_for_the_job(jq, slurm):
    slurm.running("1001", time_left="1:30:00")
    assert jq.time_left("1001") == 5400
    assert jq.time_left("9999") is None
    assert jq.time_left(None) is None


def test_the_first_bundle_is_always_claimed(jq):
    picked = jq.plan_claims(
        [("a", 109), ("b", 109), ("c", 109)],
        seconds_left=60 * 60,
        rows_per_minute=2,
        first_is_free=True,
    )
    assert picked == ["a"]


def test_bundles_are_claimed_while_the_rate_says_they_fit(jq):
    picked = jq.plan_claims(
        [("a", 109), ("b", 109), ("c", 600), ("d", 109)],
        seconds_left=60 * 60,
        rows_per_minute=10,
        first_is_free=False,
    )
    assert picked == ["a", "b", "d"]


def test_a_bundle_that_needs_more_time_than_is_left_stays_ready(jq):
    picked = jq.plan_claims(
        [("a", 300)],
        seconds_left=10 * 60,
        rows_per_minute=10,
        first_is_free=False,
    )
    assert picked == []


def test_an_unlimited_job_claims_everything(jq):
    picked = jq.plan_claims(
        [("a", 10_000), ("b", 10_000)],
        seconds_left=math.inf,
        rows_per_minute=1,
        first_is_free=False,
    )
    assert picked == ["a", "b"]


@pytest.mark.parametrize("first_is_free", [True, False])
def test_an_unknown_time_left_claims_one_bundle_per_round(jq, first_is_free):
    """A failed squeue query is not an unlimited allocation: the job takes one
    bundle, scores it, and asks again."""
    picked = jq.plan_claims(
        [("a", 10), ("b", 10), ("c", 10)],
        seconds_left=None,
        rows_per_minute=1_000,
        first_is_free=first_is_free,
    )
    assert picked == ["a"]


def test_a_failed_time_query_is_unknown_not_unlimited(jq, slurm):
    (slurm.folder / "squeue.1001").write_text("1001\n")
    (slurm.folder / "timeleft.1001").write_text("")
    assert jq.time_left("1001") is None
    slurm.running("1002", time_left="UNLIMITED")
    assert jq.time_left("1002") == math.inf


def test_the_module_imports_nothing_outside_the_standard_library():
    """It runs on a login node with no project environment (body of #732)."""
    source = QUEUE_PATH.read_text()
    stdlib = set(sys.stdlib_module_names)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            top = stripped.split()[1].split(".")[0]
            if top == "__future__":
                continue
            assert top in stdlib, line


# --- 4.1: what the submitter reads (#733) -----------------------------------------


def test_status_flags_a_ready_bundle_older_than_24_hours(setup, jq):
    queue, env, sidecar = setup
    import time

    make_bundle(queue, "old-20261023_120000-arm0", mtime=time.time() - 30 * 3600)
    make_bundle(queue, "new-20261023_120000-arm0")
    lines = jq.status_lines(queue, env, sidecar)
    old = [line for line in lines if line.startswith("old-")]
    new = [line for line in lines if line.startswith("new-")]
    assert "READY for 30 hours" in old[0]
    assert "READY for" not in new[0]


def test_status_lists_incompatible_records_of_other_sidecars(setup, jq):
    queue, env, sidecar = setup
    bundle = make_bundle(queue, "x-20261023_120000-arm0")
    (bundle / "INCOMPATIBLE.0ld").write_text("an older scorer: live pkg Y")
    text = "\n".join(jq.status_lines(queue, env, sidecar))
    assert (
        "x-20261023_120000-arm0: INCOMPATIBLE.0ld: an older scorer: live pkg Y" in text
    )


def _result(queue, basename, arms, *, scored=(), merged=(), failed=(), pending=True):
    """Bundles of one result with markers, and its pending JSON when *pending*."""
    for index in range(arms):
        folder = make_bundle(queue, f"{basename}-arm{index}")
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["arm_count"] = arms
        (folder / "manifest.json").write_text(json.dumps(manifest))
        for marker, indices in (
            ("SCORED", scored),
            ("MERGED", merged),
            ("FAILED", failed),
        ):
            if index in indices:
                (folder / marker).write_text("")
    if pending:
        (queue / "results").mkdir(exist_ok=True)
        (queue / "results" / f"{basename}.json").write_text("{}")


def test_merge_ready_names_results_whose_arms_are_all_scored(setup, jq):
    queue = setup[0]
    _result(queue, "ready-20261023_120000", 2, scored=(0, 1))
    _result(queue, "half-20261023_120000", 2, scored=(0,))
    _result(queue, "merged-20261023_120000", 2, scored=(0, 1), merged=(0, 1))
    _result(queue, "unpublished-20261023_120000", 1, scored=(0,), pending=False)
    _result(queue, "failed-20261023_120000", 2, scored=(0,), failed=(1,))
    assert jq.merge_ready(queue) == ["ready-20261023_120000"]


def test_a_result_with_one_arm_not_merged_is_ready_again(setup, jq):
    # A merge killed between two MERGED writes; the merge is idempotent.
    queue = setup[0]
    _result(queue, "r-20261023_120000", 2, scored=(0, 1), merged=(0,))
    assert jq.merge_ready(queue) == ["r-20261023_120000"]


def test_the_merge_ready_cli_needs_no_judge_env_or_sidecar(setup, jq, capsys):
    queue = setup[0]
    _result(queue, "ready-20261023_120000", 1, scored=(0,))
    assert jq.main(["--queue", str(queue), "--merge-ready"]) == 0
    assert capsys.readouterr().out.split() == ["ready-20261023_120000"]


def test_status_and_count_still_need_the_judge_env_and_sidecar(setup, jq, capsys):
    queue = setup[0]
    assert jq.main(["--queue", str(queue), "--count"]) == 2
    assert "--judge-env" in capsys.readouterr().err
