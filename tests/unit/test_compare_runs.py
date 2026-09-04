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
from src.evaluation.qa.dataset import derive_item_id

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


# --- G8: the anchors ---------------------------------------------------------


@pytest.fixture
def anchors_file(tmp_path):
    """Write an anchors file and return its path."""

    def make(rows):
        path = tmp_path / "anchor_questions.json"
        path.write_text(json.dumps(rows, indent=1))
        return str(path)

    return make


def _anchor(user_input, anchor_type, **extra):
    row = {
        "anchor_type": anchor_type,
        "status": "draft",
        "user_input": user_input,
        "sources": [],
        "reference": f"reference for {user_input}",
    }
    row.update(extra)
    return row


def test_anchor_questions_reads_the_legacy_dialect(anchors_file):
    path = anchors_file(
        [{"anchor_type": "reasoning", "question": "legacy text", "answer": "a"}]
    )

    anchors = cr.anchor_questions(path)

    assert list(anchors) == ["legacy text"]
    assert anchors["legacy text"]["anchor_type"] == "reasoning"


def test_anchors_are_matched_by_question_text_not_anchor_type(_artifact, anchors_file):
    # Every row carries an anchor_type, exactly like the FASRC bank. Only two of
    # them are anchors, and only the anchors file knows which.
    rows = [
        _row("tripwire one", anchor_type="easy_retrieve", faithfulness=0.9),
        _row("tripwire two", anchor_type="reasoning", faithfulness=0.5),
        _row("bank row a", anchor_type="reasoning", faithfulness=0.4),
        _row("bank row b", anchor_type="easy_retrieve", faithfulness=0.6),
    ]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    anchors = anchors_file(
        [
            _anchor("tripwire one", "easy_retrieve"),
            _anchor("tripwire two", "reasoning"),
        ]
    )
    arms = cr.load_arms([base, treat])

    found = cr.anchor_questions(anchors)
    bank = cr.bank_questions(arms[0], found, include_anchors=False)

    assert set(found) == {"tripwire one", "tripwire two"}
    assert bank == ["bank row a", "bank row b"]


def test_bank_counts_are_reported_asked_anchors_and_bank_rows(
    _artifact, anchors_file, capsys
):
    # The FASRC shape in miniature: one anchor duplicates a bank row, so the
    # harness asked it once. asked 4 = 3 bank-only + 2 anchors - 1 shared.
    rows = [
        _row("shared with the bank", anchor_type="reasoning", faithfulness=0.5),
        _row("anchor only", anchor_type="should_refuse", faithfulness=0.5),
        _row("bank a", faithfulness=0.5),
        _row("bank b", faithfulness=0.5),
    ]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    anchors = anchors_file(
        [
            _anchor("shared with the bank", "reasoning"),
            _anchor("anchor only", "should_refuse"),
        ]
    )

    assert cr.main([base, treat, "--anchors", anchors]) == cr.EXIT_OK

    out = capsys.readouterr().out
    assert "questions asked | 4" in out
    assert "anchors | 2" in out
    assert "bank rows | 2" in out


def test_an_anchor_that_duplicates_a_bank_row_stays_an_anchor(_artifact, anchors_file):
    rows = [
        _row("shared with the bank", anchor_type="reasoning", faithfulness=0.5),
        _row("bank a", faithfulness=0.5),
    ]
    arms = cr.load_arms([str(_artifact(rows)), str(_artifact(rows))])
    anchors = cr.anchor_questions(
        anchors_file([_anchor("shared with the bank", "reasoning")])
    )

    bank = cr.bank_questions(arms[0], anchors, include_anchors=False)
    block = cr.anchor_block(arms[0], arms, anchors, {}, {})

    assert bank == ["bank a"]
    assert [entry["question"] for entry in block] == ["shared with the bank"]


def test_bank_table_includes_anchors_only_when_flagged(_artifact, anchors_file):
    rows = [
        _row("tripwire", anchor_type="easy_retrieve", faithfulness=1.0),
        _row("bank row", faithfulness=0.0),
    ]
    arms = cr.load_arms([str(_artifact(rows)), str(_artifact(rows))])
    anchors = cr.anchor_questions(anchors_file([_anchor("tripwire", "easy_retrieve")]))

    assert cr.bank_questions(arms[0], anchors, include_anchors=False) == ["bank row"]
    assert cr.bank_questions(arms[0], anchors, include_anchors=True) == [
        "tripwire",
        "bank row",
    ]


def test_easy_retrieve_anchor_alarms_on_a_drop(_artifact, anchors_file):
    base = _artifact(
        [_row("tripwire", anchor_type="easy_retrieve", context_recall=0.95)]
    )
    treat = _artifact(
        [_row("tripwire", anchor_type="easy_retrieve", context_recall=0.80)]
    )
    arms = cr.load_arms([str(base), str(treat)])
    anchors = cr.anchor_questions(anchors_file([_anchor("tripwire", "easy_retrieve")]))

    without_sigma = cr.anchor_block(arms[0], arms, anchors, {}, {})[0]
    with_sigma = cr.anchor_block(arms[0], arms, anchors, {"context_recall": 0.2}, {})[0]

    alarms = without_sigma["arms"][arms[1].label]["alarms"]
    assert "context_recall" in alarms
    assert with_sigma["arms"][arms[1].label]["alarms"] == []


def test_an_anchor_alarm_names_the_threshold_it_crossed(_artifact, anchors_file):
    # The threshold is a RUN-level sigma applied to a SINGLE question, whose own
    # noise is far larger, so it fires easily. The report must therefore show the
    # number that fired, not just the word ALARM.
    base = _artifact([_row("tripwire", context_recall=0.95)])
    treat = _artifact([_row("tripwire", context_recall=0.80)])
    arms = cr.load_arms([str(base), str(treat)])
    anchors = cr.anchor_questions(anchors_file([_anchor("tripwire", "easy_retrieve")]))

    entry = cr.anchor_block(arms[0], arms, anchors, {"context_recall": 0.02}, {})[0]
    arm_entry = entry["arms"][arms[1].label]

    assert arm_entry["alarms"] == ["context_recall"]
    assert arm_entry["alarm_thresholds"]["context_recall"] == pytest.approx(0.02)


def test_reasoning_anchor_is_a_trend_not_an_alarm(_artifact, anchors_file):
    base = _artifact([_row("think", anchor_type="reasoning", faithfulness=0.9)])
    treat = _artifact([_row("think", anchor_type="reasoning", faithfulness=0.1)])
    arms = cr.load_arms([str(base), str(treat)])
    anchors = cr.anchor_questions(anchors_file([_anchor("think", "reasoning")]))

    entry = cr.anchor_block(arms[0], arms, anchors, {}, {})[0]

    assert entry["anchor_type"] == "reasoning"
    assert entry["arms"][arms[1].label]["alarms"] == []
    assert entry["arms"][arms[1].label]["deltas"]["faithfulness"] == pytest.approx(-0.8)


def test_should_refuse_heuristic_reads_the_answer():
    declined = (
        "I don't have FASRC documentation covering MIT's Engaging cluster - it is "
        "a separate institution's HPC system, so you should contact MIT Research "
        "Computing."
    )
    invented = (
        "Engaging has the sched_mit_hill and newnodes partitions for general use, "
        "plus a dedicated L40S pool. Request one with --gres=gpu:l40s:1 and set "
        "--partition=sched_mit_hill in your submit script, then load the CUDA "
        "module before launching your job with srun as usual on that cluster."
    )

    assert cr.refusal_verdict(declined)[0] == "PASS"
    assert cr.refusal_verdict(invented)[0] == "FAIL"
    assert cr.refusal_verdict("")[0] == "REVIEW"
    assert cr.refusal_verdict(declined)[1] == declined[:160]


def test_should_refuse_uses_the_qa_item_pass_when_a_run_covers_it(
    _artifact, anchors_file, tmp_path
):
    question = "What is the GPU partition layout on another university's cluster?"
    # A confident, non-declining answer: the heuristic alone would say FAIL.
    invented = (
        "Its partitions are alpha, beta and gamma; request one with -p alpha. " * 4
    )
    rows = [
        _row(question, reference="decline and refer", answer=invented, faithfulness=0.5)
    ]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    anchors_path = anchors_file([_anchor(question, "should_refuse")])
    item_id = derive_item_id(question, "decline and refer")
    run_dir = _qa_run(tmp_path / "qa-treat", item_id, item_pass_rate=1.0)

    arms = cr.load_arms([base, treat])
    anchors = cr.anchor_questions(anchors_path)
    qa = {arms[1].label: cr.load_qa_run(run_dir)}
    entry = cr.anchor_block(arms[0], arms, anchors, {}, qa)[0]

    assert entry["arms"][arms[0].label]["refusal"] == "FAIL"
    assert entry["arms"][arms[1].label]["refusal"] == "PASS"
    assert entry["arms"][arms[1].label]["refusal_source"] == "qa"


# --- slices ------------------------------------------------------------------


def test_slices_appear_only_for_fields_present_in_both_arms(_artifact):
    base_rows = [
        _row(f"q{i}", anchor_type="reasoning", difficulty="hard", faithfulness=0.5)
        for i in range(3)
    ]
    treat_rows = [
        _row(f"q{i}", anchor_type="reasoning", faithfulness=0.6) for i in range(3)
    ]
    arms = cr.load_arms([str(_artifact(base_rows)), str(_artifact(treat_rows))])

    slices = cr.slice_block(arms[0], arms, [f"q{i}" for i in range(3)], {})

    assert {entry["field"] for entry in slices} == {"anchor_type"}


def test_small_slices_are_marked_directional(_artifact):
    rows = [
        _row(f"easy{i}", difficulty="easy", faithfulness=0.5) for i in range(12)
    ] + [_row("hard1", difficulty="hard", faithfulness=0.5)]
    treat = [
        _row(f"easy{i}", difficulty="easy", faithfulness=0.6) for i in range(12)
    ] + [_row("hard1", difficulty="hard", faithfulness=0.9)]
    arms = cr.load_arms([str(_artifact(rows)), str(_artifact(treat))])
    questions = [row["question"] for row in rows]

    slices = cr.slice_block(arms[0], arms, questions, {})
    by_value = {
        entry["value"]: entry
        for entry in slices
        if entry["field"] == "difficulty" and entry["metric"] == "faithfulness"
    }

    assert by_value["easy"]["directional"] is False
    assert by_value["hard"]["directional"] is True
    assert by_value["hard"]["n"] == 1


# --- timing ------------------------------------------------------------------


def test_timing_reports_mean_p90_and_warm_variants(_artifact):
    rows = [
        _row("q1", time_elapsed=100.0),
        _row("q2", time_elapsed=1.0),
        _row("q3", time_elapsed=2.0),
        _row("q4", time_elapsed=3.0),
    ]
    arm = cr.load_arms([str(_artifact(rows))])[0]

    block = cr.timing_block([arm])[0]

    assert block["mean"] == pytest.approx(26.5)
    assert block["p90"] == pytest.approx(100.0)  # nearest rank: ceil(0.9*4)=4
    assert block["warm_n"] == 3
    assert block["warm_mean"] == pytest.approx(2.0)
    assert block["warm_p90"] == pytest.approx(3.0)


def test_percentile_uses_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    assert cr.percentile(values, 90) == 9.0
    assert cr.percentile(values, 50) == 5.0
    assert cr.percentile([], 90) is None


# --- sources -----------------------------------------------------------------


def test_source_block_reports_and_recomputes_source_accuracy(_artifact):
    rows = [
        _row("hit", sources=[{"url": "u1", "matched": True}]),
        _row("miss", sources=[{"url": "u2", "matched": False}]),
        _row(
            "partial",
            sources=[{"url": "u3", "matched": True}, {"url": "u4", "matched": False}],
        ),
        _row("declares nothing"),
    ]
    arm = cr.load_arms([str(_artifact(rows, total={"source_accuracy": 0.9}))])[0]

    block = cr.source_block([arm])[0]

    assert block["source_accuracy"] == pytest.approx(0.9)
    assert block["source_scored_count"] == 3
    assert block["recomputed_scored"] == 3  # the zero-source row is excluded
    assert block["recomputed_hits"] == 1  # strict: 'partial' is a miss
    assert block["recomputed_accuracy"] == pytest.approx(1 / 3)


# --- the optional QA join ----------------------------------------------------


def _qa_run(
    directory, item_id, *, item_pass_rate=1.0, atom_score=0.8, durations=(1000,)
):
    """Write the three artifacts ``archi eval qa`` leaves in a run directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "overall_attempt_pass_rate": item_pass_rate,
                "macro_mean_item_pass_rate": item_pass_rate,
                "macro_mean_scored_attempt_atom_score": atom_score,
                "items": [
                    {
                        "item_id": item_id,
                        "k": len(durations),
                        "item_pass_rate": item_pass_rate,
                        "gold_atom_pass_rates": [],
                    }
                ],
            }
        )
    )
    (directory / "answers.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "item_id": item_id,
                    "attempt_id": f"a{n}",
                    "ordinal": n,
                    "status": "answer_ready",
                    "duration_ms": duration,
                    "tool_calls": [],
                    "answer": "an answer",
                }
            )
            + "\n"
            for n, duration in enumerate(durations, 1)
        )
    )
    (directory / "evaluation_results.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "item_id": item_id,
                    "attempt_id": f"a{n}",
                    "status": "evaluated",
                    "passed": item_pass_rate >= 1.0,
                    "atom_score": atom_score,
                    "required_atom_recall": 1.0,
                    "judgments": [],
                }
            )
            + "\n"
            for n, _ in enumerate(durations, 1)
        )
    )
    return str(directory)


def test_qa_run_joins_by_derived_item_id(_artifact, tmp_path, capsys):
    question = "how do I request a GPU"
    reference = "use --gres=gpu:1"
    rows = [_row(question, reference=reference, faithfulness=0.5)]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    item_id = derive_item_id(question, reference)
    base_run = _qa_run(
        tmp_path / "qa-base",
        item_id,
        item_pass_rate=0.5,
        atom_score=0.4,
        durations=(1000, 3000),
    )
    treat_run = _qa_run(
        tmp_path / "qa-treat",
        item_id,
        item_pass_rate=1.0,
        atom_score=0.9,
        durations=(2000,),
    )
    arms = cr.load_arms([base, treat])

    code = cr.main(
        [
            base,
            treat,
            "--qa-run",
            f"{arms[0].label}={base_run}",
            "--qa-run",
            f"{arms[1].label}={treat_run}",
        ]
    )

    assert code == cr.EXIT_OK
    out = capsys.readouterr().out
    assert "## QA runs" in out
    assert "1 joined" in out


def test_qa_block_reports_rates_durations_and_paired_deltas(_artifact, tmp_path):
    question = "how do I request a GPU"
    reference = "use --gres=gpu:1"
    rows = [
        _row(question, reference=reference, faithfulness=0.5),
        _row("na row", reference="N/A"),
    ]
    arms = cr.load_arms([str(_artifact(rows)), str(_artifact(rows))])
    item_id = derive_item_id(question, reference)
    qa = {
        arms[0].label: cr.load_qa_run(
            _qa_run(
                tmp_path / "b",
                item_id,
                item_pass_rate=0.5,
                atom_score=0.4,
                durations=(1000, 3000),
            )
        ),
        arms[1].label: cr.load_qa_run(
            _qa_run(
                tmp_path / "t",
                item_id,
                item_pass_rate=1.0,
                atom_score=0.9,
                durations=(2000,),
            )
        ),
    }

    block = cr.qa_block(arms[0], arms, qa)

    by_arm = {entry["arm"]: entry for entry in block["arms"]}
    assert by_arm[arms[0].label]["joined"] == 1  # the "N/A" reference row is skipped
    assert by_arm[arms[0].label]["macro_mean_item_pass_rate"] == pytest.approx(0.5)
    assert by_arm[arms[0].label]["mean_duration_ms"] == pytest.approx(2000.0)
    assert by_arm[arms[0].label]["p90_duration_ms"] == pytest.approx(3000.0)
    assert block["paired"][0]["metric"] == "item_pass_rate"
    assert block["paired"][0]["mean"] == pytest.approx(0.5)


def test_qa_run_spec_must_name_a_known_arm(_artifact, tmp_path):
    rows = [_row("q1", faithfulness=0.5)]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    run = _qa_run(tmp_path / "qa", "qa-nope")

    assert cr.main([base, treat, "--qa-run", f"nosucharm={run}"]) == cr.EXIT_USAGE
    assert cr.main([base, treat, "--qa-run", run]) == cr.EXIT_USAGE


# --- addressing and output ---------------------------------------------------


def test_path_at_n_selects_an_arm_and_baseline_picks_the_reference(_artifact, capsys):
    sweep = _artifact(
        arms=[
            [_row("q1", faithfulness=0.10)],
            [_row("q1", faithfulness=0.40)],
        ],
        fingerprint="corpus-1",
    )
    stem = sweep.stem

    assert (
        cr.main([f"{sweep}@2", f"{sweep}@1", "--baseline", f"{stem}@2"]) == cr.EXIT_OK
    )
    out = capsys.readouterr().out
    assert f"Baseline: `{stem}@2`" in out
    assert "-0.3000" in out  # arm 1 minus arm 2

    assert cr.main([f"{sweep}@9"]) == cr.EXIT_USAGE


def test_a_bare_sweep_path_expands_into_its_arms(_artifact, capsys):
    sweep = _artifact(
        arms=[[_row("q1", faithfulness=0.10)], [_row("q1", faithfulness=0.40)]],
        fingerprint="corpus-1",
    )

    assert cr.main([str(sweep)]) == cr.EXIT_OK
    assert "+0.3000" in capsys.readouterr().out


def test_a_single_arm_needs_a_second(_artifact):
    assert cr.main([str(_artifact([_row("q1", faithfulness=0.5)]))]) == cr.EXIT_USAGE


def test_markdown_has_every_section(_artifact, anchors_file, tmp_path, capsys):
    question = "how do I request a GPU"
    rows = [
        _row(
            question,
            reference="use --gres=gpu:1",
            difficulty="easy",
            anchor_type="reasoning",
            faithfulness=0.5,
            sources=[{"url": "u", "matched": True}],
        ),
        _row(
            "tripwire", anchor_type="easy_retrieve", difficulty="easy", faithfulness=0.9
        ),
    ]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    anchors = anchors_file([_anchor("tripwire", "easy_retrieve")])
    arms = cr.load_arms([base, treat])
    run = _qa_run(tmp_path / "qa", derive_item_id(question, "use --gres=gpu:1"))
    out_json = tmp_path / "report.json"

    code = cr.main(
        [
            base,
            treat,
            "--anchors",
            anchors,
            "--noise-floor",
            "faithfulness=0.01",
            "--qa-run",
            f"{arms[1].label}={run}",
            "--json",
            str(out_json),
        ]
    )

    assert code == cr.EXIT_OK
    out = capsys.readouterr().out
    for heading in (
        "## Provenance",
        "## Gates",
        "## Paired metrics",
        "## Scored counts",
        "## Sources",
        "## Anchors",
        "## Slices",
        "## Timing",
        "## QA runs",
    ):
        assert heading in out

    report = json.loads(out_json.read_text())
    assert report["baseline"] == arms[0].label
    assert report["noise_floor"] == {"faithfulness": 0.01}
    assert [entry["question"] for entry in report["anchors"]] == ["tripwire"]
    assert report["counts"] == {"asked": 2, "anchors": 1, "bank_rows": 1}
    assert cr.render_markdown(report) == out.rstrip("\n")


def test_json_report_never_carries_a_non_finite_number(_artifact, tmp_path):
    rows = [
        _row("q1", faithfulness=float("nan")),
        _row("q2", faithfulness=float("nan")),
    ]
    base = str(_artifact(rows, fingerprint="corpus-1"))
    treat = str(_artifact(rows, fingerprint="corpus-1"))
    out_json = tmp_path / "report.json"

    assert cr.main([base, treat, "--json", str(out_json)]) == cr.EXIT_OK

    # json.loads with parse_constant proves no bare NaN/Infinity was written.
    json.loads(
        out_json.read_text(),
        parse_constant=lambda token: pytest.fail(f"report contains {token}"),
    )


def test_a_small_slice_is_never_called_significant(_artifact):
    # Under G7 alone this slice clears both 2*SE and 2*sigma. At n=3 that is an
    # arithmetic accident, not a measurement, so the slice reports its direction.
    # Found by the real-artifact smoke: the 2-row should_refuse slice of two
    # same-code runs printed SIGNIFICANT on faithfulness.
    base_rows = [_row(f"h{i}", difficulty="hard", faithfulness=0.5) for i in range(3)]
    treat_rows = [
        _row("h0", difficulty="hard", faithfulness=0.70),
        _row("h1", difficulty="hard", faithfulness=0.71),
        _row("h2", difficulty="hard", faithfulness=0.69),
    ]
    arms = cr.load_arms([str(_artifact(base_rows)), str(_artifact(treat_rows))])
    questions = [f"h{i}" for i in range(3)]

    assert (
        cr.verdict(
            cr.summarize_deltas(
                cr.paired_deltas(arms[0], arms[1], "faithfulness", questions)
            ),
            0.001,
        )
        == "SIGNIFICANT"
    )

    entry = [
        row
        for row in cr.slice_block(arms[0], arms, questions, {"faithfulness": 0.001})
        if row["metric"] == "faithfulness"
    ][0]

    assert entry["directional"] is True
    assert entry["verdict"] != "SIGNIFICANT"
    assert "directional" in entry["verdict"]


def test_refusal_heuristic_reads_the_real_no_coverage_phrasings():
    # Both answers are verbatim openings from the should_refuse anchor in
    # bench_out/benchmarking-ragas-205-20260817_{040939,052454}.json. The second
    # declines without ever saying "I"; a first-person-only rule marked it FAIL.
    first_person = (
        "I don't have information about MIT's Engaging cluster in the FASRC "
        "documentation I have access to. The sources I can retrieve cover "
        "Harvard's FASRC clusters (like Cannon), not MIT's systems."
    )
    impersonal = (
        "The indexed FASRC documentation does not appear to cover MIT's Engaging "
        "cluster or its GPU partition layout. The available sources focus on "
        "Harvard's FASRC clusters and their GPU partitions."
    )

    assert cr.refusal_verdict(first_person)[0] == "PASS"
    assert cr.refusal_verdict(impersonal)[0] == "PASS"
