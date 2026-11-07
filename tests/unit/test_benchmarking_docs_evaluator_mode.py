"""Keep the Judge/SUT docs in step with `evaluator_provider_mode` (#469).

The template renders the key and `get_ragas_llm_evaluator` consumes it, so
`docs/docs/benchmarking.md` must tell operators what it accepts and when it
inherits.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "docs" / "benchmarking.md"


def _judge_section():
    text = DOCS.read_text()
    start = text.index("### Judge/SUT split")
    end = text.index("\n### ", start + 1)
    return text[start:end]


def test_judge_section_documents_evaluator_provider_mode():
    section = _judge_section()
    assert "`evaluator_provider_mode`" in section
    assert "`ollama`" in section
    assert "`openai_compat`" in section


def test_judge_section_states_inheritance_and_refusal():
    section = _judge_section()
    assert "inherits the system-under-test `provider_mode`" in section
    assert "`ValueError`" in section
