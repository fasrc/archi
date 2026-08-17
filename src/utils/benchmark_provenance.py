"""Pure helpers that let a benchmark report attest to its own conditions.

A report is evidence only if it records what the run actually did. Two fields
previously did not:

* ``configuration`` was re-read from the YAML file on disk when the report was
  written, but the agent reads its configuration from Postgres
  (``config_access.get_full_config``). ``BenchmarkHandler.load_new_configuration``
  writes the selected file to ``CONFIG_PATH`` and ``archi()`` never reads it, so
  the two drift apart silently. A run executed at ``context_window: 8192`` was
  recorded as ``32768``.
* ``corpus_snapshot_id`` is a fresh UUID per invocation. It separates
  invocations, but it can never show that two runs saw the *same* corpus, which
  is the precondition for comparing their scores at all.

``config_divergence`` turns the first failure into a visible finding rather than
a wrong label, and ``corpus_fingerprint`` gives the second a content-derived
answer. Both are pure so the report writer stays a thin call site (the
diff-coverage gate cannot reach ``service_benchmark``'s runtime paths).
"""

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["config_divergence", "corpus_fingerprint"]


def _is_empty_container(value: Any) -> bool:
    """Is *value* an absent-or-empty mapping/sequence?

    YAML writes an empty mapping as ``None`` and JSONB reads it back as ``{}``.
    The agent behaves identically either way, so treating them as different
    would bury real divergences under serialization noise. ``0`` and ``False``
    are deliberately excluded -- they are settings, not absences.
    """
    if value is None:
        return True
    return isinstance(value, (dict, list, tuple)) and len(value) == 0


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Return *value* as a mapping to recurse into, or ``None`` if it is a leaf.

    Only ``None`` and an empty *mapping* become ``{}``. An empty sequence stays a
    leaf so that ``{}`` and ``[]`` can be told apart -- they are different
    settings, and collapsing them would be a false clearance.
    """
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return None


def _leaves_equal(left: Any, right: Any) -> bool:
    """Compare two leaves.

    ``None`` means "not configured" and matches an empty container of either
    kind, because every config consumer in this codebase reads with
    ``.get(key)`` and cannot distinguish the two. Two *present* empty containers
    are compared by kind, so an empty mapping never matches an empty sequence.
    """
    if _is_empty_container(left) and _is_empty_container(right):
        if left is None or right is None:
            return True
        return isinstance(left, dict) == isinstance(right, dict)
    # ``0 == False`` in Python; a numeric setting is not a boolean one.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _walk(left: Any, right: Any, prefix: str, found: List[str]) -> None:
    left_map, right_map = _as_mapping(left), _as_mapping(right)
    if left_map is not None and right_map is not None:
        for key in sorted(set(left_map) | set(right_map), key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk(left_map.get(key), right_map.get(key), path, found)
        return
    if not _leaves_equal(left, right):
        found.append(prefix or "<root>")


def config_divergence(intended: Any, running: Any) -> List[str]:
    """Dotted paths at which *intended* and *running* disagree.

    ``intended`` is the configuration the operator selected (the YAML file);
    ``running`` is the configuration the agent actually read. An empty list
    means the report can be trusted to describe the run. A non-empty one names
    exactly which settings the report would otherwise have misattributed.

    Lists compare by value rather than element-wise: a reordered list is a
    different setting, and per-index paths would be noise.
    """
    found: List[str] = []
    _walk(intended, running, "", found)
    return sorted(found)


def _escape(value: Any) -> str:
    """Make a field unable to forge the record separators."""
    return str(value).replace("%", "%25").replace(":", "%3A").replace("\n", "%0A")


def corpus_fingerprint(rows: Iterable[Sequence[Any]]) -> str:
    """Digest of the corpus, equal exactly when the supplied state is equal.

    *rows* are opaque ``(key, value)`` pairs. Order is irrelevant -- the rows are
    sorted before hashing -- so the digest does not depend on how the query
    happened to return them. A ``None`` value stays distinct from ``0`` and from
    the empty string: "no value recorded" is not "the value is zero".

    Values must stay opaque strings rather than numbers, because document size
    alone cannot detect a changed document. ``resource_hash`` is ``md5(url)``, an
    identity hash deliberately stable across content updates, so the caller also
    feeds in per-chunk content digests -- hex, not numeric.

    Unlike ``corpus_snapshot_id``, which is a per-invocation nonce, two runs over
    an unchanged corpus produce the same value here. That is what makes "these
    arms saw the same corpus" a checkable claim rather than an assumption.

    What it does NOT cover: re-embedding the same text with a different model
    leaves every key and value here unchanged. That shows up instead as a
    divergence on ``data_manager.embedding_name`` in the recorded configuration.
    """
    records: List[str] = []
    for row in rows:
        pair: Tuple[Any, ...] = tuple(row)
        if len(pair) != 2:
            raise ValueError(f"corpus row must be a (key, value) pair, got {pair!r}")
        key, value = pair
        rendered = "\x00none" if value is None else _escape(value)
        records.append(f"{_escape(key)}:{rendered}")
    digest = hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
