"""Unit tests for the paired, gated benchmark comparison tool.

Covers ``scripts/benchmarking/compare_runs.py``. ``scripts/`` is measured by no
coverage run (``scripts/gate.sh`` passes ``--cov=src``), so these tests are the
only bar the tool has to clear — they are written to pin *behaviour a wrong
answer would silently pass*, not to decorate the implementation:

* the three refusals that make a comparison trustworthy — a question-set
  mismatch (G4, never overridable), an unequal or unrecorded corpus fingerprint
  (G3), and a non-empty ``divergence_from_selected_file`` (Procedure E);
* the pairing rule (G5): rows join on **question text**, never on the
  positional ``question_<n>`` key, so a run that dropped a row cannot silently
  compare question 7 against a different question 7;
* the verdict rule (G2/G7): ``SIGNIFICANT`` requires ``|mean| > 2*SE`` *and* a
  measured noise floor with ``|mean| > 2*sigma``. Without sigma the tool says so
  and never claims significance;
* the anchors (G8) are found by **question text** against the anchors file. The
  FASRC bank sets ``anchor_type`` on all 109 rows, so selecting on that field
  would pull the whole bank rather than the five tripwires;
* scored counts are recomputed from finite values, because
  ``<metric>_scored`` over-reports (issue #279: ``context_precision_scored``
  says "109 of 109" in ``bench_out/benchmarking-ragas-205-20260817_040939.json``
  while 108 values are finite).
"""

import itertools
import json
import math
import statistics

import pytest

from scripts.benchmarking import compare_runs as cr

METRIC_NAMES = (
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_correctness",
)


def _row(
    question,
    *,
    status="ok",
    reference=None,
    answer="an answer",
    time_elapsed=1.0,
    anchor_type=None,
    difficulty=None,
    sources=None,
    **metrics,
):
    """One ``single_question_results`` row shaped like the real artifact."""
    row = {
        "time_elapsed": time_elapsed,
        "question": question,
        "reference_answer": (
            f"reference for {question}" if reference is None else reference
        ),
        "answer": answer,
        "status": status,
        "messages": [],
        "reference_sources_match_fields": ["url"],
        "reference_sources_metadata": [] if sources is None else sources,
        "sources_metadata": [],
        "sources_trunc_content": [],
    }
    if anchor_type is not None:
        row["anchor_type"] = anchor_type
    if difficulty is not None:
        row["difficulty"] = difficulty
    row.update(metrics)
    return row


def _honest_totals(rows, override=None):
    """``total_results`` computed the way the harness *should* compute it."""
    out = {}
    for metric in METRIC_NAMES:
        values = [row[metric] for row in rows if metric in row]
        if not values:
            continue
        finite = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
        out[f"aggregate_{metric}"] = (
            statistics.fmean(finite) if finite else float("nan")
        )
        out[f"{metric}_scored"] = f"{len(finite)} of {len(values)}"
    scored = [row for row in rows if row.get("reference_sources_metadata")]
    hits = sum(
        1
        for row in scored
        if all(entry.get("matched") for entry in row["reference_sources_metadata"])
    )
    out["source_scored_count"] = len(scored)
    out["source_accuracy"] = hits / len(scored) if scored else None
    out["relative_source_accuracy"] = out["source_accuracy"]
    if override:
        out.update(override)
    return out


def _per_arm(value, count):
    return list(value) if isinstance(value, list) else [value] * count


@pytest.fixture
def _artifact(tmp_path):
    """Write a benchmark artifact and return its path.

    ``rows`` builds a single-arm file; ``arms`` (a list of row lists) builds a
    ``-cd`` sweep. ``fingerprint``/``digest``/``divergence``/``total`` accept a
    single value applied to every arm or a per-arm list. Written with the stdlib
    default ``allow_nan=True``, so a ``float('nan')`` metric lands on disk as the
    bare ``NaN`` token the real artifacts carry.
    """
    counter = itertools.count(1)

    def make(
        rows=None,
        *,
        name=None,
        arms=None,
        fingerprint=None,
        digest="sha256:a",
        divergence=None,
        snapshot="s1",
        code="c1",
        total=None,
        metadata=None,
    ):
        arm_rows = arms if arms is not None else [rows or []]
        count = len(arm_rows)
        fingerprints = _per_arm(fingerprint, count)
        digests = _per_arm(digest, count)
        divergences = _per_arm(divergence, count)
        totals = _per_arm(total, count)
        results = []
        for index, these in enumerate(arm_rows):
            arm = {
                "single_question_results": {
                    f"question_{n}": row for n, row in enumerate(these, 1)
                },
                "total_results": _honest_totals(these, totals[index]),
                "configuration_file": f"configs/arm{index + 1}.yaml",
                "configuration": {},
                "config_version": {
                    "digest": digests[index],
                    "source": "test fixture",
                    "selected_file": f"configs/arm{index + 1}.yaml",
                    "selected_file_digest": digests[index],
                    "divergence_from_selected_file": divergences[index],
                    "key_settings": {},
                },
            }
            if fingerprints[index] is not None:
                arm["corpus_fingerprint"] = fingerprints[index]
            results.append(arm)
        document = {
            "benchmarking_results": results,
            "metadata": {
                "time": "2026-09-03 00:00:00",
                "git_info": {"last_commit": "0a157cd"},
                "corpus_snapshot_id": snapshot,
                "code_version": {"digest": code},
                "config_versions": digests,
            },
        }
        if metadata:
            document["metadata"].update(metadata)
        path = tmp_path / (name or f"run_{next(counter)}.json")
        path.write_text(json.dumps(document, indent=1))
        return path

    return make


def _pair(_artifact, base_rows, treat_rows, **kwargs):
    return str(_artifact(base_rows, **kwargs)), str(_artifact(treat_rows, **kwargs))


# --- loading -----------------------------------------------------------------


def test_loads_bare_nan_and_treats_non_finite_as_missing(_artifact):
    path = _artifact(
        [
            _row("q1", context_precision=0.5),
            _row("q2", context_precision=float("nan")),
            _row("q3", context_precision=1.0),
        ]
    )
    assert ": NaN" in path.read_text()

    arm = cr.load_arms([str(path)])[0]

    assert cr.is_finite(arm.rows["q1"]["context_precision"]) is True
    assert cr.is_finite(arm.rows["q2"]["context_precision"]) is False
    recomputed = cr.recomputed_aggregate(arm, "context_precision")
    assert recomputed["finite"] == 2
    assert recomputed["total"] == 3
    assert recomputed["mean"] == pytest.approx(0.75)


def test_refuses_duplicate_question_text_inside_one_arm(_artifact):
    path = _artifact([_row("same", faithfulness=0.1), _row("same", faithfulness=0.9)])

    with pytest.raises(cr.CompareError) as excinfo:
        cr.load_arms([str(path)])

    assert excinfo.value.code == cr.EXIT_GATE
    assert "same" in str(excinfo.value)


# --- G4: the question sets must match ----------------------------------------


def test_refuses_when_question_sets_differ(_artifact, capsys):
    base, treat = _pair(
        _artifact,
        [_row("shared", faithfulness=0.5), _row("only-in-baseline", faithfulness=0.5)],
        [_row("shared", faithfulness=0.6), _row("only-in-treatment", faithfulness=0.6)],
        fingerprint="corpus-1",
    )

    code = cr.main([base, treat])

    assert code == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "only-in-baseline" in err
    assert "only-in-treatment" in err
    assert "G4" in err


def test_question_set_mismatch_cannot_be_overridden(_artifact):
    base, treat = _pair(
        _artifact,
        [_row("shared", faithfulness=0.5), _row("dropped", faithfulness=0.5)],
        [_row("shared", faithfulness=0.6)],
        fingerprint="corpus-1",
    )

    assert (
        cr.main(
            [base, treat, "--corpus-differs-by-design", "--ignore-config-divergence"]
        )
        == cr.EXIT_GATE
    )


# --- G5: pair on question text, never on question_<n> ------------------------


def test_pairs_on_question_text_when_positional_keys_are_shuffled(_artifact):
    base = _artifact(
        [
            _row("alpha", faithfulness=0.10),
            _row("beta", faithfulness=0.20),
            _row("gamma", faithfulness=0.30),
        ],
        fingerprint="corpus-1",
    )
    treat = _artifact(
        [
            _row("gamma", faithfulness=0.35),
            _row("alpha", faithfulness=0.15),
            _row("beta", faithfulness=0.25),
        ],
        fingerprint="corpus-1",
    )
    arms = cr.load_arms([str(base), str(treat)])

    deltas = cr.paired_deltas(
        arms[0], arms[1], "faithfulness", ["alpha", "beta", "gamma"]
    )

    assert deltas == pytest.approx([0.05, 0.05, 0.05])


def test_paired_deltas_drop_degraded_and_non_finite_rows(_artifact):
    base = _artifact(
        [
            _row("ok-both", faithfulness=0.10),
            _row("degraded-in-treatment", faithfulness=0.10),
            _row("nan-in-baseline", faithfulness=float("nan")),
            _row("missing-metric"),
        ],
        fingerprint="corpus-1",
    )
    treat = _artifact(
        [
            _row("ok-both", faithfulness=0.30),
            _row("degraded-in-treatment", faithfulness=0.90, status="degraded"),
            _row("nan-in-baseline", faithfulness=0.90),
            _row("missing-metric"),
        ],
        fingerprint="corpus-1",
    )
    arms = cr.load_arms([str(base), str(treat)])

    deltas = cr.paired_deltas(
        arms[0],
        arms[1],
        "faithfulness",
        ["ok-both", "degraded-in-treatment", "nan-in-baseline", "missing-metric"],
    )

    assert deltas == pytest.approx([0.20])


def test_summarize_deltas_reports_n_mean_and_standard_error():
    summary = cr.summarize_deltas([0.1, 0.2, 0.3, 0.4])

    assert summary["n"] == 4
    assert summary["mean"] == pytest.approx(0.25)
    assert summary["se"] == pytest.approx(
        statistics.stdev([0.1, 0.2, 0.3, 0.4]) / math.sqrt(4)
    )
    assert cr.summarize_deltas([])["n"] == 0
    assert cr.summarize_deltas([0.1])["se"] is None


# --- G2/G7: the verdict ------------------------------------------------------


def test_verdict_is_significant_only_past_both_two_se_and_two_sigma():
    tight = cr.summarize_deltas([0.20, 0.21, 0.19, 0.20])

    assert cr.verdict(tight, 0.01) == "SIGNIFICANT"
    # same delta, a noise floor that swallows it -> not distinguishable
    assert cr.verdict(tight, 0.5) == "not distinguishable"
    # a delta inside its own standard error is never significant
    noisy = cr.summarize_deltas([0.5, -0.5, 0.4, -0.4])
    assert cr.verdict(noisy, 0.001) == "not distinguishable"


def test_verdict_without_a_noise_floor_is_never_significant():
    tight = cr.summarize_deltas([0.20, 0.21, 0.19, 0.20])

    assert cr.verdict(tight, None) == "noise floor not measured"
    assert cr.verdict(cr.summarize_deltas([0.1]), None) == "too few paired rows"


# --- the noise floor ---------------------------------------------------------


def test_parse_noise_floor_reads_metric_sigma_pairs():
    sigmas = cr.parse_noise_floor(
        "answer_relevancy=0.025, faithfulness=0.027,context_precision=0.016"
    )

    assert sigmas == {
        "answer_relevancy": 0.025,
        "faithfulness": 0.027,
        "context_precision": 0.016,
    }
    with pytest.raises(cr.CompareError):
        cr.parse_noise_floor("faithfulness")
    with pytest.raises(cr.CompareError):
        cr.parse_noise_floor("not_a_metric=0.1")


def test_noise_floor_from_runs_uses_recomputed_means(_artifact):
    # Three replicates. The third file over-reports its aggregate; sigma must be
    # computed from the recomputed means, not from aggregate_<metric>.
    a = _artifact([_row("q1", faithfulness=0.50), _row("q2", faithfulness=0.50)])
    b = _artifact([_row("q1", faithfulness=0.60), _row("q2", faithfulness=0.60)])
    c = _artifact(
        [_row("q1", faithfulness=0.70), _row("q2", faithfulness=0.70)],
        total={"aggregate_faithfulness": 0.99},
    )

    sigmas = cr.noise_floor_from_runs([str(a), str(b), str(c)])

    assert sigmas["faithfulness"] == pytest.approx(statistics.stdev([0.5, 0.6, 0.7]))


def test_noise_floor_from_runs_needs_at_least_two_replicates(_artifact):
    only = _artifact([_row("q1", faithfulness=0.5)])

    with pytest.raises(cr.CompareError):
        cr.noise_floor_from_runs([str(only)])


def test_noise_floor_from_runs_treats_every_arm_as_a_replicate(_artifact):
    sweep = _artifact(
        arms=[
            [_row("q1", faithfulness=0.50)],
            [_row("q1", faithfulness=0.60)],
        ]
    )

    sigmas = cr.noise_floor_from_runs([str(sweep)])

    assert sigmas["faithfulness"] == pytest.approx(statistics.stdev([0.5, 0.6]))


# --- #279: scored counts are recomputed --------------------------------------


def test_scored_counts_are_recomputed_and_flagged_over_reported(_artifact):
    path = _artifact(
        [
            _row("q1", faithfulness=0.5, context_precision=0.5),
            _row("q2", faithfulness=0.5, context_precision=float("nan")),
        ],
        # exactly the #279 defect: the harness counted the NaN cell as scored
        total={"context_precision_scored": "2 of 2"},
    )
    arm = cr.load_arms([str(path)])[0]

    rows = {row["metric"]: row for row in cr.scored_counts(arm)}

    assert rows["context_precision"]["reported"] == "2 of 2"
    assert rows["context_precision"]["finite"] == 1
    assert rows["context_precision"]["total"] == 2
    assert rows["context_precision"]["flag"] == "OVER-REPORTED"
    assert rows["faithfulness"]["flag"] == "ok"


# --- G3: the corpus gate -----------------------------------------------------


def test_corpus_gate_refuses_when_fingerprints_differ(_artifact, capsys):
    base, treat = (
        str(_artifact([_row("q1", faithfulness=0.5)], fingerprint="corpus-1")),
        str(_artifact([_row("q1", faithfulness=0.6)], fingerprint="corpus-2")),
    )

    code = cr.main([base, treat])

    assert code == cr.EXIT_GATE
    err = capsys.readouterr().err
    assert "corpus-1" in err and "corpus-2" in err
    assert "G3" in err


def test_corpus_gate_refuses_when_the_fingerprint_was_never_recorded(_artifact, capsys):
    base, treat = _pair(
        _artifact,
        [_row("q1", faithfulness=0.5)],
        [_row("q1", faithfulness=0.6)],
    )

    code = cr.main([base, treat])

    assert code == cr.EXIT_GATE
    assert "not recorded" in capsys.readouterr().err


def test_corpus_gate_flag_allows_the_run_and_prints_both_values(_artifact, capsys):
    base = str(_artifact([_row("q1", faithfulness=0.5)], fingerprint="corpus-1"))
    treat = str(_artifact([_row("q1", faithfulness=0.6)], fingerprint="corpus-2"))

    code = cr.main([base, treat, "--corpus-differs-by-design"])

    assert code == cr.EXIT_OK
    out = capsys.readouterr().out
    assert "corpus-1" in out and "corpus-2" in out
    assert "Procedure B" in out


def test_corpus_fingerprint_falls_back_to_metadata(_artifact):
    path = _artifact(
        [_row("q1", faithfulness=0.5)], metadata={"corpus_fingerprint": "meta-1"}
    )

    assert cr.load_arms([str(path)])[0].corpus_fingerprint == "meta-1"


def test_unavailable_fingerprint_counts_as_unrecorded(_artifact):
    path = _artifact(
        [_row("q1", faithfulness=0.5)], fingerprint="<unavailable: no snapshot>"
    )

    assert cr.load_arms([str(path)])[0].corpus_fingerprint is None


# --- Procedure E: the divergence gate ----------------------------------------


def test_divergence_stops_with_its_own_exit_code(_artifact, capsys):
    base = str(
        _artifact(
            [_row("q1", faithfulness=0.5)],
            fingerprint="corpus-1",
            divergence=["services.chat_app.context_editing.context_window"],
        )
    )
    treat = str(_artifact([_row("q1", faithfulness=0.6)], fingerprint="corpus-1"))

    code = cr.main([base, treat])

    assert code == cr.EXIT_DIVERGENCE
    err = capsys.readouterr().err
    assert "context_window" in err
    assert "divergence" in err.lower()


def test_divergence_can_be_ignored_explicitly(_artifact):
    base = str(
        _artifact(
            [_row("q1", faithfulness=0.5)],
            fingerprint="corpus-1",
            divergence=["services.chat_app.default_model"],
        )
    )
    treat = str(_artifact([_row("q1", faithfulness=0.6)], fingerprint="corpus-1"))

    assert cr.main([base, treat, "--ignore-config-divergence"]) == cr.EXIT_OK


def test_null_divergence_prints_the_procedure_e_caveat(_artifact, capsys):
    base, treat = _pair(
        _artifact,
        [_row("q1", faithfulness=0.5)],
        [_row("q1", faithfulness=0.6)],
        fingerprint="corpus-1",
        divergence=None,
    )

    assert cr.main([base, treat]) == cr.EXIT_OK
    out = capsys.readouterr().out
    assert "Procedure E" in out
    assert "backfilled" in out
