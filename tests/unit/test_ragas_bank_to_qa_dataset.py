"""Unit tests for the RAGAS bank -> ``qa-dataset-v2`` converter.

Covers ``scripts/benchmarking/ragas_bank_to_qa_dataset.py``. The script exists so
the #396 feature matrix can score the SAME questions with the gold-atoms
evaluator that the RAGAS harness scores, so the tests pin the three properties
that make the two runs comparable:

* the question set is the harness's own -- bank rows plus the anchor file,
  deduped on exact ``user_input`` with the bank row winning;
* item ids are the content-derived ``qa-<sha256[:20]>`` a later compare step can
  recompute from a RAGAS artifact's ``question`` + ``reference_answer``, byte
  stable across runs and identical for a legacy ``question``/``answer`` bank;
* nothing is converted quietly wrong: a row carrying both dialect spellings, a
  duplicate row, a row without a reference, and a file that is already a QA
  dataset are all refused by name rather than silently mapped.

``scripts/`` reports no lines to diff-cover (the gate measures ``--cov=src``), so
these named tests are the acceptance bar for the script, not the coverage line.
"""

import json
import os
from pathlib import Path

import pytest

from scripts.benchmarking import ragas_bank_to_qa_dataset as converter
from src.evaluation.qa.dataset import derive_item_id, iter_dataset_items

# The tracked 5-row anchor bank ships with the repo; the 105-row FASRC bank lives
# in the separate archi-config checkout, which deployments have and CI does not.
ANCHOR_BANK = Path("examples/benchmarking/anchor_questions.json")
FASRC_BANK_ENV = "FASRC_RAGAS_BANK"

BANK_ROW = {
    "user_input": "How do I request a GPU on Cannon?",
    "reference": "Add #SBATCH --gpus=1 to the submission script.",
    "sources": ["https://docs.rc.fas.harvard.edu/kb/running-jobs"],
    "source_match_field": ["url"],
    "notes": "grounded in Running Jobs page text",
    "status": "draft",
    "anchor_type": "easy_retrieve",
}

SECOND_ROW = {
    "user_input": "Where do I put scratch data on Cannon?",
    "reference": "Under /n/holyscratch01/<lab>/, which is not backed up.",
    "sources": [],
    "status": "locked",
}


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run(tmp_path, rows, *, anchors=None, out_name="out.json", extra=()):
    """Convert ``rows``; ``anchors=None`` means ``--no-anchors``."""
    bank = _write(tmp_path / "bank.json", rows)
    out = tmp_path / out_name
    argv = [str(bank), "--out", str(out)]
    if anchors is None:
        argv.append("--no-anchors")
    else:
        argv += ["--anchors", str(_write(tmp_path / "anchors.json", anchors))]
    argv += list(extra)
    return converter.main(argv), out


def _items(out):
    return list(iter_dataset_items(out))


def test_round_trips_a_ragas_bank_to_a_v2_dataset_the_cli_reads(tmp_path):
    code, out = _run(tmp_path, [BANK_ROW, SECOND_ROW])

    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == (
        "qa-dataset-v2"
    )
    items = _items(out)
    assert [item.question for item in items] == [
        BANK_ROW["user_input"],
        SECOND_ROW["user_input"],
    ]
    assert [item.answer for item in items] == [
        BANK_ROW["reference"],
        SECOND_ROW["reference"],
    ]


def test_carried_fields_survive_into_every_item(tmp_path):
    code, out = _run(tmp_path, [BANK_ROW, SECOND_ROW])

    assert code == 0
    first, second = _items(out)
    assert first.extra == {
        "sources": BANK_ROW["sources"],
        "source_match_field": BANK_ROW["source_match_field"],
        "notes": BANK_ROW["notes"],
        "status": BANK_ROW["status"],
        "anchor_type": BANK_ROW["anchor_type"],
    }
    # An empty sources list is a fact about the row (a should-refuse anchor
    # cites nothing); it must survive as [], not vanish.
    assert second.extra == {"sources": [], "status": "locked"}


def test_ids_are_content_derived_and_byte_stable_across_runs(tmp_path):
    code, first = _run(tmp_path, [BANK_ROW], out_name="first.json")
    again, second = _run(tmp_path, [BANK_ROW], out_name="second.json")

    assert (code, again) == (0, 0)
    assert first.read_bytes() == second.read_bytes()
    assert [item.id for item in _items(first)] == [
        derive_item_id(BANK_ROW["user_input"], BANK_ROW["reference"])
    ]


def test_an_explicit_id_is_kept(tmp_path):
    row = dict(BANK_ROW, id="qa-hand-authored-id")

    code, out = _run(tmp_path, [row])

    assert code == 0
    assert [item.id for item in _items(out)] == ["qa-hand-authored-id"]


def test_anchors_merge_on_user_input_with_the_bank_row_winning(tmp_path):
    shared = dict(BANK_ROW, notes="from the bank")
    anchor_twin = dict(BANK_ROW, notes="from the anchor file")
    fresh_anchor = dict(SECOND_ROW, anchor_type="should_refuse")

    code, out = _run(tmp_path, [shared], anchors=[anchor_twin, fresh_anchor])

    assert code == 0
    items = _items(out)
    assert [item.question for item in items] == [
        shared["user_input"],
        fresh_anchor["user_input"],
    ]
    assert items[0].extra["notes"] == "from the bank"


def test_no_anchors_converts_the_bank_alone(tmp_path):
    code, out = _run(tmp_path, [BANK_ROW])

    assert code == 0
    assert len(_items(out)) == 1


def test_refuses_a_row_carrying_both_user_input_and_question(tmp_path, capsys):
    row = dict(BANK_ROW, question="How do I request a GPU on Cannon (again)?")

    code, out = _run(tmp_path, [row])

    assert code == 2
    message = capsys.readouterr().err
    assert "'user_input'" in message and "'question'" in message
    assert not out.exists()


def test_refuses_a_native_qa_dataset_as_not_a_ragas_bank(tmp_path, capsys):
    native = {
        "schema_version": "qa-dataset-v2",
        "items": [
            {
                "id": "qa-native",
                "question": BANK_ROW["user_input"],
                "answer": BANK_ROW["reference"],
                "time_sensitive": False,
            }
        ],
    }

    code, out = _run(tmp_path, native)

    assert code == 2
    assert "not a RAGAS bank" in capsys.readouterr().err
    assert not out.exists()


def test_refuses_a_headerless_native_dataset_as_not_a_ragas_bank(tmp_path, capsys):
    # A native V1 array spells its question/answer the same way a LEGACY bank
    # does; ``time_sensitive`` is mandatory on every native row and absent from
    # every bank row, so it is what tells the two apart.
    native_rows = [
        {
            "id": "qa-native",
            "question": BANK_ROW["user_input"],
            "answer": BANK_ROW["reference"],
            "time_sensitive": False,
        }
    ]

    code, out = _run(tmp_path, native_rows)

    assert code == 2
    error = capsys.readouterr().err
    assert "not a RAGAS bank" in error and "time_sensitive" in error
    assert not out.exists()


def test_refuses_duplicate_rows_naming_the_row_numbers(tmp_path, capsys):
    code, out = _run(tmp_path, [BANK_ROW, SECOND_ROW, dict(BANK_ROW)])

    assert code == 2
    error = capsys.readouterr().err
    assert "duplicate" in error and "1" in error and "3" in error
    assert not out.exists()


def test_a_row_without_a_reference_fails_loudly(tmp_path, capsys):
    row = {key: value for key, value in BANK_ROW.items() if key != "reference"}

    code, out = _run(tmp_path, [row])

    assert code == 2
    assert "answer" in capsys.readouterr().err
    assert not out.exists()
    # A half-written file must not survive the refusal.
    assert list(tmp_path.glob("out.json*")) == []


def test_refuses_an_anchor_that_is_a_bank_row_up_to_line_endings(tmp_path, capsys):
    # The merge dedupes on RAW ``user_input`` because the harness does, but ids
    # are derived from newline-normalized text. A bank and an anchor file that
    # disagree only on CRLF/LF therefore reach the adapter as two rows with one
    # id. Refusing by row number is the intended outcome: silently dropping one
    # would give the QA run a different question set than the RAGAS run, and the
    # dataset cannot hold two items with the same content id.
    bank_row = dict(BANK_ROW, user_input="Which partition?\r\nAnd which flag?")
    anchor = dict(BANK_ROW, user_input="Which partition?\nAnd which flag?")

    code, out = _run(tmp_path, [bank_row], anchors=[anchor])

    assert code == 2
    error = capsys.readouterr().err
    assert "duplicate" in error and "1" in error and "2" in error
    assert not out.exists()


def test_concurrent_runs_to_one_output_do_not_share_a_staging_file(
    tmp_path, monkeypatch
):
    # A fixed staging name beside --out lets two conversions truncate each
    # other's half-written file and publish the wrong bank's bytes, because the
    # rename happens before anything re-reads the result.
    real_document = converter.v2_json_document
    staged = set()

    def spy(items):
        for chunk in real_document(items):
            staged.update(p.name for p in tmp_path.iterdir() if p.name.startswith("."))
            yield chunk

    monkeypatch.setattr(converter, "v2_json_document", spy)

    first, out = _run(tmp_path, [BANK_ROW])
    second, _again = _run(tmp_path, [BANK_ROW])

    assert (first, second) == (0, 0)
    assert len(staged) == 2
    assert all(name.startswith(".out.json") for name in staged)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.json", "out.json"]


def test_a_legacy_question_answer_bank_converts_to_the_same_ids(tmp_path):
    legacy = {
        "question": BANK_ROW["user_input"],
        "answer": BANK_ROW["reference"],
        "sources": BANK_ROW["sources"],
        "source_match_field": BANK_ROW["source_match_field"],
        "notes": BANK_ROW["notes"],
        "status": BANK_ROW["status"],
        "anchor_type": BANK_ROW["anchor_type"],
    }

    modern_code, modern = _run(tmp_path, [BANK_ROW], out_name="modern.json")
    legacy_code, converted = _run(tmp_path, [legacy], out_name="legacy.json")

    assert (modern_code, legacy_code) == (0, 0)
    assert converted.read_bytes() == modern.read_bytes()


def test_the_output_carries_no_ragas_alias_keys(tmp_path):
    code, out = _run(tmp_path, [BANK_ROW, SECOND_ROW])

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert '"user_input"' not in text
    assert '"reference"' not in text


def test_status_filters_rows_before_conversion(tmp_path):
    draft_code, drafts = _run(
        tmp_path,
        [BANK_ROW, SECOND_ROW],
        out_name="draft.json",
        extra=["--status", "draft"],
    )
    locked_code, locked = _run(
        tmp_path,
        [BANK_ROW, SECOND_ROW],
        out_name="locked.json",
        extra=["--status", "locked"],
    )

    assert (draft_code, locked_code) == (0, 0)
    assert [item.question for item in _items(drafts)] == [BANK_ROW["user_input"]]
    assert [item.question for item in _items(locked)] == [SECOND_ROW["user_input"]]


def test_json_report_carries_the_counts_and_the_output_sha256(tmp_path, capsys):
    code, out = _run(
        tmp_path,
        [BANK_ROW],
        anchors=[dict(BANK_ROW), SECOND_ROW],
        extra=["--json"],
    )

    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["import_dialect"] == "ragas"
    assert "sources" in report["carried_fields"]
    assert report["bank_rows"] == 1
    assert report["anchors_added"] == 1
    assert report["anchors_skipped"] == 1
    assert report["items"] == 2
    assert report["sha256"] == converter.sha256_file(out)


def test_a_missing_bank_file_is_a_usage_error(tmp_path, capsys):
    code = converter.main(
        [str(tmp_path / "absent.json"), "--out", str(tmp_path / "out.json")]
    )

    assert code == 1
    assert "cannot read" in capsys.readouterr().err


def test_a_bad_flag_exits_with_the_usage_status(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        converter.main([str(tmp_path / "bank.json")])  # no --out

    assert excinfo.value.code == 1


def test_the_output_path_must_be_json(tmp_path, capsys):
    code, _out = _run(tmp_path, [BANK_ROW], out_name="out.jsonl")

    assert code == 1
    assert ".json" in capsys.readouterr().err


def test_the_tracked_anchor_file_converts_to_five_items(tmp_path):
    anchors = json.loads(ANCHOR_BANK.read_text(encoding="utf-8"))

    code, out = _run(tmp_path, anchors)

    assert code == 0
    assert len(_items(out)) == 5


@pytest.mark.skipif(
    not os.environ.get(FASRC_BANK_ENV),
    reason=f"set {FASRC_BANK_ENV} to the FASRC bank to run the real-bank check",
)
def test_the_real_fasrc_bank_and_anchors_produce_109_items(tmp_path):
    bank = Path(os.environ[FASRC_BANK_ENV])
    with_anchors = tmp_path / "with-anchors.json"
    without = tmp_path / "without-anchors.json"

    merged_code = converter.main([str(bank), "--out", str(with_anchors)])
    alone_code = converter.main([str(bank), "--no-anchors", "--out", str(without)])

    assert (merged_code, alone_code) == (0, 0)
    # 105 bank rows + 5 anchors - 1 anchor already in the bank.
    assert len(_items(with_anchors)) == 109
    assert len(_items(without)) == 105
