"""The extended judge identity (design D10, task 3.6a) and the scorer identity.

``ragas_judge_identity_effective`` keeps #618's four fields and adds the fields
that change scores or score coverage. Per-job values (job id, node, times) are
not in it; they go to ``judge_execution``.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

from src.utils.benchmark_provenance import collect_code_version
from src.utils.benchmark_schema import ragas_run_config_kwargs
from src.utils.judge_identity import (
    IDENTITY_FIELDS,
    canonical_identity,
    compute_scorer_identity,
    describe_decoding,
    describe_embedding,
    differing_fields,
    extended_identity,
    write_scorer_identity,
)

QUEUE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "judge"
    / "judge_queue.py"
)


class OpenAIEmbeddings:
    def __init__(self, model="text-embedding-ada-002"):
        self.model = model


class HuggingFaceEmbeddings:
    def __init__(self, model_name="sentence-transformers/all-mpnet-base-v2"):
        self.model_name = model_name
        self.model_kwargs = {"revision": "e8c3b32"}


def _identity(**overrides):
    values = dict(
        model_revision="rev1",
        precision="bf16",
        server_image_digest="sha256:vllm",
        server_args="--max-model-len 32768",
        decoding={"temperature": 0.0, "top_p": None, "max_tokens": None},
        run_config=ragas_run_config_kwargs({}),
        batch_size=None,
        chunk_size=8,
        embedding=describe_embedding(OpenAIEmbeddings()),
        metrics=["answer_relevancy", "faithfulness"],
        scorer={"code_digest": "sha256:c", "package_digest": "sha256:p"},
    )
    values.update(overrides)
    base = {"provider": "vllm", "model": "llama", "base_url": None, "mode": "x"}
    return extended_identity(base, **values)


def test_the_extended_identity_keeps_the_four_fields_and_adds_the_d10_fields():
    identity = _identity()
    assert set(identity) == {"provider", "model", "base_url", "mode", *IDENTITY_FIELDS}
    assert identity["timeout"] == 180 and identity["max_workers"] == 16


def test_two_jobs_with_the_same_settings_give_equal_identities():
    assert canonical_identity(_identity()) == canonical_identity(_identity())


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"precision": "fp8"}, "precision"),
        ({"embedding": describe_embedding(HuggingFaceEmbeddings())}, "embedding"),
        (
            {"embedding": describe_embedding(OpenAIEmbeddings("text-embedding-3"))},
            "embedding",
        ),
        ({"run_config": ragas_run_config_kwargs({"timeout": 600})}, "timeout"),
        ({"run_config": ragas_run_config_kwargs({"max_workers": 6})}, "max_workers"),
        ({"batch_size": 4}, "batch_size"),
        ({"chunk_size": 16}, "chunk_size"),
        (
            {"scorer": {"code_digest": "sha256:c2", "package_digest": "sha256:p"}},
            "scorer",
        ),
    ],
)
def test_a_change_of_a_d10_field_changes_the_identity(overrides, field):
    assert differing_fields([_identity(), _identity(**overrides)]) == [field]


def test_an_explicit_default_equals_an_unset_value():
    explicit = _identity(run_config=ragas_run_config_kwargs({"timeout": 180.0}))
    assert canonical_identity(explicit) == canonical_identity(_identity())


def test_the_log_setting_is_not_part_of_the_identity():
    quiet = _identity(run_config=ragas_run_config_kwargs({}, verbosity=0))
    loud = _identity(run_config=ragas_run_config_kwargs({}, verbosity=4))
    assert canonical_identity(quiet) == canonical_identity(loud)


def test_decoding_is_read_from_the_built_object():
    llm = types.SimpleNamespace(temperature=0.0, top_p=0.9, max_tokens=512)
    assert describe_decoding(llm) == {
        "temperature": 0.0,
        "top_p": 0.9,
        "max_tokens": 512,
    }


def test_decoding_keeps_only_json_scalars():
    llm = types.SimpleNamespace(temperature=object(), top_p=None)
    assert describe_decoding(llm) == {
        "temperature": None,
        "top_p": None,
        "max_tokens": None,
    }


def test_the_embedding_is_read_from_the_built_object():
    assert describe_embedding(OpenAIEmbeddings()) == {
        "class": "OpenAIEmbeddings",
        "model": "text-embedding-ada-002",
        "revision": None,
        "endpoint": None,
    }
    assert describe_embedding(HuggingFaceEmbeddings()) == {
        "class": "HuggingFaceEmbeddings",
        "model": "sentence-transformers/all-mpnet-base-v2",
        "revision": "e8c3b32",
        "endpoint": None,
    }


def test_the_embedding_endpoint_is_part_of_the_identity():
    proxied = OpenAIEmbeddings()
    proxied.openai_api_base = "https://proxy.example/v1"
    assert describe_embedding(proxied)["endpoint"] == "https://proxy.example/v1"
    assert differing_fields(
        [
            _identity(embedding=describe_embedding(OpenAIEmbeddings())),
            _identity(embedding=describe_embedding(proxied)),
        ]
    ) == ["embedding"]


def test_the_embedding_endpoint_falls_back_to_the_built_client():
    """With no configured base, the openai client's own base URL is the endpoint."""
    built = OpenAIEmbeddings()
    built.openai_api_base = None
    built.client = types.SimpleNamespace(
        _client=types.SimpleNamespace(base_url="https://api.openai.com/v1/")
    )
    assert describe_embedding(built)["endpoint"] == "https://api.openai.com/v1/"


def test_the_real_openai_embeddings_report_their_endpoint(monkeypatch):
    from langchain_openai import OpenAIEmbeddings as RealOpenAIEmbeddings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    endpoint = describe_embedding(RealOpenAIEmbeddings())["endpoint"]
    assert endpoint.startswith("https://api.openai.com/v1")


def test_the_scorer_code_digest_agrees_with_the_benchmark_digest(tmp_path):
    """Task 3.0: the scorer image and the benchmark use one function for one src."""
    src = tmp_path / "src"
    (src / "utils").mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "utils" / "ragas_scoring.py").write_text("x = 1\n")
    identity = compute_scorer_identity(
        str(src),
        image_digest="sha256:image",
        package_digest_fn=lambda: {"digest": "sha256:pkg", "source": "pip freeze"},
    )
    assert identity["code_digest"] == collect_code_version(str(src), None)["digest"]
    assert identity["package_digest"] == "sha256:pkg"
    assert identity["image_digest"] == "sha256:image"
    assert "ragas_version" in identity


def test_the_scorer_identity_refuses_an_unavailable_package_digest(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(ValueError, match="package digest"):
        compute_scorer_identity(
            str(tmp_path),
            package_digest_fn=lambda: {"digest": None, "source": "<unavailable: x>"},
        )


def test_the_scorer_identity_refuses_an_unavailable_code_digest(tmp_path):
    with pytest.raises(ValueError, match="code digest"):
        compute_scorer_identity(
            str(tmp_path / "empty"),
            package_digest_fn=lambda: {"digest": "sha256:pkg", "source": "pip"},
        )


def test_the_written_sidecar_is_sorted_json_the_queue_can_read(tmp_path):
    spec = importlib.util.spec_from_file_location("judge_queue_for_sidecar", QUEUE_PATH)
    queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(queue)

    identity = {"package_digest": "sha256:p", "code_digest": "sha256:c"}
    path = tmp_path / "scorer-identity.json"
    write_scorer_identity(path, identity)
    assert path.read_text() == json.dumps(identity, sort_keys=True, indent=2) + "\n"
    assert queue.read_sidecar(path).identity == identity
