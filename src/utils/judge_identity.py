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
import importlib.metadata
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.utils.benchmark_provenance import collect_code_version, collect_package_digest

#: The fields #732 adds to #618's four (provider, model, base_url, mode). An
#: API judge has no revision, precision, image or server: those are None.
IDENTITY_FIELDS = (
    "model_revision",
    "precision",
    "server_image_digest",
    "server_args",
    "decoding",
    "timeout",
    "max_workers",
    "batch_size",
    "chunk_size",
    "embedding",
    "metrics",
    "scorer",
)


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


def _scalar(value: Any) -> Any:
    """*value* when it is a JSON scalar that compares stably, else None."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def describe_decoding(llm: Any) -> Dict[str, Any]:
    """The decoding settings the built judge object sends (D10)."""
    return {
        name: _scalar(getattr(llm, name, None))
        for name in ("temperature", "top_p", "max_tokens")
    }


def describe_embedding(embeddings: Any) -> Dict[str, Any]:
    """The embedding ragas uses, read from the built object, not the config.

    The config value ``openai`` or ``huggingface`` names no model: each provider
    uses its library's default, and an unknown value falls back to OpenAI.
    """
    kwargs = getattr(embeddings, "model_kwargs", None)
    revision = kwargs.get("revision") if isinstance(kwargs, dict) else None
    return {
        "class": type(embeddings).__name__,
        "model": _scalar(getattr(embeddings, "model", None))
        or _scalar(getattr(embeddings, "model_name", None)),
        "revision": _scalar(revision),
        "endpoint": _embedding_endpoint(embeddings),
    }


def _embedding_endpoint(embeddings: Any) -> Any:
    """The base URL the embedding client sends to; None for a local model.

    The configured base when there is one, else the base URL of the built
    openai client, so one model name behind two endpoints is two identities.
    """
    for name in ("openai_api_base", "base_url"):
        value = _scalar(getattr(embeddings, name, None))
        if isinstance(value, str) and value:
            return value
    client = getattr(getattr(embeddings, "client", None), "_client", None)
    base = getattr(client, "base_url", None)
    return None if base is None else str(base)


def extended_identity(
    base: Mapping[str, Any],
    *,
    model_revision: Optional[str],
    precision: Optional[str],
    server_image_digest: Optional[str],
    server_args: Optional[str],
    decoding: Mapping[str, Any],
    run_config: Mapping[str, Any],
    batch_size: Optional[int],
    chunk_size: Optional[int],
    embedding: Mapping[str, Any],
    metrics: Sequence[str],
    scorer: Mapping[str, Any],
) -> Dict[str, Any]:
    """#618's identity *base* plus the D10 fields, for the inline and offline paths.

    *run_config* is the output of ``ragas_run_config_kwargs``; its ``timeout``
    and ``max_workers`` go in, ``log_tenacity`` does not (it changes the log,
    not the scores).
    """
    return {
        **base,
        "model_revision": model_revision,
        "precision": precision,
        "server_image_digest": server_image_digest,
        "server_args": server_args,
        "decoding": dict(decoding),
        "timeout": run_config["timeout"],
        "max_workers": run_config["max_workers"],
        "batch_size": batch_size,
        "chunk_size": chunk_size,
        "embedding": dict(embedding),
        "metrics": list(metrics),
        "scorer": dict(scorer),
    }


def ragas_version() -> Optional[str]:
    try:
        return importlib.metadata.version("ragas")
    except importlib.metadata.PackageNotFoundError:
        return None


def inline_scorer_identity(
    package_dir: str, *, package_digest_fn: Optional[Callable[[], Dict]] = None
) -> Dict[str, Any]:
    """The scorer of an inline run is the benchmark image itself (D10).

    Never raises: a digest that cannot be computed is recorded as None.
    """
    package = (package_digest_fn or collect_package_digest)()
    return {
        "code_digest": collect_code_version(package_dir, None)["digest"],
        "package_digest": package.get("digest"),
        "image_digest": None,
        "ragas_version": ragas_version(),
    }


def compute_scorer_identity(
    package_dir: str,
    *,
    image_digest: Optional[str] = None,
    package_digest_fn: Optional[Callable[[], Dict]] = None,
) -> Dict[str, Any]:
    """The scorer identity of an installed ``src`` (D10), for ``scorer-identity.json``.

    The code digest comes from ``collect_code_version``, the function that
    makes the benchmark's ``code_version.digest``, so a scorer built from the
    run's ``src`` matches its bundles. Raises ValueError when either digest is
    unavailable: a sidecar with no digest could never match a bundle.
    """
    code = collect_code_version(package_dir, None)
    if code["digest"] is None:
        raise ValueError(f"the scorer code digest is unavailable: {code['source']}")
    package = (package_digest_fn or collect_package_digest)()
    if package.get("digest") is None:
        raise ValueError(
            f"the scorer package digest is unavailable: {package.get('source')}"
        )
    return {
        "code_digest": code["digest"],
        "package_digest": package["digest"],
        "image_digest": image_digest,
        "ragas_version": ragas_version(),
    }


def write_scorer_identity(path, identity: Mapping[str, Any]) -> bytes:
    """Write ``scorer-identity.json`` (sorted keys); a rebuild replaces it."""
    data = (json.dumps(dict(identity), sort_keys=True, indent=2) + "\n").encode()
    target = Path(path)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return data
