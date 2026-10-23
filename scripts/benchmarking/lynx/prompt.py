"""The Lynx prompt: the model-card template with three fields filled (design D2).

``LYNX_PROMPT_TEMPLATE`` is the ``PROMPT`` string of the
``PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct`` model card (README revision
``5523f869eb5eecb4bca417b6e6d3532e746a9664``), byte for byte, with its first and
last newline. Lynx has one task and was trained on this text: do not edit it.
"""

from __future__ import annotations

from typing import Any, Mapping

LYNX_PROMPT_TEMPLATE = """
Given the following QUESTION, DOCUMENT and ANSWER you must analyze the provided answer and determine whether it is faithful to the contents of the DOCUMENT. The ANSWER must not offer new information beyond the context provided in the DOCUMENT. The ANSWER also must not contradict information provided in the DOCUMENT. Output your final verdict by strictly following this format: "PASS" if the answer is faithful to the DOCUMENT and "FAIL" if the answer is not faithful to the DOCUMENT. Show your reasoning.

--
QUESTION (THIS DOES NOT COUNT AS BACKGROUND INFORMATION):
{question}

--
DOCUMENT:
{context}

--
ANSWER:
{answer}

--

Your output should be in JSON FORMAT with the keys "REASONING" and "SCORE":
{{"REASONING": <your reasoning as bullet points>, "SCORE": <your final score>}}
"""

CONTEXT_SEPARATOR = "\n\n"


def build_prompt(row: Mapping[str, Any]) -> str:
    """The Lynx prompt for one bundle row; ``reference`` is not used.

    ``str.format`` reads braces only in the template, so braces in the row's
    text reach the model unchanged.
    """
    return LYNX_PROMPT_TEMPLATE.format(
        question=row["user_input"],
        context=CONTEXT_SEPARATOR.join(row["retrieved_contexts"]),
        answer=row["response"],
    )
