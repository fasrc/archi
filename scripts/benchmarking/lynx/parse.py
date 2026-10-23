"""A strict reader of the Lynx verdict (design D4).

Lynx answers ``{"REASONING": ..., "SCORE": "PASS" | "FAIL"}``. Only those two
exact strings, after ``strip()``, are verdicts. Anything else is a parse error,
which counts as neither PASS nor FAIL: a lenient reader would turn a malformed
answer into a verdict that the model never gave.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, List, Optional

VERDICTS = ("PASS", "FAIL")


@dataclasses.dataclass(frozen=True)
class Parsed:
    """A verdict with its reasoning, or no verdict and the parse error."""

    verdict: Optional[str]
    reasoning: Any = None
    error: Optional[str] = None


def _reject_constant(name: str) -> Any:
    # NaN and Infinity are not JSON, and the canonical output cannot hold them.
    raise ValueError(f"{name} is not JSON")


def _top_level_objects(text: str) -> List[dict]:
    """Every JSON object in *text* that is not inside another one, in order.

    Raises ValueError at a ``{`` outside these objects that does not start a
    complete JSON object. Scanning on inside it would read a nested object of a
    cut-off answer, such as ``{"REASONING": {"SCORE": "PASS"}, "SCORE": "FAIL"``,
    as the verdict.
    """
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    objects: List[dict] = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            raise ValueError(
                f"the '{{' at offset {index} does not start a complete JSON object"
            ) from None
        objects.append(value)
        index = text.find("{", end)
    return objects


def parse_output(text: str) -> Parsed:
    """The verdict in one Lynx output, or a parse error that names the reason."""
    try:
        objects = _top_level_objects(text)
    except ValueError as exc:
        return Parsed(None, error=str(exc))
    if not objects:
        return Parsed(None, error="no JSON object in the output")
    scored = [obj for obj in objects if "SCORE" in obj]
    if not scored:
        return Parsed(None, error="no JSON object in the output has a SCORE key")
    verdicts = []
    for obj in scored:
        score = obj["SCORE"]
        if not isinstance(score, str) or score.strip() not in VERDICTS:
            return Parsed(None, error=f"SCORE {score!r} is not 'PASS' or 'FAIL'")
        verdicts.append(score.strip())
    if len(set(verdicts)) > 1:
        return Parsed(
            None, error=f"the output has conflicting scores: {', '.join(verdicts)}"
        )
    return Parsed(verdicts[0], reasoning=scored[0].get("REASONING"))
