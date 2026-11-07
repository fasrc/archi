"""compare.py lists a judge only once it has a scores file: a job that died before scoring
leaves an empty scores/<judge>/ folder (or only a checkpoint), which must not produce an
empty report section (fasrc/archi#737, step 5.3)."""

from __future__ import annotations

from scripts.benchmarking.judge_trial import compare as cmp


def test_judges_in_lists_only_judges_with_a_scores_file(tmp_path):
    (tmp_path / "scores" / "a-judge").mkdir(parents=True)
    (tmp_path / "scores" / "b-judge").mkdir()
    (tmp_path / "scores" / "c-judge").mkdir()
    (tmp_path / "scores" / "a-judge" / "run-arm0.judge_scores.json").write_text("{}")
    (tmp_path / "scores" / "c-judge" / "run-arm0.scores.abc.partial.jsonl").write_text(
        ""
    )
    assert cmp.judges_in(tmp_path) == ["a-judge"]


def test_judges_in_is_empty_without_a_scores_folder(tmp_path):
    assert cmp.judges_in(tmp_path) == []
