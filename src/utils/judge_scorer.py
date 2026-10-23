"""The offline RAGAS judge scorer (judge offload design D5, D5a, D7, D10).

One judge job runs ``run_job``: it checks its live scorer identity against its
sidecar, resumes the bundles it already claims (a requeued job keeps its job id),
claims READY bundles that match it while the time budget allows, and scores each
with ``build_ragas_scorer``, the code of the inline path. The queue rules (match,
claims, stale claims, budget) come from ``scripts/benchmarking/judge/
judge_queue.py`` as ``ctx.queue_api``, so they stay standard-library only.

Per bundle, ``score_bundle``:

1. refuses a bundle whose ``rows.jsonl`` does not have the manifest's digest or
   row count (``FAILED`` with the reason);
2. writes ``INCOMPATIBLE.<sidecar token>`` and no score when the live code or
   package digest differs from the manifest (not terminal);
3. scores each metric's eligible rows in chunks of ``chunk_size`` and appends
   each chunk to ``scores.<identity digest>.partial.jsonl``, one
   ``{key, metric, value, error}`` line per pair (D7). A restart reads only its
   own identity's file, truncates a torn last line, uses the first record of a
   repeated pair, and scores only the pairs that are not recorded. An invalid
   line that is not the last line makes the bundle ``FAILED``;
4. computes the aggregates from the complete set with
   ``score_metrics_per_eligibility``, writes ``judge_scores.json``, then
   ``SCORED``.

An exception from the judge (for example a stopped vLLM server) is not a bundle
failure: it stops the job with no terminal marker, and stale-claim recovery lets
the next job resume from the checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.utils.benchmark_schema import (
    eligible_subset,
    json_safe,
    score_metrics_per_eligibility,
)
from src.utils.exclusive_link import link_exclusive
from src.utils.judge_identity import extended_identity, identity_digest
from src.utils.ragas_scoring import RagasScorer, build_ragas_scorer

DEFAULT_CHUNK_SIZE = 8
SCORES_FILE = "judge_scores.json"
_SCORER_FIELDS = ("code_digest", "package_digest", "image_digest", "ragas_version")
_MANIFEST_KEYS = ("enabled_metrics", "ragas_run_config_kwargs", "judge_model")


class BundleFailed(Exception):
    """The bundle cannot be scored; the message is the FAILED reason."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobContext:
    """What one judge job knows: its queue, judge, scorer and live identity."""

    queue: Path
    job_id: str
    judge_env: Mapping[str, Optional[str]]
    sidecar: Any  # judge_queue.Sidecar: identity, digest, token
    live: Mapping[str, Optional[str]]
    judge_factory: Callable[[], Any]
    embeddings_factory: Callable[[str], Any]
    queue_api: Any
    chunk_size: int = DEFAULT_CHUNK_SIZE
    node: str = field(default_factory=socket.gethostname)
    requeue_count: int = 0
    now: Callable[[], str] = _utc_now
    monotonic: Callable[[], float] = time.monotonic
    started_at: str = ""
    claimed: List[str] = field(default_factory=list)


def load_bundle(bundle: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """The manifest, rows and keys of *bundle*; raises BundleFailed with the reason."""
    try:
        manifest = json.loads((bundle / "manifest.json").read_bytes())
    except (OSError, ValueError) as exc:
        raise BundleFailed(f"manifest.json does not load: {exc}") from exc
    if not isinstance(manifest, dict) or any(k not in manifest for k in _MANIFEST_KEYS):
        raise BundleFailed(f"manifest.json lacks one of {', '.join(_MANIFEST_KEYS)}")
    try:
        data = (bundle / "rows.jsonl").read_bytes()
    except OSError as exc:
        raise BundleFailed(f"rows.jsonl cannot be read: {exc}") from exc
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if digest != manifest.get("rows_sha256"):
        raise BundleFailed(
            f"rows.jsonl has digest {digest}, but the manifest records "
            f"{manifest.get('rows_sha256')}"
        )
    rows: List[Dict[str, Any]] = []
    keys: List[str] = []
    for line in data.decode("utf-8").splitlines():
        record = json.loads(line)
        keys.append(record.pop("key"))
        rows.append(record)
    if len(rows) != manifest.get("row_count"):
        raise BundleFailed(
            f"rows.jsonl has {len(rows)} rows, but the manifest records row_count "
            f"{manifest.get('row_count')}"
        )
    return manifest, rows, keys


def checkpoint_path(bundle: Path, identity: Mapping[str, Any]) -> Path:
    """``scores.<identity digest>.partial.jsonl``: one checkpoint per identity (D7)."""
    return bundle / f"scores.{identity_digest(identity).split(':', 1)[1]}.partial.jsonl"


def read_checkpoint(path: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """The recorded pairs of a checkpoint, first record first (D7).

    A last line with no final newline, or a newline-terminated last line that is
    not valid JSON, is a torn write: the file is truncated to the end of the
    last complete line. Any other invalid line raises BundleFailed with its
    line number.
    """
    if not path.exists():
        return {}
    data = path.read_bytes()
    body, newline, tail = data.rpartition(b"\n")
    lines = body.split(b"\n") if newline else []
    keep = len(body) + len(newline)
    records: Dict[Tuple[str, str], Dict[str, Any]] = {}
    offset = 0
    for number, raw in enumerate(lines, 1):
        try:
            record = json.loads(raw)
        except ValueError:
            record = None
        if (
            not isinstance(record, dict)
            or "key" not in record
            or "metric" not in record
        ):
            if number == len(lines) and not tail:
                keep = offset
                break
            raise BundleFailed(
                f"{path.name}: line {number} is not a valid score record"
            )
        records.setdefault((record["key"], record["metric"]), record)
        offset += len(raw) + 1
    if keep < len(data):
        with open(path, "r+b") as handle:
            handle.truncate(keep)
            handle.flush()
            os.fsync(handle.fileno())
    return records


def append_records(path: Path, records: List[Dict[str, Any]]) -> None:
    """Append newline-terminated records and fsync them."""
    data = b"".join(
        (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()
        for record in records
    )
    with open(path, "ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _record(key: str, metric: str, score: Any) -> Dict[str, Any]:
    usable = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
    )
    if usable:
        return {"key": key, "metric": metric, "value": score, "error": None}
    error = (
        "the judge returned no score"
        if isinstance(score, float)
        else f"the judge returned {score!r}"
    )
    return {"key": key, "metric": metric, "value": None, "error": error}


def offline_identity(
    manifest: Mapping[str, Any], ctx: JobContext, scorer: RagasScorer
) -> Dict[str, Any]:
    """The extended ``ragas_judge_identity_effective`` of this job for *manifest*."""
    env = ctx.judge_env
    base = {
        "provider": "vllm",
        "model": env["JUDGE_MODEL_ID"],
        "base_url": None,
        "mode": "unix_socket",
    }
    return extended_identity(
        base,
        model_revision=env["JUDGE_MODEL_REVISION"],
        precision=env["JUDGE_PRECISION"],
        server_image_digest=env.get("JUDGE_IMAGE_DIGEST"),
        server_args=env.get("JUDGE_SERVER_ARGS"),
        decoding=scorer.decoding,
        run_config=manifest["ragas_run_config_kwargs"],
        batch_size=manifest.get("batch_size") or None,
        chunk_size=ctx.chunk_size,
        embedding=scorer.embedding,
        metrics=scorer.metrics,
        scorer={name: ctx.sidecar.identity.get(name) for name in _SCORER_FIELDS},
    )


def _scorer_mismatch(expected: Mapping[str, Any], live: Mapping[str, Any]) -> str:
    """Both pairs of digests when they differ, else an empty string."""
    if (expected.get("code_digest"), expected.get("package_digest")) == (
        live.get("code_digest"),
        live.get("package_digest"),
    ):
        return ""
    return (
        f"expected code digest {expected.get('code_digest')} and package digest "
        f"{expected.get('package_digest')}; the live scorer has code digest "
        f"{live.get('code_digest')} and package digest {live.get('package_digest')}"
    )


def _fail(bundle: Path, reason: str) -> str:
    link_exclusive(bundle / "FAILED", f"{reason}\n".encode())
    return "FAILED"


def score_bundle(bundle: Path, ctx: JobContext) -> str:
    """Score one claimed bundle; returns SCORED, FAILED or INCOMPATIBLE."""
    bundle = Path(bundle)
    for marker in ("SCORED", "FAILED"):
        if (bundle / marker).exists():
            return marker
    try:
        manifest, rows, keys = load_bundle(bundle)
    except BundleFailed as exc:
        return _fail(bundle, str(exc))
    needed = {
        "code_digest": (manifest.get("code_version") or {}).get("digest"),
        "package_digest": manifest.get("package_digest"),
    }
    mismatch = _scorer_mismatch(needed, ctx.live)
    if mismatch:
        link_exclusive(
            bundle / f"INCOMPATIBLE.{ctx.sidecar.token}", f"{mismatch}\n".encode()
        )
        return "INCOMPATIBLE"

    run_config = manifest["ragas_run_config_kwargs"]
    scorer = build_ragas_scorer(
        enabled_metrics=manifest["enabled_metrics"],
        ragas_settings={
            "timeout": run_config["timeout"],
            "max_workers": run_config["max_workers"],
            "batch_size": manifest.get("batch_size"),
        },
        verbosity=4 if run_config.get("log_tenacity") else 0,
        judge_factory=ctx.judge_factory,
        embeddings_factory=lambda: ctx.embeddings_factory(
            manifest.get("embedding_model") or "openai"
        ),
        judge_provider="vllm",
        judge_model=ctx.judge_env["JUDGE_MODEL_ID"],
    )
    identity = offline_identity(manifest, ctx, scorer)
    path = checkpoint_path(bundle, identity)
    try:
        done = read_checkpoint(path)
    except BundleFailed as exc:
        return _fail(bundle, str(exc))

    for metric in scorer.metrics:
        eligible_rows, eligible_keys = eligible_subset(rows, keys, metric)
        todo = [
            (row, key)
            for row, key in zip(eligible_rows, eligible_keys)
            if (key, metric) not in done
        ]
        for start in range(0, len(todo), ctx.chunk_size):
            chunk = todo[start : start + ctx.chunk_size]
            scores = list(scorer.score_fn(metric, [row for row, _ in chunk]))
            if len(scores) != len(chunk):
                raise RuntimeError(
                    f"the judge returned {len(scores)} scores for {len(chunk)} rows"
                )
            records = [_record(key, metric, s) for (_, key), s in zip(chunk, scores)]
            append_records(path, records)
            for record in records:
                done.setdefault((record["key"], metric), record)

    def recorded(metric, _eligible_rows):
        _, eligible_keys = eligible_subset(rows, keys, metric)
        values = [done[(key, metric)]["value"] for key in eligible_keys]
        return [math.nan if value is None else value for value in values]

    by_key: Dict[str, Dict[str, Any]] = {key: {} for key in keys}
    aggregates = score_metrics_per_eligibility(
        rows, keys, scorer.metrics, by_key, recorded
    )
    nan_counts = {
        metric: sum(
            1
            for key in eligible_subset(rows, keys, metric)[1]
            if done[(key, metric)]["value"] is None
        )
        for metric in scorer.metrics
    }
    result = {
        "bundle": bundle.name,
        "rows_sha256": manifest["rows_sha256"],
        "ragas_judge_identity_effective": identity,
        "identity_digest": identity_digest(identity),
        "ragas_effective_settings": {
            "timeout": run_config["timeout"],
            "max_workers": run_config["max_workers"],
        },
        "judge_execution": {
            "job_id": ctx.job_id,
            "node": ctx.node,
            "started_at": ctx.started_at or ctx.now(),
            "finished_at": ctx.now(),
            "claimed_bundles": list(ctx.claimed),
            "requeue_count": ctx.requeue_count,
        },
        "scores": {key: cells for key, cells in by_key.items() if cells},
        "aggregates": aggregates,
        "nan_counts": nan_counts,
    }
    data = json.dumps(json_safe(result), sort_keys=True, indent=2, allow_nan=False)
    # A job that died after this file and before SCORED left a complete file
    # (the name never exists without its content): keep it.
    link_exclusive(bundle / SCORES_FILE, (data + "\n").encode())
    link_exclusive(bundle / "SCORED", b"")
    return "SCORED"


def preflight(ctx: JobContext) -> str:
    """``stale``, ``work`` or ``none``, before any model is loaded (D5, D10).

    A live identity that differs from the sidecar creates
    ``<queue>/SCORER_STALE.<sidecar token>``, so the match rule stops offering
    this sidecar's bundles. The work counts the bundles this job already claims
    (a requeued job) and the available ones.
    """
    mismatch = _scorer_mismatch(ctx.sidecar.identity, ctx.live)
    if mismatch:
        link_exclusive(
            Path(ctx.queue) / f"SCORER_STALE.{ctx.sidecar.token}",
            f"sidecar {ctx.sidecar.digest}: {mismatch}\n".encode(),
        )
        return "stale"
    qa = ctx.queue_api
    if qa.own_work(ctx.queue, ctx.job_id) or qa.available(
        ctx.queue, ctx.judge_env, ctx.sidecar
    ):
        return "work"
    return "none"


def _row_count(ctx: JobContext, bundle: Path) -> int:
    manifest = ctx.queue_api.read_manifest(bundle) or {}
    count = manifest.get("row_count")
    return count if isinstance(count, int) else 0


def _claim_round(
    ctx: JobContext, pending: List[Path], rows_per_minute: float, first_is_free: bool
) -> List[Path]:
    qa = ctx.queue_api
    candidates = [
        (bundle, _row_count(ctx, bundle))
        for bundle in qa.available(ctx.queue, ctx.judge_env, ctx.sidecar)
        if bundle not in pending
    ]
    seconds = qa.time_left(ctx.job_id)
    if seconds is not None:
        seconds -= sum(_row_count(ctx, b) for b in pending) / rows_per_minute * 60
    picked = qa.plan_claims(
        candidates,
        seconds_left=seconds,
        rows_per_minute=rows_per_minute,
        first_is_free=first_is_free,
    )
    claimed = []
    for bundle in picked:
        if qa.try_claim(bundle, ctx.job_id):
            claimed.append(bundle)
            ctx.claimed.append(bundle.name)
    return claimed


def run_job(ctx: JobContext) -> int:
    """Score this job's work; 0 when done or idle, 2 when the sidecar is stale."""
    state = preflight(ctx)
    if state == "stale":
        return 2
    if state == "none":
        return 0
    ctx.started_at = ctx.started_at or ctx.now()
    work = list(ctx.queue_api.own_work(ctx.queue, ctx.job_id))
    ctx.claimed.extend(bundle.name for bundle in work)
    bootstrap = float(ctx.judge_env["JUDGE_BOOTSTRAP_ROWS_PER_MINUTE"])
    rows_done = 0
    seconds_spent = 0.0

    def rate() -> float:
        if not rows_done:
            return bootstrap
        return rows_done / (seconds_spent / 60) if seconds_spent > 0 else math.inf

    work += _claim_round(ctx, work, rate(), first_is_free=not work)
    while work:
        bundle = work.pop(0)
        started = ctx.monotonic()
        outcome = score_bundle(bundle, ctx)
        elapsed = ctx.monotonic() - started
        if outcome == "SCORED":
            rows_done += _row_count(ctx, bundle)
            seconds_spent += elapsed
        work += _claim_round(ctx, work, rate(), first_is_free=False)
    return 0
