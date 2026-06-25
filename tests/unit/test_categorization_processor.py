"""Tests for CategorizationProcessor (optional LLM document categorization)."""

from types import SimpleNamespace

import pytest

# Import the real langchain message classes at module load so they are pinned in
# sys.modules before sibling test modules (e.g. the vectorstore tests) install bare
# langchain_core stubs that would otherwise shadow langchain_core.messages. This keeps
# CategorizationProcessor._build_messages on the real-message-object path under the
# full suite, exactly as in production.
from langchain_core.messages import HumanMessage, SystemMessage

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
    factory_calls = {"count": 0}

    def factory(p, m, cfg):
        factory_calls["args"] = (p, m, cfg)
        factory_calls["count"] += 1
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


def test_reasoning_model_trailing_label_is_extracted():
    """A reasoning model that thinks out loud and ends with the bare category on
    the final non-empty line is still categorized correctly. The exact-match-on-
    whole-content check would mark this 'uncategorized' even though the model
    answered correctly."""
    reply = (
        "The user wants me to classify this document.\n"
        "It discusses disk quotas and Lustre filesystems, so it is about storage.\n"
        "I will output only the category name.\n\n"
        "storage"
    )
    model = _FakeChatModel(reply=reply)
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "storage"


def test_reasoning_model_prefixed_final_line_is_extracted():
    """A final line like 'Category: policy' (label preceded by a prefix word)
    resolves to the category token it contains."""
    reply = "Let me reason about this.\nThis is governance text.\nCategory: policy"
    model = _FakeChatModel(reply=reply)
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "policy"


def test_label_match_is_case_insensitive_returns_canonical():
    """Reasoning models often capitalize ('Storage'); match case-insensitively but
    write the canonical category as configured."""
    model = _FakeChatModel(reply="Storage")
    proc, _ = _make(model=model)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "storage"


def test_reasoning_without_any_valid_category_stays_uncategorized():
    """Tail parsing must not invent a label: reasoning whose final answer is not in
    the list still yields uncategorized (no false positives)."""
    reply = "I think this document is about cooking recipes.\nFinal answer: cooking"
    model = _FakeChatModel(reply=reply)
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


def test_no_provider_or_model_defaults_uncategorized_without_factory_call():
    """A categorizer built with no provider/model never calls the factory and
    yields uncategorized — the model is not constructed."""
    model = _FakeChatModel(reply="compute")
    proc, calls = _make(model=model, provider=None, model_name=None)
    out = proc.process(_resource())
    assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"
    assert calls["count"] == 0


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
    # Messages are langchain message objects; the document content is in the human turn.
    sent = "".join(str(m.content) for m in model.last_messages)
    assert "x" * 10 in sent
    assert "x" * 11 not in sent


def test_messages_are_langchain_message_objects():
    """The model is invoked with SystemMessage/HumanMessage objects (matching
    base_react.py), not role/content tuples that some chat models don't honor."""
    model = _FakeChatModel(reply="compute")
    proc, _ = _make(model=model)
    proc.process(_resource(content="body"))

    messages = model.last_messages
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "body" in str(messages[-1].content)


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


def test_chat_model_is_built_once_across_documents():
    """The chat model is built lazily and reused — NOT rebuilt per document. This
    protects the per-document cost guarantee on large crawls."""
    model = _FakeChatModel(reply="compute")
    proc, calls = _make(model=model)

    for _ in range(5):
        proc.process(_resource())

    assert calls["count"] == 1


def test_chat_model_build_failure_is_not_retried_per_document():
    """A model that fails to build is recorded as failed and not rebuilt on every
    subsequent document (avoids hammering a broken endpoint per doc)."""
    proc, calls = _make(model=None)  # factory raises -> build fails

    for _ in range(4):
        out = proc.process(_resource())
        assert out.get_metadata().as_dict()["llm_category"] == "uncategorized"

    assert calls["count"] == 1
