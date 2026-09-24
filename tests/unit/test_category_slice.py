"""The per-category slice reads only a snapshot bound to its run (#524, #538).

Four per-arm checks gate the snapshot; a pair also needs equal corpus
fingerprints. A map mismatch between arms voids any comparison with a
map-reading arm (r0a) and otherwise costs only the slice, unless a trace shows
``search_metadata_index``. A QA run joins an arm only with matching map
readings and the arm's own prompt.
"""

import hashlib

import pytest

from scripts.benchmarking import category_slice as cs
from src.utils.benchmark_provenance import (
    category_map_digest,
    category_map_records,
    category_map_text,
)

KB = "https://docs.rc.fas.harvard.edu/kb"


def _snapshot(tmp_path, pairs, name="bench-1_category_map_1.tsv"):
    records = category_map_records(pairs)
    (tmp_path / name).write_bytes(category_map_text(records).encode("utf-8"))
    return name, category_map_digest(records)


def _entry(tmp_path, pairs=((f"{KB}/a", "A"),), **overrides):
    name, digest = _snapshot(tmp_path, list(pairs))
    entry = {
        "corpus_fingerprint": "sha256:corpus",
        "corpus_fingerprint_before": "sha256:corpus",
        "corpus_unchanged_at_endpoints": True,
        "category_map_sha256_start": digest,
        "category_map_sha256_end": digest,
        "category_map_unchanged_at_endpoints": True,
        "category_map_file": name,
        "agent_md_sha256": "abc",
    }
    entry.update(overrides)
    return entry


# --- snapshot checks -------------------------------------------------------------------


def test_a_bound_snapshot_loads(tmp_path):
    check = cs.snapshot_check(_entry(tmp_path), tmp_path)
    assert check.reason is None
    assert check.category_of == {f"{KB}/a": "A"}


def test_legacy_entry_has_no_snapshot(tmp_path):
    check = cs.snapshot_check({"corpus_fingerprint": "sha256:x"}, tmp_path)
    assert check.reason == "no snapshot"


def test_missing_file_has_no_snapshot(tmp_path):
    entry = _entry(tmp_path)
    (tmp_path / entry["category_map_file"]).unlink()
    assert cs.snapshot_check(entry, tmp_path).reason == "no snapshot"


def test_unstable_corpus_is_named(tmp_path):
    entry = _entry(tmp_path, corpus_unchanged_at_endpoints=False)
    assert (
        cs.snapshot_check(entry, tmp_path).reason
        == "corpus not stable at the endpoints"
    )


def test_map_changed_is_named(tmp_path):
    entry = _entry(tmp_path, category_map_unchanged_at_endpoints=False)
    assert cs.snapshot_check(entry, tmp_path).reason == "map changed between endpoints"


def test_failed_reading_is_named(tmp_path):
    entry = _entry(tmp_path, category_map_unchanged_at_endpoints=None)
    assert cs.snapshot_check(entry, tmp_path).reason == "a map reading failed"


def test_replaced_snapshot_is_named(tmp_path):
    entry = _entry(tmp_path)
    (tmp_path / entry["category_map_file"]).write_text(f"{KB}/a\tB")
    reason = cs.snapshot_check(entry, tmp_path).reason
    assert reason == "snapshot does not match the end digest"


def test_escaped_fields_round_trip(tmp_path):
    entry = _entry(tmp_path, pairs=[(f"{KB}/a", "Tab\tHere")])
    assert cs.snapshot_check(entry, tmp_path).category_of == {f"{KB}/a": "Tab\tHere"}


def test_pair_on_different_corpora_has_no_slice(tmp_path):
    base = _entry(tmp_path)
    arm = _entry(tmp_path, corpus_fingerprint="sha256:other")
    assert cs.pair_slice_reason(base, arm, tmp_path, tmp_path) == (
        "arms scored different corpora"
    )


def test_pair_of_bound_snapshots_slices(tmp_path):
    assert (
        cs.pair_slice_reason(_entry(tmp_path), _entry(tmp_path), tmp_path, tmp_path)
        is None
    )


# --- category table -----------------------------------------------------------------------


def _row(*slugs, status="ok", matched=None):
    refs = [{"url": f"{KB}/{slug}"} for slug in slugs]
    for ref, hit in zip(refs, matched or [False] * len(refs)):
        ref["matched"] = hit
    return {"status": status, "reference_sources_metadata": refs}


def test_two_sources_in_different_categories_end_to_end():
    """#525: one row under A for rows and completion, out of both accuracies."""
    rows = {"q": _row("a", "b", matched=[False, True])}
    category_of = {f"{KB}/a": "A", f"{KB}/b": "B"}
    table = cs.category_table(rows, category_of)

    a = table["categories"]["A"]
    assert a["gold_rows"] == 1 and a["completion"] == 1.0
    assert a["source_accuracy"] is None and a["source_rows"] == 0
    assert table["categories"]["B"]["gold_rows"] == 0
    assert table["categories"]["B"]["coverage"] == 1
    assert table["cross_category"] == ["q"]


def test_uncategorized_first_source_end_to_end():
    """#538: no owner, on the uncategorized line, B's coverage kept."""
    rows = {"q": _row("a", "b")}
    table = cs.category_table(rows, {f"{KB}/a": None, f"{KB}/b": "B"})
    assert table["uncategorized"] == ["q"]
    assert table["categories"]["B"]["gold_rows"] == 0
    assert table["categories"]["B"]["coverage"] == 1


def test_accuracy_uses_relative_hits_on_single_category_rows():
    rows = {
        "q1": _row("a1", matched=[True]),
        "q2": _row("a2", matched=[False]),
        "q3": _row("a3", status="degraded"),
    }
    table = cs.category_table(rows, {f"{KB}/a{i}": "A" for i in (1, 2, 3)})
    a = table["categories"]["A"]
    assert a["source_rows"] == 2 and a["source_accuracy"] == pytest.approx(0.5)
    assert a["completion"] == pytest.approx(2 / 3)


def test_power_is_per_metric_and_underpowered_metric_has_no_verdict():
    rows = {"q1": _row("a1"), "q2": _row("a2"), "q3": _row("a3", "b1")}
    category_of = {f"{KB}/a1": "A", f"{KB}/a2": "A", f"{KB}/a3": "A", f"{KB}/b1": "B"}
    a = cs.category_table(rows, category_of)["categories"]["A"]
    assert a["power"] == {"completion": 3, "source": 2}
    assert a["underpowered"] == ["source"]


# --- traces -------------------------------------------------------------------------------


def test_benchmark_trace_names_the_metadata_tool():
    rows = {
        "q": {"messages": [{"type": "tool_call", "tool_name": "search_metadata_index"}]}
    }
    assert cs.called_metadata_search(rows, []) is True


def test_qa_trace_names_the_metadata_tool():
    answers = [
        {"tool_calls": [{"name": "search_vectorstore_hybrid"}]},
        {"tool_calls": [{"name": "search_metadata_index"}]},
    ]
    assert cs.called_metadata_search({}, answers) is True


def test_no_trace_of_the_metadata_tool():
    rows = {
        "q": {"messages": [{"type": "tool_call", "tool_name": "search_local_files"}]}
    }
    assert cs.called_metadata_search(rows, [{"tool_calls": []}]) is False


# --- map rule -------------------------------------------------------------------------------


def _readings(start="sha256:m", end="sha256:m"):
    return {"category_map_sha256_start": start, "category_map_sha256_end": end}


def test_matching_maps_stand():
    assert cs.map_rule(
        _readings(), _readings(), routes_on_category=False, traced=False
    ) == (
        "ok",
        None,
    )


def test_routed_arm_with_a_different_map_is_void():
    status, reason = cs.map_rule(
        _readings(),
        _readings("sha256:x", "sha256:x"),
        routes_on_category=True,
        traced=False,
    )
    assert status == "void" and "category map" in reason


def test_routed_arm_with_a_failed_start_and_matching_end_is_void():
    arm = _readings("<unavailable: boom>", "sha256:m")
    assert (
        cs.map_rule(_readings(), arm, routes_on_category=True, traced=False)[0]
        == "void"
    )


def test_unrouted_pair_with_different_maps_loses_only_the_slice():
    status, _ = cs.map_rule(
        _readings(),
        _readings("sha256:x", "sha256:x"),
        routes_on_category=False,
        traced=False,
    )
    assert status == "slice-only"


def test_unrouted_pair_with_a_metadata_trace_is_void():
    status, reason = cs.map_rule(
        _readings(),
        _readings("sha256:x", "sha256:x"),
        routes_on_category=False,
        traced=True,
    )
    assert status == "void" and "search_metadata_index" in reason


def test_legacy_pair_has_no_map_rule():
    assert cs.map_rule({}, {}, routes_on_category=True, traced=True) == ("ok", None)


# --- QA join ---------------------------------------------------------------------------------


def test_qa_run_with_matching_readings_and_prompt_joins():
    arm = {"category_map_sha256_end": "sha256:m", "agent_md_sha256": "abc"}
    readings = {"start": "sha256:m", "end": "sha256:m"}
    assert cs.qa_join_reason(arm, readings, "abc") is None


def test_map_edited_during_the_qa_run_is_refused():
    arm = {"category_map_sha256_end": "sha256:m"}
    reason = cs.qa_join_reason(arm, {"start": "sha256:m", "end": "sha256:x"}, None)
    assert reason == "map changed during the QA run"


def test_missing_qa_readings_are_refused():
    arm = {"category_map_sha256_end": "sha256:m"}
    assert cs.qa_join_reason(arm, None, None) == "no map readings for this QA run"


def test_unavailable_qa_reading_is_refused():
    arm = {"category_map_sha256_end": "sha256:m"}
    reason = cs.qa_join_reason(
        arm, {"start": "<unavailable: x>", "end": "sha256:m"}, None
    )
    assert reason == "a QA map reading failed"


def test_qa_run_of_another_prompt_is_refused():
    arm = {"agent_md_sha256": "abc"}
    reason = cs.qa_join_reason(arm, None, "def")
    assert reason.startswith("QA run used a different agent spec")
    assert "abc" in reason and "def" in reason


def test_legacy_arm_joins_as_today():
    assert cs.qa_join_reason({}, None, "anything") is None


def test_file_hash_helper_matches_the_digest(tmp_path):
    name, digest = _snapshot(tmp_path, [(f"{KB}/a", "A")])
    body = (tmp_path / name).read_bytes()
    assert digest == f"sha256:{hashlib.sha256(body).hexdigest()}"
