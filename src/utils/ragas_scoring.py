"""RAGAS scoring helpers extracted from service_benchmark (design D2, D4).

The parent design of record is openspec/changes/archive/2026-10-23-offload-ragas-judge-to-slurm/.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from langchain_openai import ChatOpenAI

from src.archi.providers.local_provider import normalize_base_url
from src.utils.benchmark_schema import (
    RAGAS_METRIC_NAMES,
    build_ragas_metric_objects,
    ragas_result_column,
    ragas_run_config_kwargs,
    score_metrics_per_eligibility,
)
from src.utils.judge_identity import describe_decoding, describe_embedding
from src.utils.llm_usage import UsageRecorder


@dataclass
class RagasScoreOutcome:
    aggregates: Dict[str, Any] = field(default_factory=dict)
    scored: bool = False
    usage: Optional[Dict[str, Any]] = None
    # What the built judge and embedding objects report (D10), or None when
    # the judge was never built.
    decoding: Optional[Dict[str, Any]] = None
    embedding: Optional[Dict[str, Any]] = None


@dataclass
class RagasScorer:
    """A built judge: ``score_fn(metric, rows)`` returns one score per row."""

    metrics: List[str]
    score_fn: Callable[[str, List[Dict[str, Any]]], List[Any]]
    recorder: UsageRecorder
    decoding: Dict[str, Any]
    embedding: Dict[str, Any]


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
    model_name,
    socket_path,
    *,
    api_key_env="VLLM_API_KEY",
    chat_openai=ChatOpenAI,
    timeout=300,
    max_retries=2,
):
    """Build a RAGAS judge LLM whose HTTP transport connects to a Unix domain socket.

    Reads the key from the api_key_env variable; raises ValueError naming the
    variable if it is absent or empty, and makes no request in that case (design D6).

    The defaults are the production values (#739): 300 seconds per request, the
    timeout of every inline judge run, and 2 retries of the openai client. Unit
    tests pass their own, shorter bound.
    """
    import os

    import httpx

    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ValueError(f"{api_key_env} environment variable is not set or empty")

    transport = httpx.HTTPTransport(uds=socket_path)
    http_client = httpx.Client(transport=transport, timeout=timeout)
    # RAGAS calls the judge with ainvoke, which uses only the async client. Without
    # it, the async client connects over TCP to the localhost placeholder in base_url.
    async_transport = httpx.AsyncHTTPTransport(uds=socket_path)
    http_async_client = httpx.AsyncClient(transport=async_transport, timeout=timeout)

    # ChatOpenAI gives the openai client its own request timeout (None when unset),
    # which outranks the timeout of the httpx clients, so pass it here as well.
    return chat_openai(
        model=model_name,
        api_key=api_key,
        base_url="http://localhost/v1",
        http_client=http_client,
        http_async_client=http_async_client,
        timeout=timeout,
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


def build_ragas_scorer(
    *,
    enabled_metrics,
    ragas_settings,
    verbosity,
    judge_factory,
    embeddings_factory,
    judge_provider,
    judge_model,
    on_call=None,
) -> RagasScorer:
    """Build the judge, the embeddings and the ragas call settings once.

    The inline path (``score_ragas``) and the offline scorer (#732) both score
    through the returned ``score_fn``, so one ragas call serves both. Keeps lazy
    ragas imports, and builds the judge and embeddings eagerly. ``on_call`` runs
    before each ragas call.
    """
    from ragas import EvaluationDataset, RunConfig, evaluate

    ragas_metrics = importlib.import_module("ragas.metrics")
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    metrics = [name for name in RAGAS_METRIC_NAMES if name in enabled_metrics]
    all_metrics = build_ragas_metric_objects(ragas_metrics, metrics)

    batch_size = ragas_settings["batch_size"] or None
    runconfig = RunConfig(**ragas_run_config_kwargs(ragas_settings, verbosity))
    judge = judge_factory()
    llm = LangchainLLMWrapper(judge)
    embedder = embeddings_factory()
    embeddings = LangchainEmbeddingsWrapper(embedder)
    recorder = UsageRecorder(judge_provider, judge_model or "")

    def score_fn(metric, eligible_rows):
        if on_call is not None:
            on_call()
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

    return RagasScorer(
        metrics=metrics,
        score_fn=score_fn,
        recorder=recorder,
        decoding=describe_decoding(judge),
        embedding=describe_embedding(embedder),
    )


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

    Builds the judge and embeddings eagerly before checking row eligibility,
    matching today's order in service_benchmark.
    """

    def mark_scored():
        outcome.scored = True

    scorer = build_ragas_scorer(
        enabled_metrics=enabled_metrics,
        ragas_settings=ragas_settings,
        verbosity=verbosity,
        judge_factory=judge_factory,
        embeddings_factory=embeddings_factory,
        judge_provider=judge_provider,
        judge_model=judge_model,
        on_call=mark_scored,
    )
    outcome.decoding = scorer.decoding
    outcome.embedding = scorer.embedding
    try:
        outcome.aggregates = score_metrics_per_eligibility(
            rows, keys, scorer.metrics, results_by_key, scorer.score_fn
        )
    finally:
        outcome.usage = scorer.recorder.snapshot()
