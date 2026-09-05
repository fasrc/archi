import json
import math
from pathlib import Path

import pytest

_BENCH_OUT_DIR = Path(__file__).resolve().parents[2] / "bench_out"

if not _BENCH_OUT_DIR.is_dir():
    pytest.skip("bench_out directory absent", allow_module_level=True)


def _raise_on_constant(val):
    raise ValueError(f"non-JSON constant: {val!r}")


def _is_artifact(data):
    return isinstance(data, dict) and isinstance(data.get("benchmarking_results"), list)


@pytest.fixture(scope="module")
def bench_out_artifacts():
    artifacts = []
    for path in sorted(_BENCH_OUT_DIR.glob("*.json")):
        text = path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not _is_artifact(data):
            continue
        artifacts.append((path, text, data))
    return artifacts


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
