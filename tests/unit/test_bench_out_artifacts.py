import json
import math
import re
from pathlib import Path

import pytest

_BENCH_OUT_DIR = Path(__file__).resolve().parents[2] / "bench_out"


def _require_bench_out():
    """Skip a check that reads the committed directory when it is absent.

    Scoped to the tests that need the directory rather than applied to the
    module: glob() on a missing directory yields nothing, so those checks would
    pass over zero files and report a false green. The scanner's own tests build
    their input under tmp_path and must still run in a checkout without it.
    """
    if not _BENCH_OUT_DIR.is_dir():
        pytest.skip("bench_out directory absent")


def _require_nonempty(found, directory, what):
    """Refuse to report green on a check that examined nothing.

    ``_require_bench_out`` guards the directory's existence; this guards its
    *contents*. A present-but-empty ``bench_out/`` makes the checks below pass
    over zero files, and the spec requires them to run "over every artifact
    committed under ``bench_out/``" -- which enforcing nothing satisfies
    vacuously. The value of this change is recurrence prevention, so a guard
    that guarded nothing has to say so.
    """
    assert found, (
        f"examined zero {what} under {directory}: the checks here would report "
        f"green having verified nothing. Either the committed artifacts are "
        f"missing, or the glob no longer matches them."
    )


def _raise_on_constant(val):
    raise ValueError(f"non-JSON constant: {val!r}")


def _is_artifact(data):
    return isinstance(data, dict) and isinstance(data.get("benchmarking_results"), list)


def _scan_bench_out(directory):
    """One pass over ``directory``: ``(artifacts, unparseable)``.

    A file that will not parse is *reported*, never skipped. Dropping it would
    let a truncated or trailing-comma artifact clear both checks below by never
    being examined, which is the opposite of what the spec asks for -- the
    checks run "over every artifact committed under bench_out/". A document that
    parses but is not an artifact is a different thing and is skipped.
    """
    artifacts = []
    unparseable = []
    for path in sorted(Path(directory).glob("*.json")):
        # The read is inside the guard too: undecodable bytes and an unreadable
        # path are the same fact to a caller -- a committed *.json this suite
        # cannot check -- and both must name the file rather than raise out of
        # the fixture and error every test that depends on it.
        try:
            text = path.read_text()
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            unparseable.append((path, str(exc)))
            continue
        if not _is_artifact(data):
            continue
        artifacts.append((path, text, data))
    return artifacts, unparseable


@pytest.fixture(scope="module")
def bench_out_scan():
    _require_bench_out()
    return _scan_bench_out(_BENCH_OUT_DIR)


@pytest.fixture(scope="module")
def bench_out_artifacts(bench_out_scan):
    return bench_out_scan[0]


def test_all_artifacts_are_strict_json(bench_out_artifacts):
    _require_nonempty(bench_out_artifacts, _BENCH_OUT_DIR, "artifacts")
    bad = []
    for path, text, _ in bench_out_artifacts:
        try:
            json.loads(text, parse_constant=_raise_on_constant)
        except ValueError:
            bad.append(path.name)
    assert (
        not bad
    ), f"{len(bad)} file(s) contain non-JSON constants (NaN/Infinity): {bad}"


def test_reports_contain_no_nan():
    _require_bench_out()
    patterns = ["*_report.md", "*_report.html"]
    bad = []
    reports = []
    nan_re = re.compile(r"\bnan\b")
    for pattern in patterns:
        for path in sorted(_BENCH_OUT_DIR.glob(pattern)):
            reports.append(path)
            text = path.read_text(errors="replace")
            if nan_re.search(text):
                bad.append(path.name)
    _require_nonempty(reports, _BENCH_OUT_DIR, "rendered reports")
    assert not bad, f"{len(bad)} report(s) contain \\bnan\\b: {bad}"


def test_scored_strings_match_finite_counts(bench_out_artifacts):
    _require_nonempty(bench_out_artifacts, _BENCH_OUT_DIR, "artifacts")
    bad = []
    for path, _, data in bench_out_artifacts:
        for arm in data["benchmarking_results"]:
            sqr = arm.get("single_question_results", {})
            if not isinstance(sqr, dict):
                continue
            tr = arm.get("total_results", {})
            total = sum(1 for q in sqr.values() if q.get("status", "ok") == "ok")
            for key in tr:
                if not key.endswith("_scored") or key == "source_scored_count":
                    continue
                metric = key[: -len("_scored")]
                finite = sum(
                    1
                    for q in sqr.values()
                    if q.get("status", "ok") == "ok"
                    and isinstance(q.get(metric), (int, float))
                    and not isinstance(q.get(metric), bool)
                    and math.isfinite(q[metric])
                )
                expected = f"{finite} of {total}"
                if tr[key] != expected:
                    bad.append((path.name, key, tr[key], expected))
    assert (
        not bad
    ), f"{len(bad)} scored string(s) do not match finite counts:\n" + "\n".join(
        f"  {name} {key}: {actual!r} != {expected!r}"
        for name, key, actual, expected in bad
    )


def test_a_json_file_that_will_not_parse_is_reported_not_skipped(tmp_path):
    """A truncated or trailing-comma artifact must fail, not vanish from the set.

    The spec requires the regression test to run "over every artifact committed
    under bench_out/". A scan that drops an unparseable file lets it clear both
    the strict-JSON check and the denominator check by never being examined --
    the one failure mode a committed artifact is most likely to arrive in.
    """
    (tmp_path / "truncated.json").write_text('{"benchmarking_results": [')
    (tmp_path / "artifact.json").write_text('{"benchmarking_results": []}')
    (tmp_path / "not_an_artifact.json").write_text('{"other": 1}')

    artifacts, unparseable = _scan_bench_out(tmp_path)

    assert [path.name for path, _ in unparseable] == ["truncated.json"]
    assert [path.name for path, _, _ in artifacts] == ["artifact.json"]


def test_every_committed_json_file_parses(bench_out_scan):
    """The guard the scan above exists to make possible."""
    _, unparseable = bench_out_scan

    assert (
        not unparseable
    ), f"{len(unparseable)} file(s) are not valid JSON:\n" + "\n".join(
        f"  {path.name}: {reason}" for path, reason in unparseable
    )


def test_a_json_file_that_will_not_even_decode_is_reported_not_raised(tmp_path):
    """Undecodable bytes are a read failure, not a parse failure, and escaped.

    read_text() sat outside the handler, so a file holding a stray 0xff raised
    UnicodeDecodeError out of the fixture and errored every test that depends
    on it, instead of naming the one bad file. It failed closed, but it named
    nothing -- and the helper's contract is that a file it cannot use is
    reported.
    """
    (tmp_path / "artifact.json").write_text('{"benchmarking_results": []}')
    (tmp_path / "binary.json").write_bytes(b'{"benchmarking_results": [\xff]}')

    artifacts, unparseable = _scan_bench_out(tmp_path)

    assert [path.name for path, _ in unparseable] == ["binary.json"]
    assert [path.name for path, _, _ in artifacts] == ["artifact.json"]


def test_an_empty_set_is_a_false_green_not_a_clean_one(tmp_path):
    """A check that examined nothing has not passed, and must not report green.

    `_require_bench_out` guards only the directory's *existence*, and its own
    docstring names the hazard it leaves open: "glob() on a missing directory
    yields nothing, so those checks would pass over zero files and report a
    false green." A present-but-empty `bench_out/` reaches exactly that state --
    `test_all_artifacts_are_strict_json`, `test_scored_strings_match_finite_counts`
    and `test_reports_contain_no_nan` all pass having examined zero files.

    The spec is explicit that this is not enough: the regression test asserts
    "over **every** artifact committed under `bench_out/`". Enforcing nothing
    satisfies that vacuously, and this change's whole value is recurrence
    prevention, so the guard has to say when it guarded nothing.
    """
    with pytest.raises(AssertionError) as excinfo:
        _require_nonempty([], tmp_path, "artifacts")

    assert "zero" in str(excinfo.value), "the failure must say it examined nothing"
    assert str(tmp_path) in str(
        excinfo.value
    ), "and must name the directory it found empty"

    # A non-empty set is the normal case and must pass through untouched.
    _require_nonempty(["something"], tmp_path, "artifacts")


def test_the_committed_directory_is_not_empty(bench_out_artifacts):
    """The live assertion the helper above exists to make.

    Measured 2026-09-07: 18 `bench_out/*.json` files, all 18 of them artifacts.
    """
    _require_nonempty(bench_out_artifacts, _BENCH_OUT_DIR, "artifacts")
