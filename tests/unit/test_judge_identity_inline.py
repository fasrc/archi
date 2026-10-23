"""The inline path records the extended judge identity (#732, D10, task 3.6a).

``_ragas_judge_identity`` keeps #618's four fields (the characterization tests pin
it); the extension is a separate record that ``run()`` merges in when it passes
the identity to ``handle_results``.
"""

from __future__ import annotations

import types

import pandas as pd

from src.bin.service_benchmark import Benchmarker, ResultHandler
from src.utils.judge_identity import IDENTITY_FIELDS, inline_scorer_identity
from tests.unit.test_ragas_scoring_characterization import (
    _install_ragas_stub,
    _make_bench,
)

_SCORER = {
    "code_digest": "sha256:code",
    "package_digest": "sha256:pkg",
    "image_digest": None,
    "ragas_version": "0.3.5",
}


class OpenAIEmbeddings:
    model = "text-embedding-ada-002"


def _score_one_metric(monkeypatch):
    def _evaluate(dataset, metrics=None, **kwargs):
        return types.SimpleNamespace(
            to_pandas=lambda: pd.DataFrame({metrics[0].name: [0.5] * len(dataset)})
        )

    _install_ragas_stub(monkeypatch, _evaluate)
    monkeypatch.setattr(
        "src.bin.service_benchmark.ChatOpenAI",
        lambda **kw: types.SimpleNamespace(temperature=0.0, top_p=None, max_tokens=None),
    )
    monkeypatch.setattr("src.bin.service_benchmark.OpenAIEmbeddings", OpenAIEmbeddings)
    monkeypatch.setattr(
        "src.bin.service_benchmark.inline_scorer_identity",
        lambda package_dir: dict(_SCORER),
    )
    bench = _make_bench(["answer_relevancy"])
    rows = [
        {"user_input": "q", "retrieved_contexts": ["c"], "response": "a", "reference": ""}
    ]
    bench.get_ragas_results(rows, ["question_1"], {"question_1": {}})
    return bench


def test_the_four_field_identity_is_unchanged_after_scoring(monkeypatch):
    bench = _score_one_metric(monkeypatch)
    assert set(bench._ragas_judge_identity) == {"provider", "model", "base_url", "mode"}


def test_the_recorded_inline_identity_has_the_extended_shape(monkeypatch):
    bench = _score_one_metric(monkeypatch)
    identity = bench._recorded_judge_identity()

    assert set(identity) == {"provider", "model", "base_url", "mode", *IDENTITY_FIELDS}
    assert identity["provider"] == "openai"
    # An API judge has no revision, precision, image or server settings.
    for field in ("model_revision", "precision", "server_image_digest", "server_args"):
        assert identity[field] is None
    assert identity["chunk_size"] is None
    assert identity["timeout"] == 120 and identity["max_workers"] == 16
    assert identity["batch_size"] is None
    assert identity["decoding"] == {"temperature": 0.0, "top_p": None, "max_tokens": None}
    assert identity["embedding"] == {
        "class": "OpenAIEmbeddings",
        "model": "text-embedding-ada-002",
        "revision": None,
    }
    assert identity["metrics"] == ["answer_relevancy"]
    assert identity["scorer"] == _SCORER


def test_with_no_judge_built_the_recorded_identity_is_the_base():
    bench = object.__new__(Benchmarker)
    assert bench._recorded_judge_identity() is None
    bench._ragas_judge_identity = {"provider": "openai"}
    assert bench._recorded_judge_identity() == {"provider": "openai"}


def test_the_scorer_identity_is_computed_once_per_run(monkeypatch):
    calls = []

    def fake(package_dir):
        calls.append(package_dir)
        return dict(_SCORER)

    bench = _score_one_metric(monkeypatch)
    monkeypatch.setattr("src.bin.service_benchmark.inline_scorer_identity", fake)
    bench._recorded_judge_identity()
    bench._recorded_judge_identity()
    assert len(calls) == 1


def test_run_passes_the_extended_identity_to_handle_results(monkeypatch):
    calls = []
    base = {"provider": "openai", "model": "gpt-4o", "base_url": None, "mode": None}
    fields = {
        "model_revision": None,
        "precision": None,
        "server_image_digest": None,
        "server_args": None,
        "decoding": {},
        "run_config": {"timeout": 180, "max_workers": 16},
        "batch_size": None,
        "chunk_size": None,
        "embedding": {},
        "metrics": ["faithfulness"],
    }

    class _RunStub(Benchmarker):
        def __init__(self):
            self.benchmarking_configs = {"modes": ["RAGAS"]}
            self.benchmark_name = "t"
            self.queries_to_answers = []
            self.all_config_files = ["a.yaml", "b.yaml"]
            self.current_config = "a.yaml"
            self.chain = None
            self.config = {}

        def wait_for_ingestion_completion(self):
            return 0.0

        def _merge_anchor_questions(self):
            return None

        def _process_config(self, modes_being_run):
            assert self._ragas_judge_extension is None
            self._ragas_judge_identity = dict(base)
            self._ragas_judge_extension = dict(fields)
            return {}, {}

        def load_new_configuration(self):
            self.all_config_files.pop(0)
            if self.all_config_files:
                self.current_config = self.all_config_files[0]

    monkeypatch.setattr(ResultHandler, "check_collection", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_corpus_fingerprint", lambda cfg: None)
    monkeypatch.setattr(ResultHandler, "get_category_map", lambda cfg: (None, None))
    monkeypatch.setattr(
        ResultHandler,
        "handle_results",
        lambda *args, **kwargs: calls.append(kwargs["judge_identity"]),
    )
    monkeypatch.setattr(ResultHandler, "add_metadata", lambda: None)
    monkeypatch.setattr(ResultHandler, "dump_artifacts", lambda name, **kwargs: None)
    monkeypatch.setattr(ResultHandler, "results", [])
    monkeypatch.setattr(
        "src.bin.service_benchmark.inline_scorer_identity",
        lambda package_dir: dict(_SCORER),
    )
    monkeypatch.delenv("ARCHI_ARGILLA", raising=False)

    _RunStub().run()

    assert len(calls) == 2
    for identity in calls:
        assert identity["scorer"] == _SCORER
        assert identity["timeout"] == 180
        assert identity["provider"] == "openai"


def test_the_inline_scorer_identity_never_raises(tmp_path):
    identity = inline_scorer_identity(
        str(tmp_path / "missing"),
        package_digest_fn=lambda: {"digest": None, "source": "<unavailable: x>"},
    )
    assert identity["code_digest"] is None
    assert identity["package_digest"] is None
    assert identity["image_digest"] is None
