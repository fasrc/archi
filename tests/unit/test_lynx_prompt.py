"""The Lynx prompt is the model-card template with three fields filled (#734, D2)."""

from pathlib import Path

from scripts.benchmarking.lynx import prompt

FIXTURE = Path(__file__).parent / "fixtures" / "lynx_prompt_template.tmpl"


def _template() -> str:
    # The PROMPT string of the PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct model
    # card (README revision 5523f869), copied byte for byte.
    return FIXTURE.read_bytes().decode("utf-8")


def _row(**overrides):
    row = {
        "key": "question_1",
        "user_input": "Where do I run jobs?",
        "retrieved_contexts": ["Run jobs on compute nodes.", "Login nodes are shared."],
        "response": "Run them on compute nodes.",
        "reference": "On the compute nodes, never the login nodes.",
    }
    row.update(overrides)
    return row


def test_the_prompt_is_the_template_with_three_fields_filled():
    row = _row()
    expected = (
        _template()
        .replace("{question}", row["user_input"])
        .replace("{context}", "Run jobs on compute nodes.\n\nLogin nodes are shared.")
        .replace("{answer}", row["response"])
        .replace("{{", "{")
        .replace("}}", "}")
    )
    assert prompt.build_prompt(row).encode("utf-8") == expected.encode("utf-8")


def test_the_template_constant_equals_the_model_card_copy():
    assert prompt.LYNX_PROMPT_TEMPLATE.encode("utf-8") == FIXTURE.read_bytes()


def test_the_prompt_keeps_the_leading_and_final_newline_of_the_card():
    text = prompt.build_prompt(_row())
    assert text.startswith("\nGiven the following QUESTION, DOCUMENT and ANSWER")
    assert text.endswith(
        '{"REASONING": <your reasoning as bullet points>, '
        '"SCORE": <your final score>}\n'
    )


def test_contexts_join_with_a_blank_line():
    text = prompt.build_prompt(_row(retrieved_contexts=["one", "two", "three"]))
    assert "DOCUMENT:\none\n\ntwo\n\nthree\n\n--" in text


def test_braces_in_a_row_stay_unchanged():
    text = prompt.build_prompt(
        _row(
            user_input="What does {name} mean?",
            retrieved_contexts=["Use {{ and }} in Jinja."],
            response='It prints {"a": 1}.',
        )
    )
    assert "What does {name} mean?" in text
    assert "Use {{ and }} in Jinja." in text
    assert 'It prints {"a": 1}.' in text


def test_the_reference_answer_is_not_used():
    text = prompt.build_prompt(_row(reference="UNIQUE-REFERENCE-TEXT"))
    assert "UNIQUE-REFERENCE-TEXT" not in text


def test_no_contexts_give_an_empty_document():
    text = prompt.build_prompt(_row(retrieved_contexts=[]))
    assert "DOCUMENT:\n\n\n--" in text
