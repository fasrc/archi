## ADDED Requirements

### Requirement: The benchmark RAGAS scoring keeps its results when it moves to a shared module
The benchmark SHALL score RAGAS metrics through one function in `src/utils/ragas_scoring.py`, which `Benchmarker.get_ragas_results` wraps, and for the same rows, settings and judge it SHALL return the same `aggregate_<metric>` and `<metric>_scored` values, write the same per-row scores onto the results, and set `_ragas_scored`, `_judge_usage` and `_ragas_judge_identity` to the same values as before the move.

`Benchmarker.get_ragas_llm_evaluator` and `get_ragas_embedding_model` become thin wrappers over builders in the same module. The judge and the embeddings are built before scoring, also when no metric has an eligible row.

#### Scenario: A stub judge scores fixed rows
- **WHEN** a stub judge returns fixed per-row scores and fixed token counts for an answer metric and a context metric over three rows, one of which has an empty `reference`
- **THEN** `get_ragas_results` returns the same aggregates and scored counts, the results hold the same per-row scores, `_ragas_scored` is `True`, and `_judge_usage` and `_ragas_judge_identity` equal the values recorded before the move

#### Scenario: No metric has an eligible row
- **WHEN** only a context metric is enabled and every row has an empty `reference`
- **THEN** the metric records `n/a`, ragas `evaluate` is never called, `_ragas_scored` is `False`, `_judge_usage` is `None`, and `_ragas_judge_identity` is set

#### Scenario: Existing tests patch the service module
- **WHEN** a test patches `src.bin.service_benchmark.ChatOpenAI`, `src.bin.service_benchmark.get_model` or `src.bin.service_benchmark.ragas_judge_effective_identity`, or replaces `get_ragas_llm_evaluator` on the instance
- **THEN** the replacement is used for the judge, as before the move

### Requirement: The shared judge builder can reach a judge through a Unix socket
The shared scoring module SHALL provide a judge builder that sends every request of the judge client through a given Unix socket path, with a bearer key read from a named environment variable (default `VLLM_API_KEY`), and the builder SHALL refuse, before it builds a client, when that variable is unset or empty.

The builder takes no key value as an argument, so the key cannot come from a command line or a config file. The inline benchmark path does not call this builder.

#### Scenario: A request goes through the socket with the key
- **WHEN** `VLLM_API_KEY` is `k-test` and the judge built for a socket path is invoked once
- **THEN** a server on that socket receives one `POST /v1/chat/completions` with the header `Authorization: Bearer k-test`, and the judge returns the server's content

#### Scenario: The key is absent
- **WHEN** `VLLM_API_KEY` is unset or empty
- **THEN** the builder raises an error that names the variable, and the server on the socket receives no request
