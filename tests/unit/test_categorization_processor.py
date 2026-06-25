"""Tests for CategorizationProcessor (optional LLM document categorization)."""

from types import SimpleNamespace

import pytest

from src.data_manager.collectors.processing import CategorizationProcessor
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource


class _FakeChatModel:
    """Minimal stand-in for a LangChain BaseChatModel."""

    def __init__(self, reply="compute", raises=False):
        self._reply = reply
        self._raises = raises
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        if self._raises:
            raise RuntimeError("model exploded")
        return SimpleNamespace(content=self._reply)


def _resource(content="some body text", suffix="md", **kwargs):
    return ScrapedResource(
        url="https://example.com/doc",
        content=content,
        suffix=suffix,
        source_type="web",
        **kwargs,
    )


def _make(
    model=None,
    categories=("compute", "storage", "policy"),
    max_chars=1000,
    provider="local",
    model_name="qwen",
    provider_config=None,
):
    if provider_config is None:
        provider_config = {"base_url": "http://vllm:8001"}
    factory_calls = {}

    def factory(p, m, cfg):
        factory_calls["args"] = (p, m, cfg)
        if model is None:
            raise RuntimeError("no model configured")
        return model

    proc = CategorizationProcessor(
        categories=list(categories),
        provider=provider,
        model=model_name,
        provider_config=provider_config,
        max_chars=max_chars,
        model_factory=factory,
    )
    return proc, factory_calls


def test_valid_in_list_label_assigned():
    model = _FakeChatModel(reply="compute")
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "compute"


def test_out_of_list_label_defaults_uncategorized():
    model = _FakeChatModel(reply="banana")
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"


def test_model_raises_defaults_uncategorized():
    model = _FakeChatModel(raises=True)
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"


def test_missing_model_defaults_uncategorized():
    proc, _ = _make(model=None)  # factory raises
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"


def test_empty_category_list_defaults_uncategorized_without_model_call():
    model = _FakeChatModel(reply="compute")
    proc, calls = _make(model=model, categories=[])
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"
    # Model must not be built when there are no categories to choose from.
    assert "args" not in calls
    assert model.last_messages is None


def test_content_truncated_to_max_chars():
    model = _FakeChatModel(reply="compute")
    proc, _ = _make(model=model, max_chars=10)
    proc.process(_resource(content="x" * 500))
    # Messages are (role, content) tuples; the document content is in the human turn.
    sent = "".join(content for _role, content in model.last_messages)
    assert "x" * 10 in sent
    assert "x" * 11 not in sent


def test_source_category_never_overwritten():
    model = _FakeChatModel(reply="compute")
    proc, _ = _make(model=model)
    resource = _resource()
    resource.metadata["category"] = "Indico Event Category"
    out = proc.process(resource)
    meta = out.get_metadata().as_dict()
    assert meta["category"] == "Indico Event Category"
    assert meta["llm_category"] == "compute"


def test_provider_config_passed_to_factory():
    model = _FakeChatModel(reply="storage")
    cfg = {"base_url": "http://custom:9000", "models": ["qwen"]}
    proc, calls = _make(
        model=model, provider="local", model_name="qwen", provider_config=cfg
    )
    proc.process(_resource())
    p, m, passed_cfg = calls["args"]
    assert p == "local"
    assert m == "qwen"
    assert passed_cfg == cfg
