"""The Lynx scorer CLI over one judge bundle (#734, D3, D5, D6, D7)."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmarking.lynx import backends, score
from src.utils.judge_bundle import write_judge_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
BASENAME = "bench-20260101_000000"
PASS = '{"REASONING": ["fits the document"], "SCORE": "PASS"}'
FAIL = '{"REASONING": ["adds a claim"], "SCORE": "FAIL"}'


def _row(question, answer="An answer.", contexts=("A document.",)):
    return {
        "user_input": question,
        "retrieved_contexts": list(contexts),
        "response": answer,
        "reference": "A reference.",
    }


def _bundle(root, rows=None, keys=None, arm_index=0):
    rows = rows if rows is not None else [_row("Q1"), _row("Q2"), _row("Q3")]
    keys = keys if keys is not None else [f"question_{i + 1}" for i in range(len(rows))]
    out = write_judge_bundle(
        root / "judge_bundles",
        result_basename=BASENAME,
        arm_index=arm_index,
        arm_count=arm_index + 1,
        rows=rows,
        keys=keys,
        enabled_metrics=["faithfulness"],
        ragas_settings={"evaluator_model": "judge", "evaluator_model_revision": "r1"},
        verbosity=0,
        code_version={"digest": "sha256:code", "source": "test"},
        package_digest="sha256:packages",
    )
    return root / "judge_bundles" / out["folder"]


class _Factory:
    """Builds the given backend and records each call."""

    def __init__(self, backend):
        self.backend = backend
        self.calls = []

    def __call__(self, name, model, revision):
        self.calls.append((name, model, revision))
        return self.backend


def _run(folder, out_dir, backend=None, extra=()):
    factory = _Factory(backend or backends.StubBackend())
    argv = [
        str(folder),
        "--model",
        "PatronusAI/Lynx-8B",
        "--revision",
        "rev1",
        "--out-dir",
        str(out_dir),
        "--backend",
        "stub",
        *extra,
    ]
    return score.main(argv, backend_factory=factory), factory


def _lynx_file(out_dir, arm_index=0):
    return out_dir / f"{BASENAME}-arm{arm_index}.lynx.json"


def _digests(folder):
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _rewrite_rows(folder, data: bytes, row_count):
    """Replace rows.jsonl and make the manifest agree with it."""
    (folder / "rows.jsonl").write_bytes(data)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["rows_sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
    manifest["row_count"] = row_count
    (folder / "manifest.json").write_text(json.dumps(manifest))


def test_a_bundle_from_the_writer_scores_with_the_stub(tmp_path):
    folder = _bundle(tmp_path)
    code, factory = _run(folder, tmp_path)

    assert code == 0
    assert factory.calls == [("stub", "PatronusAI/Lynx-8B", "rev1")]
    document = json.loads(_lynx_file(tmp_path).read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    assert document["model_id"] == "PatronusAI/Lynx-8B"
    assert document["model_revision"] == "rev1"
    assert document["backend"] == "stub"
    assert document["result_basename"] == BASENAME
    assert document["arm_index"] == 0
    assert document["bundle_rows_sha256"] == manifest["rows_sha256"]
    assert document["token_budget"] == {"context_tokens": 8000, "max_new_tokens": 600}
    assert sorted(document["rows"]) == ["question_1", "question_2", "question_3"]
    assert document["rows"]["question_1"] == {
        "verdict": "PASS",
        "status": "scored",
        "reasoning": ["stub backend"],
        "prompt_tokens": document["rows"]["question_1"]["prompt_tokens"],
    }
    assert document["counts"] == {
        "lynx_scored": 3,
        "lynx_pass": 3,
        "lynx_skipped_overflow": 0,
        "lynx_parse_errors": 0,
    }
    assert document["lynx_faithfulness"] == 1.0


def test_the_lynx_file_goes_to_the_arm_index_of_the_manifest(tmp_path):
    folder = _bundle(tmp_path, arm_index=2)
    code, _ = _run(folder, tmp_path)
    assert code == 0
    assert json.loads(_lynx_file(tmp_path, 2).read_text())["arm_index"] == 2


def test_a_row_that_does_not_fit_is_skipped_and_never_sent(tmp_path):
    folder = _bundle(
        tmp_path, rows=[_row("BIG question"), _row("small question"), _row("Q3")]
    )
    stub = backends.StubBackend(
        token_counter=lambda text: 7401 if "BIG" in text else 7400
    )
    code, _ = _run(folder, tmp_path, backend=stub)

    assert code == 0
    assert len(stub.received) == 2
    assert not any("BIG" in prompt for prompt in stub.received)
    rows = json.loads(_lynx_file(tmp_path).read_text())["rows"]
    assert rows["question_1"] == {
        "verdict": None,
        "status": "skipped_overflow",
        "reasoning": None,
        "prompt_tokens": 7401,
    }
    assert rows["question_2"]["status"] == "scored"
    assert rows["question_2"]["prompt_tokens"] == 7400


def test_the_rate_divides_by_scored_rows_only(tmp_path):
    folder = _bundle(
        tmp_path,
        rows=[
            _row("pass one"),
            _row("pass two"),
            _row("fail one"),
            _row("BIG overflow"),
            _row("garbage one"),
        ],
    )

    def answer(prompt):
        if "fail one" in prompt:
            return FAIL
        if "garbage one" in prompt:
            return "I think it is fine."
        return PASS

    stub = backends.StubBackend(
        output=answer, token_counter=lambda text: 9000 if "BIG" in text else 10
    )
    code, _ = _run(folder, tmp_path, backend=stub)

    assert code == 0
    document = json.loads(_lynx_file(tmp_path).read_text())
    assert document["counts"] == {
        "lynx_scored": 3,
        "lynx_pass": 2,
        "lynx_skipped_overflow": 1,
        "lynx_parse_errors": 1,
    }
    assert document["lynx_faithfulness"] == pytest.approx(2 / 3)
    error_row = document["rows"]["question_5"]
    assert error_row["verdict"] is None
    assert error_row["status"] == "parse_error"
    assert error_row["output"] == "I think it is fine."
    assert "no JSON object" in error_row["error"]
    assert document["rows"]["question_3"]["verdict"] == "FAIL"


def test_a_non_finite_number_in_one_output_fails_only_that_row(tmp_path):
    folder = _bundle(tmp_path, rows=[_row("huge one"), _row("Q2")])

    def answer(prompt):
        if "huge one" in prompt:
            return '{"REASONING": [1e309], "SCORE": "PASS"}'
        return PASS

    code, _ = _run(folder, tmp_path, backend=backends.StubBackend(output=answer))

    assert code == 0
    document = json.loads(_lynx_file(tmp_path).read_text())
    assert document["rows"]["question_1"]["status"] == "parse_error"
    assert document["rows"]["question_2"]["verdict"] == "PASS"
    assert document["counts"]["lynx_parse_errors"] == 1


def test_nothing_scored_gives_a_null_rate(tmp_path):
    folder = _bundle(tmp_path, rows=[_row("Q1")])
    stub = backends.StubBackend(output="no verdict")
    code, _ = _run(folder, tmp_path, backend=stub)
    assert code == 0
    document = json.loads(_lynx_file(tmp_path).read_text())
    assert document["counts"]["lynx_scored"] == 0
    assert document["lynx_faithfulness"] is None


def test_an_empty_bundle_scores_nothing_and_calls_no_model(tmp_path):
    folder = _bundle(tmp_path, rows=[], keys=[])
    stub = backends.StubBackend()
    code, _ = _run(folder, tmp_path, backend=stub)
    assert code == 0
    assert stub.received == []
    document = json.loads(_lynx_file(tmp_path).read_text())
    assert document["rows"] == {}
    assert document["lynx_faithfulness"] is None


def _no_marker(folder):
    (folder / "BUNDLE_COMPLETE").unlink()


def _digest_mismatch(folder):
    data = bytearray((folder / "rows.jsonl").read_bytes())
    data[-3] = ord("X")
    (folder / "rows.jsonl").write_bytes(bytes(data))


def _wrong_folder_name(folder):
    folder.rename(folder.parent / f"{BASENAME}-arm7")


def _manifest_does_not_load(folder):
    (folder / "manifest.json").write_text("{not json")


def _malformed_row(folder):
    line = json.dumps({"key": "question_1", "user_input": "Q", "response": "A"})
    _rewrite_rows(folder, (line + "\n").encode(), 1)


def _contexts_not_strings(folder):
    row = {"key": "q", "user_input": "Q", "response": "A", "retrieved_contexts": [1]}
    _rewrite_rows(folder, (json.dumps(row) + "\n").encode(), 1)


def _row_not_json(folder):
    _rewrite_rows(folder, b"not json\n", 1)


def _row_count_differs(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["row_count"] = 99
    (folder / "manifest.json").write_text(json.dumps(manifest))


def _repeated_key(folder):
    row = json.dumps({"key": "question_1", **_row("Q")})
    _rewrite_rows(folder, (row + "\n" + row + "\n").encode(), 2)


@pytest.mark.parametrize(
    "damage, reason",
    [
        (_no_marker, "neither BUNDLE_COMPLETE nor READY"),
        (_digest_mismatch, "rows_sha256"),
        (_wrong_folder_name, "folder name"),
        (_manifest_does_not_load, "manifest.json"),
        (_malformed_row, "line 1"),
        (_contexts_not_strings, "line 1"),
        (_row_not_json, "line 1"),
        (_row_count_differs, "row_count"),
        (_repeated_key, "question_1"),
    ],
)
def test_a_bad_bundle_is_refused_before_any_backend(tmp_path, capsys, damage, reason):
    folder = _bundle(tmp_path)
    damage(folder)
    folder = next((tmp_path / "judge_bundles").iterdir())
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    code, factory = _run(folder, out_dir)

    assert code == 1
    assert factory.calls == []
    assert list(out_dir.iterdir()) == []
    assert reason in capsys.readouterr().err


def test_a_folder_that_is_not_there_is_refused(tmp_path):
    code, factory = _run(tmp_path / "missing", tmp_path)
    assert code == 1
    assert factory.calls == []


def test_a_ready_queue_copy_scores(tmp_path):
    folder = _bundle(tmp_path)
    queued = tmp_path / "queue" / folder.name
    queued.mkdir(parents=True)
    for name in ("rows.jsonl", "manifest.json"):
        shutil.copyfile(folder / name, queued / name)
    (queued / "READY").write_bytes(b"")
    out_dir = tmp_path / "queue" / "results"
    out_dir.mkdir()

    code, _ = _run(queued, out_dir)

    assert code == 0
    assert _lynx_file(out_dir).is_file()


def test_the_scorer_leaves_the_bundle_unchanged(tmp_path):
    folder = _bundle(tmp_path)
    before = _digests(folder)
    code, _ = _run(folder, tmp_path)
    assert code == 0
    assert _digests(folder) == before
    assert _lynx_file(tmp_path).is_file()


@pytest.mark.parametrize("inside", ["", "sub"])
def test_an_out_dir_in_the_bundle_is_refused(tmp_path, capsys, inside):
    folder = _bundle(tmp_path)
    out_dir = folder / inside if inside else folder
    out_dir.mkdir(exist_ok=True)
    before = _digests(folder)

    code, factory = _run(folder, out_dir)

    assert code == 1
    assert factory.calls == []
    assert _digests(folder) == before
    assert "inside the bundle" in capsys.readouterr().err


def test_an_out_dir_that_is_not_there_is_refused(tmp_path):
    folder = _bundle(tmp_path)
    code, factory = _run(folder, tmp_path / "no-such-folder")
    assert code == 1
    assert factory.calls == []


def test_an_existing_lynx_file_is_not_replaced(tmp_path, capsys):
    folder = _bundle(tmp_path)
    _lynx_file(tmp_path).write_text("old\n")

    code, factory = _run(folder, tmp_path)

    assert code == 1
    assert factory.calls == []
    assert _lynx_file(tmp_path).read_text() == "old\n"
    assert "exists" in capsys.readouterr().err


def test_a_lynx_file_that_appears_during_scoring_is_not_replaced(tmp_path):
    folder = _bundle(tmp_path)

    def answer(prompt):
        if not _lynx_file(tmp_path).exists():
            _lynx_file(tmp_path).write_text("other\n")
        return PASS

    code, _ = _run(folder, tmp_path, backend=backends.StubBackend(output=answer))

    assert code == 1
    assert _lynx_file(tmp_path).read_text() == "other\n"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("flag", ["--model", "--revision"])
def test_an_empty_model_or_revision_is_refused(tmp_path, flag):
    folder = _bundle(tmp_path)
    code, factory = _run(folder, tmp_path, extra=(flag, " "))
    assert code == 1
    assert factory.calls == []


def test_the_output_is_canonical_json(tmp_path):
    folder = _bundle(tmp_path)
    _run(folder, tmp_path)
    data = _lynx_file(tmp_path).read_bytes()
    canonical = json.dumps(json.loads(data), sort_keys=True, indent=2, allow_nan=False)
    assert data == (canonical + "\n").encode("utf-8")


def test_a_backend_that_drops_an_output_fails_loudly(tmp_path):
    class Short(backends.StubBackend):
        def generate(self, prompts):
            return super().generate(prompts)[:-1]

    folder = _bundle(tmp_path)
    with pytest.raises(RuntimeError, match="3 prompts"):
        _run(folder, tmp_path, backend=Short())
    assert not _lynx_file(tmp_path).exists()


def test_the_module_runs_as_a_cli(tmp_path):
    folder = _bundle(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.benchmarking.lynx.score",
            str(folder),
            "--model",
            "stub",
            "--revision",
            "0",
            "--out-dir",
            str(tmp_path),
            "--backend",
            "stub",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "lynx_scored 3" in result.stdout
    assert _lynx_file(tmp_path).is_file()
