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
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "KEY_SETTING_PATHS",
    "code_fingerprint",
    "code_version",
    "collect_code_version",
    "config_divergence",
    "config_fingerprint",
    "config_version",
    "corpus_fingerprint",
    "loaded_module_files",
    "read_module_sources",
    "reconstruct_version_stamp",
    "settings_at_paths",
]

#: Settings a campaign is known to vary, surfaced compactly so a reader can see
#: which arm an artifact describes without parsing a 500-key configuration blob.
#:
#: This list is a convenience, never the guarantee. It is necessarily incomplete
#: -- ``services.chat_app.context_editing`` did not exist when the 2026-08-11
#: runs were recorded, and the next campaign will vary something not listed here.
#: ``config_version``'s ``digest`` is the guarantee: it covers every setting,
#: so two runs with equal digests had equal configurations whether or not the
#: settings they varied ever appeared below.
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
    """Return *value* as a mapping to recurse into, or ``None`` if it is a leaf."""
    if isinstance(value, dict):
        return value
    if _is_empty_container(value):
        return {}
    return None


def _leaves_equal(left: Any, right: Any) -> bool:
    """Compare two leaves.

    Reached only when at least one side is a non-empty non-mapping, because
    ``_as_mapping`` turns every absent-or-empty container into ``{}`` and those
    are recursed into instead. So there is no "both empty" case to handle here.
    """
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
    """Digest of the corpus, equal exactly when its content is equal.

    *rows* are ``(resource_hash, size_bytes)`` pairs, one per document. Order is
    irrelevant -- the rows are sorted before hashing -- so the digest does not
    depend on how the query happened to return them. A ``None`` size is kept
    distinct from ``0``: a document with no recorded size is not a zero-byte
    document.

    Unlike ``corpus_snapshot_id``, which is a per-invocation nonce, two runs
    over an unchanged corpus produce the same value here. That is what makes
    "these arms saw the same corpus" a checkable claim rather than an
    assumption.
    """
    records: List[str] = []
    for row in rows:
        pair: Tuple[Any, ...] = tuple(row)
        if len(pair) != 2:
            raise ValueError(
                f"corpus row must be a (resource_hash, size_bytes) pair, got {pair!r}"
            )
        resource_hash, size_bytes = pair
        size = "" if size_bytes is None else str(int(size_bytes))
        records.append(f"{_escape(resource_hash)}:{size}")
    digest = hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def config_fingerprint(config: Any) -> str:
    """Digest of a configuration, equal exactly when its content is equal.

    ``config_divergence`` can only run while both the selected file and the
    running configuration are in hand, so it cannot answer the question a reader
    of a finished artifact asks: *was this the same configuration as that other
    run?* A content digest survives in the artifact and answers it forever.

    Keys are sorted, so a mapping round-tripped through YAML or JSONB
    fingerprints the same as the one that went in. JSON keeps ``0`` and
    ``False`` distinct, which matters -- they are different settings.

    Never raises: provenance must not be the reason a finished run loses its
    scores, so a value JSON cannot encode falls back to its ``repr``.
    """
    canonical = json.dumps(
        config, sort_keys=True, separators=(",", ":"), default=repr, ensure_ascii=True
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def code_fingerprint(sources: Iterable[Tuple[str, bytes]]) -> str:
    """Digest of the code that was actually loaded, equal when that code is equal.

    *sources* are ``(module_name, source_bytes)`` pairs. The module name is part
    of the identity, not just the bytes: a renamed or newly imported module is
    different code even when every body is unchanged.

    This exists because ``git_info.last_commit`` cannot do the job.
    ``git_info.yaml`` is written once by ``archi create`` and then frozen, so
    every run between 2026-08-11 and 2026-08-17 reports ``0a157cdce0`` with an
    empty diff -- the commit identifies the *deploy*, not the image, and cannot
    distinguish two arms that ran different code against one deployment.

    Raises ``ValueError`` on an empty *sources*: an empty digest would silently
    claim that two images whose code was never inspected had matched.
    """
    records: List[str] = []
    for module_name, body in sources:
        body_digest = hashlib.sha256(body).hexdigest()
        records.append(f"{_escape(module_name)}:{body_digest}")
    if not records:
        raise ValueError("cannot fingerprint code: no module sources were supplied")
    digest = hashlib.sha256("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def loaded_module_files(
    modules: Mapping[str, Any], package: str = "src"
) -> List[Tuple[str, str]]:
    """``(module_name, file_path)`` for every loaded module under *package*.

    Pass ``sys.modules``. The benchmark imports and calls ``archi()`` in-process,
    so the modules loaded here *are* the code under test -- and they are the
    baked site-packages copy, not the ``src/`` bind mount, which is precisely
    the code a commit hash fails to identify.

    Modules with no source file (namespace packages, C extensions) are skipped:
    there is nothing to hash. The prefix check is on the dotted path, so
    ``srcfoo`` is not mistaken for a submodule of ``src``.
    """
    prefix = f"{package}."
    found: List[Tuple[str, str]] = []
    for name, module in modules.items():
        if name != package and not name.startswith(prefix):
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        found.append((name, path))
    return sorted(found)


def settings_at_paths(config: Any, paths: Iterable[str]) -> Dict[str, Any]:
    """The values of *paths* that are present in *config*, keyed by dotted path.

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

    Records a content digest of the loaded code as the identity, and keeps the
    deploy-time commit alongside it -- labelled, so a reader does not mistake a
    frozen value for the code under test.
    """
    info = deploy_git_info or {}
    commit = (info.get("last_commit") or "").strip() or None

    try:
        digest: Optional[str] = code_fingerprint(sources)
        source = "content digest of the `src` modules loaded in the benchmark image"
    except ValueError as exc:
        digest = None
        source = f"<unavailable: {exc}>"

    return {
        "digest": digest,
        "source": source,
        "module_count": len(sources),
        "deploy_git_commit": commit,
        "deploy_git_dirty": bool((info.get("git_diff") or "").strip()),
        "deploy_git_note": _DEPLOY_GIT_NOTE,
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
            "module_count": None,
            "deploy_git_commit": commit,
            "deploy_git_dirty": bool((info.get("git_diff") or "").strip()),
            "deploy_git_note": _DEPLOY_GIT_NOTE,
        },
        "config_version": {
            "digest": config_fingerprint(recorded_config) if have_config else None,
            "source": (
                "reconstructed from the configuration file recorded in this "
                "artifact; the running configuration was never captured, so this "
                "may not describe the run"
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


def read_module_sources(files: Iterable[Tuple[str, str]]) -> List[Tuple[str, bytes]]:
    """Read each ``(module_name, file_path)`` into ``(module_name, source_bytes)``.

    A file that cannot be read is skipped rather than fatal: a partial digest
    still distinguishes two images, and a benchmark that has already scored its
    questions must not lose them because one source file was unreadable.
    """
    sources: List[Tuple[str, bytes]] = []
    for module_name, path in files:
        try:
            with open(path, "rb") as handle:
                sources.append((module_name, handle.read()))
        except OSError:
            continue
    return sources


def collect_code_version(
    modules: Mapping[str, Any],
    deploy_git_info: Optional[Mapping[str, Any]],
    package: str = "src",
) -> Dict[str, Any]:
    """Build the ``code_version`` block from a live ``sys.modules``.

    The single call site a report writer needs: it selects the loaded modules of
    *package*, reads their sources, and digests them.
    """
    sources = read_module_sources(loaded_module_files(modules, package=package))
    return code_version(sources=sources, deploy_git_info=deploy_git_info)


def config_version(
    running: Any, selected: Any, selected_file: Optional[str]
) -> Dict[str, Any]:
    """The ``config_version`` block for a report's metadata.

    The digest is taken from *running* -- the configuration the agent actually
    read from Postgres -- because that is what produced the scores. *selected*
    (the YAML file the operator chose) is fingerprinted separately and the
    settings where the two disagree are named, so the artifact carries the
    mislabel as a visible finding rather than as a wrong number.

    If *running* is unavailable the digest falls back to *selected* and says so.
    A degraded, labelled answer beats a confident wrong one.
    """
    have_running = running is not None
    basis = running if have_running else selected

    return {
        "digest": config_fingerprint(basis),
        "source": (
            "running configuration read from Postgres (what the agent used)"
            if have_running
            else "selected file on disk -- the running configuration was unavailable, "
            "so this may not describe the run"
        ),
        "selected_file": selected_file,
        "selected_file_digest": (
            None if selected is None else config_fingerprint(selected)
        ),
        "divergence_from_selected_file": (
            config_divergence(selected, running) if have_running else None
        ),
        "key_settings": settings_at_paths(basis, KEY_SETTING_PATHS),
    }
