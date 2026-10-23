#!/usr/bin/env python3
"""The offline RAGAS judge scorer (judge offload design D5, D6, D10).

    score.py write-identity --out <scorer-identity.json> [--image-digest D] \
        [--package-dir <src>]
    score.py check --queue Q --judge-env E --sidecar S [--job-id ID]
    score.py run --queue Q --judge-env E --sidecar S --socket P [--job-id ID] \
        [--chunk-size 8]

``write-identity`` runs at the scorer image build (#733): it writes the code
digest of the installed ``src`` (the function of the benchmark's
``code_version.digest``), the package digest, the image digest and the ragas
version. ``check`` runs before vLLM starts: exit 0 when the job has work, 3 when
it has none, 2 when the sidecar does not describe the live environment (it then
writes ``SCORER_STALE.<token>``). ``run`` scores the job's bundles through the
judge on the Unix socket, with the key in ``VLLM_API_KEY``; vLLM must serve the
model under the name ``JUDGE_MODEL_ID``. Its exit codes are 0 (done or idle) and
2 (stale sidecar). Bad input exits 1. ``--job-id`` defaults to
``$SLURM_JOB_ID``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils import judge_scorer  # noqa: E402
from src.utils.benchmark_provenance import (  # noqa: E402
    collect_code_version,
    collect_package_digest,
)
from src.utils.judge_identity import (  # noqa: E402
    compute_scorer_identity,
    write_scorer_identity,
)
from src.utils.judge_scorer import (  # noqa: E402
    DEFAULT_CHUNK_SIZE,
    JobContext,
    preflight,
    run_job,
)

# The `src` package this process imported (src/utils/judge_scorer.py -> src/).
# `src` has no __init__.py, so its own __file__ is None.
PACKAGE_DIR = str(Path(judge_scorer.__file__).resolve().parents[1])


def load_queue():
    """``judge_queue.py`` from this folder, by path (it is not a package)."""
    path = Path(__file__).resolve().parent / "judge_queue.py"
    spec = importlib.util.spec_from_file_location("judge_queue", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_identity(package_dir: str) -> Dict[str, Optional[str]]:
    """The code and package digests of the environment this process runs in."""
    return {
        "code_digest": collect_code_version(package_dir, None)["digest"],
        "package_digest": collect_package_digest().get("digest"),
    }


def _context(args, queue_api) -> JobContext:
    judge_env = queue_api.read_judge_env(args.judge_env)
    socket_path = getattr(args, "socket", None)

    def judge_factory():
        from src.utils.ragas_scoring import build_uds_judge_llm

        return build_uds_judge_llm(judge_env["JUDGE_MODEL_ID"], socket_path)

    def embeddings_factory(name):
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_openai import OpenAIEmbeddings

        from src.utils.ragas_scoring import build_ragas_embeddings

        return build_ragas_embeddings(
            name,
            openai_embeddings=OpenAIEmbeddings,
            huggingface_embeddings=HuggingFaceEmbeddings,
        )

    return JobContext(
        queue=args.queue,
        job_id=args.job_id,
        judge_env=judge_env,
        sidecar=queue_api.read_sidecar(args.sidecar),
        live=live_identity(PACKAGE_DIR),
        judge_factory=judge_factory,
        embeddings_factory=embeddings_factory,
        queue_api=queue_api,
        chunk_size=getattr(args, "chunk_size", DEFAULT_CHUNK_SIZE),
        requeue_count=int(os.environ.get("SLURM_RESTART_COUNT") or 0),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The offline RAGAS judge scorer.")
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("write-identity")
    identity.add_argument("--out", required=True, type=Path)
    identity.add_argument("--image-digest")
    identity.add_argument("--package-dir", default=PACKAGE_DIR)
    for name in ("check", "run"):
        command = commands.add_parser(name)
        command.add_argument("--queue", required=True, type=Path)
        command.add_argument("--judge-env", required=True, type=Path)
        command.add_argument("--sidecar", required=True, type=Path)
        command.add_argument("--job-id", default=os.environ.get("SLURM_JOB_ID"))
        if name == "run":
            command.add_argument("--socket", required=True)
            command.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args(argv)

    if args.command == "write-identity":
        try:
            values = compute_scorer_identity(
                args.package_dir, image_digest=args.image_digest
            )
        except ValueError as exc:
            print(f"score: {exc}", file=sys.stderr)
            return 1
        write_scorer_identity(args.out, values)
        return 0

    if not args.job_id:
        print("score: give --job-id or set SLURM_JOB_ID", file=sys.stderr)
        return 1
    queue_api = load_queue()
    try:
        ctx = _context(args, queue_api)
    except (OSError, ValueError) as exc:
        print(f"score: {exc}", file=sys.stderr)
        return 1
    if args.command == "check":
        return {"stale": 2, "work": 0, "none": 3}[preflight(ctx)]
    return run_job(ctx)


if __name__ == "__main__":
    sys.exit(main())
