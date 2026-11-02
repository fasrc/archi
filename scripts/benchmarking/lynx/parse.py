"""A strict reader of the Lynx verdict (design D4).

Lynx answers ``{"REASONING": ['...', '...'], "SCORE": PASS}``: single-quoted
reasoning items and a bare verdict, which is not JSON. The reader takes JSON plus
those two forms (openspec change accept-lynx-native-output). Only ``PASS`` and
``FAIL``, after ``strip()``, are verdicts. Anything else is a parse error, which
counts as neither PASS nor FAIL: a lenient reader would turn a malformed answer
into a verdict that the model never gave.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from json.decoder import scanstring  # type: ignore[attr-defined]
from typing import Any, List, Optional, Tuple

VERDICTS = ("PASS", "FAIL")

_WHITESPACE = re.compile(r"[ \t\n\r]*")
# JSON's number grammar, with ASCII digits only.
_NUMBER = re.compile(r"(-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?")
_WORD = re.compile(r"[A-Za-z]+")
_LITERALS = {"true": True, "false": False, "null": None}
# The escapes of a single-quoted string; any other escape stays as written (D2).
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t", "r": "\r"}
# A ' ends a single-quoted string only before one of these or the end of the text,
# so an apostrophe in a reasoning item stays in the item (D2).
_STRING_ENDS = ",]}"


@dataclasses.dataclass(frozen=True)
class Parsed:
    """A verdict with its reasoning, or no verdict and the parse error."""

    verdict: Optional[str]
    reasoning: Any = None
    error: Optional[str] = None


def _reject_constant(name: str) -> Any:
    # NaN and Infinity are not JSON, and the canonical output cannot hold them.
    raise ValueError(f"{name} is not JSON")


def _finite_float(text: str) -> float:
    # A number too large for a float (1e309) decodes to inf, which the
    # canonical output cannot hold either.
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is not a finite number")
    return value


def strict_loads(text: str) -> Any:
    """``json.loads`` that refuses NaN, Infinity and numbers too large for a float."""
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)


def _skip(text: str, index: int) -> int:
    return _WHITESPACE.match(text, index).end()  # type: ignore[union-attr]


def _read_quoted(text: str, index: int) -> Tuple[str, int]:
    """The single-quoted string at *index* and the index after it (D2)."""
    position = index + 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char < " ":
            raise ValueError(f"control character in the string at char {position}")
        if char == "'":
            after = _skip(text, position + 1)
            if after == len(text) or text[after] in _STRING_ENDS:
                body = text[index + 1 : position]
                return (
                    _ESCAPE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(0)), body),
                    position + 1,
                )
        position += 1
    raise ValueError(f"the string at char {index} has no closing quote")


def _read_value(text: str, index: int, *, score: bool = False) -> Tuple[Any, int]:
    """The value at *index* and the index after it; a bare word only for SCORE (D3)."""
    char = text[index : index + 1]
    if char == "{":
        return _read_object(text, index)
    if char == "[":
        return _read_array(text, index)
    if char == '"':
        # json's own string reader (not in the type stubs), so a JSON string reads
        # exactly as json reads it.
        return scanstring(text, index + 1)
    if char == "'":
        return _read_quoted(text, index)
    number = _NUMBER.match(text, index)
    if number:
        integer, fraction, exponent = number.groups()
        if fraction or exponent:
            value = _finite_float(integer + (fraction or "") + (exponent or ""))
            return value, number.end()
        return int(integer), number.end()
    word = _WORD.match(text, index)
    if word:
        if word.group() in _LITERALS:
            return _LITERALS[word.group()], word.end()
        if score:
            return word.group(), word.end()
        raise ValueError(
            f"the bare word {word.group()!r} at char {index} is not a value; "
            "only SCORE takes a bare word"
        )
    raise ValueError(f"expected a value at char {index}")


def _read_array(text: str, index: int) -> Tuple[List[Any], int]:
    """The array at ``text[index] == "["`` and the index after it."""
    items: List[Any] = []
    position = _skip(text, index + 1)
    if text.startswith("]", position):
        return items, position + 1
    while True:
        item, position = _read_value(text, position)
        items.append(item)
        position = _skip(text, position)
        if text.startswith("]", position):
            return items, position + 1
        if not text.startswith(",", position):
            raise ValueError(f"expected ',' or ']' at char {position}")
        position = _skip(text, position + 1)


def _read_object(text: str, index: int) -> Tuple[dict, int]:
    """The object at ``text[index] == "{"`` and the index after it (D1).

    Reads JSON as ``json`` reads it, plus a single-quoted string where a value goes
    and a bare word as the value of ``SCORE``. Keys are JSON strings.
    """
    result: dict = {}
    position = _skip(text, index + 1)
    if text.startswith("}", position):
        return result, position + 1
    while True:
        if not text.startswith('"', position):
            raise ValueError(f"expected a double-quoted key at char {position}")
        key, position = scanstring(text, position + 1)
        position = _skip(text, position)
        if not text.startswith(":", position):
            raise ValueError(f"expected ':' at char {position}")
        position = _skip(text, position + 1)
        result[key], position = _read_value(text, position, score=key == "SCORE")
        position = _skip(text, position)
        if text.startswith("}", position):
            return result, position + 1
        if not text.startswith(",", position):
            raise ValueError(f"expected ',' or '}}' at char {position}")
        position = _skip(text, position + 1)


def _top_level_objects(text: str) -> List[dict]:
    """Every object in *text* that is not inside another one, in order.

    Raises ValueError at a ``{`` outside these objects that does not start a
    complete object. Scanning on inside it would read a nested object of a
    cut-off answer, such as ``{"REASONING": {"SCORE": "PASS"}, "SCORE": "FAIL"``,
    as the verdict.
    """
    objects: List[dict] = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = _read_object(text, index)
        except (ValueError, RecursionError) as exc:
            # RecursionError: deep nesting must cost one row, not the whole run.
            raise ValueError(
                f"the '{{' at offset {index} does not start a complete JSON "
                f"object: {exc}"
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
