"""RAGAS scoring helpers extracted from service_benchmark (design D2, D4).

The parent design of record is openspec/changes/offload-ragas-judge-to-slurm/.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.archi.providers.local_provider import normalize_base_url
from src.utils.benchmark_schema import (
    RAGAS_METRIC_NAMES,
    build_ragas_metric_objects,
    ragas_result_column,
    ragas_run_config_kwargs,
    score_metrics_per_eligibility,
)
from src.utils.llm_usage import UsageRecorder


@dataclass
class RagasScoreOutcome:
    aggregates: Dict[str, Any] = field(default_factory=dict)
    scored: bool = False
    usage: Optional[Dict[str, Any]] = None


def build_ragas_judge_llm(identity, *, chat_openai, get_model):
    """Build the ragas judge LLM from an effective identity dict.

    Takes constructors as keyword arguments so the service_benchmark patch
    points still apply when called from the thin wrapper.
    """
    provider_key = identity["provider"]
    model_name = identity["model"]

    match provider_key:
        case "openai":
            return chat_openai(model=model_name)
        case "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=model_name,
                base_url=identity["base_url"],
                num_predict=-2,
                model_kwargs={"format": "json"},
            )
        case "local":
            if identity["mode"] == "openai_compat":
                override = (
                    {"base_url": normalize_base_url(identity["base_url"])}
                    if identity["base_url"]
                    else {}
                )
                return get_model(
                    "local",
                    model_name,
                    {"base_url": identity["base_url"], "mode": "openai_compat"},
                    **override,
                )
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=model_name,
                base_url=identity["base_url"],
                num_predict=-2,
                model_kwargs={"format": "json"},
            )
        case "huggingface":
            return get_model(
                "local",
                model_name,
                {"base_url": identity["base_url"], "mode": "openai_compat"},
                base_url=normalize_base_url(identity["base_url"]),
            )
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=model_name)
        case "huit_bedrock":
            return get_model(
                "huit_bedrock", model_name, {"base_url": identity["base_url"]}
            )
        case _:
            return chat_openai(model=model_name)


def build_uds_judge_llm(
    socket_path, model_name, *, chat_openai, timeout=10, max_retries=0
):
    """Build a RAGAS judge LLM whose HTTP transport connects to a Unix domain socket.

    Reads VLLM_API_KEY from the environment; raises ValueError naming the variable
    if it is absent or empty, and makes no request in that case (design D6).
    """
    import os

    import httpx

    api_key = os.environ.get("VLLM_API_KEY", "")
    if not api_key:
        raise ValueError("VLLM_API_KEY environment variable is not set or empty")

    transport = httpx.HTTPTransport(uds=socket_path)
    http_client = httpx.Client(transport=transport, timeout=timeout)

    return chat_openai(
        model=model_name,
        api_key=api_key,
        base_url="http://localhost/v1",
        http_client=http_client,
        max_retries=max_retries,
    )


def build_ragas_embeddings(
    embedding_model, *, openai_embeddings, huggingface_embeddings
):
    """Build the ragas embeddings model.

    Takes constructors as keyword arguments so the service_benchmark patch
    points still apply when called from the thin wrapper.
    """
    match embedding_model.lower():
        case "openai":
            return openai_embeddings()
        case "huggingface":
            return huggingface_embeddings()
        case _:
            return openai_embeddings()


def score_ragas(
    rows,
    keys,
    results_by_key,
    *,
    enabled_metrics,
    ragas_settings,
    verbosity,
    judge_factory,
    embeddings_factory,
    judge_provider,
    judge_model,
    outcome,
):
    """Score RAGAS metrics, writing per-row results into results_by_key and outcome.

    Keeps lazy ragas imports and builds the judge and embeddings eagerly before
    checking row eligibility, matching today's order in service_benchmark.
    """
    from ragas import EvaluationDataset, RunConfig, evaluate

    ragas_metrics = importlib.import_module("ragas.metrics")
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    metrics = [name for name in RAGAS_METRIC_NAMES if name in enabled_metrics]
    all_metrics = build_ragas_metric_objects(ragas_metrics, metrics)

    batch_size = ragas_settings["batch_size"] or None
    runconfig = RunConfig(**ragas_run_config_kwargs(ragas_settings, verbosity))
    llm = LangchainLLMWrapper(judge_factory())
    embeddings = LangchainEmbeddingsWrapper(embeddings_factory())
    recorder = UsageRecorder(judge_provider, judge_model or "")

    def score_fn(metric, eligible_rows):
        outcome.scored = True
        dataset = EvaluationDataset.from_list(eligible_rows)
        evaluation = evaluate(
            dataset,
            metrics=[all_metrics[metric]],
            llm=llm,
            embeddings=embeddings,
            run_config=runconfig,
            batch_size=batch_size,
            callbacks=[recorder],
        )
        column = ragas_result_column(all_metrics[metric])
        return evaluation.to_pandas()[column].tolist()

    try:
        outcome.aggregates = score_metrics_per_eligibility(
            rows, keys, metrics, results_by_key, score_fn
        )
    finally:
        outcome.usage = recorder.snapshot()
