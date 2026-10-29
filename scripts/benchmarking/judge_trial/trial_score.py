#!/usr/bin/env python3
"""Score one judge bundle with a candidate RAGAS judge served by vLLM (judge trial, #737 step 5.3).

    trial_score.py --bundle <bundle folder> --out-dir <folder> --judge-env <file> \
        (--socket <unix socket> | --base-url <url>) [--metrics m1,m2] [--chunk-size 8] \
        [--server-image-digest D] [--scorer-image-digest D] [--job-id ID]

This is the trial sibling of ``scripts/benchmarking/judge/score.py``: the same ragas
code as the inline path (``build_ragas_scorer``), the same per-pair checkpoint format
(``judge_scorer.read_checkpoint`` / ``append_records``), the same aggregates
(``score_metrics_per_eligibility``), but no queue, no claims and no identity match: a
trial scores one bundle with whichever judge the job serves, and records that judge's
identity in the output. The bundle folder is never written; the checkpoint and the
output go to ``--out-dir``. Exit codes: 0 scored (or already scored), 1 refused, 2 usage.

The judge env file is ``KEY=VALUE`` lines (the production ``judge.env`` rules):
``JUDGE_MODEL_ID``, ``JUDGE_MODEL_REVISION``, ``JUDGE_PRECISION``, optional
``JUDGE_SERVER_ARGS``, ``JUDGE_REASONING_EFFORT`` (sent as the request's
``reasoning_effort``), ``JUDGE_CHAT_TEMPLATE_KWARGS`` (a JSON object sent as
``chat_template_kwargs``), ``JUDGE_MAX_TOKENS`` and ``JUDGE_SEED``.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import socket
import sys  # noqa: E402  (os is needed before the sys.path edit below)
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# In the repo this file is scripts/benchmarking/judge_trial/trial_score.py; in a trial
# package it is bind-mounted elsewhere and the job names archi's root in ARCHI_ROOT
# (/root/archi in the scorer image).
REPO_ROOT = Path(os.environ.get("ARCHI_ROOT") or Path(__file__).resolve().parents[3])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.benchmark_provenance import (  # noqa: E402
    collect_code_version,
    collect_package_digest,
)
from src.utils.benchmark_resilience import build_ragas_aggregates  # noqa: E402
from src.utils.benchmark_schema import (  # noqa: E402
    RAGAS_METRIC_NAMES,
    eligible_subset,
    json_safe,
    score_metrics_per_eligibility,
)
from src.utils.judge_identity import (  # noqa: E402
    extended_identity,
    identity_digest,
    ragas_version,
)
from src.utils.judge_scorer import (  # noqa: E402
    DEFAULT_CHUNK_SIZE,
    BundleFailed,
    _record,
    append_records,
    load_bundle,
    read_checkpoint,
)

SCORES_SUFFIX = ".judge_scores.json"
_ENV_KEYS = ("JUDGE_MODEL_ID", "JUDGE_MODEL_REVISION", "JUDGE_PRECISION")


class Refused(Exception):
    """A refusal before any judge call; the message is for the operator."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_judge_env(path: Path) -> Dict[str, Optional[str]]:
    """The judge file, by the rules of the production ``judge_queue.read_judge_env``:
    ``KEY=VALUE`` lines, a comment on its own line, matching outer quotes stripped.
    Re-implemented here because that module loads ``src`` by its own file path and so
    cannot be copied into a package. Only the three model keys are required."""
    values: Dict[str, Optional[str]] = {}
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise Refused(f"{path}: {exc}") from exc
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Refused(f"{path}: line {number} is not KEY=VALUE: {raw!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value or None
    for key in _ENV_KEYS:
        if not (values.get(key) or "").strip():
            raise Refused(f"{path}: {key} is missing or empty")
    return values


def request_options(env: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """The per-request settings of the judge client, from the env file.

    They become part of the judge identity (``decoding``), because they change
    what the judge writes: the reasoning effort, the chat-template switches, the
    output budget and the seed.
    """
    options: Dict[str, Any] = {}
    effort = (env.get("JUDGE_REASONING_EFFORT") or "").strip()
    if effort:
        options["reasoning_effort"] = effort
    kwargs = (env.get("JUDGE_CHAT_TEMPLATE_KWARGS") or "").strip()
    if kwargs:
        try:
            parsed = json.loads(kwargs)
        except ValueError as exc:
            raise Refused(f"JUDGE_CHAT_TEMPLATE_KWARGS is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise Refused("JUDGE_CHAT_TEMPLATE_KWARGS must be a JSON object")
        options["extra_body"] = {"chat_template_kwargs": parsed}
    for key, name in (("JUDGE_MAX_TOKENS", "max_tokens"), ("JUDGE_SEED", "seed")):
        raw = (env.get(key) or "").strip()
        if raw:
            try:
                options[name] = int(raw)
            except ValueError as exc:
                raise Refused(f"{key} must be an integer, not {raw!r}") from exc
    return options


def judge_factory_for(
    env: Dict[str, Optional[str]],
    socket_path: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: int = 300,
):
    """A judge factory with the trial's request options.

    With ``socket_path`` it is the production Unix-socket client
    (``build_uds_judge_llm``); with ``base_url`` it is a plain OpenAI-compatible TCP
    client, for a smoke test against a server that already runs (the dev host). The
    key comes from ``VLLM_API_KEY`` in both cases.
    """
    if bool(socket_path) == bool(base_url):
        raise Refused("give exactly one of --socket and --base-url")
    model_id = env["JUDGE_MODEL_ID"]
    if not model_id:
        raise Refused("JUDGE_MODEL_ID is empty")
    options = request_options(env)

    def factory():
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        from src.utils.ragas_scoring import build_uds_judge_llm

        if socket_path:
            chat_openai: Any = functools.partial(ChatOpenAI, **options)
            return build_uds_judge_llm(model_id, socket_path, chat_openai=chat_openai)
        return ChatOpenAI(
            model=model_id,
            api_key=SecretStr(os.environ.get("VLLM_API_KEY") or "none"),
            base_url=base_url,
            timeout=timeout,
            max_retries=2,
            **options,
        )

    return factory


def embeddings_factory_for(manifest: Dict[str, Any]):
    def factory():
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_openai import OpenAIEmbeddings

        from src.utils.ragas_scoring import build_ragas_embeddings

        return build_ragas_embeddings(
            manifest.get("embedding_model") or "openai",
            openai_embeddings=OpenAIEmbeddings,
            huggingface_embeddings=HuggingFaceEmbeddings,
        )

    return factory


def pick_metrics(manifest: Dict[str, Any], requested: Optional[str]) -> List[str]:
    if not requested or requested == "manifest":
        return list(manifest["enabled_metrics"])
    if requested == "all":
        return list(RAGAS_METRIC_NAMES)
    names = [m.strip() for m in requested.split(",") if m.strip()]
    unknown = [m for m in names if m not in RAGAS_METRIC_NAMES]
    if unknown:
        raise Refused(f"unknown metrics {unknown}; known: {list(RAGAS_METRIC_NAMES)}")
    return [m for m in RAGAS_METRIC_NAMES if m in names]


def scores_path(out_dir: Path, bundle: Path) -> Path:
    return out_dir / f"{bundle.name}{SCORES_SUFFIX}"


def score_trial_bundle(
    bundle: Path,
    out_dir: Path,
    env: Dict[str, Optional[str]],
    *,
    judge_factory,
    embeddings_factory,
    metrics: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    server_image_digest: Optional[str] = None,
    scorer_image_digest: Optional[str] = None,
    job_id: Optional[str] = None,
    build_scorer=None,
    live_identity: Optional[Dict[str, Any]] = None,
) -> Path:
    """Score *bundle* and write ``<out_dir>/<bundle name>.judge_scores.json``.

    Resumes from ``<out_dir>/<bundle name>.scores.<identity digest>.partial.jsonl``.
    """
    from src.utils.ragas_scoring import build_ragas_scorer

    build_scorer = build_scorer or build_ragas_scorer
    bundle = Path(bundle)
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise Refused(f"--out-dir {out_dir} is not a folder")
    if (
        out_dir.resolve() == bundle.resolve()
        or bundle.resolve() in out_dir.resolve().parents
    ):
        raise Refused(f"--out-dir {out_dir} must be outside the bundle folder")
    target = scores_path(out_dir, bundle)
    if target.exists():
        print(f"trial_score: {target} exists; nothing to do", file=sys.stderr)
        return target
    try:
        manifest, rows, keys = load_bundle(bundle)
    except BundleFailed as exc:
        raise Refused(f"{bundle}: {exc}") from exc

    started = _utc_now()
    t0 = time.monotonic()
    enabled = pick_metrics(manifest, metrics)
    run_config = manifest["ragas_run_config_kwargs"]
    scorer = build_scorer(
        enabled_metrics=enabled,
        ragas_settings={
            "timeout": run_config["timeout"],
            "max_workers": run_config["max_workers"],
            "batch_size": manifest.get("batch_size"),
        },
        verbosity=4 if run_config.get("log_tenacity") else 0,
        judge_factory=judge_factory,
        embeddings_factory=embeddings_factory,
        judge_provider="vllm",
        judge_model=env["JUDGE_MODEL_ID"],
    )
    live = live_identity if live_identity is not None else _live_identity()
    identity = extended_identity(
        {
            "provider": "vllm",
            "model": env["JUDGE_MODEL_ID"],
            "base_url": None,
            "mode": "unix_socket",
        },
        model_revision=env["JUDGE_MODEL_REVISION"],
        precision=env["JUDGE_PRECISION"],
        server_image_digest=server_image_digest,
        server_args=env.get("JUDGE_SERVER_ARGS"),
        decoding={**dict(scorer.decoding), "request_options": request_options(env)},
        run_config=run_config,
        batch_size=manifest.get("batch_size") or None,
        chunk_size=chunk_size,
        embedding=scorer.embedding,
        metrics=scorer.metrics,
        scorer={
            "code_digest": live.get("code_digest"),
            "package_digest": live.get("package_digest"),
            "image_digest": scorer_image_digest,
            "ragas_version": ragas_version(),
        },
    )
    digest = identity_digest(identity).split(":", 1)[1]
    checkpoint = out_dir / f"{bundle.name}.scores.{digest}.partial.jsonl"
    try:
        done = read_checkpoint(checkpoint)
    except BundleFailed as exc:
        raise Refused(f"{checkpoint}: {exc}") from exc
    resumed = len(done)

    chunk_times: List[float] = []
    for metric in scorer.metrics:
        eligible_rows, eligible_keys = eligible_subset(rows, keys, metric)
        todo = [
            (row, key)
            for row, key in zip(eligible_rows, eligible_keys)
            if (key, metric) not in done
        ]
        for start in range(0, len(todo), chunk_size):
            chunk = todo[start : start + chunk_size]
            t_chunk = time.monotonic()
            scores = list(scorer.score_fn(metric, [row for row, _ in chunk]))
            chunk_times.append(time.monotonic() - t_chunk)
            if len(scores) != len(chunk):
                raise RuntimeError(
                    f"the judge returned {len(scores)} scores for {len(chunk)} rows"
                )
            records = [_record(key, metric, s) for (_, key), s in zip(chunk, scores)]
            append_records(checkpoint, records)
            for record in records:
                done.setdefault((record["key"], metric), record)
            print(
                f"trial_score: {bundle.name} {metric} {min(start + chunk_size, len(todo))}"
                f"/{len(todo)} pairs ({chunk_times[-1]:.1f}s)",
                file=sys.stderr,
                flush=True,
            )

    def recorded(metric, _eligible_rows):
        _, eligible_keys = eligible_subset(rows, keys, metric)
        values = [done[(key, metric)]["value"] for key in eligible_keys]
        return [math.nan if value is None else value for value in values]

    by_key: Dict[str, Dict[str, Any]] = {key: {} for key in keys}
    if rows:
        aggregates = score_metrics_per_eligibility(
            rows, keys, scorer.metrics, by_key, recorded
        )
    else:
        aggregates = build_ragas_aggregates(None, enabled_metrics=scorer.metrics)
    nan_counts = {
        metric: sum(
            1
            for key in eligible_subset(rows, keys, metric)[1]
            if done[(key, metric)]["value"] is None
        )
        for metric in scorer.metrics
    }
    errors = {
        metric: sorted(
            {
                done[(key, metric)]["error"]
                for key in eligible_subset(rows, keys, metric)[1]
                if done[(key, metric)]["value"] is None
            }
        )
        for metric in scorer.metrics
    }
    elapsed = time.monotonic() - t0
    pairs = sum(len(eligible_subset(rows, keys, m)[1]) for m in scorer.metrics)
    result = {
        "bundle": bundle.name,
        "rows_sha256": manifest["rows_sha256"],
        "result_basename": manifest.get("result_basename"),
        "bundle_judge_mode": manifest.get("judge_mode"),
        "row_count": len(rows),
        "metrics": scorer.metrics,
        "ragas_judge_identity_effective": identity,
        "identity_digest": identity_digest(identity),
        "ragas_effective_settings": {
            "timeout": run_config["timeout"],
            "max_workers": run_config["max_workers"],
        },
        "judge_execution": {
            "job_id": job_id,
            "node": socket.gethostname(),
            "started_at": started,
            "finished_at": _utc_now(),
            "elapsed_seconds": round(elapsed, 1),
            "pairs_total": pairs,
            "pairs_resumed_from_checkpoint": resumed,
            "pairs_judged_this_run": pairs - resumed,
            "chunk_seconds": [round(t, 1) for t in chunk_times],
            "pairs_per_minute": (
                round((pairs - resumed) / (elapsed / 60.0), 2) if elapsed > 0 else None
            ),
            "requeue_count": int(os.environ.get("SLURM_RESTART_COUNT") or 0),
        },
        "judge_usage": scorer.recorder.snapshot(),
        "scores": {key: cells for key, cells in by_key.items() if cells},
        "aggregates": aggregates,
        "nan_counts": nan_counts,
        "errors": errors,
    }
    data = json.dumps(json_safe(result), sort_keys=True, indent=2, allow_nan=False)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(data + "\n")
    os.replace(tmp, target)
    return target


def _live_identity() -> Dict[str, Optional[str]]:
    package_dir = str(REPO_ROOT / "src")
    return {
        "code_digest": collect_code_version(package_dir, None)["digest"],
        "package_digest": collect_package_digest().get("digest"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score one judge bundle with the candidate judge a trial job serves."
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--judge-env", required=True, type=Path)
    parser.add_argument("--socket", help="the vLLM Unix socket (the Slurm job)")
    parser.add_argument(
        "--base-url", help="an OpenAI-compatible URL instead of --socket (smoke tests)"
    )
    parser.add_argument("--metrics", default="manifest", help="manifest, all or a list")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--server-image-digest")
    parser.add_argument("--scorer-image-digest")
    parser.add_argument("--job-id", default=os.environ.get("SLURM_JOB_ID"))
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        print("trial_score: --chunk-size must be 1 or more", file=sys.stderr)
        return 2
    try:
        env = read_judge_env(args.judge_env)
        manifest_path = args.bundle / "manifest.json"
        if not manifest_path.is_file():
            raise Refused(f"{args.bundle} has no manifest.json")
        manifest = json.loads(manifest_path.read_text())
        target = score_trial_bundle(
            args.bundle,
            args.out_dir,
            env,
            judge_factory=judge_factory_for(
                env, socket_path=args.socket, base_url=args.base_url
            ),
            embeddings_factory=embeddings_factory_for(manifest),
            metrics=args.metrics,
            chunk_size=args.chunk_size,
            server_image_digest=args.server_image_digest,
            scorer_image_digest=args.scorer_image_digest,
            job_id=args.job_id,
        )
    except Refused as exc:
        print(f"trial_score: refused: {exc}", file=sys.stderr)
        return 1
    print(f"trial_score: wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
