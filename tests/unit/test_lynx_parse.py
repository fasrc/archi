"""Only the exact strings PASS and FAIL are Lynx verdicts (#734, D4).

The parser reads JSON and the form Lynx writes: single-quoted reasoning items and a bare
SCORE word (openspec change accept-lynx-native-output, D1-D4).
"""

import json

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
        # A number too large for a float decodes to inf, which is not JSON either.
        '{"REASONING": [1e309], "SCORE": "PASS"}',
        '{"REASONING": {"x": -1e400}, "SCORE": "FAIL"}',
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


# What PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct at 5523f869 answered on 2026-10-24,
# verbatim: (output, verdict, number of reasoning items). None of them is JSON.
REAL_OUTPUTS = {
    "faithful_gpu": (
        """{"REASONING": ['The QUESTION asks about the maximum duration a GPU job can run.', 'The CONTEXT specifies that jobs on the FASRC gpu partition may run for at most 3 days.', 'The ANSWER states that a GPU job can run up to 3 days.', 'The ANSWER directly reflects the information provided in the CONTEXT, which sets a 3-day limit for GPU job runs.', 'Therefore, the ANSWER is faithful to the CONTEXT given the QUESTION.'], "SCORE": PASS}""",
        "PASS",
        5,
    ),
    "unfaithful_gpu": (
        """{"REASONING": ['The CONTEXT specifies that jobs on the FASRC gpu partition may run for at most 3 days.', 'The ANSWER states that a gpu job can run up to 7 days, which contradicts the information provided in the CONTEXT.', 'The CONTEXT does not mention any extension or variation in the job duration beyond the specified 3 days.', 'Therefore, the ANSWER is not faithful to the CONTEXT because it provides a duration that exceeds the maximum allowed by the FASRC gpu partition.'], "SCORE": FAIL}""",
        "FAIL",
        4,
    ),
    "faithful_scratch": (
        """{"REASONING": ['The CONTEXT explicitly states that files on netscratch that have not been modified for 90 days are deleted.', 'The CONTEXT also explicitly states that netscratch is not backed up.', 'The ANSWER correctly reflects the information provided in the CONTEXT by stating that netscratch is not backed up.', 'The QUESTION asks specifically about the backup status of netscratch, and the ANSWER directly addresses this by confirming that it is not backed up.'], "SCORE": PASS}""",
        "PASS",
        4,
    ),
    "unfaithful_scratch": (
        """{"REASONING": ['The CONTEXT explicitly states that netscratch is not backed up.', 'The ANSWER claims that netscratch is backed up nightly to tape, which directly contradicts the information provided in the CONTEXT.', 'The CONTEXT also mentions that files older than 90 days are deleted, which implies that there is no backup process in place.'], "SCORE": FAIL}""",
        "FAIL",
        3,
    ),
}


@pytest.mark.parametrize("name", sorted(REAL_OUTPUTS))
def test_a_real_lynx_output_parses_to_its_verdict(name):
    output, verdict, items = REAL_OUTPUTS[name]
    result = parse.parse_output(output)
    assert result.error is None
    assert result.verdict == verdict
    assert len(result.reasoning) == items
    assert all(isinstance(item, str) and item for item in result.reasoning)


def test_the_reasoning_items_of_a_real_output_read_whole():
    output, _, _ = REAL_OUTPUTS["unfaithful_scratch"]
    assert parse.parse_output(output).reasoning == [
        "The CONTEXT explicitly states that netscratch is not backed up.",
        "The ANSWER claims that netscratch is backed up nightly to tape, which directly"
        " contradicts the information provided in the CONTEXT.",
        "The CONTEXT also mentions that files older than 90 days are deleted, which"
        " implies that there is no backup process in place.",
    ]


@pytest.mark.parametrize(
    "item, text",
    [
        (
            "'The ANSWER's claim contradicts the CONTEXT.'",
            "The ANSWER's claim contradicts the CONTEXT.",
        ),
        ("'the users''", "the users'"),
        ("'It\\'s escaped.'", "It's escaped."),
        ("'a \\\\ b \\\" c \\n d \\t e \\r f'", 'a \\ b " c \n d \t e \r f'),
        ("'keeps \\d as written'", "keeps \\d as written"),
        ('"double-quoted, it\'s JSON"', "double-quoted, it's JSON"),
    ],
)
def test_a_single_quoted_item_keeps_its_apostrophes_and_reads_its_escapes(item, text):
    result = parse.parse_output('{"REASONING": [%s, \'next\'], "SCORE": FAIL}' % item)
    assert result.verdict == "FAIL"
    assert result.reasoning == [text, "next"]


@pytest.mark.parametrize(
    "output",
    [
        "{\"SCORE\": 'PASS'}",
        '{"REASONING": ["a"], "SCORE": PASS}',
        '{"REASONING": \'one string\', "SCORE": PASS}',
        'Here is my answer: {"REASONING": [\'a\'], "SCORE": PASS} Done.',
    ],
)
def test_a_quoted_or_bare_pass_is_a_verdict(output):
    assert parse.parse_output(output).verdict == "PASS"


@pytest.mark.parametrize(
    "output",
    [
        # Cut off: no closing brace, or in the middle of the verdict.
        '{"REASONING": [\'a\'], "SCORE": PASS',
        '{"REASONING": [\'a\'], "SCORE": PAS',
        '{"REASONING": [\'a\', {"SCORE": PASS}], "SCORE": FA',
        # A bare word that is not exactly PASS or FAIL.
        '{"REASONING": [\'a\'], "SCORE": PAS}',
        '{"REASONING": [\'a\'], "SCORE": pass}',
        '{"REASONING": [\'a\'], "SCORE": Pass}',
        '{"REASONING": [\'a\'], "SCORE": PASS.}',
        # Two Lynx objects with different verdicts.
        '{"REASONING": [\'a\'], "SCORE": PASS} {"REASONING": [\'b\'], "SCORE": FAIL}',
        # A bare word anywhere but the value of SCORE, and a single-quoted key.
        '{"REASONING": [maybe], "SCORE": PASS}',
        "{'REASONING': ['a'], \"SCORE\": PASS}",
        # Broken strings and objects.
        '{"REASONING": [\'unclosed], "SCORE": PASS}',
        '{"REASONING": [\'line\nbreak\'], "SCORE": PASS}',
        '{"REASONING": [\'a\'] "SCORE": PASS}',
        '{"REASONING": [\'a\'], "SCORE" PASS}',
        '{"REASONING": [\'a\'], "SCORE": PASS,}',
        "{\"REASONING\": ['a'}",
        # D2: an apostrophe before a comma ends the item early.
        '{"REASONING": [\'the users\', he said\'], "SCORE": PASS}',
    ],
)
def test_the_lynx_form_is_a_parse_error_when_it_is_malformed(output):
    result = parse.parse_output(output)
    assert result.verdict is None
    assert isinstance(result.error, str) and result.error


def test_the_lynx_form_errors_name_the_reason():
    assert "'pass'" in parse.parse_output('{"SCORE": pass}').error
    assert (
        "'maybe'" in parse.parse_output('{"REASONING": [maybe], "SCORE": PASS}').error
    )
    assert "closing quote" in parse.parse_output('{"REASONING": [\'open').error


JSON_OBJECTS = [
    "{}",
    '{"a": [1, -2.5e-3, 1E+2, 0, -0.0, true, false, null]}',
    '{"a": {"b": [[], {}, [{"c": "d"}]]}}',
    '{"u": "\\u00e9\\n\\"q\\" \\/ \\\\", "s": "it\'s"}',
    '{"a": 1, "a": 2}',
    '{"t": "uses {braces}, [brackets] and \'quotes\'"}',
    '{ "spaced" :\t[ 1 ,2 ]\n}',
]


@pytest.mark.parametrize("text", JSON_OBJECTS)
def test_valid_json_reads_as_json_reads_it(text):
    ours = parse._read_object(text, 0)
    theirs = json.JSONDecoder().raw_decode(text)
    assert ours == theirs
    assert json.dumps(ours[0]) == json.dumps(theirs[0])


@pytest.mark.parametrize(
    "text",
    [
        '{"a": NaN}',
        '{"a": [Infinity]}',
        '{"a": -Infinity}',
        '{"a": 1e309}',
        '{"a": 1,}',
        '{"a": [1,]}',
        '{"a" 1}',
        "{a: 1}",
        '{"a": 01}',
        '{"a": .5}',
        '{"a": +1}',
        '{"a": [1, 2}',
        '{"a": "x',
        '{"a": tru}',
        '{"a": "tab\there"}',
        '{"a": 1',
    ],
)
def test_what_json_refuses_is_refused(text):
    with pytest.raises(ValueError):
        parse.strict_loads(text)
    with pytest.raises(ValueError):
        parse._read_object(text, 0)


def test_deep_nesting_is_a_parse_error_not_a_crash():
    # A crash would lose the whole run: the scorer cannot resume a bundle.
    output = '{"REASONING": ' + "[" * 5000 + "]" * 5000 + ', "SCORE": PASS}'
    result = parse.parse_output(output)
    assert result.verdict is None
    assert "recursion" in result.error
