"""Smoke test: HUIT Bedrock as the RAGAS judge LLM.

Runs a 3-question RAGAS evaluation using ``answer_relevancy``,
``faithfulness``, ``context_precision`` and ``context_recall``, with
HUIT Bedrock Claude Sonnet 4.5 as the judge. Asserts that every record
receives finite float values for all four metrics — the failure mode we
care about is silent NaN/None scores hiding a broken judge.

Run inside the benchmarks container (where ``ragas`` is installed):

    archi evaluate --config config/benchmarking/ragas.yaml --name smoke ...
    docker exec archi-benchmark-smoke python -m tests.smoke.ragas_smoke

Or in a standalone env with ``ragas`` + ``langchain_huggingface`` and
``HUIT_API_KEY`` exported.
"""

from __future__ import annotations

import math
import os
import sys

# Late imports so the file can be imported in environments without ragas
# (e.g. pyright in the local dev env) without crashing.
def main() -> int:
    if not os.environ.get("HUIT_API_KEY"):
        print("ERROR: HUIT_API_KEY not set in environment.", file=sys.stderr)
        return 2

    try:
        from datasets import Dataset
        from langchain_huggingface import HuggingFaceEmbeddings  # pyright: ignore[reportMissingImports]
        from ragas import evaluate  # pyright: ignore[reportMissingImports]
        from ragas.embeddings import LangchainEmbeddingsWrapper  # pyright: ignore[reportMissingImports]
        from ragas.llms import LangchainLLMWrapper  # pyright: ignore[reportMissingImports]
        from ragas.metrics import (  # pyright: ignore[reportMissingImports]
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError as exc:
        print(
            f"ERROR: missing required dep ({exc}). Run inside the benchmarks container "
            "or install ragas + langchain-huggingface + datasets locally.",
            file=sys.stderr,
        )
        return 2

    from src.archi.providers.huit_bedrock_provider import (
        DEFAULT_HUIT_BEDROCK_MODEL,
        HuitBedrockChat,
    )

    chat = HuitBedrockChat(
        model_id=DEFAULT_HUIT_BEDROCK_MODEL,
        api_key=os.environ["HUIT_API_KEY"],
        max_tokens=1024,
    )
    llm_judge = LangchainLLMWrapper(chat)
    emb = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    )

    ds = Dataset.from_dict({
        "question": [
            "What partition do I use for GPU jobs on FASRC Cannon?",
            "How do I check my fairshare on Cannon?",
            "Where should I store large datasets that don't need long-term backup?",
        ],
        "answer": [
            "Use the `gpu` partition. Request GPUs with `--gres=gpu:N` in your SLURM submit script.",
            "Run `sshare -U $USER` to see your fairshare value.",
            "Use `/n/holyscratch01/<lab>/` — fast, no backups, periodically purged.",
        ],
        "contexts": [
            ["FASRC Cannon has a `gpu` partition for GPU-bound jobs. Request GPUs via --gres=gpu:N."],
            ["Use sshare -U $USER to see your fairshare value on Cannon."],
            ["holyscratch01 is the fast scratch filesystem; no backups; auto-purged."],
        ],
        "ground_truth": [
            "Use the gpu partition with --gres=gpu:N.",
            "Run sshare -U $USER.",
            "Use /n/holyscratch01/.",
        ],
    })

    metrics = [answer_relevancy, faithfulness, context_precision, context_recall]
    print(f"Running RAGAS eval on {len(ds)} questions with HUIT Bedrock judge...")
    result = evaluate(ds, metrics=metrics, llm=llm_judge, embeddings=emb)
    df = result.to_pandas()
    print(df)

    metric_cols = [m.name for m in metrics]
    failed = []
    for col in metric_cols:
        if col not in df.columns:
            failed.append(f"missing column: {col}")
            continue
        for idx, val in df[col].items():
            if val is None or (isinstance(val, float) and math.isnan(val)):
                failed.append(f"{col}[{idx}] is NaN/None")

    if failed:
        print("FAIL — broken or missing scores:", failed, file=sys.stderr)
        return 1

    print("PASS — all 4 metrics produced finite floats for all 3 questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
