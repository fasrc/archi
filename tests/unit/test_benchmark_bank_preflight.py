"""Unit tests for the benchmark question-bank preflight (openspec change
`add-benchmark-bank-preflight`).

The preflight validates a question bank against the schema the harness requires
for the configured modes BEFORE `archi evaluate` deploys/ingests, so a bank/mode
mismatch fails in seconds instead of after a ~50-min ingest. The logic lives in
the pure, ragas-free `src.utils.benchmark_schema` module (reusing
`normalize_bank` / `required_fields_for_modes` / `metric_required_column`) so it
can never drift from what `Benchmarker._process_config` enforces at grading time.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.benchmark_schema import (
    bank_eligibility_warnings,
    effective_benchmarking,
    preflight_bank_file,
    preflight_benchmark_configs,
    validate_bank,
)

_FIXTURES = Path(__file__).parent / "fixtures"

# --- validate_bank: per-mode schema errors ----------------------------------


def _modern_bank_without_sources():
    return [
        {
            "user_input": "q1",
            "reference": "r1",
            "retrieved_contexts": [],
            "response": "",
        },
        {
            "user_input": "q2",
            "reference": "r2",
            "retrieved_contexts": [],
            "response": "",
        },
    ]


def test_missing_sources_under_sources_mode_errors_per_item():
    errors = validate_bank(
        _modern_bank_without_sources(), {"modes": ["RAGAS", "SOURCES"]}
    )
    assert len(errors) == 2
    assert all("sources" in e for e in errors)
    # error identifies the offending item index
    assert any(e.startswith("item[0]") for e in errors)
    assert any(e.startswith("item[1]") for e in errors)


def test_same_bank_passes_without_sources_mode():
    assert validate_bank(_modern_bank_without_sources(), {"modes": ["RAGAS"]}) == []


def test_legacy_dialect_is_normalized_before_check():
    # legacy `question` maps to `user_input`; with `sources` present it satisfies
    # [RAGAS, SOURCES].
    bank = [{"question": "q", "answer": "a", "sources": ["https://x"]}]
    assert validate_bank(bank, {"modes": ["RAGAS", "SOURCES"]}) == []


def test_non_dict_item_is_reported_not_raised():
    errors = validate_bank(["not-a-dict"], {"modes": ["RAGAS"]})
    assert len(errors) == 1
    assert "item[0]" in errors[0]


def test_valid_bank_returns_empty():
    bank = [{"user_input": "q", "sources": ["https://x"]}]
    assert validate_bank(bank, {"modes": ["RAGAS", "SOURCES"]}) == []


# --- bank_eligibility_warnings: per-metric denominators ----------------------


def _cfg_with_metrics(modes, metrics):
    return {
        "modes": modes,
        "mode_settings": {"ragas_settings": {"enabled_metrics": metrics}},
    }


def test_context_metric_with_some_empty_reference_warns_with_denominator():
    bank = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": ""},  # draft: excluded from context metrics
        {"user_input": "q3", "reference": "r3"},
    ]
    warnings = bank_eligibility_warnings(
        bank, _cfg_with_metrics(["RAGAS"], ["context_recall"])
    )
    assert any("context_recall" in w for w in warnings)
    assert any("2/3" in w for w in warnings)


def test_no_warning_when_all_rows_eligible():
    bank = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": "r2"},
    ]
    assert (
        bank_eligibility_warnings(
            bank, _cfg_with_metrics(["RAGAS"], ["context_recall"])
        )
        == []
    )


def test_answer_metric_never_warns_on_empty_reference():
    bank = [{"user_input": "q1", "reference": ""}]
    assert (
        bank_eligibility_warnings(
            bank, _cfg_with_metrics(["RAGAS"], ["answer_relevancy", "faithfulness"])
        )
        == []
    )


def test_no_ragas_mode_no_eligibility_warnings():
    bank = [{"user_input": "q1", "reference": ""}]
    assert (
        bank_eligibility_warnings(
            bank, _cfg_with_metrics(["SOURCES"], ["context_recall"])
        )
        == []
    )


# --- preflight_bank_file: load + validate, never raises ----------------------


def test_preflight_valid_file_returns_no_errors(tmp_path):
    p = tmp_path / "bank.json"
    p.write_text(json.dumps([{"user_input": "q", "sources": ["https://x"]}]))
    errors, warnings = preflight_bank_file(str(p), {"modes": ["RAGAS", "SOURCES"]})
    assert errors == []


def test_preflight_bank_missing_sources_returns_errors(tmp_path):
    p = tmp_path / "bank.json"
    p.write_text(json.dumps(_modern_bank_without_sources()))
    errors, _ = preflight_bank_file(str(p), {"modes": ["RAGAS", "SOURCES"]})
    assert len(errors) == 2


def test_preflight_missing_file_is_single_hard_error(tmp_path):
    errors, warnings = preflight_bank_file(
        str(tmp_path / "nope.json"), {"modes": ["RAGAS"]}
    )
    assert len(errors) == 1
    assert warnings == []


def test_preflight_non_json_file_is_single_hard_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("this is not json {")
    errors, _ = preflight_bank_file(str(p), {"modes": ["RAGAS"]})
    assert len(errors) == 1


def test_preflight_non_list_json_is_single_hard_error(tmp_path):
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"user_input": "q"}))
    errors, _ = preflight_bank_file(str(p), {"modes": ["RAGAS"]})
    assert len(errors) == 1


# --- CI drift guard: a committed canonical modern bank must stay valid --------


def test_canonical_modern_bank_validates_under_ragas_and_sources():
    """If a future change to `required_fields_for_modes` outdates the modern bank
    shape (the exact drift that wasted a ~50-min run), this fails in CI even
    though the real banks are git-ignored."""
    bank = json.loads((_FIXTURES / "canonical_bank_modern.json").read_text())
    assert validate_bank(bank, {"modes": ["RAGAS", "SOURCES"]}) == []


# --- defensive guards --------------------------------------------------------


def test_validate_bank_non_list_is_single_error():
    errors = validate_bank("not-a-list", {"modes": ["RAGAS"]})
    assert len(errors) == 1
    assert "not a JSON list" in errors[0]


def test_eligibility_warnings_non_dict_config_is_empty():
    assert bank_eligibility_warnings([{"user_input": "q"}], None) == []


def test_eligibility_warnings_empty_bank_is_empty():
    assert (
        bank_eligibility_warnings([], _cfg_with_metrics(["RAGAS"], ["context_recall"]))
        == []
    )


# --- F3: None / non-str queries path must not raise -------------------------


def test_preflight_none_path_is_single_hard_error():
    # queries_path absent in config -> None reaches the helper; open(None) would
    # TypeError. Contract says it returns a hard error, never raises.
    errors, warnings = preflight_bank_file(None, {"modes": ["RAGAS"]})
    assert len(errors) == 1
    assert warnings == []


# --- F1b: effective_benchmarking applies base-config.yaml defaults ----------


def test_effective_benchmarking_defaults_modes_to_sources_and_ragas():
    # base-config.yaml defaults modes to [SOURCES, RAGAS] when omitted, so an
    # omitted-modes config effectively requires `sources`.
    eff = effective_benchmarking({})
    assert set(eff["modes"]) == {"SOURCES", "RAGAS"}
    assert eff["queries_path"] == "queries"


def test_effective_benchmarking_preserves_explicit_values():
    eff = effective_benchmarking({"modes": ["RAGAS"], "queries_path": "x.json"})
    assert eff["modes"] == ["RAGAS"]
    assert eff["queries_path"] == "x.json"


def test_effective_benchmarking_defaults_enabled_metrics():
    eff = effective_benchmarking({})
    metrics = eff["mode_settings"]["ragas_settings"]["enabled_metrics"]
    assert "context_recall" in metrics and "answer_relevancy" in metrics


# --- F1a: preflight_benchmark_configs validates EVERY config -----------------


def _cfg_with_bank(tmp_path, name, modes, items, extra_bench=None):
    bank = tmp_path / f"{name}.json"
    bank.write_text(json.dumps(items))
    bench = {"modes": modes, "queries_path": str(bank)}
    if extra_bench:
        bench.update(extra_bench)
    return {"name": name, "services": {"benchmarking": bench}}


def test_preflight_configs_flags_a_later_config(tmp_path):
    good = _cfg_with_bank(
        tmp_path, "c0", ["RAGAS"], [{"user_input": "q"}]
    )  # RAGAS only: ok
    bad = _cfg_with_bank(
        tmp_path, "c1", ["RAGAS", "SOURCES"], [{"user_input": "q"}]
    )  # needs sources
    errors, _ = preflight_benchmark_configs([good, bad])
    assert errors  # the second config is caught, not just the first
    assert any("c1" in e for e in errors)
    assert all("c0" not in e for e in errors)


def test_preflight_configs_omitted_modes_requires_sources(tmp_path):
    # A config that omits `modes` still renders as [SOURCES, RAGAS] -> must carry
    # sources. Without the default applied this would wrongly pass.
    cfg = tmp_path / "bank.json"
    cfg.write_text(json.dumps([{"user_input": "q"}]))
    config = {"services": {"benchmarking": {"queries_path": str(cfg)}}}  # no modes
    errors, _ = preflight_benchmark_configs([config])
    assert any("sources" in e for e in errors)


# --- F2: anchors are part of the effective question set ----------------------


def test_preflight_configs_validates_enabled_anchor_file(tmp_path):
    anchors = tmp_path / "anchors.json"
    anchors.write_text(json.dumps([{"user_input": "a"}]))  # missing sources
    cfg = _cfg_with_bank(
        tmp_path,
        "c",
        ["RAGAS", "SOURCES"],
        [{"user_input": "q", "sources": ["u"]}],  # queries OK
        extra_bench={"anchors": {"enabled": True, "path": str(anchors)}},
    )
    errors, _ = preflight_benchmark_configs([cfg])
    # queries pass, but the anchor bank lacks sources -> flagged
    assert any("anchors" in e for e in errors)


def test_preflight_configs_skips_disabled_anchors(tmp_path):
    anchors = tmp_path / "anchors.json"
    anchors.write_text(json.dumps([{"user_input": "a"}]))  # would fail if checked
    cfg = _cfg_with_bank(
        tmp_path,
        "c",
        ["RAGAS", "SOURCES"],
        [{"user_input": "q", "sources": ["u"]}],
        extra_bench={"anchors": {"enabled": False, "path": str(anchors)}},
    )
    errors, _ = preflight_benchmark_configs([cfg])
    assert errors == []  # disabled anchors are never validated


def test_preflight_configs_skips_missing_anchor_file(tmp_path):
    # Mirror runtime: a missing anchor file WARNS+skips, it does not fail the run.
    cfg = _cfg_with_bank(
        tmp_path,
        "c",
        ["RAGAS", "SOURCES"],
        [{"user_input": "q", "sources": ["u"]}],
        extra_bench={"anchors": {"enabled": True, "path": str(tmp_path / "nope.json")}},
    )
    errors, _ = preflight_benchmark_configs([cfg])
    assert errors == []  # a missing anchor file is skipped, not an error


def test_preflight_configs_accepts_a_single_non_list_config(tmp_path):
    cfg = tmp_path / "bank.json"
    cfg.write_text(json.dumps([{"user_input": "q"}]))  # no sources, defaulted SOURCES
    single = {"services": {"benchmarking": {"queries_path": str(cfg)}}}
    errors, _ = preflight_benchmark_configs(single)  # a dict, not a list
    assert any("sources" in e for e in errors)
