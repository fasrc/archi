import json
import math
import re
from pathlib import Path

import pytest

_BENCH_OUT_DIR = Path(__file__).resolve().parents[2] / "bench_out"

if not _BENCH_OUT_DIR.is_dir():
    pytest.skip("bench_out directory absent", allow_module_level=True)


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
        text = path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            unparseable.append((path, str(exc)))
            continue
        if not _is_artifact(data):
            continue
        artifacts.append((path, text, data))
    return artifacts, unparseable


@pytest.fixture(scope="module")
def bench_out_scan():
    return _scan_bench_out(_BENCH_OUT_DIR)


@pytest.fixture(scope="module")
def bench_out_artifacts(bench_out_scan):
    return bench_out_scan[0]


def test_all_artifacts_are_strict_json(bench_out_artifacts):
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
    patterns = ["*_report.md", "*_report.html"]
    bad = []
    nan_re = re.compile(r"\bnan\b")
    for pattern in patterns:
        for path in sorted(_BENCH_OUT_DIR.glob(pattern)):
            text = path.read_text(errors="replace")
            if nan_re.search(text):
                bad.append(path.name)
    assert not bad, f"{len(bad)} report(s) contain \\bnan\\b: {bad}"


def test_scored_strings_match_finite_counts(bench_out_artifacts):
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
