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
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlsplit, urlunsplit

import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import AgentSpecError, load_agent_spec
from src.archi.providers import get_model
from src.bin.benchmark_sut import apply_sut_local_provider, resolve_local_mode
from src.utils.benchmark_provenance import (
    asserted_config_divergence,
    collect_code_version,
    config_version,
    corpus_fingerprint,
)
from src.utils.benchmark_resilience import (
    OK,
    build_failure_entry,
    build_ragas_aggregates,
    build_source_aggregates,
    classify_metadata,
    is_scorable,
    scorable_items,
    source_hits,
)
from src.utils.benchmark_schema import (
    DEFAULT_ENABLED_METRICS,
    normalize_bank,
    required_fields_for_modes,
    score_metrics_per_eligibility,
)
from src.utils.config_access import get_static_config
from src.utils.env import read_secret
from src.utils.generate_benchmark_report import (
    format_markdown_output,
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

# The `src` package as installed in this image. The benchmark builds the agent
# in-process, so these files ARE the code under test -- and they are the baked
# site-packages copy, not a bind mount, which is exactly the code a deploy-time
# commit fails to identify. Derived from this module's own location so it follows
# the package wherever the image puts it.
PACKAGE_DIR = str(Path(__file__).resolve().parent.parent)
OUTPUT_DIR = Path(OUTPUT_PATH)

# The corpus's retrievable state, as opaque (key, value) pairs for
# `corpus_fingerprint`. Every row is keyed by `documents.resource_hash`, never by
# a SERIAL row id: two ingests of an identical corpus -- a rebuilt deployment, a
# re-seeded database -- get different serials, so keying by `document_id` made the
# cross-run comparison this field exists for impossible, rejecting runs that were
# in fact comparable.
#
# Three kinds of row, because retrieval reads all three:
#
#   doc     the live document list and byte sizes.
#   chunk   per-chunk content digests. `resource_hash` is `md5(url)`, an identity
#           hash deliberately stable across content updates, so the document list
#           alone would miss an edit that preserved the byte count. Hashing per
#           chunk index also catches re-chunking.
#   parent  `document_parent_nodes.parent_text`, plus the ordered list of child
#           chunk indexes grouped under it. Under `hierarchical_rerank` -- enabled
#           for every chunk in the FASRC deployment -- what reaches the agent is
#           the parent text, not the leaf chunks, and parents are neither embedded
#           nor indexed so no other part of this query sees them. Hashing leaves
#           alone would certify two arms as having seen the same corpus while the
#           context they were given differed. The child list is folded in because
#           re-grouping children changes that context even when every individual
#           text is untouched.
#
# Deleted documents are excluded from all three: soft-deleted rows stay in the
# tables but are not part of the corpus.
CORPUS_STATE_QUERY = """
SELECT 'doc:' || d.resource_hash, d.size_bytes::text
FROM documents d
WHERE d.is_deleted = FALSE
UNION ALL
SELECT 'chunk:' || d.resource_hash || ':' || c.chunk_index::text,
       md5(c.chunk_text)
FROM document_chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.is_deleted = FALSE
UNION ALL
SELECT 'parent:' || d.resource_hash || ':' || p.parent_index::text,
       md5(
           p.parent_text || '|' ||
           COALESCE(
               string_agg(c.chunk_index::text, ',' ORDER BY c.chunk_index), ''
           )
       )
FROM document_parent_nodes p
JOIN documents d ON d.id = p.document_id
LEFT JOIN document_chunks c ON c.metadata->>'parent_id' = p.id::text
WHERE d.is_deleted = FALSE
GROUP BY d.resource_hash, p.parent_index, p.parent_text
"""

#: Distinguishes a provenance field that was never recorded (a result file
#: written before provenance existed) from one recorded as undetermined.
_NOT_RECORDED = object()

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

    #: Prefix of a fingerprint that records why the corpus could not be read.
    CORPUS_UNAVAILABLE = "<unavailable:"

    @staticmethod
    def corpus_reading_failed(fingerprint: Optional[str]) -> bool:
        """Is *fingerprint* a non-observation rather than a corpus state?"""
        return fingerprint is None or str(fingerprint).startswith(
            ResultHandler.CORPUS_UNAVAILABLE
        )

    @staticmethod
    def arms_comparable(records: List[Dict[str, Any]]) -> bool:
        """Can these arms' scores be set against each other?

        Only when, for every arm, the corpus provenance is established, they all
        observed the same corpus, and the arm actually ran the settings it was
        selected to run. A diverged arm did not test its intended condition, so
        ranking it against the others asserts a controlled comparison that did
        not happen.

        Note what is NOT checked: arm identity (name, model, provider,
        agent_md_file). The harness reads those from the selected file's
        ``services.benchmarking`` and passes them to ``archi()`` as explicit
        keyword arguments, so the file is authoritative for them and the labels
        are accurate. Only ``services.chat_app`` and the rest of what the agent
        reads from Postgres can diverge.

        Used by BOTH comparison artifacts -- the leaderboard and the pairwise
        A/B dump -- because a guard on one of them still lets a reader draw the
        unsupported conclusion from the other.

        Records with no provenance keys at all predate provenance and are left
        comparable: historical sweeps are not retroactively invalidated.
        """
        fingerprints = set()
        for record in records:
            stability = record.get("corpus_unchanged_at_endpoints", _NOT_RECORDED)
            if stability is not _NOT_RECORDED and stability is not True:
                return False
            if record.get("configuration_divergence"):
                return False
            fingerprint = record.get("corpus_fingerprint")
            if fingerprint is not None:
                fingerprints.add(fingerprint)
        return len(fingerprints) <= 1

    @staticmethod
    def ab_summary_line(
        name_a: str,
        name_b: str,
        question_count: int,
        aggregate: Dict[str, Any],
    ) -> str:
        """One operator-facing line for a pair, withheld winners included.

        Withholding sets the tallies to ``None``, and the caller's format string
        was left as ``Wins A=%d, B=%d, Ties=%d``. ``'%d' % None`` raises;
        ``logging`` catches that in ``handleError`` rather than aborting the run,
        so the effect is not a crash but a *lost* line -- the operator gets
        "--- Logging error ---" and a traceback where the pair summary should be,
        in exactly the incomparable case the guard was added to report.

        ``0`` is a tally, not an absence: only ``None`` means withheld.
        """
        head = f"  {name_a} vs {name_b}: {question_count} questions."
        if aggregate.get("wins_a") is None:
            return f"{head} Winners withheld: the arms are not comparable."
        return (
            f"{head} Wins A={aggregate['wins_a']}, "
            f"B={aggregate['wins_b']}, Ties={aggregate['ties']}"
        )

    @staticmethod
    def get_corpus_fingerprint() -> str:
        """Digest of the live corpus, or a marker explaining why it is missing.

        Unlike the per-invocation nonce above, equal digests mean equal corpora,
        so "these arms were scored against the same documents" becomes a
        checkable claim.

        Covers the retrievable state, not just the document list -- see
        ``CORPUS_STATE_QUERY`` for what is hashed and why. Re-embedding the same
        text with a different model is NOT covered; that appears as a divergence
        on ``data_manager.embedding_name`` in the recorded configuration.

        Reads through the pool the run actually opened -- the one `_init_runtime`
        installed on PostgresServiceFactory -- and NOT `ConnectionPool.get_instance`.
        The two are unrelated singletons: the factory builds its pools directly
        (`from_config`, and the lazy `connection_pool` property), so nothing ever
        populates `ConnectionPool._instance`, and asking it for the pool raised
        `ValueError` on every real run. Because provenance failure is swallowed
        below, that filed an unavailable-marker instead of crashing, so the field
        was inert wherever it was consumed while the unit tests stayed green --
        they monkeypatched the very call that could not work (#273).

        Never raises: a finished benchmark must not lose its scores because
        provenance could not be collected. It does now warn, because an artifact
        key nobody thinks to check is how the inert version survived review.
        """
        try:
            factory = PostgresServiceFactory.get_instance()
            if factory is None:
                raise RuntimeError(
                    "PostgresServiceFactory is not initialized; _init_runtime() "
                    "installs it when this module is run as a script"
                )
            rows = factory.connection_pool.execute(CORPUS_STATE_QUERY)
            return corpus_fingerprint(rows)
        except Exception as exc:  # noqa: BLE001 - provenance is never fatal
            logger.warning(
                "Corpus provenance unavailable: %s. This run cannot be shown to "
                "have scored against the same corpus as any other, so comparisons "
                "involving it will be withheld.",
                exc,
            )
            return f"{ResultHandler.CORPUS_UNAVAILABLE} {exc}>"

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
    def handle_results(
        config_path: Path,
        results: Dict,
        total_results: Dict,
        *,
        running_config: Optional[Dict[str, Any]],
        corpus_before: Optional[str] = None,
        ingest_wall_seconds: Optional[float] = None,
    ):
        with open(config_path, "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

        ResultHandler.map_prompts(config)

        # The file above is what the operator SELECTED. The agent reads its
        # configuration from Postgres, and load_new_configuration writes the
        # selected file to CONFIG_PATH -- which archi() never reads. Recording
        # only the file therefore labels the run with settings it may never have
        # used.
        #
        # `running_config` is the snapshot archi.__init__ took when it built the
        # chain (src/archi/archi.py). It is passed in rather than re-queried
        # here: the query would run AFTER the arm's questions, so a config change
        # during the arm would certify settings the chain never held -- and would
        # clear the divergence list while doing it.
        #
        # Scoped to what the file ASSERTS, not the two dicts whole. get_full_config
        # returns the configuration after seeding, defaulting and reshaping, so a
        # whole-dict comparison reported ~192 differences on a deployment seeded
        # from the very file being compared -- making arms_comparable() False on
        # every arm of every run. See asserted_config_divergence.
        if running_config is None:
            divergence = ["<unavailable: the run reported no configuration>"]
        else:
            divergence = asserted_config_divergence(config, running_config)

        if divergence:
            logger.warning(
                "This report may not describe the run: the selected configuration "
                "(%s) and the configuration the agent read disagree at %d "
                "setting(s): %s",
                config_path,
                len(divergence),
                ", ".join(divergence),
            )

        corpus_after = ResultHandler.get_corpus_fingerprint()
        # None, not False, when either reading is missing or failed. A failure is
        # not an observation: get_corpus_fingerprint reports one as
        # "<unavailable: ...>", and two identical failures compare equal, so
        # plain equality would certify the corpus as stable at exactly the moment
        # nothing about it was actually observed.
        if ResultHandler.corpus_reading_failed(
            corpus_before
        ) or ResultHandler.corpus_reading_failed(corpus_after):
            corpus_unchanged_at_endpoints = None
        else:
            corpus_unchanged_at_endpoints = corpus_before == corpus_after
        if corpus_unchanged_at_endpoints is False:
            logger.warning(
                "The corpus changed while this arm was running (%s -> %s); its "
                "questions were not all scored against the same documents",
                corpus_before,
                corpus_after,
            )

        current_results = {
            "single_question_results": results,
            "total_results": total_results,
            "configuration_file": str(config_path),
            "configuration": config,
            "running_configuration": running_config,
            "configuration_divergence": divergence,
            # Sampled around the arm, not once per sweep. Ingestion runs
            # continuously in this deployment, so an arm can straddle a
            # re-ingest and score different questions against different
            # corpora; a single reading taken afterwards would report the final
            # state as though it had covered the whole arm.
            "corpus_fingerprint_before": corpus_before,
            "corpus_fingerprint": corpus_after,
            "corpus_unchanged_at_endpoints": corpus_unchanged_at_endpoints,
            # What the corpus above COST to build, in harness-observed seconds.
            # Three readings, kept distinct on purpose: key absent = artifact
            # predates the field; null = no ingest was observed (the run reused
            # an existing corpus); a float = seconds. Never 0.0 for "not
            # measured". Recorded per arm and nowhere else -- a sweep runs
            # several arms, so a run-level copy would label them all with one.
            "ingest_wall_seconds": ingest_wall_seconds,
            # Per arm, not per file. One invocation runs every config in the
            # sweep directory (the `while self.all_config_files` loop), so a
            # single version on the metadata block would label every arm with
            # whichever ran last; bench-sweep-20260610 holds three arms.
            #
            # Divergence above catches a mislabel while both sources are in
            # hand. This digest answers the other question -- "was this the same
            # configuration as that other run?" -- from the finished artifact
            # alone, long after Postgres has moved on.
            "config_version": config_version(
                running=running_config,
                selected=config,
                selected_file=str(config_path),
            ),
        }

        ResultHandler.results.append(current_results)

    @staticmethod
    def add_metadata():
        try:
            with open(EXTRA_METADATA_PATH, "r") as f:
                additional_info = yaml.safe_load(f)
        except OSError as exc:  # noqa: BLE001 - provenance is never fatal
            logger.warning("Could not read %s: %s", EXTRA_METADATA_PATH, exc)
            additional_info = None

        meta_data = {
            "time": str(datetime.now(timezone.utc)),
            "git_info": additional_info,
            # git_info.yaml is written by `archi create` and then frozen. Re-running
            # the benchmark container against an existing deployment reports the
            # commit that was checked out at DEPLOY time, not the code in the image
            # -- every arm of a campaign reports the same commit even when the arms
            # ran different code. Say so in the artifact rather than in a comment.
            "git_info_captured_at": "deploy (`archi create`), not the running image",
            # What the frozen commit above cannot provide: an identity for the
            # code this run actually executed. Digested from the `src` package
            # files in the image, so it is per invocation (one image runs every
            # arm) and independent of which code paths the run happened to take.
            "code_version": collect_code_version(PACKAGE_DIR, additional_info),
            # The config version is per arm and lives on each result record; this
            # only summarises their digests, in the order the arms ran.
            "config_versions": [
                (record.get("config_version") or {}).get("digest")
                for record in ResultHandler.results
            ],
            "corpus_snapshot_id": ResultHandler.get_corpus_snapshot_id(),
            "corpus_fingerprint": ResultHandler.get_corpus_fingerprint(),
        }

        ResultHandler.metadata.update(meta_data)

    @staticmethod
    def dump_artifacts(benchmark_name: Path):
        """Write the run's JSON artifact and its markdown report.

        The timestamp is captured ONCE so the report is always the JSON's
        `_report.md` sibling — the invariant the backfill script's bulk
        re-render path locates reports by. The JSON is written first (it is
        the source of truth); a report failure is logged and swallowed, and
        `--regenerate-md` on the backfill script rebuilds the report later.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        json_path = ResultHandler.dump(benchmark_name, timestamp)
        try:
            ResultHandler.dump_report(benchmark_name, timestamp)
        except Exception:
            # The hint names the exact artifact: the backfill script's default
            # glob is the repo's bench_out/, which is NOT where OUTPUT_DIR
            # points inside the benchmark container.
            logger.exception(
                f"Markdown report generation failed — the JSON artifact was "
                f"still dumped to {json_path}; rebuild the report with "
                f"scripts/benchmarking/backfill_report_provenance.py "
                f"--regenerate-md {json_path}"
            )

    @staticmethod
    def dump_report(benchmark_name: Path, timestamp: str):

        config_data, config_name, run_time, questions, total_results, provenance = (
            parse_benchmark_results(ResultHandler.results, ResultHandler.metadata)
        )

        logger.info(config_data)

        markdown_content = format_markdown_output(
            config_data, config_name, run_time, questions, total_results, provenance
        )

        file_path = OUTPUT_DIR / f"{benchmark_name}-{timestamp}_report.md"

        logger.info(f"Dumping results to {file_path}")

        with open(file_path, "w") as f:
            f.write(markdown_content)

        logger.info(f"✅ Markdown report generated: {file_path}")

    @staticmethod
    def dump(benchmark_name: Path, timestamp: str):
        filename = f"{benchmark_name}-{timestamp}.json"
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
        return file_path

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
            "answer_correctness",
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

            # Never pair a failed/degraded row: it carries no real answer and would
            # skew the A/B comparison (blank-answer or truncated-context result).
            if not is_scorable(qa) or not is_scorable(qb):
                logger.warning(
                    "Question key %s is a failed/degraded row in a config; skipping A/B pairing.",
                    key,
                )
                continue

            # Only metrics BOTH arms scored can be compared. Reading a missing
            # side as NaN published a "tie" verdict on a metric one arm never
            # measured, and the aggregate then fabricated a 0.0 mean for it — the
            # WORST possible score — reading as "this arm is bad at it" rather
            # than "this arm did not measure it". Present-but-NaN on both sides is
            # a different case: that is scored-and-failed, and stays a tie.
            shared_metrics = [m for m in ragas_metrics if m in qa and m in qb]
            ragas_a = {m: qa.get(m, float("nan")) for m in shared_metrics}
            ragas_b = {m: qb.get(m, float("nan")) for m in shared_metrics}

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

        # Same guard as the leaderboard's: a per-metric winner is a claim that
        # the two arms were measured under the same conditions. Guarding only
        # the leaderboard would still let a reader draw the unsupported
        # conclusion from this artifact.
        comparable = ResultHandler.arms_comparable(
            [ResultHandler.results[idx_a], ResultHandler.results[idx_b]]
        )

        wins_a: Optional[int] = 0
        wins_b: Optional[int] = 0
        ties: Optional[int] = 0
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

        if not comparable:
            # Withhold the verdict, keep the measurements: per-question ragas_a
            # and ragas_b stay so an operator can still inspect the run.
            for row in per_question:
                row["winner_by_metric"] = {}
            wins_a = wins_b = ties = None
            logger.warning(
                "A/B winners withheld for '%s' vs '%s': corpus provenance does "
                "not establish that both arms were scored against the same "
                "documents",
                config_a_meta["name"],
                config_b_meta["name"],
            )

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
            "comparable": comparable,
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
        ("answer_correctness", "aggregate_answer_correctness"),
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
        primary_was_enabled = False
        # Accumulate shared-context candidates to detect drift across configs.
        ctx_fields: Dict[str, set] = {
            "model": set(),
            "provider": set(),
            "evaluator_model": set(),
            "queries_path": set(),
            "corpus_fingerprint": set(),
        }
        corpus_warnings: List[str] = []

        for record in ResultHandler.results:
            bench = _benchmarking(record)
            total = record.get("total_results", {}) or {}

            agent_md_file = bench.get("agent_md_file", "") or ""
            name = bench.get("name") or (
                Path(agent_md_file).stem if agent_md_file else ""
            )

            # ``incomplete`` means "a metric this run was SUPPOSED to produce is
            # missing" — it flags the row in the console table and sorts it last.
            # LEADERBOARD_METRICS is a static SUPERSET of what any one run scores,
            # so judge only the metrics this run actually enabled; otherwise every
            # run that declines an optional metric reads as a defective run. A
            # config that omits the list runs the template default, which
            # DEFAULT_ENABLED_METRICS mirrors.
            ragas_settings = (bench.get("mode_settings") or {}).get(
                "ragas_settings"
            ) or {}
            expected = ragas_settings.get("enabled_metrics") or DEFAULT_ENABLED_METRICS

            metrics: Dict[str, Optional[float]] = {}
            incomplete = False
            for metric_name, agg_key in ResultHandler.LEADERBOARD_METRICS:
                value = total.get(agg_key)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    metrics[metric_name] = None
                    if metric_name in expected:
                        incomplete = True
                else:
                    metrics[metric_name] = float(value)

            # A rank is a claim about the metric being ranked BY, so a row with no
            # value for the primary metric cannot be ordered against one that has
            # it — whatever the run enabled. The sort reads a None primary score
            # as 0.0, so without this the row would take a normal numeric rank in
            # the complete tier instead of sorting last.
            if metrics[primary_metric] is None:
                incomplete = True

            # A record with no metric list but a real score for the primary metric
            # demonstrably ran it, so an observed score counts as evidence. Warning
            # "never enabled" there would be false, and a false warning teaches the
            # operator to ignore the real one.
            if primary_metric in expected or metrics[primary_metric] is not None:
                primary_was_enabled = True

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
                # Publish a sample size unless we are making no claim at all
                # about this metric: not enabled AND no score. A static list would
                # report 0 for a metric nobody enabled, which reads identically to
                # an enabled metric whose every judge call failed. A metric that
                # DID produce a score always keeps its count, even when it is
                # absent from `expected` (which falls back to the template
                # default when a record carries no metric list).
                if metric_name not in expected and metrics[metric_name] is None:
                    continue
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
                    [m for m in metric_names if metrics[m] is None and m in expected],
                )
            # Surface under-sampling even when the aggregate is a valid float.
            answered = len(single_question_results)
            undersampled = [
                f"{m}={scored_counts.get(m, 0)}/{answered}"
                for m in metric_names
                if metrics[m] is not None and scored_counts.get(m, 0) < answered
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

            ctx_fields["model"].add(bench.get("model"))
            ctx_fields["provider"].add(bench.get("provider"))
            ctx_fields["evaluator_model"].add(ragas_settings.get("evaluator_model"))
            ctx_fields["queries_path"].add(bench.get("queries_path"))
            # The corpus is a swept-context field like any other: ranking arms
            # scored against different documents asserts controlled conditions
            # the run cannot support.
            ctx_fields["corpus_fingerprint"].add(record.get("corpus_fingerprint"))
            # An ABSENT key means the record predates corpus provenance and has
            # nothing to say; a key present and None means provenance ran and
            # came back undetermined. Only the latter is a finding.
            stability = record.get("corpus_unchanged_at_endpoints", _NOT_RECORDED)
            if stability is False:
                corpus_warnings.append(
                    f"the corpus changed while variant '{name}' was running; its "
                    "questions were not all scored against the same documents"
                )
            elif stability is None:
                corpus_warnings.append(
                    f"corpus stability is unknown for variant '{name}'; it was "
                    "not observed before and after the run"
                )
            divergence = record.get("configuration_divergence") or []
            if divergence:
                corpus_warnings.append(
                    f"variant '{name}' did not run the settings it was selected "
                    f"to run; these differ: {', '.join(divergence)}"
                )

        # Complete rows first, then by descending primary score; incomplete last.
        rows.sort(
            key=lambda r: (
                1 if r["incomplete"] else 0,
                -(r["primary_score"] if r["primary_score"] is not None else 0.0),
            )
        )

        # A rank is a machine-readable claim that these variants were measured
        # under the same conditions. When corpus provenance says they were not,
        # withhold the ranking rather than manufacture an ordering a consumer
        # would read from rows[*].rank without ever seeing the warnings. The
        # metrics stay, so an operator can still inspect the run.
        comparable = ResultHandler.arms_comparable(ResultHandler.results)

        # Withholding every rank is correct but silent on its own; say why, or the
        # only symptom an operator sees is a leaderboard of null ranks.
        if rows and not primary_was_enabled:
            logger.warning(
                "Leaderboard: primary_metric '%s' was not enabled by any swept "
                "config, so no variant scored it and every rank is withheld "
                "(null). Add it to "
                "services.benchmarking.mode_settings.ragas_settings.enabled_metrics, "
                "or rank by a metric the run actually scored.",
                primary_metric,
            )

        # Dense ranking: equal primary scores share a rank.
        rank = 0
        prev_score: Any = object()
        for row in rows:
            score = row["primary_score"]
            if score is None:
                # A rank is a claim ABOUT the primary metric, so a row with no
                # score for it carries no rank — not a number a consumer would
                # compare. Sorting it last is not enough: the number itself is
                # what gets read out of the JSON. It also does not consume a rank,
                # so the scored rows keep 1..n.
                row["rank"] = None
                continue
            if score != prev_score:
                rank += 1
                prev_score = score
            row["rank"] = rank if comparable else None

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
        warnings.extend(corpus_warnings)
        if warnings:
            for w in warnings:
                logger.warning("Leaderboard shared-context drift: %s", w)
        shared_context["warnings"] = warnings

        ResultHandler.leaderboard = {
            "shared_context": shared_context,
            "primary_metric": primary_metric,
            "comparable": comparable,
            "rows": rows,
        }
        return ResultHandler.leaderboard


class _IngestWaitBudgets(NamedTuple):
    """The three knobs that bound the benchmark's wait for the data-manager.

    ``stall_seconds`` is time since the *last successful* status poll, not total
    runtime -- an ingest that keeps answering can take as long as it needs.
    ``max_wait_seconds`` is the absolute backstop for an ingest that is alive
    but stuck; ``0`` disables it.
    """

    stall_seconds: int
    max_wait_seconds: int
    poll_interval_seconds: int


#: States that can count as evidence the ingest is working. Deliberately
#: narrow: `ingestion_status.py:29-33` starts the endpoint at "pending" and
#: only the ingestion thread moves it on, so an endpoint answering "pending"
#: (or anything unrecognized) forever means the ingest never got going.
_INGEST_PROGRESS_STATES = frozenset({"running"})

#: The step published *before* `ingestion_lock` is acquired
#: (`ingestion_status.py:46-48`). Every later step comes from inside the lock
#: (`data_manager.py:90-109`), so this is the one step that proves work has NOT
#: started.
_INGEST_PRELOCK_STEP = "initializing"


def _ingest_is_progressing(state: str, step: Any) -> bool:
    """Is this status payload evidence the ingest is actually doing work?

    Only payloads this accepts restart the stall budget. Two shapes are
    excluded on purpose, because both are indistinguishable from a healthy
    long run if you look only at "did the endpoint answer":

    - any state but "running" -- notably the initial "pending", which persists
      forever if the ingestion thread never starts;
    - "running" at step "initializing" -- published before `ingestion_lock` is
      taken, so it is also exactly what a benchmark sees while its own ingest
      is queued behind a scheduled task or an upload-triggered vectorstore
      update, neither of which touches this status dict
      (`service_data_manager.py:70-83`).
    """
    if state not in _INGEST_PROGRESS_STATES:
        return False
    return str(step).strip().lower() != _INGEST_PRELOCK_STEP


def _ingest_wait_budgets() -> _IngestWaitBudgets:
    return _IngestWaitBudgets(
        stall_seconds=int(os.environ.get("BENCH_INGEST_WAIT_TIMEOUT", "7200")),
        max_wait_seconds=int(os.environ.get("BENCH_INGEST_MAX_WAIT", "21600")),
        poll_interval_seconds=int(os.environ.get("BENCH_INGEST_POLL_INTERVAL", "5")),
    )


def _fetch_ingestion_status(url: str) -> Dict[str, Any]:
    """Read one ingestion-status payload. The injection seam for the wait loop."""
    with url_request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _ingest_wait_timeout_message(
    reason: str,
    *,
    candidate_urls: List[str],
    last_ok_url: Optional[str],
    last_state: Optional[str],
    last_step: Any,
    last_error: Optional[BaseException],
) -> str:
    """Explain an ingest timeout in terms of what the harness actually observed.

    `last_error` only ever appears when it is still the live reason nothing is
    answering: the wait loop clears it on every successful poll, so this can no
    longer quote a connection failure from a candidate URL it fell past (issue
    #378, defect 2).
    """
    if last_ok_url is None:
        observed = (
            "none of the candidate status URLs ever answered "
            f"({', '.join(candidate_urls)})"
        )
    else:
        observed = (
            f"last successful poll was {last_ok_url} -> "
            f"state={last_state} step={last_step}"
        )
    message = f"Timed out waiting for data-manager ingestion: {reason}. {observed}."
    if last_error is not None:
        message = f"{message} Last error: {last_error}"
    return message


class Benchmarker:

    def __init__(self, configs: Path, q_to_a: dict[str, str]):
        self.queries_to_answers = normalize_bank(q_to_a)
        self.required_fields = ["user_input"]
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
        # Schema validation is per-mode and SEPARATE from metric eligibility:
        # user_input is always required, SOURCES additionally requires `sources`,
        # and RAGAS requires nothing extra (an empty `reference` is a valid draft
        # row that per-metric eligibility, not load validation, excludes from the
        # context metrics). Recomputed fresh per config (never accumulated).
        self.required_fields = required_fields_for_modes(self.benchmarking_configs)

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

        # Bridge a `provider: local` SUT into the config the agent reads
        # (services.chat_app.providers.local), so an OpenAI-compatible endpoint
        # (e.g. the FASRC vLLM at .../v1) builds a ChatOpenAI client instead of the
        # Ollama client. No-op for non-local providers. See issue #73.
        apply_sut_local_provider(benchmark_cfg, get_static_config())

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
                future = executor.submit(_ask, chain, qid, question_item["user_input"])
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
                # Mirror the SUT bridge (#73): a /v1 judge endpoint is
                # OpenAI-compatible, so build a ChatOpenAI client instead of
                # ChatOllama (which 404s against /v1). An explicit provider_mode
                # (judge-specific or inherited from the SUT) overrides the
                # /v1 auto-detection.
                explicit_mode = ragas_configs.get(
                    "evaluator_provider_mode"
                ) or benchmark_cfg.get("provider_mode")
                if resolve_local_mode(ollama_url, explicit_mode) == "openai_compat":
                    return get_model(
                        "local",
                        model_name,
                        {"base_url": ollama_url, "mode": "openai_compat"},
                    )
                from langchain_ollama import ChatOllama

                return ChatOllama(
                    model=model_name,
                    base_url=ollama_url,
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
        if n_sources == 0:
            # Nothing to pair. A zero-reference row (e.g. a `should_refuse` anchor)
            # declares no sources, so a declared match field has nothing to match
            # against — that is not the count mismatch the raise below guards. See
            # `source_hits`, which already scores an empty match list as a clean
            # row rather than a failure.
            return []
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

    @staticmethod
    def _canonical_source(value: Any) -> str:
        """Canonical form of a gold/retrieved source value, for comparison only.

        Strips surrounding whitespace and a single trailing ``/`` from the URL
        *path* — the one difference that actually occurs between an authored bank
        URL and the ingested ``documents.url``. Deliberately conservative: it does
        NOT lowercase (paths are case-sensitive), normalize the scheme, or drop the
        query/fragment, because over-matching would silently conflate distinct
        pages — a worse failure than the miss it fixes, and an invisible one.

        The slash is stripped from the path only, so a query or fragment that
        legitimately ends in ``/`` (e.g. ``...?redirect=/kb/foo/``) is preserved.
        A value with no scheme (e.g. a ``file_name`` match field) parses as a bare
        path, so the same one-trailing-slash rule applies without special-casing.
        """
        text = str(value).strip()
        parts = urlsplit(text)
        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
            return urlunsplit(parts._replace(path=path))
        return text

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

        Comparison is on the canonical form of both sides (see ``_canonical_source``):
        banks author the canonical page URL with a trailing slash, while the
        sitemap-driven ingest stores it without one, and an exact compare scored
        every gold source as a miss regardless of retrieval quality.
        """
        sources = result.get("source_documents", [])
        logger.info("Agent found %s sources.", len(sources))

        matches: List[bool] = []
        for source in formatted_reference_sources:
            field, reference = list(source.items())[0]
            canonical_reference = self._canonical_source(reference)
            logger.debug(
                "Checking for reference source '%s' in field '%s'", reference, field
            )
            for document in sources:
                metadata = getattr(document, "metadata", {}) or {}
                value = metadata.get(field)
                if value is None:
                    continue
                if isinstance(value, list):
                    values = [self._canonical_source(v) for v in value if v is not None]
                else:
                    values = [self._canonical_source(value)]
                logger.info("Returned source '%s': %s", field, values)
                logger.debug(
                    "Checking reference '%s' against document metadata field '%s': %s",
                    reference,
                    field,
                    values,
                )
                if canonical_reference in values:
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

    def get_ragas_results(self, rows, keys, results_by_key):
        """Score each enabled RAGAS metric over its OWN eligible subset, attaching
        each per-row score back to its question by key.

        ``rows`` are the modern-dialect ragas records
        (``user_input``/``retrieved_contexts``/``response``/``reference``) for the
        scorable questions; ``keys`` are their per-question keys (from #92's
        ``scorable_items``) in the same order; ``results_by_key`` is the keyed
        result dict each score is written onto. A context metric skips rows whose
        ``reference`` is empty (a draft row) and a metric with no eligible row
        records ``n/a`` WITHOUT invoking ragas — so each aggregate is a mean over
        real rows, not a skip-NaN mean over a hidden partial denominator. Returns
        the per-metric ``aggregate_<metric>`` + ``<metric>_scored`` dict.

        WARNING: mutates ``results_by_key`` in place (adds each metric's score to
        the matching question entry).
        """
        # Lazy import: ragas (and its transitive `datasets` dep) is benchmark-only
        # and absent from the unit-test environment. See the module-header note.
        from ragas import EvaluationDataset, RunConfig, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import (
            answer_correctness,
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        # Use the PRE-INSTANTIATED ``answer_correctness`` rather than building a
        # FactualCorrectness: scores are read back as ``to_pandas()[metric]``, and
        # only the pre-instantiated object's result column is named exactly after
        # the metric (FactualCorrectness's can carry a mode suffix).
        all_metrics = {
            "answer_relevancy": answer_relevancy,
            "faithfulness": faithfulness,
            "context_precision": context_precision,
            "context_recall": context_recall,
            "answer_correctness": answer_correctness,
        }
        enabled_metrics = self.benchmarking_configs["mode_settings"]["ragas_settings"][
            "enabled_metrics"
        ]
        metrics = [name for name in all_metrics if name in enabled_metrics]

        ragas_settings = self.config["services"]["benchmarking"]["mode_settings"][
            "ragas_settings"
        ]
        # The archi config-render pipeline can strip global.verbosity; tolerate
        # missing key (verbosity 4 enables tenacity retry logging in ragas).
        log_tenacity = self.config.get("global", {}).get("verbosity", 0) >= 4
        batch_size = ragas_settings["batch_size"] or None
        runconfig = RunConfig(
            timeout=ragas_settings["timeout"], log_tenacity=log_tenacity
        )
        llm = LangchainLLMWrapper(self.get_ragas_llm_evaluator())
        embeddings = LangchainEmbeddingsWrapper(self.get_ragas_embedding_model())

        def score_fn(metric, eligible_rows):
            # One metric at a time over its own eligible subset: keeps a single
            # bad metric from failing the batch and preserves per-metric
            # denominators (the modern EvaluationDataset replaces the legacy
            # datasets.Dataset + column names).
            dataset = EvaluationDataset.from_list(eligible_rows)
            evaluation = evaluate(
                dataset,
                metrics=[all_metrics[metric]],
                llm=llm,
                embeddings=embeddings,
                run_config=runconfig,
                batch_size=batch_size,
            )
            return evaluation.to_pandas()[metric].tolist()

        return score_metrics_per_eligibility(
            rows, keys, metrics, results_by_key, score_fn
        )

    def _source_scorable_count(self) -> int:
        """The source-accuracy denominator: questions that declare expected sources.

        A zero-source row — the `should_refuse` anchor is the reason this exists —
        has nothing to retrieve, so it is neither a hit nor a miss and must not sit
        in the denominator. Every other row does, so a failed retrieval still
        registers as a miss rather than quietly vanishing from the average.
        """
        return sum(
            1
            for q in self.queries_to_answers
            if isinstance(q, dict) and q.get("sources")
        )

    def _process_config(self, modes_being_run):
        """Answer + score every question for the current config.

        Returns ``(question_wise_results, total_results)``. Per-question failures
        are isolated (see ``_answer_and_score_question``) so one bad question never
        aborts the run; an all-failed config yields ``n/a`` aggregates rather than
        an empty RAGAS dataset.
        """
        question_id = 0
        question_wise_results: Dict[str, Any] = {}
        total_results: Dict[str, Any] = {}
        ragas_input: List[Dict[str, Any]] = []
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

            logger.info(f"Question: {question_item['user_input']}")
            logger.info(f"Reference Answer: {question_item.get('reference') or 'N/A'}")
            logger.info(f"Reference Sources: {question_item.get('sources', 'N/A')}")

            question_id += 1
            # Answer + score is isolated: a failure returns a marked entry instead
            # of aborting the run (see _answer_and_score_question).
            bundle = self._answer_and_score_question(
                question_item, question_id, modes_being_run
            )
            q_results = bundle["q_results"]
            question_wise_results[f"question_{question_id}"] = q_results

            # Only clean successes contribute RAGAS input and source matches;
            # failed/degraded rows return None for both.
            if bundle["dataset_result"] is not None:
                ragas_input.append(bundle["dataset_result"])

            rel_hit, strict_hit = source_hits(
                bundle["matches"], q_results.get("reference_sources_metadata", [])
            )
            relative_source_accuracy += rel_hit
            source_accuracy += strict_hit

            logger.info("====================================")
            logger.info("")

        if "RAGAS" in modes_being_run:
            if ragas_input:
                logger.info("Starting to collect RAGAS results")
                # scorable_items carries #92's per-question keys in ragas_input
                # order; get_ragas_results scores each metric over its own
                # eligible subset and attaches scores back BY KEY (never
                # positionally — Codex #93 F5).
                scorable = scorable_items(question_wise_results)
                total_results.update(
                    self.get_ragas_results(ragas_input, list(scorable.keys()), scorable)
                )
            else:
                # No scorable input (all failed/degraded): #92's config-level n/a
                # guard emits NaN for every metric, with no empty-Dataset ragas call.
                # Tolerant read: this is the FAILURE path, so an unreadable or
                # absent metric list must not turn a degraded run into a crash.
                # None falls back to emitting every known metric, the behaviour
                # before the list was threaded through.
                enabled = (
                    (
                        (getattr(self, "benchmarking_configs", None) or {}).get(
                            "mode_settings"
                        )
                        or {}
                    ).get("ragas_settings")
                    or {}
                ).get("enabled_metrics")
                total_results.update(
                    build_ragas_aggregates(
                        None, enabled_metrics=enabled or DEFAULT_ENABLED_METRICS
                    )
                )

        if "SOURCES" in modes_being_run:
            # Denominator is the questions that DECLARE expected sources, not the
            # total count. A failed/degraded row still counts as a miss, but a
            # zero-source row (a `should_refuse` anchor) has no source to hit or
            # miss — counting it would either fabricate a hit or dilute the score.
            # The count is emitted alongside so the report stops re-deriving it
            # from len(questions).
            total_results.update(
                build_source_aggregates(
                    relative_source_accuracy,
                    source_accuracy,
                    self._source_scorable_count(),
                )
            )

        return question_wise_results, total_results

    def _answer_and_score_question(self, question_item, question_id, modes_being_run):
        """Answer and score one question, isolating failures.

        Isolation boundary (openspec harden-benchmark-and-agent-resilience): any
        exception from answering OR per-question scoring is caught and returned as a
        marked failure entry, so one bad question never aborts the whole run. A
        context-overflow *degraded* answer (marked by the agent in
        ``PipelineOutput.metadata``) is recorded with ``status="degraded"`` and
        excluded from the RAGAS input and source scoring, so it is never counted as a
        clean success.

        Returns a dict with keys ``q_results`` (always), ``dataset_result`` (RAGAS
        input for this question, or ``None``), and ``matches`` (source-match booleans,
        or ``None``).
        """
        question = question_item["user_input"]
        reference_answer = question_item.get("reference", "")
        reference_sources = question_item.get("sources", "N/A")
        try:
            formatted_question = [("User", question)]
            start = time.perf_counter()
            result = self.chain(history=formatted_question)
            end = time.perf_counter()
            logger.info(
                f"Finished answering question: {question_id} ({end - start:.2f}s)"
            )

            status = classify_metadata(
                result.get("metadata") if hasattr(result, "get") else None
            )

            q_results: Dict[str, Any] = {}
            q_results["time_elapsed"] = end - start
            q_results["question"] = question
            # reference_answer is "" for a draft row (empty reference). That raw
            # empty drives context-metric eligibility in dataset_result below, but
            # the human-facing result / Argilla record needs a non-empty value
            # (its reference_answer field is a required TextField), so store an
            # "N/A" sentinel for display while the ragas payload keeps the raw "".
            q_results["reference_answer"] = reference_answer or "N/A"
            q_results["answer"] = result["answer"]
            q_results["status"] = status
            q_results["messages"] = self.prepare_messages(result.get("messages", []))

            match_fields_list, formatted_reference_sources = (
                self._resolve_reference_match_fields(
                    question_item, reference_sources, modes_being_run
                )
            )
            q_results["reference_sources_match_fields"] = match_fields_list
            q_results["reference_sources_metadata"] = formatted_reference_sources

            # A degraded (context-overflow) answer must not be scored as a clean
            # success: skip source matching AND RAGAS input for it, so it neither
            # stamps `matched` onto its sources (Codex F4) nor feeds aggregates.
            scorable = status == OK

            matches = None
            if "SOURCES" in modes_being_run and scorable:
                matches = self.get_source_results(result, formatted_reference_sources)
                for idx, source in enumerate(q_results["reference_sources_metadata"]):
                    source["matched"] = matches[idx]

            sources_metadata: List[Dict[str, Any]] = []
            sources_trunc_content: List[str] = []
            for document in result["source_documents"]:
                metadata = getattr(document, "metadata", {}) or {}
                sources_metadata.append(metadata)
                sources_trunc_content.append(
                    getattr(document, "page_content", "")[:300]
                )
            q_results["sources_metadata"] = sources_metadata
            q_results["sources_trunc_content"] = sources_trunc_content
            q_results["anchor_type"] = (
                question_item.get("anchor_type", "")
                if isinstance(question_item, dict)
                else ""
            )

            dataset_result = None
            if "RAGAS" in modes_being_run and scorable:
                contexts = [s.page_content for s in result["source_documents"]]
                # ragas 0.3.5 modern dialect: the agent's answer is `response`;
                # the bank's ground-truth answer is `reference` (never `response`).
                dataset_result = {
                    "user_input": question,
                    "retrieved_contexts": contexts,
                    "response": result["answer"],
                    "reference": reference_answer,
                }

            return {
                "q_results": q_results,
                "dataset_result": dataset_result,
                "matches": matches,
            }
        except Exception as exc:  # isolate: one question must not abort the run
            logger.error(
                "Question %s failed; recording a failure entry and continuing: %s",
                question_id,
                exc,
            )
            return {
                "q_results": build_failure_entry(
                    question=question,
                    reference_answer=reference_answer,
                    error=exc,
                ),
                "dataset_result": None,
                "matches": None,
            }

    def run(self):
        ingest_wall_seconds = self.wait_for_ingestion_completion()

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
            # Read the corpus BEFORE the arm's questions, so the report can show
            # whether they were all scored against the same documents.
            corpus_before = ResultHandler.get_corpus_fingerprint()
            question_wise_results, total_results = self._process_config(modes_being_run)
            ResultHandler.handle_results(
                Path(self.current_config),
                question_wise_results,
                total_results,
                corpus_before=corpus_before,
                # The chain's own snapshot, taken by archi.__init__ before these
                # questions ran -- not a fresh query, which would report the
                # config as it stands now rather than as the arm used it.
                running_config=getattr(self.chain, "config", None),
                # Measured once, before the sweep, and stamped on every arm:
                # all arms of one invocation share the corpus this built.
                ingest_wall_seconds=ingest_wall_seconds,
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
                    ResultHandler.ab_summary_line(
                        name_a, name_b, len(paired), comp["aggregate"]
                    )
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
                "  %-4s %-28s %-10s %-10s %-10s %-10s %-10s %-10s %s",
                "rank",
                "name",
                "ans_rel",
                "faith",
                "ctx_prec",
                "ctx_rec",
                "ans_corr",
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
                    "  %-4d %-28s %-12s %-12s %-12s %-12s %-12s %-10d %s%s",
                    row["rank"],
                    row["name"][:28],
                    _fmt("answer_relevancy"),
                    _fmt("faithfulness"),
                    _fmt("context_precision"),
                    _fmt("context_recall"),
                    _fmt("answer_correctness"),
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

        ResultHandler.dump_artifacts(self.benchmark_name)
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

        # The anchor file is a load path separate from queries_path; normalize it
        # onto the modern dialect so migrated (or legacy) anchors dedup and merge
        # on `user_input` rather than being silently skipped (Codex #93 F1).
        anchors = normalize_bank(anchors)

        existing_questions = {
            q.get("user_input")
            for q in self.queries_to_answers
            if isinstance(q, dict) and q.get("user_input")
        }
        merged = list(self.queries_to_answers)
        added = 0
        for a in anchors:
            if not isinstance(a, dict) or not a.get("user_input"):
                continue
            if a["user_input"] in existing_questions:
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

    def wait_for_ingestion_completion(
        self,
        *,
        fetch: Optional[Callable[[str], Dict[str, Any]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Optional[float]:
        """Block until the data-manager reports ingestion complete.

        Returns the wall-clock seconds this ingest was observed working, or
        `None` when it was never observed working at all -- the run found the
        corpus already built. Never `0.0`: that would put a fabricated
        measurement where "not measured" belongs (issue #417).

        The span runs from the first poll `_ingest_is_progressing` accepts to
        the one reporting `completed`, so queue time behind another holder of
        `ingestion_lock` is excluded but everything after work starts is
        included. It is still harness-observed and therefore a ceiling on the
        ingest proper; exact phase timing needs `started_at`/`finished_at` in
        the status payload, which is a data-manager change.

        `fetch`, `clock` and `sleep` are injection seams for the tests only;
        production calls this with no arguments.

        Neither bound on this wait is a total-runtime deadline on a *healthy*
        ingest. `BENCH_INGEST_WAIT_TIMEOUT` is a **stall** budget: it restarts
        on every poll reporting `state=running`, so an ingest that keeps
        reporting progress is never killed merely for being slow. That was
        issue #378 -- a 106-minute embedding phase aborted at exactly 7200s
        while all 1433 of its status polls were succeeding, two minutes short
        of finishing. `BENCH_INGEST_MAX_WAIT` is the absolute backstop for the
        other failure: an ingest that reports progress forever without ever
        completing.

        Two judgement calls, both deliberate:

        - Restart on any *running* poll, not on a **changing `step`**.
          `data_manager.py:108-109` emits "Updating vectorstore" once for the
          whole embedding phase, so the step string is constant for hours on a
          healthy run; a step-change rule would kill exactly the runs this
          exists to protect.
        - Restart on *progress* only, not on any **answered** poll --
          `_ingest_is_progressing` decides. An endpoint stuck at `pending`, or
          at `running`/`initializing` because this ingest is queued behind
          another holder of `ingestion_lock`, is answering happily while
          nothing of ours is happening; the stall budget must end those,
          exactly as the old absolute deadline did.
        """
        budgets = _ingest_wait_budgets()
        fetch = fetch or _fetch_ingestion_status
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
        start_time = clock()
        last_ok_at = start_time
        last_ok_url: Optional[str] = None
        last_state: Optional[str] = None
        last_step: Any = None
        last_error: Optional[BaseException] = None
        # When this ingest was first seen actually working -- NOT when the
        # waiting started. A run queued behind another holder of
        # `ingestion_lock` sits at `running`/`initializing` while nothing of its
        # own happens, and charging that queue time to the corpus would make the
        # campaign's cost table depend on what else the data-manager was doing.
        # Still None at the completed poll = no ingest was observed at all (#417).
        ingest_started_at: Optional[float] = None
        attempt = 0

        logger.info(
            "Waiting for data-manager ingestion to complete before benchmarking..."
        )
        if not budgets.max_wait_seconds:
            # The status payload carries no progress counter (only state/step,
            # `ingestion_status.py:29-33`), so an ingest wedged *inside*
            # `update_vectorstore()` still answers "running" forever and only
            # the ceiling can end it. Disabling the ceiling is a legitimate
            # choice for a corpus larger than the default 6h -- but an
            # unattended run that hangs silently burns its allocation, so it
            # must not be a quiet one.
            logger.warning(
                "BENCH_INGEST_MAX_WAIT=0: no absolute ceiling on this wait. An "
                "ingest that wedges while still reporting state=running will "
                "block the benchmark indefinitely."
            )
        while True:
            attempt += 1
            for status_url in status_urls:
                try:
                    payload = fetch(status_url)
                except (
                    url_error.URLError,
                    TimeoutError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    last_error = exc
                    continue

                state = str(payload.get("state", "")).lower()
                step = payload.get("step")
                logger.info(
                    "Ingestion status check #%s via %s -> state=%s step=%s",
                    attempt,
                    status_url,
                    state,
                    step,
                )
                # This URL answered, so whatever an earlier candidate raised is
                # no longer the reason for anything -- drop it (defect 2).
                # Reachability facts update on any answer; the stall budget
                # restarts only on evidence of actual progress.
                last_error = None
                last_ok_url = status_url
                last_state = state
                last_step = step
                if _ingest_is_progressing(state, step):
                    last_ok_at = clock()
                    if ingest_started_at is None:
                        ingest_started_at = last_ok_at

                if state == "completed":
                    logger.info("Data-manager ingestion completed; starting benchmark.")
                    if ingest_started_at is None:
                        return None
                    return clock() - ingest_started_at
                if state == "error":
                    raise RuntimeError(
                        f"Data-manager ingestion failed at step '{step}': "
                        f"{payload.get('error')}"
                    )
                break

            now = clock()
            stalled_for = now - last_ok_at
            elapsed = now - start_time
            if stalled_for >= budgets.stall_seconds:
                raise TimeoutError(
                    _ingest_wait_timeout_message(
                        f"no progress reported for {stalled_for:.0f}s "
                        f"(BENCH_INGEST_WAIT_TIMEOUT={budgets.stall_seconds}s; "
                        "the budget restarts whenever the ingest reports "
                        "progress, never on total runtime)",
                        candidate_urls=status_urls,
                        last_ok_url=last_ok_url,
                        last_state=last_state,
                        last_step=last_step,
                        last_error=last_error,
                    )
                )
            if budgets.max_wait_seconds and elapsed >= budgets.max_wait_seconds:
                raise TimeoutError(
                    _ingest_wait_timeout_message(
                        f"still not complete after {elapsed:.0f}s "
                        f"(BENCH_INGEST_MAX_WAIT={budgets.max_wait_seconds}s)",
                        candidate_urls=status_urls,
                        last_ok_url=last_ok_url,
                        last_state=last_state,
                        last_step=last_step,
                        last_error=last_error,
                    )
                )

            sleep(budgets.poll_interval_seconds)


if __name__ == "__main__":

    _init_runtime()

    query_file = Path("QandA.txt")
    configs_folder = Path("configs")

    with open(Path(query_file), "r") as f:
        question_to_answer = json.load(f)

    benchmarker = Benchmarker(configs_folder, question_to_answer)
    benchmarker.run()
    logger.info("\n\nFINISHED RUNNING\n\n")
