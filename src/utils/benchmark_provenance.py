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

``asserted_config_divergence`` turns the first failure into a visible finding
rather than a wrong label, and ``corpus_fingerprint`` gives the second a
content-derived answer. Both are pure so the report writer stays a thin call site
(the diff-coverage gate cannot reach ``service_benchmark``'s runtime paths).

``config_divergence`` is the symmetric primitive underneath, kept because
comparing two configurations whole is a genuinely different question from asking
which of an operator's stated intentions the agent contradicted. Only the latter
belongs in a report, for the reasons set out on
``asserted_config_divergence``.
"""

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "ARM_OVERRIDE_PATHS",
    "DEPLOY_REWRITTEN_PATHS",
    "DIVERGENCE_IGNORED_PATHS",
    "asserted_config_divergence",
    "config_divergence",
    "corpus_fingerprint",
]

#: Config subtrees the benchmark harness reads from the SELECTED file and passes
#: straight to ``archi()``, bypassing Postgres entirely.
#:
#: ``BenchmarkHandler.load_new_configuration`` takes ``agent_class``, ``provider``,
#: ``model`` and ``agent_md_file`` out of the selected file's
#: ``services.benchmarking`` and hands them to ``archi()`` as constructor
#: arguments. They never reach the configuration the agent reads, so Postgres
#: keeps whatever was seeded at deploy while every arm of a sweep varies the file
#: -- which made these paths diverge on every arm by construction. Eleven
#: spurious paths per arm on the real fasrc-cannon sweep.
ARM_OVERRIDE_PATHS: Tuple[str, ...] = ("services.benchmarking",)

#: Paths the deploy pipeline REWRITES on its way into the container, so the file
#: and the running configuration disagree by construction on every deployment.
#:
#: ``TemplatesManager._render_config_files`` replaces these host paths with fixed
#: container paths -- ``services.chat_app.agents_dir`` goes from, say,
#: ``/home/austin/Projects/archi/deploy/fasrc-dev/agents`` to
#: ``/root/archi/agents``. That is a path translation, not a setting the run
#: failed to honour. ``services.benchmarking.agent_md_file`` and the prompt paths
#: get the same treatment and are already covered by ``ARM_OVERRIDE_PATHS``.
#:
#: Source of truth: ``src/cli/managers/templates_manager.py`` in
#: ``_render_config_files``. Add to this list if that rewriting grows.
DEPLOY_REWRITTEN_PATHS: Tuple[str, ...] = tuple(
    f"services.{service}.{key}"
    for service in ("chat_app", "redmine_mailbox", "piazza")
    for key in ("agents_dir", "skills_dir")
)

#: Paths excluded from the divergence check because the two sides differ by
#: design rather than by fault.
#:
#: ``name`` is the deployment name in the configuration the agent reads
#: (``archi_dev``) and the configuration's own name in the selected file
#: (``fasrc-cannon-v1-strict``). They are different facts that happen to share a
#: key, so comparing them reports a difference that means nothing.
DIVERGENCE_IGNORED_PATHS: Tuple[str, ...] = (
    ARM_OVERRIDE_PATHS + DEPLOY_REWRITTEN_PATHS + ("name",)
)


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


def _walk_asserted(
    selected: Any,
    running: Any,
    prefix: str,
    found: List[str],
    ignore: Tuple[str, ...],
) -> None:
    if prefix and prefix in ignore:
        return
    selected_map, running_map = _as_mapping(selected), _as_mapping(running)
    if selected_map is not None and running_map is not None:
        # Only the keys the selected file asserts, unlike _walk's union: a key
        # present only in the running config is something the operator never
        # claimed anything about.
        for key in sorted(selected_map, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk_asserted(selected_map[key], running_map.get(key), path, found, ignore)
        return
    if not _leaves_equal(selected, running):
        found.append(prefix or "<root>")


def asserted_config_divergence(
    selected: Any,
    running: Any,
    *,
    ignore_paths: Tuple[str, ...] = DIVERGENCE_IGNORED_PATHS,
) -> List[str]:
    """Settings the selected file asserts that the agent's configuration contradicts.

    Deliberately asymmetric, which is what distinguishes this from
    ``config_divergence``. The selected file is *sparse operator intent*;
    ``get_full_config()`` returns the configuration after seeding, defaulting and
    reshaping. Those differ by design in at least four ways, none of which is a
    fault:

    * **Synthesized** -- ``config_version``, ``available_models``,
      ``available_pipelines`` and ``available_providers`` are built by the config
      service. No YAML file has them.
    * **Defaulted** -- the seeder fills in whole sections the operator omitted.
      archi-dev's seed file has neither ``global`` nor ``mcp_servers``; the
      running configuration has both.
    * **Reshaped** -- the file writes ``data_manager.sources``; the running
      configuration additionally exposes a top-level ``sources`` copy.
    * **Differently scoped** -- see ``DIVERGENCE_IGNORED_PATHS``.

    Comparing the two whole therefore reported *every* run as mislabelled.
    Measured against the file that actually seeded archi-dev, versus what
    archi-dev then served -- the same source by construction --
    ``config_divergence`` reports 192 paths and this reports 1. Because
    ``arms_comparable()`` consults the result, that difference is between a guard
    that never passes and one that works: the whole-dict version stripped every
    leaderboard rank and A/B winner on every run, and buried the 8192-vs-32768
    mislabel it exists to catch as 1 finding among 192.

    A key the running configuration has and the file does not is therefore NOT
    reported -- that is every default in the system. A key the file asserts and
    the running configuration lacks IS reported: asking for a setting the agent
    never received is exactly a mislabel.

    Leaf semantics are shared with ``config_divergence``: ``None`` matches an
    empty container of either kind because every consumer reads with
    ``.get(key)``, while ``0`` and ``False`` stay settings rather than absences.
    """
    found: List[str] = []
    _walk_asserted(selected, running, "", found, tuple(ignore_paths))
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
