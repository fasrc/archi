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
belongs in a report, for the reasons set out on ``asserted_config_divergence``.

Divergence and identity answer different questions, and the report needs both.
Divergence is computable only at write time, while the selected file and the
config the chain held are both in hand -- it catches a mislabel as it happens. A
*digest* is computable forever, from the finished artifact alone, so a reader
weeks later can ask "was this the same code and the same settings as that other
run?" without either source still existing. ``code_version`` and
``config_version`` supply that half.
"""

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "ARM_OVERRIDE_PATHS",
    "DEPLOY_REWRITTEN_PATHS",
    "DIVERGENCE_IGNORED_PATHS",
    "KEY_SETTING_PATHS",
    "asserted_config_divergence",
    "code_fingerprint",
    "code_version",
    "collect_code_version",
    "config_divergence",
    "config_fingerprint",
    "config_version",
    "corpus_fingerprint",
    "effective_config",
    "package_module_files",
    "read_module_sources",
    "reconstruct_version_stamp",
    "settings_at_paths",
]

#: Config subtrees the benchmark harness reads from the SELECTED file and passes
#: straight to ``archi()``, bypassing Postgres entirely.
#:
#: ``BenchmarkHandler.load_new_configuration`` takes ``agent_class``,
#: ``provider``, ``model`` and ``agent_md_file`` out of the selected file's
#: ``services.benchmarking`` and hands them to ``archi()`` as constructor
#: arguments. They never appear in the config the agent reads, which matters
#: twice over:
#:
#: * A digest taken from the running config alone cannot tell two arms apart when
#:   what varies between them lives here -- and that is the common case: the
#:   fasrc-cannon sweep arms differ only in ``agent_md_file`` and ``name``. So
#:   ``effective_config`` overlays this subtree from the selected file.
#: * Postgres keeps whatever was seeded at deploy while every arm of a sweep
#:   varies the file, so a divergence check over this subtree fires on every arm
#:   by construction -- eleven spurious paths per arm on the real fasrc-cannon
#:   sweep. So ``asserted_config_divergence`` ignores it.
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

#: Settings a campaign is known to vary, surfaced compactly so a reader can see
#: which arm an artifact describes without parsing a 500-key configuration blob.
#:
#: This list is a convenience, never the guarantee. It is necessarily incomplete
#: -- ``services.chat_app.context_editing`` did not exist when the 2026-08-11
#: runs were recorded, and the next campaign will vary something not listed here.
#: ``config_version``'s ``digest`` is the guarantee: it covers every setting.
KEY_SETTING_PATHS: Tuple[str, ...] = (
    "services.chat_app.agent_class",
    "services.chat_app.context_editing",
    "services.chat_app.default_model",
    "services.chat_app.default_provider",
    "services.chat_app.recursion_limit",
    "services.benchmarking.agent_class",
    "services.benchmarking.agent_md_file",
    "services.benchmarking.model",
    "services.benchmarking.modes",
    "services.benchmarking.mode_settings",
    "services.benchmarking.provider",
    "services.vectorstore.backend",
    "services.vectorstore.distance_metric",
    "data_manager.chunk_overlap",
    "data_manager.chunk_size",
    "data_manager.chunking",
    "data_manager.distance_metric",
    "data_manager.embedding_name",
    "data_manager.retrievers",
    "data_manager.stemming",
)

#: Distinguishes "this path is absent" from "this path is set to None".
_MISSING = object()


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


def corpus_fingerprint(rows: Iterable[Sequence[Any]], version: str = "") -> str:
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

    *version* goes into the prefix (``sha256/v2:``), so a digest of one query
    can never equal a digest of another query over the same rows.

    What it does NOT cover: re-embedding the same text with a different model
    leaves every key and value here unchanged, on purpose. The model travels
    beside the digest as ``retrieval_identity.embedding_model``, checked against
    the chunks' ``embedding_model`` tags. ``embedding_name`` does not show it:
    #216 changes only ``model_name``.
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
    prefix = f"sha256/{version}" if version else "sha256"
    return f"{prefix}:{digest}"


# The metadata keys that reach the agent and its citations through
# ``_merge_row_metadata`` and the hierarchical retriever. Every chunk and parent
# node carries them in its own ``metadata``; for a linked chunk the document's
# non-empty columns override them, so the doc row hashes those columns too. The whole ``metadata`` object is never hashed:
# ``parent_id`` is a SERIAL that changes on every ingest, the ingest status
# fields churn, ``embedding_model`` must stay out (the digest is model-neutral),
# and ``category`` belongs to the category-map digest.
CITATION_FIELDS = ("url", "display_name", "source_type", "title", "filename")

# What retrieval in one collection can return: the filter
# ``PostgresVectorStore`` applies, with the ``LEFT JOIN`` it uses, so a chunk
# with no document link is in scope.
_SCOPED_CHUNKS = """
WITH scoped AS (
    SELECT c.id, c.chunk_index, c.chunk_text, c.metadata AS meta,
           (c.embedding IS NULL) AS no_vector,
           d.id AS doc_id, d.resource_hash, d.url, d.display_name,
           d.source_type, d.extra_json
    FROM document_chunks c
    LEFT JOIN documents d ON d.id = c.document_id
    WHERE (c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)
      AND (d.id IS NULL OR d.is_deleted = FALSE)
)
"""


def _citations(alias: str) -> str:
    return ", ".join(f"{alias}->>'{name}'" for name in CITATION_FIELDS)


# Fingerprint v2 (#570). Each value is md5 of a JSON array, which keeps field
# boundaries and keeps NULL apart from "". Rows:
#   chunk   text, collection tag, null-vector flag, and the chunk's own citation
#           fields: ``filename`` exists only there, and the retrieval overlay keeps
#           the chunk's ``url``/``title`` wherever the document leaves them empty;
#   parent  only parents an in-scope chunk references: text, the ordered list
#           of in-scope child indexes, and the parent's citation fields;
#   doc     only live documents that own an in-scope chunk: the columns the
#           retrieval overlay reads. ``size_bytes`` is out, because retrieval
#           never reads it and the chunk rows catch every text change.
CORPUS_STATE_V2_QUERY = (
    _SCOPED_CHUNKS
    + f"""
SELECT 'chunk:' || COALESCE(s.resource_hash, s.meta->>'resource_hash', \
s.meta->>'chunk_id', 'id:' || s.id::text) || ':' || s.chunk_index::text,
       md5(jsonb_build_array(
           s.chunk_text, s.meta->>'collection', s.no_vector,
           {_citations("s.meta")}
       )::text)
FROM scoped s
UNION ALL
SELECT DISTINCT 'doc:' || s.resource_hash,
       md5(jsonb_build_array(
           s.url, s.display_name, s.source_type, s.extra_json->>'title'
       )::text)
FROM scoped s
WHERE s.doc_id IS NOT NULL
UNION ALL
SELECT 'parent:' || COALESCE(pd.resource_hash, p.metadata->>'resource_hash', \
'id:' || p.id::text) || ':' || p.parent_index::text,
       md5(jsonb_build_array(
           p.parent_text,
           array_agg(s.chunk_index ORDER BY s.chunk_index),
           {_citations("p.metadata")}
       )::text)
FROM document_parent_nodes p
JOIN scoped s ON s.meta->>'parent_id' = p.id::text
LEFT JOIN documents pd ON pd.id = p.document_id
GROUP BY p.id, pd.resource_hash
"""
)

# The URL -> category map, in the same scope: a relabel of a document that no
# in-scope chunk belongs to is invisible to the run.
CATEGORY_MAP_V2_QUERY = """
SELECT d.url, d.extra_json->>'category'
FROM documents d
WHERE NOT d.is_deleted AND d.url IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM document_chunks c
      WHERE c.document_id = d.id
        AND (c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)
  )
"""


def corpus_state_rows(cursor: Any, collection: str) -> List[Tuple[Any, Any]]:
    """The v2 ``(key, value)`` rows for *collection*, read on the caller's cursor.

    The caller owns the transaction, so a census can read this and the
    category map in one snapshot.
    """
    cursor.execute(CORPUS_STATE_V2_QUERY, (collection,))
    return [tuple(row) for row in cursor.fetchall()]


def category_map_rows(cursor: Any, collection: str) -> List[Tuple[Any, Any]]:
    """Raw ``(url, category)`` rows for *collection*, on the caller's cursor."""
    cursor.execute(CATEGORY_MAP_V2_QUERY, (collection,))
    return [tuple(row) for row in cursor.fetchall()]


def canonical_source_url(value: Any) -> str:
    """Canonical form of a gold/retrieved source value, for comparison only.

    Strips surrounding whitespace and a single trailing ``/`` from the URL
    *path* — the one difference that actually occurs between an authored bank
    URL and the ingested ``documents.url`` (PR #106). Deliberately conservative:
    it does NOT lowercase (paths are case-sensitive), normalize the scheme, or
    drop the query/fragment, because over-matching would silently conflate
    distinct pages — a worse failure than the miss it fixes, and an invisible one.

    The slash is stripped from the path only, so a query or fragment that
    legitimately ends in ``/`` (e.g. ``...?redirect=/kb/foo/``) is preserved.
    A value with no scheme (e.g. a ``file_name`` match field) parses as a bare
    path, so the same one-trailing-slash rule applies without special-casing.

    The harness's source matching, the category-map records and the category
    slice join all use this one rule, so they agree about which page a URL names.
    """
    text = str(value).strip()
    parts = urlsplit(text)
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
        return urlunsplit(parts._replace(path=path))
    return text


def _escape_map_field(value: str) -> str:
    """Make a category-map field unable to forge the tab/newline separators."""
    return (
        value.replace("%", "%25")
        .replace("\t", "%09")
        .replace("\n", "%0A")
        .replace("\r", "%0D")
    )


def category_map_records(rows: Iterable[Sequence[Any]]) -> List[str]:
    """Sorted ``<canonical_url>\\t<category>`` records for a URL -> category map.

    *rows* are ``(url, category)`` pairs, one per non-deleted document. A
    document without a URL cannot be joined to a bank source, so it contributes
    no record; a missing category is an empty field. Duplicate URLs stay as
    separate records — the consumer reports a URL with two categories as
    unresolved rather than choosing one. Sorting makes the digest independent
    of the order the query returned the rows in (#538 rule 5).
    """
    records: List[str] = []
    for url, category in rows:
        if url is None or not str(url).strip():
            continue
        canonical = _escape_map_field(canonical_source_url(url))
        field = "" if category is None else _escape_map_field(str(category))
        records.append(f"{canonical}\t{field}")
    return sorted(records)


def category_map_text(records: Sequence[str]) -> str:
    """The exact text that is hashed and written to the per-arm snapshot file."""
    return "\n".join(records)


def category_map_digest(records: Sequence[str]) -> str:
    """``sha256:<hex>`` of :func:`category_map_text`, so a file's hash equals it."""
    text = category_map_text(records)
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def prompt_text_sha256(path: Optional[Any]) -> Optional[str]:
    """Hex sha256 of an agent prompt as ``load_agent_spec`` reads it.

    Hashes ``read_text()`` re-encoded as UTF-8, not the raw bytes: that is the
    text the harness parses (newline-normalized), so the digest names the
    prompt the arm actually ran. Plain hex, the same form the QA workflow
    records as ``agent_spec_sha256``. ``None`` when the arm names no prompt;
    an ``<unavailable: …>`` marker when it cannot be read, because provenance
    is never fatal.
    """
    if path is None:
        return None
    try:
        text = Path(str(path)).read_text()
    except OSError as exc:
        return f"<unavailable: {exc}>"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_fingerprint(config: Any) -> str:
    """Digest of a configuration, equal exactly when its content is equal.

    Keys are sorted, so a mapping round-tripped through YAML or JSONB
    fingerprints the same as the one that went in. JSON keeps ``0`` and ``False``
    distinct, which matters -- they are different settings.

    Never raises: provenance must not be the reason a finished run loses its
    scores, so a value JSON cannot encode falls back to its ``repr``.
    """
    canonical = json.dumps(
        config, sort_keys=True, separators=(",", ":"), default=repr, ensure_ascii=True
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _overlay(base: Any, overlay: Any, path: str) -> Any:
    """Return *base* with *overlay*'s value at dotted *path* substituted in."""
    if not path:
        return overlay
    head, _, rest = path.partition(".")
    base_map = base if isinstance(base, dict) else {}
    overlay_map = overlay if isinstance(overlay, dict) else {}
    if head not in overlay_map:
        return base
    merged = dict(base_map)
    merged[head] = _overlay(base_map.get(head), overlay_map.get(head), rest)
    return merged


def effective_config(
    running: Any, selected: Any, override_paths: Iterable[str] = ARM_OVERRIDE_PATHS
) -> Any:
    """The configuration that actually determined the run.

    The agent reads Postgres, so *running* is the right basis. But it is not the
    whole story: ``load_new_configuration`` pulls ``agent_class``, ``provider``,
    ``model`` and ``agent_md_file`` out of the *selected* file's
    ``services.benchmarking`` and passes them to ``archi()`` directly, so those
    settings shape the run without ever appearing in Postgres.

    Digesting *running* alone therefore gives every arm of a prompt sweep the
    same fingerprint -- the fasrc-cannon arms differ only in ``agent_md_file``
    and ``name``, both under ``services.benchmarking`` -- which defeats the whole
    point of a per-arm stamp. Overlaying those subtrees from the selected file
    restores the distinction.

    Returns *selected* when *running* is unavailable: a degraded basis beats no
    basis, and the caller labels it.
    """
    if running is None:
        return selected
    merged = running
    for path in override_paths:
        merged = _overlay(merged, selected, path)
    return merged


def code_fingerprint(sources: Iterable[Tuple[str, bytes]]) -> str:
    """Digest of the code under test, equal exactly when that code is equal.

    *sources* are ``(relative_path, source_bytes)`` pairs. The path is part of
    the identity, not just the bytes: a renamed or newly added module is
    different code even when every body is unchanged.

    This exists because ``git_info.last_commit`` cannot do the job.
    ``git_info.yaml`` is written once by ``archi create`` and then frozen, so
    every run between 2026-08-11 and 2026-08-17 reports ``0a157cdce0`` with an
    empty diff -- the commit identifies the *deploy*, not the image, and cannot
    distinguish two arms that ran different code against one deployment.

    Raises ``ValueError`` on empty *sources*: an empty digest would silently
    claim that two images whose code was never inspected had matched.
    """
    records: List[str] = []
    for relative_path, body in sources:
        body_digest = hashlib.sha256(body).hexdigest()
        records.append(f"{_escape(relative_path)}:{body_digest}")
    if not records:
        raise ValueError("cannot fingerprint code: no module sources were supplied")
    digest = hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def package_module_files(package_dir: str) -> List[Tuple[str, str]]:
    """Every ``.py`` under *package_dir*, as ``(relative_path, absolute_path)``.

    Deliberately a directory walk rather than a scan of ``sys.modules``. The
    loaded-module set depends on which code paths the run happened to take --
    ``src.utils.rbac.registry`` is imported only when a decorated agent tool
    executes, so two runs of one image would report different code versions
    depending on whether the model chose that tool. That breaks the property the
    digest exists to provide. A manifest of the files on disk is the same for
    every run of the same image.

    ``__pycache__`` is skipped: compiled artifacts vary with interpreter and
    invocation without the source having changed.
    """
    found: List[Tuple[str, str]] = []
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in files:
            if not name.endswith(".py"):
                continue
            absolute = os.path.join(root, name)
            found.append((os.path.relpath(absolute, package_dir), absolute))
    return sorted(found)


def read_module_sources(files: Iterable[Tuple[str, str]]) -> List[Tuple[str, bytes]]:
    """Read each ``(relative_path, file_path)`` into ``(relative_path, bytes)``.

    A file that cannot be read is skipped rather than fatal: a partial digest
    still distinguishes two images, and a benchmark that has already scored its
    questions must not lose them because one source file was unreadable.
    """
    sources: List[Tuple[str, bytes]] = []
    for relative_path, path in files:
        try:
            with open(path, "rb") as handle:
                sources.append((relative_path, handle.read()))
        except OSError:
            continue
    return sources


def settings_at_paths(config: Any, paths: Iterable[str]) -> Dict[str, Any]:
    """The values of *paths* present in *config*, keyed by dotted path.

    Absent paths are omitted rather than recorded as ``null`` -- "this setting
    did not exist" and "this setting was set to null" are different facts, and
    the first is the common case when reading an artifact written before a
    feature landed.
    """
    found: Dict[str, Any] = {}
    for path in paths:
        cursor: Any = config
        for part in path.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = _MISSING
                break
            cursor = cursor[part]
        if cursor is not _MISSING:
            found[path] = cursor
    return found


_DEPLOY_GIT_NOTE = (
    "Written once by `archi create` and then frozen: this identifies the deploy, "
    "not the image the benchmark ran. Two arms of one campaign share it. "
    "Compare `digest` instead."
)


def code_version(
    sources: Sequence[Tuple[str, bytes]], deploy_git_info: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """The ``code_version`` block for a report's metadata.

    Records a content digest of the package on disk as the identity, and keeps
    the deploy-time commit alongside it -- labelled, so a reader does not mistake
    a frozen value for the code under test.
    """
    info = deploy_git_info or {}
    commit = (info.get("last_commit") or "").strip() or None

    try:
        digest: Optional[str] = code_fingerprint(sources)
        source = "content digest of the `src` package files in the benchmark image"
    except ValueError as exc:
        digest = None
        source = f"<unavailable: {exc}>"

    return {
        "digest": digest,
        "source": source,
        "file_count": len(sources),
        "deploy_git_commit": commit,
        "deploy_git_dirty": bool((info.get("git_diff") or "").strip()),
        "deploy_git_note": _DEPLOY_GIT_NOTE,
    }


def collect_code_version(
    package_dir: str, deploy_git_info: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Build the ``code_version`` block from the package on disk.

    The single call site a report writer needs. Never raises: an unreadable
    package directory yields an ``<unavailable: ...>`` source rather than costing
    a finished benchmark its scores.
    """
    try:
        sources = read_module_sources(package_module_files(package_dir))
    except OSError as exc:
        return {
            "digest": None,
            "source": f"<unavailable: could not read {package_dir}: {exc}>",
            "file_count": 0,
            "deploy_git_commit": (
                ((deploy_git_info or {}).get("last_commit") or "").strip() or None
            ),
            "deploy_git_dirty": bool(
                ((deploy_git_info or {}).get("git_diff") or "").strip()
            ),
            "deploy_git_note": _DEPLOY_GIT_NOTE,
        }
    return code_version(sources=sources, deploy_git_info=deploy_git_info)


def config_version(
    running: Any,
    selected: Any,
    selected_file: Optional[str],
    effective_selected: Any = None,
) -> Dict[str, Any]:
    """The ``config_version`` block for one arm of a run.

    The digest covers the *effective* configuration -- what the agent read from
    Postgres, overlaid with the subtrees the harness passes to ``archi()`` from
    the selected file. Digesting the running config alone would give every arm of
    a sweep the same fingerprint (see ``effective_config``).

    *selected* is fingerprinted separately and the settings where the two
    disagree are named, so a mislabel lands as a visible finding rather than a
    wrong number. That list is scoped the same way the report's own check is
    scoped -- see ``asserted_config_divergence``. Comparing the two whole would
    stamp roughly 192 meaningless paths into every arm of every artifact, since
    ``get_full_config`` synthesizes keys no YAML file has and the deploy rewrites
    host paths into container paths.

    *effective_selected* is an optional stand-in for *selected* in the DIGEST
    basis only, for values the run normalizes before use -- the judge knobs,
    where an invalid setting is replaced by its default. It must not reach
    ``selected_file_digest`` or the divergence list: those two describe the file
    as it was written, and that is their whole audit purpose. Two files that
    differ must fingerprint differently even when they drive identical runs.
    """
    have_running = running is not None
    basis = effective_config(
        running, selected if effective_selected is None else effective_selected
    )

    return {
        "digest": config_fingerprint(basis),
        "source": (
            "effective configuration: what the agent read from Postgres, overlaid "
            "with the `services.benchmarking` settings the harness passes to "
            "archi() from the selected file"
            if have_running
            else "selected file on disk -- the configuration the chain held was "
            "unavailable, so this may not describe the run"
        ),
        "selected_file": selected_file,
        "selected_file_digest": (
            None if selected is None else config_fingerprint(selected)
        ),
        "divergence_from_selected_file": (
            asserted_config_divergence(selected, running) if have_running else None
        ),
        "key_settings": settings_at_paths(basis, KEY_SETTING_PATHS),
    }


def reconstruct_version_stamp(
    metadata: Optional[Mapping[str, Any]],
    recorded_config: Any,
    configuration_file: Optional[str],
) -> Dict[str, Any]:
    """Version blocks for an artifact written before version stamping existed.

    The reports already in ``bench_out/`` cannot be re-run, so this gives them
    the *identity* they lacked while refusing to assert the facts they never
    held:

    * ``code_version.digest`` stays ``None``. ``git_info.last_commit`` is the
      deploy's commit -- every run from 2026-08-11 to 2026-08-17 shares
      ``0a157cdce0`` with an empty diff -- so promoting it to "the code this run
      used" would manufacture exactly the false attribution this module exists
      to prevent. The commit is carried over, labelled.
    * ``config_version.digest`` is real: the configuration *file* was recorded,
      so it can be fingerprinted, and two artifacts with different digests
      definitely ran different files. But the file is not necessarily what the
      agent read -- ``bench-8192-20260817_170850.json`` recorded 32768 for the
      8192 arm -- so the source says so, and divergence is ``None`` (unknown)
      rather than ``[]`` (checked and agreed).
    """
    info = (metadata or {}).get("git_info") or {}
    commit = (info.get("last_commit") or "").strip() or None
    have_config = recorded_config is not None

    return {
        "code_version": {
            "digest": None,
            "source": (
                "<not recorded: this artifact predates code-version stamping, and "
                "the code it ran cannot be recovered from it>"
            ),
            "file_count": None,
            "deploy_git_commit": commit,
            "deploy_git_dirty": bool((info.get("git_diff") or "").strip()),
            "deploy_git_note": _DEPLOY_GIT_NOTE,
        },
        "config_version": {
            "digest": config_fingerprint(recorded_config) if have_config else None,
            "source": (
                "reconstructed from the configuration file recorded in this "
                "artifact; the configuration the agent read was never captured, "
                "so this may not describe the run"
                if have_config
                else "<not recorded: no configuration was captured in this artifact>"
            ),
            "selected_file": configuration_file,
            "selected_file_digest": (
                config_fingerprint(recorded_config) if have_config else None
            ),
            "divergence_from_selected_file": None,
            "key_settings": settings_at_paths(recorded_config, KEY_SETTING_PATHS),
        },
    }


# Constructor kwargs that name the model, per embedding class (base-config.yaml).
_MODEL_KWARGS = ("model_name", "model")


@dataclass(frozen=True)
class RetrievalIdentity:
    """What a run searched: the collection tag and the model behind its vectors.

    ``embedding_name`` is the config key of the embedding class, and it does not
    change when #216 swaps the model: only ``embedding_model`` does. A field is
    ``None`` when the config does not say.
    """

    collection: Optional[str]
    embedding_name: Optional[str]
    embedding_model: Optional[str]

    def as_dict(self) -> Dict[str, Optional[str]]:
        return asdict(self)


def retrieval_identity(config: Any) -> RetrievalIdentity:
    """Derive the retrieval identity from a config dict, with no connection.

    The collection formula is the one ``VectorstoreConnector`` uses, so the value
    equals the tag the search filters on. The model is the class's model kwarg
    (``model_name`` for HuggingFace, ``model`` for OpenAI), else the class name.
    The embedding class is never imported, so a recorded
    ``running_configuration`` is enough input.
    """
    data_manager = _as_mapping((_as_mapping(config) or {}).get("data_manager"))
    if data_manager is None:
        return RetrievalIdentity(None, None, None)
    embedding_name = data_manager.get("embedding_name")
    collection_name = data_manager.get("collection_name")
    collection = (
        f"{collection_name}_with_{embedding_name}"
        if collection_name is not None and embedding_name is not None
        else None
    )
    class_map = _as_mapping(data_manager.get("embedding_class_map")) or {}
    entry = _as_mapping(class_map.get(embedding_name)) or {}
    kwargs = _as_mapping(entry.get("kwargs")) or {}
    model = next((kwargs[k] for k in _MODEL_KWARGS if kwargs.get(k)), None)
    if model is None:
        # A resolved config (``get_full_config(resolve_embeddings=True)``)
        # holds the class itself; name it as the unresolved config does.
        model = entry.get("class") or embedding_name
        model = getattr(model, "__name__", model)
    return RetrievalIdentity(
        collection=collection,
        embedding_name=embedding_name,
        embedding_model=None if model is None else str(model),
    )


def _searched_collection(config: Any) -> str:
    collection = retrieval_identity(config).collection
    if collection is None:
        raise ValueError(
            "config names no collection (data_manager.collection_name and "
            "embedding_name are required)"
        )
    return collection


def _read_on_pool(pool: Any, read: Any, collection: str) -> Any:
    with pool.get_connection() as connection:
        with connection.cursor() as cursor:
            return read(cursor, collection)


def live_corpus_fingerprint(pool: Any, config: Any) -> str:
    """The v2 fingerprint of the collection *config* searches, read on *pool*.

    *config* is required: the default ``get_full_config()`` needs an installed
    ``PostgresServiceFactory``, and a caller that built its own pool may not
    have one. Every consumer reads through this, so their digests agree.
    """
    rows = _read_on_pool(pool, corpus_state_rows, _searched_collection(config))
    return corpus_fingerprint(rows, version="v2")


def live_category_map(pool: Any, config: Any) -> Tuple[list, List[str], str]:
    """``(rows, records, digest)`` of the category map in *config*'s collection."""
    rows = _read_on_pool(pool, category_map_rows, _searched_collection(config))
    records = category_map_records(rows)
    return rows, records, category_map_digest(records)


def _container_factory_and_config() -> Tuple[Any, Any]:
    """Build the factory from the environment, install it, and read the config.

    The install comes first: ``get_full_config()`` reads through
    ``PostgresServiceFactory.get_instance()`` and raises ``ConfigNotReadyError``
    when no factory is installed.
    """
    from src.utils import config_access
    from src.utils.postgres_service_factory import PostgresServiceFactory

    factory = PostgresServiceFactory.from_env()
    PostgresServiceFactory.set_instance(factory)
    return factory, config_access.get_full_config()


def container_corpus_fingerprint() -> str:
    """The v2 fingerprint as ``feature_matrix/lib.sh`` reads it in a stack.

    It runs inside the stack's data-manager, with that stack's config, so the
    sweep's pin check and the harness compute one digest.
    """
    factory, config = _container_factory_and_config()
    return live_corpus_fingerprint(factory.connection_pool, config)


def container_category_map_digest() -> str:
    """The searched collection's category-map digest, read in a stack."""
    factory, config = _container_factory_and_config()
    return live_category_map(factory.connection_pool, config)[2]


class CollectionNotReadyError(RuntimeError):
    """The searched collection cannot give this run a valid score."""


_READINESS_QUERY = """
SELECT count(*),
       count(c.embedding),
       count(*) FILTER (WHERE c.metadata->>'embedding_model' IS NULL),
       array_agg(DISTINCT c.metadata->>'embedding_model')
           FILTER (WHERE c.metadata->>'embedding_model' IS NOT NULL)
FROM document_chunks c
LEFT JOIN documents d ON d.id = c.document_id
WHERE (c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)
  AND (d.id IS NULL OR d.is_deleted = FALSE)
"""


def readiness_counts(cursor: Any, collection: str) -> Tuple[int, int, int, list]:
    """``(chunk_count, usable_chunk_count, untagged_chunk_count, tags)``.

    Counted under the retrieval filter, so the rows are the ones a search in
    *collection* can return.
    """
    cursor.execute(_READINESS_QUERY, (collection,))
    chunk_count, usable, untagged, tags = cursor.fetchone()
    return int(chunk_count), int(usable), int(untagged), list(tags or [])


def collection_readiness(pool: Any, identity: RetrievalIdentity) -> Dict[str, Any]:
    """Refuse an empty or mismatched collection; say how well the model is known.

    Raises ``CollectionNotReadyError`` when the collection has no chunk, no
    chunk with a vector, or a chunk tagged with a model other than
    ``identity.embedding_model``. Otherwise returns the counts and
    ``embedding_model_source``: ``"chunks"`` when every chunk carries the run's
    model, ``"chunks (N untagged)"`` when N chunks carry no tag, and
    ``"config (chunks untagged)"`` when none does. The last two warn: those
    vectors have no recorded model.
    """
    if identity.collection is None:
        raise CollectionNotReadyError("the run's config names no collection")
    chunk_count, usable, untagged, tags = _read_on_pool(
        pool, readiness_counts, identity.collection
    )
    where = (
        f"collection {identity.collection!r} "
        f"(embedding_name={identity.embedding_name!r})"
    )
    if chunk_count == 0:
        raise CollectionNotReadyError(f"{where} has no chunks; ingest it first")
    if usable == 0:
        raise CollectionNotReadyError(
            f"{where} has no chunk with a vector: chunk_count={chunk_count}, "
            f"usable_chunk_count={usable}"
        )
    others = sorted(tag for tag in tags if tag != identity.embedding_model)
    if others:
        raise CollectionNotReadyError(
            f"{where} holds chunks embedded by {', '.join(others)}, but this run "
            f"queries with {identity.embedding_model}; re-embed or fix the config"
        )
    if untagged == chunk_count:
        source = "config (chunks untagged)"
    elif untagged:
        source = f"chunks ({untagged} untagged)"
    else:
        source = "chunks"
    if untagged:
        _logger().warning(
            "%s: %d of %d chunks carry no embedding_model tag, so their model "
            "is not verified; the run records embedding_model_source=%r",
            where,
            untagged,
            chunk_count,
            source,
        )
    return {
        "chunk_count": chunk_count,
        "usable_chunk_count": usable,
        "untagged_chunk_count": untagged,
        "embedding_model_source": source,
    }


def retrieval_record(
    identity: RetrievalIdentity, readiness: Mapping[str, Any]
) -> Dict[str, Any]:
    """The ``retrieval_identity`` block a run writes: identity plus the counts."""
    return {**identity.as_dict(), **readiness}


def _logger() -> Any:
    from src.utils.logging import get_logger

    return get_logger(__name__)
