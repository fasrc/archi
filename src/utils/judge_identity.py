"""The RAGAS judge identity and how it is compared (judge offload design D10).

``ragas_judge_identity_effective`` started as four strings (#618: provider,
model, base_url, mode). #732 extends it with nested values: the metric list, the
decoding settings, the embedding and the scorer identity. A ``set`` of
``tuple(sorted(identity.items()))`` cannot hold a list or a dict, so every reader
compares identities through ``canonical_identity`` instead:

- ``ResultHandler.arms_incomparability_reason`` (the in-run rule, #618);
- the leaderboard's shared-context reader;
- gate G12 in ``compare_runs.py`` (#733).

Numbers are normalized first: plain ``json.dumps`` writes ``0`` and ``0.0``
differently, and a configured ``temperature: 0`` against an effective default of
``0.0`` must not withhold ranks.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Sequence


def normalize_numbers(value: Any) -> Any:
    """A copy of *value* in which every integral float is the equal ``int``.

    Goes into dicts, lists and tuples. A ``bool`` stays a ``bool``: it is a
    subclass of ``int``, but ``True`` is not the setting ``1``. A NaN or an
    infinity is left as it is.
    """
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_numbers(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_identity(identity: Any) -> str:
    """The one comparable text of a judge identity: sorted keys, normalized numbers.

    The separators are the ``json.dumps`` defaults, so a #618 identity (four
    strings) reads the same as the ``json.dumps(identity, sort_keys=True)`` that
    the leaderboard showed before this helper existed.
    """
    return json.dumps(normalize_numbers(identity), sort_keys=True, default=str)


def identity_digest(identity: Any) -> str:
    """``sha256:<hex>`` of the canonical identity; names a checkpoint file (D7)."""
    digest = hashlib.sha256(canonical_identity(identity).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def differing_fields(identities: Sequence[Mapping[str, Any]]) -> List[str]:
    """The top-level fields whose values are not equal in all *identities*.

    A field that one identity has and another lacks differs too: a missing field
    never equals a recorded one, also when the recorded value is ``None``.
    """
    names = sorted({name for identity in identities for name in identity})
    found: List[str] = []
    for name in names:
        if any(name not in identity for identity in identities):
            found.append(name)
        elif len({canonical_identity(identity[name]) for identity in identities}) > 1:
            found.append(name)
    return found
