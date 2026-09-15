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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
    running: Any, selected: Any, selected_file: Optional[str]
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
    """
    have_running = running is not None
    basis = effective_config(running, selected)

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
