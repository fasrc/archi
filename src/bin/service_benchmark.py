import json
import math
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as url_error
from urllib import request as url_request

import pandas as pd
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import AgentSpecError, load_agent_spec
from src.archi.providers import get_model
from src.utils.env import read_secret
from src.utils.generate_benchmark_report import (
    format_html_output,
    parse_benchmark_results,
)
from src.utils.logging import get_logger, setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory

# NOTE: `datasets` and `ragas` are heavy, benchmark-only deps that live in the
# benchmarking Docker image but NOT the lean unit-test environment. They are
# imported lazily inside the methods that use them (get_ragas_results, run)
# so that importing this module for its pure helpers (e.g. ResultHandler.
# build_leaderboard / dump, exercised by unit tests) does not require them.


CONFIG_PATH = "/root/archi/config.yaml"
OUTPUT_PATH = "/root/archi/benchmarks"
EXTRA_METADATA_PATH = "/root/archi/git_info.yaml"
OUTPUT_DIR = Path(OUTPUT_PATH)

setup_logging()
logger = get_logger(__name__)


def _init_runtime() -> None:
    """Load secrets into the environment and open the Postgres connection pool.

    Called only when this module is run as a script (see __main__), NOT at import
    time — importing the module for its pure helpers (e.g. ResultHandler.
    build_leaderboard, exercised by unit tests) must not require live secrets or
    a reachable database.
    """
    os.environ["OPENAI_API_KEY"] = read_secret("OPENAI_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = read_secret("ANTHROPIC_API_KEY")
    os.environ["HUGGING_FACE_HUB_TOKEN"] = read_secret("HUGGING_FACE_HUB_TOKEN")
    os.environ["HUIT_API_KEY"] = read_secret("HUIT_API_KEY")

    factory = PostgresServiceFactory.from_env(
        password_override=os.environ.get("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)


@dataclass
class ABResult:
    """Paired A/B comparison result for a single question."""

    question: str
    reference_answer: str
    answer_a: str
    answer_b: str
    time_a: float
    time_b: float
    ragas_a: Dict[str, float] = field(default_factory=dict)
    ragas_b: Dict[str, float] = field(default_factory=dict)
    sources_a: List[Dict[str, Any]] = field(default_factory=list)
    sources_b: List[Dict[str, Any]] = field(default_factory=list)
    messages_a: List[Dict[str, Any]] = field(default_factory=list)
    messages_b: List[Dict[str, Any]] = field(default_factory=list)
    winner_by_metric: Dict[str, str] = field(default_factory=dict)
    llm_judge_a: Dict[str, Any] = field(default_factory=dict)
    llm_judge_b: Dict[str, Any] = field(default_factory=dict)
    llm_judge_pairwise: Dict[str, Any] = field(default_factory=dict)


class ResultHandler:
    results = []  # store the results for each config
    metadata = {}  # store the metadata about the benchmark run
    ab_comparison: Dict[str, Any] = (
        {}
    )  # single-pair compat (populated only in ab_mode with 2 configs)
    ab_comparisons: List[Dict[str, Any]] = (
        []
    )  # multi-pair: list of pair comparison dicts
    leaderboard: Dict[str, Any] = (
        {}
    )  # prompt-sweep leaderboard (populated only when 2+ configs run)
    # Per-invocation identifier shared by every config in this archi-evaluate run.
    # Stamped onto Argilla records as metadata so the analysis notebook can refuse
    # to compute primary-outcome statistics across configs that were NOT run
    # together (different invocations -> different snapshot ids -> different
    # corpus state). Spec: argilla-benchmark-grading "Sweep guarantees same corpus".
    # Initialized lazily on first read or in add_metadata, whichever comes first.
    _corpus_snapshot_id: Optional[str] = None

    @staticmethod
    def get_corpus_snapshot_id() -> str:
        """Return the per-invocation corpus snapshot id, generating it once on first access."""
        if ResultHandler._corpus_snapshot_id is None:
            # Respect an override so re-runs or smoke tests can pin the id.
            override = os.environ.get("ARCHI_CORPUS_SNAPSHOT_ID")
            ResultHandler._corpus_snapshot_id = override or str(uuid.uuid4())
        return ResultHandler._corpus_snapshot_id

    @staticmethod
    def map_prompts(config: Dict[str, Any]):
        prompts = config.get("services", {}).get("benchmarking", {}).get("prompts")
        if not isinstance(prompts, dict):
            return
        for _, section in prompts.items():
            if not isinstance(section, dict):
                continue
            for prompt_name, file_path in section.items():
                if not file_path:
                    continue
                path = Path(file_path)
                if not path.exists():
                    continue
                with open(path, "r") as f:
                    prompt_str = f.read()
                section[prompt_name] = prompt_str

    @staticmethod
    def handle_results(config_path: Path, results: Dict, total_results: Dict):
        with open(config_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

        ResultHandler.map_prompts(config)

        current_results = {
            "single_question_results": results,
            "total_results": total_results,
            "configuration_file": str(config_path),
            "configuration": config,
        }

        ResultHandler.results.append(current_results)

    @staticmethod
    def add_metadata():
        with open(EXTRA_METADATA_PATH, "r") as f:
            additional_info = yaml.safe_load(f)

        meta_data = {
            "time": str(datetime.now(timezone.utc)),
            "git_info": additional_info,
            "corpus_snapshot_id": ResultHandler.get_corpus_snapshot_id(),
        }

        ResultHandler.metadata.update(meta_data)

    @staticmethod
    def dump_html(benchmark_name: Path):

        config_data, config_name, timestamp, questions, total_results = (
            parse_benchmark_results(ResultHandler.results, ResultHandler.metadata)
        )

        logger.info(config_data)

        html_content = format_html_output(
            config_data, config_name, timestamp, questions, total_results
        )

        filename = f"{benchmark_name}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_report.html"
        file_path = OUTPUT_DIR / filename

        logger.info(f"Dumping results to {file_path}")

        with open(file_path, "w") as f:
            f.write(html_content)

        logger.info(f"✅ HTML report generated: {file_path}")

    @staticmethod
    def dump(benchmark_name: Path):
        filename = f"{benchmark_name}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        file_path = OUTPUT_DIR / filename
        logger.info(f"Dumping results to {file_path}")
        logger.debug(f"Full results: {ResultHandler.results}")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output: Dict[str, Any] = {
            "benchmarking_results": ResultHandler.results,
            "metadata": ResultHandler.metadata,
        }
        if ResultHandler.ab_comparison:
            output["ab_comparison"] = ResultHandler.ab_comparison
        if ResultHandler.ab_comparisons:
            output["ab_comparisons"] = ResultHandler.ab_comparisons
        if ResultHandler.leaderboard:
            output["leaderboard"] = ResultHandler.leaderboard
        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)

    @staticmethod
    def pair_ab_results(idx_a: int = 0, idx_b: int = 1) -> List[ABResult]:
        """Pair results from two benchmark configs into ABResult objects."""
        if idx_a >= len(ResultHandler.results) or idx_b >= len(ResultHandler.results):
            raise ValueError(
                f"Result indices ({idx_a}, {idx_b}) out of range for {len(ResultHandler.results)} results"
            )

        results_a = ResultHandler.results[idx_a]["single_question_results"]
        results_b = ResultHandler.results[idx_b]["single_question_results"]

        ragas_metrics = [
            "answer_relevancy",
            "faithfulness",
            "context_precision",
            "context_recall",
        ]

        paired: List[ABResult] = []
        all_keys = list(results_a.keys()) + [k for k in results_b if k not in results_a]
        for key in all_keys:
            if key not in results_a:
                logger.warning(
                    "Question key %s not found in config A results, skipping.", key
                )
                continue
            if key not in results_b:
                logger.warning(
                    "Question key %s not found in config B results, skipping.", key
                )
                continue
            qa = results_a[key]
            qb = results_b[key]

            ragas_a = {m: qa.get(m, float("nan")) for m in ragas_metrics if m in qa}
            ragas_b = {m: qb.get(m, float("nan")) for m in ragas_metrics if m in qb}

            winner_by_metric: Dict[str, str] = {}
            for m in ragas_a:
                sa, sb = ragas_a.get(m, float("nan")), ragas_b.get(m, float("nan"))
                if math.isnan(sa) or math.isnan(sb):
                    winner_by_metric[m] = "tie"
                elif abs(sa - sb) < 1e-9:
                    winner_by_metric[m] = "tie"
                elif sa > sb:
                    winner_by_metric[m] = "a"
                else:
                    winner_by_metric[m] = "b"

            paired.append(
                ABResult(
                    question=qa["question"],
                    reference_answer=qa.get("reference_answer", ""),
                    answer_a=qa.get("answer", ""),
                    answer_b=qb.get("answer", ""),
                    time_a=qa.get("time_elapsed", 0.0),
                    time_b=qb.get("time_elapsed", 0.0),
                    ragas_a=ragas_a,
                    ragas_b=ragas_b,
                    sources_a=qa.get("sources_metadata", []),
                    sources_b=qb.get("sources_metadata", []),
                    messages_a=qa.get("messages", []),
                    messages_b=qb.get("messages", []),
                    winner_by_metric=winner_by_metric,
                    llm_judge_a={
                        k.replace("llm_judge_", ""): v
                        for k, v in qa.items()
                        if k.startswith("llm_judge_")
                    },
                    llm_judge_b={
                        k.replace("llm_judge_", ""): v
                        for k, v in qb.items()
                        if k.startswith("llm_judge_")
                    },
                )
            )

        return paired

    @staticmethod
    def dump_ab_comparison(paired: List[ABResult], idx_a: int = 0, idx_b: int = 1):
        """Build an ab_comparison section from paired results.

        When called with default indices (0, 1), also sets ab_comparison
        for backward compatibility.
        """
        config_a = ResultHandler.results[idx_a].get("configuration", {})
        config_b = ResultHandler.results[idx_b].get("configuration", {})
        bench_a = config_a.get("services", {}).get("benchmarking", {})
        bench_b = config_b.get("services", {}).get("benchmarking", {})

        config_a_meta = {
            "name": bench_a.get("name", f"config_{idx_a}"),
            "agent_class": bench_a.get("agent_class", ""),
            "model": bench_a.get("model", ""),
            "provider": bench_a.get("provider", ""),
            "config_file": ResultHandler.results[idx_a].get("configuration_file", ""),
        }
        config_b_meta = {
            "name": bench_b.get("name", f"config_{idx_b}"),
            "agent_class": bench_b.get("agent_class", ""),
            "model": bench_b.get("model", ""),
            "provider": bench_b.get("provider", ""),
            "config_file": ResultHandler.results[idx_b].get("configuration_file", ""),
        }

        per_question = [asdict(r) for r in paired]

        wins_a, wins_b, ties = 0, 0, 0
        all_metrics = set()
        for r in paired:
            for m, w in r.winner_by_metric.items():
                all_metrics.add(m)
                if w == "a":
                    wins_a += 1
                elif w == "b":
                    wins_b += 1
                else:
                    ties += 1

        mean_scores_a: Dict[str, float] = {}
        mean_scores_b: Dict[str, float] = {}
        for m in all_metrics:
            vals_a = [
                r.ragas_a[m]
                for r in paired
                if r.ragas_a.get(m) is not None
                and not math.isnan(r.ragas_a.get(m, float("nan")))
            ]
            vals_b = [
                r.ragas_b[m]
                for r in paired
                if r.ragas_b.get(m) is not None
                and not math.isnan(r.ragas_b.get(m, float("nan")))
            ]
            mean_scores_a[m] = sum(vals_a) / len(vals_a) if vals_a else 0.0
            mean_scores_b[m] = sum(vals_b) / len(vals_b) if vals_b else 0.0

        comparison = {
            "config_a": config_a_meta,
            "config_b": config_b_meta,
            "per_question": per_question,
            "aggregate": {
                "wins_a": wins_a,
                "wins_b": wins_b,
                "ties": ties,
                "mean_scores_a": mean_scores_a,
                "mean_scores_b": mean_scores_b,
            },
        }

        ResultHandler.ab_comparisons.append(comparison)

        if idx_a == 0 and idx_b == 1:
            ResultHandler.ab_comparison = comparison

    @staticmethod
    def generate_pairwise_combinations(n_configs: int) -> List[Tuple[int, int]]:
        """Generate all pairwise index combinations for N configs."""
        return list(combinations(range(n_configs), 2))

    # Leaderboard metric name -> the aggregate key the run loop writes onto
    # total_results (service_benchmark.py RAGAS block). Order is display order.
    LEADERBOARD_METRICS: List[Tuple[str, str]] = [
        ("answer_relevancy", "aggregate_answer_relevancy"),
        ("faithfulness", "aggregate_faithfulness"),
        ("context_precision", "aggregate_context_precision"),
        ("context_recall", "aggregate_context_recall"),
    ]

    @staticmethod
    def build_leaderboard(primary_metric: str = "faithfulness") -> Dict[str, Any]:
        """Rank swept prompt variants by mean RAGAS metric.

        Reads each config's per-run aggregates from ResultHandler.results
        (the means the RAGAS block already wrote onto total_results) and
        builds a ranked leaderboard. Independent of the pairwise A/B plumbing:
        it never touches pair_ab_results/ab_comparisons.

        Each row: {name, agent_md_file, metrics{...}, primary_score, rank,
        incomplete, query_count, scored_counts{...}}. A metric is None (and the
        row `incomplete`) when its aggregate key is absent or NaN — never
        silently zeroed. Incomplete rows always sort after complete ones. Ties
        share a rank. `query_count` is the number of questions answered;
        `scored_counts[metric]` is how many non-NaN per-question scores actually
        backed that metric's mean (a judge timeout shrinks the sample without
        making the aggregate NaN).

        shared_context records the run context common to all variants and
        flags any drift (a hand-edited config that breaks apples-to-apples).
        """
        metric_names = [name for name, _ in ResultHandler.LEADERBOARD_METRICS]
        if primary_metric not in metric_names:
            logger.warning(
                "Leaderboard primary_metric '%s' is not a known RAGAS metric %s; "
                "falling back to 'faithfulness'.",
                primary_metric,
                metric_names,
            )
            primary_metric = "faithfulness"

        def _benchmarking(record: Dict[str, Any]) -> Dict[str, Any]:
            return (
                record.get("configuration", {})
                .get("services", {})
                .get("benchmarking", {})
            )

        rows: List[Dict[str, Any]] = []
        # Accumulate shared-context candidates to detect drift across configs.
        ctx_fields: Dict[str, set] = {
            "model": set(),
            "provider": set(),
            "evaluator_model": set(),
            "queries_path": set(),
        }

        for record in ResultHandler.results:
            bench = _benchmarking(record)
            total = record.get("total_results", {}) or {}

            agent_md_file = bench.get("agent_md_file", "") or ""
            name = bench.get("name") or (
                Path(agent_md_file).stem if agent_md_file else ""
            )

            metrics: Dict[str, Optional[float]] = {}
            incomplete = False
            for metric_name, agg_key in ResultHandler.LEADERBOARD_METRICS:
                value = total.get(agg_key)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    metrics[metric_name] = None
                    incomplete = True
                else:
                    metrics[metric_name] = float(value)

            # Per-metric sample size actually behind each mean. The RAGAS block
            # computes aggregate_* via pandas .mean(), which skips NaN, so a
            # judge timeout on one question silently shrinks the sample for that
            # metric without making the aggregate NaN. Count the non-NaN
            # per-question scores so the leaderboard can show, e.g., a
            # faithfulness mean taken over 4 of 9 answered questions instead of
            # implying all 9 backed it. query_count is the answered count.
            single_question_results = record.get("single_question_results") or {}
            scored_counts: Dict[str, int] = {}
            for metric_name, _agg_key in ResultHandler.LEADERBOARD_METRICS:
                count = 0
                for q in single_question_results.values():
                    if not isinstance(q, dict):
                        continue
                    v = q.get(metric_name)
                    if v is not None and not (isinstance(v, float) and math.isnan(v)):
                        count += 1
                scored_counts[metric_name] = count

            if incomplete:
                logger.warning(
                    "Leaderboard: variant '%s' (%s) is incomplete — missing/NaN metrics: %s",
                    name,
                    agent_md_file,
                    [m for m in metric_names if metrics[m] is None],
                )
            # Surface under-sampling even when the aggregate is a valid float.
            answered = len(single_question_results)
            undersampled = [
                f"{m}={scored_counts[m]}/{answered}"
                for m in metric_names
                if metrics[m] is not None and scored_counts[m] < answered
            ]
            if undersampled:
                logger.warning(
                    "Leaderboard: variant '%s' (%s) has under-sampled metrics "
                    "(mean over fewer than %d answered questions): %s",
                    name,
                    agent_md_file,
                    answered,
                    undersampled,
                )

            rows.append(
                {
                    "name": name,
                    "agent_md_file": agent_md_file,
                    "metrics": metrics,
                    "primary_score": metrics[primary_metric],
                    "incomplete": incomplete,
                    "query_count": answered,
                    "scored_counts": scored_counts,
                }
            )

            ragas_settings = (bench.get("mode_settings", {}) or {}).get(
                "ragas_settings", {}
            ) or {}
            ctx_fields["model"].add(bench.get("model"))
            ctx_fields["provider"].add(bench.get("provider"))
            ctx_fields["evaluator_model"].add(ragas_settings.get("evaluator_model"))
            ctx_fields["queries_path"].add(bench.get("queries_path"))

        # Complete rows first, then by descending primary score; incomplete last.
        rows.sort(
            key=lambda r: (
                1 if r["incomplete"] else 0,
                -(r["primary_score"] if r["primary_score"] is not None else 0.0),
            )
        )

        # Dense ranking: equal primary scores share a rank.
        rank = 0
        prev_score: Any = object()
        for row in rows:
            score = row["primary_score"]
            if score != prev_score:
                rank += 1
                prev_score = score
            row["rank"] = rank

        warnings: List[str] = []
        shared_context: Dict[str, Any] = {
            "corpus_snapshot_id": ResultHandler.get_corpus_snapshot_id(),
        }
        for field_name, values in ctx_fields.items():
            present = {v for v in values if v is not None}
            if len(present) <= 1:
                shared_context[field_name] = next(iter(present), None)
            else:
                shared_context[field_name] = sorted(str(v) for v in present)
                warnings.append(
                    f"{field_name} differs across swept configs: {sorted(str(v) for v in present)}"
                )
        if warnings:
            for w in warnings:
                logger.warning("Leaderboard shared-context drift: %s", w)
        shared_context["warnings"] = warnings

        ResultHandler.leaderboard = {
            "shared_context": shared_context,
            "primary_metric": primary_metric,
            "rows": rows,
        }
        return ResultHandler.leaderboard


class Benchmarker:

    def __init__(self, configs: Path, q_to_a: dict[str, str]):
        self.queries_to_answers = q_to_a
        self.required_fields = ["question"]
        self.benchmark_name = os.environ["container_name"]
        self.all_config_files = self.get_all_configs(configs)
        self.all_config_files.append("FINISHED")
        self.previous_input_list = []
        self.chain = None
        self.config = None
        self.current_config = None

        self.load_new_configuration()
        self.data_path = self.config["global"]["DATA_PATH"]

    def get_all_configs(self, configs_dir):
        all_paths = []
        for root, _, filenames in os.walk(configs_dir):
            for file in filenames:
                full_path = os.path.join(root, file)
                all_paths.append(full_path)
        return all_paths

    def load_new_configuration(self):
        self.current_config = self.all_config_files.pop(0)
        if self.current_config == "FINISHED":
            return
        with open(self.current_config, "r") as f:
            config = yaml.safe_load(f)

        with open(CONFIG_PATH, "w") as f:
            yaml.dump(config, stream=f)

        del self.chain
        self.config = config
        self.benchmarking_configs = config["services"]["benchmarking"]
        if "SOURCES" in self.benchmarking_configs:
            self.required_fields += ["sources"]
        elif "RAGAS" in self.benchmarking_configs:
            self.required_fields += ["answer"]

        # for now it only uses one pipeline (the first one) but maybe later we make this work for mulitple
        logger.info(f"loaded new configuration: {self.current_config}")
        benchmark_cfg = (
            config.get("services", {}).get("benchmarking", {})
            if isinstance(config, dict)
            else {}
        )
        pipeline = benchmark_cfg.get("agent_class")
        provider = benchmark_cfg.get("provider")
        model = benchmark_cfg.get("model")
        agent_md_file = benchmark_cfg.get("agent_md_file")
        ollama_url = benchmark_cfg.get("ollama_url")
        missing = [
            k
            for k, v in {
                "agent_class": pipeline,
                "provider": provider,
                "model": model,
                "agent_md_file": agent_md_file,
            }.items()
            if not v
        ]
        if missing:
            raise ValueError(
                f"Missing required benchmarking runtime fields in services.benchmarking: {', '.join(missing)}"
            )
        if str(provider).lower() == "local" and not ollama_url:
            raise ValueError(
                "Missing required benchmarking runtime field in services.benchmarking: ollama_url (required when provider is local)"
            )
        if ollama_url:
            os.environ["OLLAMA_HOST"] = str(ollama_url)

        agent_spec = None
        try:
            agent_spec = load_agent_spec(Path(str(agent_md_file)))
        except AgentSpecError as exc:
            raise ValueError(
                f"Failed to load benchmark agent spec '{agent_md_file}': {exc}"
            ) from exc

        self._chain_kwargs = dict(
            pipeline=pipeline,
            agent_spec=agent_spec,
            default_provider=provider,
            default_model=model,
            prompt_overrides={},
        )
        self.chain = archi(
            pipeline,
            agent_spec=agent_spec,
            default_provider=provider,
            default_model=model,
            prompt_overrides={},
        )

    # Phase 1 audit (2026-06-01): archi() is NOT safe for parallel instantiation
    # due to three shared-global-state blockers:
    #   1. AsyncLoopThread MCP singleton at src/utils/mcp_utils.py:20
    #   2. PostgresServiceFactory.set_instance at src/utils/postgres_service_factory.py:169
    #   3. HuggingFaceEmbeddings singleton at src/data_manager/vectorstore_connector.py:33
    # Until those are fixed, the parallel chain pool MUST be invoked with
    # n_workers=1. The guard below is intentional — callers can lift it after a
    # follow-up "thread-safe archi" change resolves the three blockers.
    _PARALLEL_SAFE_MAX_WORKERS = 1

    def _create_chain_pool(self, n_workers: int) -> list:
        """Create a pool of independent chain instances for parallel execution."""
        if n_workers > self._PARALLEL_SAFE_MAX_WORKERS:
            raise RuntimeError(
                f"archi() is not thread-safe yet (Phase 1 audit identified 3 shared-state blockers); "
                f"n_workers={n_workers} would risk data corruption. Set n_workers=1 until blockers are fixed."
            )
        chains = [self.chain]
        kw = self._chain_kwargs
        for _ in range(n_workers - 1):
            chains.append(
                archi(
                    kw["pipeline"],
                    agent_spec=kw["agent_spec"],
                    default_provider=kw["default_provider"],
                    default_model=kw["default_model"],
                    prompt_overrides=kw["prompt_overrides"],
                )
            )
        logger.info(
            "Created pool of %d chain instances for parallel execution.", n_workers
        )
        return chains

    def _prefetch_questions_parallel(
        self,
        n_workers,
        config_num,
        total_configs,
        total_questions,
        run_start,
    ):
        """Run all questions in parallel using a pool of independent chain instances.

        Returns a dict mapping 1-based question_id to (result, elapsed_seconds).
        """
        if n_workers > self._PARALLEL_SAFE_MAX_WORKERS:
            raise RuntimeError(
                f"_prefetch_questions_parallel called with n_workers={n_workers}; "
                f"only n_workers=1 is safe today (see _create_chain_pool comment)."
            )
        chains = self._create_chain_pool(n_workers)
        logger.info(
            "Prefetching %d questions with %d parallel workers...",
            total_questions,
            n_workers,
        )

        def _ask(chain, question_id, question_text):
            formatted = [("User", question_text)]
            start = time.perf_counter()
            result = chain(history=formatted)
            elapsed = time.perf_counter() - start
            logger.info(
                "[Config %d/%d] Question %d/%d finished (%.2fs)",
                config_num,
                total_configs,
                question_id,
                total_questions,
                elapsed,
            )
            return question_id, result, elapsed

        results = {}
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {}
            for idx, question_item in enumerate(self.queries_to_answers):
                if type(question_item) is not dict:
                    continue
                if not all(f in question_item for f in self.required_fields):
                    continue
                qid = idx + 1
                chain = chains[idx % n_workers]
                future = executor.submit(_ask, chain, qid, question_item["question"])
                futures[future] = qid

            for future in as_completed(futures):
                try:
                    qid, result, elapsed = future.result()
                    results[qid] = (result, elapsed)
                except Exception:
                    qid = futures[future]
                    logger.exception("Question %d failed in parallel execution", qid)

        wall_elapsed = time.perf_counter() - run_start
        mins, secs = divmod(int(wall_elapsed), 60)
        logger.info(
            "Parallel prefetch complete: %d/%d questions in %dm%02ds wall time.",
            len(results),
            total_questions,
            mins,
            secs,
        )
        return results

    def get_ragas_llm_evaluator(self):
        ragas_configs = self.config["services"]["benchmarking"]["mode_settings"][
            "ragas_settings"
        ]
        benchmark_cfg = self.config.get("services", {}).get("benchmarking", {})
        # Judge/SUT config split: when ragas_settings.evaluator_* is set, the RAGAS judge
        # uses an independent model from the system under test. Falls back to the SUT
        # provider/model when the evaluator_* keys are absent.
        provider = ragas_configs.get("evaluator_provider") or benchmark_cfg.get(
            "provider"
        )
        model_name = ragas_configs.get("evaluator_model") or benchmark_cfg.get("model")
        ollama_url = ragas_configs.get("evaluator_ollama_url") or benchmark_cfg.get(
            "ollama_url"
        )

        match str(provider).lower():
            case "openai":
                return ChatOpenAI(model=model_name)
            case "ollama":
                from langchain_ollama import ChatOllama

                base_url = ollama_url
                return ChatOllama(
                    model=model_name,
                    base_url=base_url,
                    num_predict=-2,
                    model_kwargs={"format": "json"},
                )
            case "local":
                from langchain_ollama import ChatOllama

                base_url = ollama_url
                return ChatOllama(
                    model=model_name,
                    base_url=base_url,
                    num_predict=-2,
                    model_kwargs={"format": "json"},
                )
            case "huggingface":
                base_url = ollama_url or "http://localhost:8000/v1"
                return get_model(
                    "local", model_name, base_url=base_url, local_mode="openai_compat"
                )
            case "anthropic":
                from langchain_anthropic import ChatAnthropic

                return ChatAnthropic(model=model_name)
            case "huit_bedrock":
                base_url = (
                    benchmark_cfg.get("base_url")
                    or "https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2"
                )
                return get_model("huit_bedrock", model_name, {"base_url": base_url})
            case _:
                return ChatOpenAI(model=model_name)

    def get_ragas_embedding_model(self):
        ragas_configs = self.config["services"]["benchmarking"]["mode_settings"][
            "ragas_settings"
        ]
        embedding_model = ragas_configs["embedding_model"]

        match embedding_model.lower():
            case "openai":
                return OpenAIEmbeddings()
            case "huggingface":
                return HuggingFaceEmbeddings()
            case _:
                return OpenAIEmbeddings()

    def prepare_match_fields(self, question_item):

        # either grab the match field(s) from the question item or use the default
        match_fields = question_item.get("source_match_field")
        if not match_fields:
            match_fields = self.benchmarking_configs["mode_settings"][
                "sources_settings"
            ]["default_match_field"]

        # make it to a list if it's passed as a string
        if isinstance(match_fields, str):
            match_fields = [match_fields] if match_fields else []

        n_sources = len(question_item.get("sources", []))
        if not match_fields:
            # hardcode a default if nothing is provided
            match_fields = ["file_name"] * n_sources
        elif len(match_fields) == 1 and n_sources > 1:
            # expand single field to all sources
            match_fields = match_fields * n_sources
        elif len(match_fields) != n_sources:
            logger.error(
                "Number of match fields (%s) does not align with number of reference sources (%s); reusing the last field for the remaining references.",
                len(match_fields),
                n_sources,
            )
            raise ValueError(
                "Mismatch between number of match fields and reference sources."
            )

        return match_fields

    def _resolve_reference_match_fields(
        self, question_item, reference_sources, modes_being_run
    ):
        """Reference source match fields, computed only when SOURCES mode runs.

        ``prepare_match_fields`` requires the per-question match-field count to
        equal the number of reference sources. RAGAS-only banks legitimately
        carry zero-source rows (e.g. ``should_refuse`` questions), so computing
        match fields for them would raise even though SOURCES scoring is off.
        Returning empty lists for non-SOURCES runs keeps such banks consumable.
        """
        if "SOURCES" not in modes_being_run:
            return [], []
        match_fields_list = self.prepare_match_fields(question_item)
        formatted = self.prepare_reference_sources(reference_sources, match_fields_list)
        return match_fields_list, formatted

    def prepare_reference_sources(self, reference_sources, match_fields):

        # Clean and prepare reference sources
        raw_references: List[str] = []
        if isinstance(reference_sources, str):
            cleaned = reference_sources.strip()
            if cleaned and cleaned != "N/A":
                raw_references = [reference_sources]
        elif isinstance(reference_sources, list):
            raw_references = [ref for ref in reference_sources if ref not in (None, "")]
        elif reference_sources is None:
            raw_references = []
        else:
            raw_references = [reference_sources]
        reference_sources_list: List[str] = []
        for ref in raw_references:
            ref_str = str(ref).strip()
            if ref_str and ref_str != "N/A":
                reference_sources_list.append(ref_str)

        formatted_reference_sources = []
        for field, reference in zip(match_fields, reference_sources_list):
            formatted_reference_sources.append({field: reference})

        return formatted_reference_sources

    def prepare_messages(self, raw_messages):
        """Format the langchain Messages into something we can store and view later."""
        formatted_messages = []
        for msg in raw_messages:
            if type(msg) is AIMessage:
                # there are two types of AI messages, content and tool calls
                # e.g. tool_calls=[{'name': 'search_vectorstore', 'args': {'query': 'CMSTRANSF-1078'}, 'id': '4a73724f-db40-41eb-9843-7f325df76f58', 'type': 'tool_call'}]
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        formatted_messages.append(
                            {
                                "type": "tool_call",
                                "tool_name": tool_call.get("name"),
                                "tool_args": tool_call.get("args", {}).get(
                                    "query", "No query found."
                                ),
                                "total_duration": getattr(
                                    msg, "response_metadata", {}
                                ).get("total_duration", None),
                            }
                        )
                elif hasattr(msg, "content"):
                    formatted_messages.append(
                        {
                            "type": "ai_message",
                            "content": msg.content,
                            "total_duration": getattr(msg, "response_metadata", {}).get(
                                "total_duration", None
                            ),
                        }
                    )
            elif type(msg) is HumanMessage:
                # we don't store these...
                pass
            elif type(msg) is ToolMessage:
                # we don't store these?
                logger.debug(msg)
                pass
            else:
                logger.warning(f"Unexpected message type: {type(msg)}")
        return formatted_messages

    def get_source_results(
        self,
        result: Dict,
        formatted_reference_sources: List[Dict[str, str]],
    ) -> List[bool]:
        """
        For each reference source, check the specified metadata field in the retrieved documents.
        The reference sources and match fields are paired one-to-one; a single string field is
        expanded to cover all provided sources. Returns summary information and whether all
        reference sources were found.
        """
        sources = result.get("source_documents", [])
        logger.info("Agent found %s sources.", len(sources))

        matches: List[bool] = []
        for source in formatted_reference_sources:
            field, reference = list(source.items())[0]
            logger.debug(
                "Checking for reference source '%s' in field '%s'", reference, field
            )
            for document in sources:
                metadata = getattr(document, "metadata", {}) or {}
                value = metadata.get(field)
                if value is None:
                    continue
                if isinstance(value, list):
                    values = [str(v).strip() for v in value if v is not None]
                else:
                    values = [str(value).strip()]
                logger.info("Returned source '%s': %s", field, values)
                logger.debug(
                    "Checking reference '%s' against document metadata field '%s': %s",
                    reference,
                    field,
                    values,
                )
                if reference in values:
                    logger.debug(
                        "Matched reference source '%s' in document metadata.", reference
                    )
                    matches.append(True)
                    break
            else:
                matches.append(False)

        # match is determined if at least once source is found
        logger.info("Source matching result: %s", matches)
        return matches

    def get_ragas_results(self, data, to_add):
        """WARNING: this method modifies the to_add dictionary to add the relevant scores to the relevant questions"""
        # Lazy import: ragas (and its transitive `datasets` dep) is benchmark-only
        # and absent from the unit-test environment. See the module-header note.
        from ragas import RunConfig, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        all_metrics_dict = {
            "answer_relevancy": answer_relevancy,
            "faithfulness": faithfulness,
            "context_precision": context_precision,
            "context_recall": context_recall,
        }

        enabled_metrics = self.benchmarking_configs["mode_settings"]["ragas_settings"][
            "enabled_metrics"
        ]

        metrics_dict = {
            k: v for k, v in all_metrics_dict.items() if k in enabled_metrics
        }

        res = pd.DataFrame()

        ragas_settings = self.config["services"]["benchmarking"]["mode_settings"][
            "ragas_settings"
        ]
        # The archi config-render pipeline can strip global.verbosity; tolerate
        # missing key (verbosity 4 enables tenacity retry logging in ragas).
        log_tenacity = self.config.get("global", {}).get("verbosity", 0) >= 4
        timeout = ragas_settings["timeout"]
        batch_settings = ragas_settings["batch_size"]
        if not batch_settings:
            batch_settings = None

        runconfig = RunConfig(timeout=timeout, log_tenacity=log_tenacity)
        # going one metric at a time prevents errors
        for metric_name, metric in metrics_dict.items():
            evaluation_results = evaluate(
                data,
                metrics=[metric],
                llm=LangchainLLMWrapper(self.get_ragas_llm_evaluator()),
                embeddings=LangchainEmbeddingsWrapper(self.get_ragas_embedding_model()),
                run_config=runconfig,
                batch_size=batch_settings,
            )

            metric_results = evaluation_results.to_pandas()
            res[metric_name] = metric_results[metric_name]

        for question_idx, question in enumerate(to_add.values()):
            for metric in metrics_dict.keys():
                question[metric] = res.at[question_idx, metric]

        return res

    def run(self):
        self.wait_for_ingestion_completion()

        modes_being_run = set(self.benchmarking_configs["modes"])

        # Merge anchor questions, if any. Anchors live in a separate JSON so
        # they can be versioned independently of the per-round query bank.
        # Each anchor carries an `anchor_type` ("easy_retrieve", "reasoning",
        # "should_refuse"); we propagate that into per-question results below
        # so the Argilla push and analysis notebook can surface it as
        # metadata only (graders see no "anchor" marker in any field).
        self._merge_anchor_questions()

        logger.info("")
        logger.info("====== Starting benchmark: %s ======", self.benchmark_name)
        logger.info("Modes being run: %s", modes_being_run)
        logger.info(
            f"Processing {len(self.queries_to_answers)} questions and {len(self.all_config_files)} configuration(s)."
        )
        logger.info("")

        while self.all_config_files:

            question_id = 0

            # results for each question
            question_wise_results = {}

            # results for all of the questions in this config
            total_results = {}

            # RAGAS mode: ragas inputs
            ragas_input = []

            # SOUCES mode: sources accuracy
            relative_source_accuracy = 0.0
            source_accuracy = 0.0

            for question_item in self.queries_to_answers:

                logger.info("")
                logger.info("====================================")
                logger.info(f"Answering question: {question_id + 1}")

                if type(question_item) is not dict:
                    logger.error(
                        f"Each item in the question to answer list must be a dictionary, but got {type(question_item)}"
                    )
                    continue
                if not all(field in question_item for field in self.required_fields):
                    logger.error(
                        f"Each item in the question to answer list must contain the following fields: {self.required_fields}, but got {question_item.keys()}"
                    )
                    continue

                question = question_item["question"]
                reference_answer = question_item.get("answer", "N/A")
                reference_sources = question_item.get("sources", "N/A")

                logger.info(f"Question: {question}")
                logger.info(f"Reference Answer: {reference_answer}")
                logger.info(f"Reference Sources: {reference_sources}")

                question_id += 1
                formatted_question = [("User", question)]
                start = time.perf_counter()
                result = self.chain(history=formatted_question)
                end = time.perf_counter()
                logger.info(
                    f"Finished answering question: {question_id} ({end - start:.2f}s)"
                )
                q_results = {}

                # prepare info to store for this question
                q_results["time_elapsed"] = end - start
                q_results["question"] = question
                q_results["reference_answer"] = reference_answer
                q_results["answer"] = result["answer"]

                # format the messages
                q_results["messages"] = self.prepare_messages(
                    result.get("messages", [])
                )

                # format the reference sources (only when SOURCES scoring runs;
                # RAGAS-only banks may carry zero-source refusal rows)
                match_fields_list, formatted_reference_sources = (
                    self._resolve_reference_match_fields(
                        question_item, reference_sources, modes_being_run
                    )
                )
                q_results["reference_sources_match_fields"] = match_fields_list
                q_results["reference_sources_metadata"] = formatted_reference_sources

                if "RAGAS" in modes_being_run:
                    # we collect the necessary info for ragas evaluation
                    # TODO this is likely broken now
                    contexts = [s.page_content for s in result["source_documents"]]
                    dataset_result = {
                        "question": question,
                        "contexts": contexts,
                        "answer": result["answer"],
                        "ground_truth": reference_answer,
                    }
                    ragas_input.append(dataset_result)

                if "SOURCES" in modes_being_run:
                    # sources evaluation is done on the fly -- check if each of the given sources was found
                    matches = self.get_source_results(
                        result,
                        formatted_reference_sources,
                    )
                    # we count accuracy via any of the sources matching
                    if any(matches):
                        relative_source_accuracy += 1.0
                    if len(matches) == len(formatted_reference_sources) and all(
                        matches
                    ):
                        source_accuracy += 1.0
                    # but we still store the match of each reference source in its metadata
                    for idx, source in enumerate(
                        q_results["reference_sources_metadata"]
                    ):
                        source["matched"] = matches[idx]
                    logger.info(
                        f"Current relative accuracy: {relative_source_accuracy / question_id if question_id > 0 else 0.0}"
                    )
                    logger.info(
                        f"Current strict accuracy: {source_accuracy / question_id if question_id > 0 else 0.0}"
                    )

                # store the sources metadata and truncated content
                sources_metadata: List[Dict[str, Any]] = []
                sources_trunc_content: List[str] = []
                for document in result["source_documents"]:
                    metadata = getattr(document, "metadata", {}) or {}
                    sources_metadata.append(metadata)
                    sources_trunc_content.append(
                        getattr(document, "page_content", "")[:300]
                    )  # first 300 chars
                q_results["sources_metadata"] = sources_metadata
                q_results["sources_trunc_content"] = sources_trunc_content
                # Forward the anchor marker so the Argilla push can stamp it
                # onto record metadata. Empty string means "not an anchor"
                # (Argilla TermsMetadataProperty accepts any string).
                q_results["anchor_type"] = (
                    question_item.get("anchor_type", "")
                    if isinstance(question_item, dict)
                    else ""
                )
                logger.debug("Sources returned: %s", sources_metadata)

                # store the results for this question
                question_wise_results[f"question_{question_id}"] = q_results

                logger.info("====================================")
                logger.info("")

            if "RAGAS" in modes_being_run:
                # TODO this is likely broken now
                logger.info(f"Starting to collect RAGAS results")
                from datasets import (
                    Dataset,  # lazy: benchmark-only dep (see module header)
                )

                data = Dataset.from_list(ragas_input)
                # were modifying final_addition here to add ragas results by question
                ragas_results = self.get_ragas_results(data, question_wise_results)

                answer_relevancy = ragas_results["answer_relevancy"].mean()
                faithfulness = ragas_results["faithfulness"].mean()
                context_precision = ragas_results["context_precision"].mean()
                context_recall = ragas_results["context_recall"].mean()

                total_results["aggregate_answer_relevancy"] = answer_relevancy
                total_results["aggregate_faithfulness"] = faithfulness
                total_results["aggregate_context_precision"] = context_precision
                total_results["aggregate_context_recall"] = context_recall

            if "SOURCES" in modes_being_run:
                total_results["relative_source_accuracy"] = (
                    relative_source_accuracy / len(self.queries_to_answers)
                )
                total_results["source_accuracy"] = source_accuracy / len(
                    self.queries_to_answers
                )

            ResultHandler.handle_results(
                Path(self.current_config), question_wise_results, total_results
            )
            self.load_new_configuration()

        ResultHandler.add_metadata()

        # A/B comparison: pair results across configs when 2+ were run.
        # Auto-enabled — no explicit flag — because there's no useful "skip
        # pairing" case when the user gave us multiple configs.
        if len(ResultHandler.results) >= 2:
            pairs = ResultHandler.generate_pairwise_combinations(
                len(ResultHandler.results)
            )
            logger.info("Generating %d pairwise A/B comparisons...", len(pairs))
            for idx_a, idx_b in pairs:
                paired = ResultHandler.pair_ab_results(idx_a, idx_b)
                ResultHandler.dump_ab_comparison(paired, idx_a, idx_b)
                comp = ResultHandler.ab_comparisons[-1]
                name_a = comp["config_a"].get("name", f"config_{idx_a}")
                name_b = comp["config_b"].get("name", f"config_{idx_b}")
                logger.info(
                    "  %s vs %s: %d questions. Wins A=%d, B=%d, Ties=%d",
                    name_a,
                    name_b,
                    len(paired),
                    comp["aggregate"]["wins_a"],
                    comp["aggregate"]["wins_b"],
                    comp["aggregate"]["ties"],
                )

        # Prompt-sweep leaderboard: rank every config by mean RAGAS metric.
        # Independent of the pairwise block above (reads per-config aggregates
        # directly). Only meaningful with 2+ variants.
        if len(ResultHandler.results) >= 2:
            primary_metric = str(
                self.config.get("services", {})
                .get("benchmarking", {})
                .get("primary_metric", "faithfulness")
            )
            leaderboard = ResultHandler.build_leaderboard(primary_metric)
            logger.info(
                "Prompt-sweep leaderboard (ranked by %s):",
                leaderboard["primary_metric"],
            )
            logger.info(
                "  %-4s %-28s %-10s %-10s %-10s %-10s %-10s %s",
                "rank",
                "name",
                "ans_rel",
                "faith",
                "ctx_prec",
                "ctx_rec",
                "n_q",
                "prompt",
            )
            for row in leaderboard["rows"]:
                m = row["metrics"]
                answered = row["query_count"]
                scored = row.get("scored_counts", {})

                # Annotate a metric with @<n> when its mean is over fewer than
                # the answered questions (judge timeouts), so an under-sampled
                # score can't masquerade as fully-backed.
                def _fmt(metric_name: str) -> str:
                    v = m[metric_name]
                    if not isinstance(v, float):
                        return "    n/a"
                    n = scored.get(metric_name, answered)
                    return f"{v:.4f}@{n}" if n < answered else f"{v:.4f}"

                flag = "  (incomplete)" if row["incomplete"] else ""
                logger.info(
                    "  %-4d %-28s %-12s %-12s %-12s %-12s %-10d %s%s",
                    row["rank"],
                    row["name"][:28],
                    _fmt("answer_relevancy"),
                    _fmt("faithfulness"),
                    _fmt("context_precision"),
                    _fmt("context_recall"),
                    answered,
                    row["agent_md_file"],
                    flag,
                )

        # Push to Argilla when ARCHI_ARGILLA=1 in the benchmarks container env.
        # The CLI flag --argilla on `archi evaluate` sets this (see Task 2.5).
        argilla_enabled = os.environ.get("ARCHI_ARGILLA", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if argilla_enabled:
            try:
                from src.utils.benchmark_argilla import (
                    generate_dataset_name,
                    push_ab_results_to_argilla,
                    push_multi_ab_results_to_argilla,
                    push_single_results_to_argilla,
                    write_state_file,
                )

                corpus_id = ResultHandler.get_corpus_snapshot_id()
                # services.benchmarking.argilla.min_submitted (default 2) drives
                # inter-rater reliability sample size by configuring rg.TaskDistribution.
                argilla_cfg = (
                    self.config.get("services", {})
                    .get("benchmarking", {})
                    .get("argilla", {})
                    or {}
                )
                min_submitted = int(argilla_cfg.get("min_submitted", 2))
                if (
                    ResultHandler.ab_comparisons
                    and len(ResultHandler.ab_comparisons) > 1
                ):
                    dataset_names = push_multi_ab_results_to_argilla(
                        ResultHandler.ab_comparisons,
                        self.benchmark_name,
                        corpus_snapshot_id=corpus_id,
                        min_submitted=min_submitted,
                    )
                    write_state_file(
                        dataset_name=dataset_names[0] if dataset_names else "",
                        dataset_names=dataset_names,
                    )
                    ResultHandler.metadata["argilla_datasets"] = dataset_names
                    logger.info(
                        "Argilla export complete. %d datasets created (corpus_snapshot_id=%s). "
                        "Open Argilla to grade: archi grade --serve",
                        len(dataset_names),
                        corpus_id,
                    )
                elif ResultHandler.ab_comparison:
                    argilla_dataset_name = generate_dataset_name(self.benchmark_name)
                    benchmark_output = {
                        "benchmarking_results": ResultHandler.results,
                        "ab_comparison": ResultHandler.ab_comparison,
                    }
                    push_ab_results_to_argilla(
                        benchmark_output,
                        argilla_dataset_name,
                        corpus_snapshot_id=corpus_id,
                        min_submitted=min_submitted,
                    )
                    write_state_file(argilla_dataset_name)
                    ResultHandler.metadata["argilla_dataset"] = argilla_dataset_name
                    logger.info(
                        "Argilla export complete. Dataset: '%s' (corpus_snapshot_id=%s). "
                        "Open Argilla to grade: archi grade --serve",
                        argilla_dataset_name,
                        corpus_id,
                    )
                else:
                    argilla_dataset_name = generate_dataset_name(self.benchmark_name)
                    benchmark_output = {
                        "benchmarking_results": ResultHandler.results,
                    }
                    push_single_results_to_argilla(
                        benchmark_output,
                        argilla_dataset_name,
                        corpus_snapshot_id=corpus_id,
                        min_submitted=min_submitted,
                    )
                    write_state_file(argilla_dataset_name)
                    ResultHandler.metadata["argilla_dataset"] = argilla_dataset_name
                    logger.info(
                        "Argilla export complete. Dataset: '%s' (corpus_snapshot_id=%s). "
                        "Open Argilla to grade: archi grade --serve",
                        argilla_dataset_name,
                        corpus_id,
                    )
            except Exception:
                logger.exception(
                    "Argilla push failed — results were still dumped to disk."
                )

        ResultHandler.dump(self.benchmark_name)
        ResultHandler.dump_html(self.benchmark_name)
        return

    def _merge_anchor_questions(self) -> None:
        """Splice anchor questions into the run's question set.

        Anchors are per-FASRC reference questions of three types
        (easy_retrieve, reasoning, should_refuse) that run on every round.
        They detect cross-round regressions and ground the comparison —
        they should NOT live in the main per-round question bank.

        Config knobs (all under services.benchmarking.anchors, all optional):
          enabled (bool, default True)   — disable entirely with `false`
          path (str)                     — override the default JSON path
        Default path: examples/benchmarking/anchor_questions.json

        Each anchor gets `anchor_type` set on the merged question dict; this
        flows through to the per-question result, then onto the Argilla
        record as metadata (NOT a visible field). Graders see anchors as
        ordinary records.
        """
        anchor_cfg = self.benchmarking_configs.get("anchors", {}) or {}
        if anchor_cfg.get("enabled", True) is False:
            logger.info("Anchor merging disabled by config; skipping.")
            return

        path_str = (
            anchor_cfg.get("path") or "examples/benchmarking/anchor_questions.json"
        )
        anchor_path = Path(path_str)
        if not anchor_path.is_absolute():
            # Resolve relative to the data path (matches how queries_path is read).
            candidates = [Path(self.data_path) / anchor_path, anchor_path]
            anchor_path = next((p for p in candidates if p.exists()), candidates[-1])

        if not anchor_path.exists():
            logger.warning(
                "Anchor questions file not found at %s; running without anchors.",
                anchor_path,
            )
            return

        try:
            anchors = json.loads(anchor_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read anchor file %s: %s", anchor_path, exc)
            return

        if not isinstance(anchors, list) or not anchors:
            logger.warning("Anchor file %s is empty or malformed.", anchor_path)
            return

        existing_questions = {
            q.get("question")
            for q in self.queries_to_answers
            if isinstance(q, dict) and q.get("question")
        }
        merged = list(self.queries_to_answers)
        added = 0
        for a in anchors:
            if not isinstance(a, dict) or not a.get("question"):
                continue
            if a["question"] in existing_questions:
                continue  # Anchor already in the bank — don't duplicate.
            merged.append(a)
            added += 1
        self.queries_to_answers = merged
        logger.info(
            "Merged %d anchor questions from %s (%d total questions).",
            added,
            anchor_path,
            len(merged),
        )

    def wait_for_ingestion_completion(self):
        timeout_seconds = int(os.environ.get("BENCH_INGEST_WAIT_TIMEOUT", "3600"))
        poll_interval_seconds = int(os.environ.get("BENCH_INGEST_POLL_INTERVAL", "5"))
        dm_cfg = self.config.get("services", {}).get("data_manager", {})
        # external_port is the HOST-side mapping (e.g. 7881 for benchmarks);
        # internal_port is what the data-manager listens on INSIDE the compose
        # network (e.g. 7871). The container-to-container URL must use the
        # internal port; the host-network fallbacks use the external port.
        dm_external_port = dm_cfg.get("external_port", 7871)
        dm_internal_port = dm_cfg.get("internal_port", 7871)
        # Order matters: try the cheap-success cases first. In bridge mode the
        # in-network hostname resolves; in --hostmode the container shares the
        # host network so the data-manager is reachable at localhost on its
        # *internal* port (it binds directly to the host, no port mapping).
        status_urls = [
            f"http://data-manager:{dm_internal_port}/api/ingestion/status",
            f"http://localhost:{dm_internal_port}/api/ingestion/status",
            f"http://localhost:{dm_external_port}/api/ingestion/status",
            f"http://host.containers.internal:{dm_external_port}/api/ingestion/status",
        ]
        start_time = time.monotonic()
        attempt = 0

        logger.info(
            "Waiting for data-manager ingestion to complete before benchmarking..."
        )
        while True:
            attempt += 1
            last_error = None
            for status_url in status_urls:
                try:
                    with url_request.urlopen(status_url, timeout=5) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    state = str(payload.get("state", "")).lower()
                    step = payload.get("step")
                    err = payload.get("error")
                    logger.info(
                        "Ingestion status check #%s via %s -> state=%s step=%s",
                        attempt,
                        status_url,
                        state,
                        step,
                    )
                    if state == "completed":
                        logger.info(
                            "Data-manager ingestion completed; starting benchmark."
                        )
                        return
                    if state == "error":
                        raise RuntimeError(
                            f"Data-manager ingestion failed at step '{step}': {err}"
                        )
                    break
                except (
                    url_error.URLError,
                    TimeoutError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    last_error = exc
                    continue

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_seconds:
                if last_error:
                    raise TimeoutError(
                        f"Timed out after {timeout_seconds}s waiting for ingestion status endpoint. Last error: {last_error}"
                    )
                raise TimeoutError(
                    f"Timed out after {timeout_seconds}s waiting for ingestion completion."
                )

            time.sleep(poll_interval_seconds)


if __name__ == "__main__":

    _init_runtime()

    query_file = Path("QandA.txt")
    configs_folder = Path("configs")

    with open(Path(query_file), "r") as f:
        question_to_answer = json.load(f)

    benchmarker = Benchmarker(configs_folder, question_to_answer)
    benchmarker.run()
    logger.info("\n\nFINISHED RUNNING\n\n")
