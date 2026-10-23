"""Only the exact strings PASS and FAIL are Lynx verdicts (#734, D4)."""

import pytest

from scripts.benchmarking.lynx import parse


@pytest.mark.parametrize("score", ["PASS", "FAIL"])
def test_pass_and_fail_parse_to_verdicts(score):
    result = parse.parse_output('{"REASONING": "...", "SCORE": "%s"}' % score)
    assert result.verdict == score
    assert result.error is None
    assert result.reasoning == "..."


def test_spaces_around_the_score_are_stripped():
    assert parse.parse_output('{"SCORE": "  PASS\\n"}').verdict == "PASS"


def test_text_around_the_object_is_ignored():
    output = 'Here is my answer:\n{"REASONING": ["a", "b"], "SCORE": "FAIL"}\nDone.'
    result = parse.parse_output(output)
    assert result.verdict == "FAIL"
    assert result.reasoning == ["a", "b"]


@pytest.mark.parametrize(
    "output",
    [
        '{"REASONING": "...", "SCORE": "pass."}',
        '{"REASONING": "...", "SCORE": "pass"}',
        '{"REASONING": "...", "SCORE": "PASS."}',
        '{"REASONING": "...", "SCORE": "Pass"}',
        '{"REASONING": "...", "SCORE": 1}',
        '{"REASONING": "...", "SCORE": null}',
        '{"REASONING": "..."}',
        "PASS",
        "The answer is faithful. SCORE: PASS",
        "",
        '{"REASONING": "unterminated", "SCORE": "PASS"',
        '{"REASONING": "a", "SCORE": "PASS"} {"REASONING": "b", "SCORE": "FAIL"}',
        '["PASS"]',
        '{"REASONING": NaN, "SCORE": "PASS"}',
        '{"REASONING": [Infinity], "SCORE": "FAIL"}',
        # A cut-off outer object must not yield the verdict of an inner one.
        '{"REASONING": {"SCORE": "PASS"}, "SCORE": "FAIL"',
        '{"REASONING": ["a", {"SCORE": "PASS"}], "SCORE": "FA',
        'Note {draft} {"REASONING": "r", "SCORE": "PASS"}',
    ],
)
def test_anything_else_is_a_parse_error(output):
    result = parse.parse_output(output)
    assert result.verdict is None
    assert isinstance(result.error, str) and result.error


def test_two_objects_with_the_same_score_give_that_score():
    output = '{"SCORE": "PASS"}\n{"REASONING": "again", "SCORE": "PASS"}'
    assert parse.parse_output(output).verdict == "PASS"


def test_a_second_object_without_a_score_does_not_change_the_verdict():
    output = '{"REASONING": "r", "SCORE": "FAIL"} {"note": "x"}'
    assert parse.parse_output(output).verdict == "FAIL"


def test_a_nested_score_is_not_read_as_a_second_object():
    output = '{"REASONING": {"SCORE": "FAIL"}, "SCORE": "PASS"}'
    assert parse.parse_output(output).verdict == "PASS"


def test_braces_inside_a_complete_object_are_fine():
    output = '{"REASONING": "uses {name} and }", "SCORE": "PASS"}'
    assert parse.parse_output(output).verdict == "PASS"


def test_a_brace_that_starts_no_object_names_its_offset():
    error = parse.parse_output('{"SCORE": "PASS"} {broken').error
    assert "offset 18" in error


def test_the_errors_name_the_reason():
    assert "no JSON object" in parse.parse_output("nothing here").error
    assert "SCORE" in parse.parse_output('{"REASONING": "r"}').error
    assert "'pass'" in parse.parse_output('{"SCORE": "pass"}').error
    conflicting = parse.parse_output('{"SCORE": "PASS"} {"SCORE": "FAIL"}').error
    assert "PASS" in conflicting and "FAIL" in conflicting
