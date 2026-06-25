"""Tests for build_persistence (shared factory wiring the processing pipeline)."""

from pathlib import Path

from src.data_manager.collectors.processing import (
    CategorizationProcessor,
    HtmlToMarkdownProcessor,
    ProcessingPersistenceService,
    build_persistence,
)


class _FakePersistence:
    """Stand-in for PersistenceService that does not touch Postgres."""

    def __init__(self, data_path, *, pg_config):
        self.data_path = Path(data_path)
        self.pg_config = pg_config


def _factory(data_path, *, pg_config):
    return _FakePersistence(data_path, pg_config=pg_config)


def _build(processing=None, providers=None):
    config = {
        "data_manager": {},
        "services": {"chat_app": {"providers": providers or {}}},
    }
    if processing is not None:
        config["data_manager"]["processing"] = processing
    return build_persistence(
        config, "/data", {"host": "db"}, persistence_factory=_factory
    )


def test_missing_block_converts_and_skips_categorization():
    service = _build(processing=None)
    assert isinstance(service, ProcessingPersistenceService)
    processors = service._pipeline.processors
    assert any(isinstance(p, HtmlToMarkdownProcessor) for p in processors)
    assert not any(isinstance(p, CategorizationProcessor) for p in processors)


def test_all_disabled_returns_bare_service():
    service = _build(
        processing={
            "html_to_markdown": {"enabled": False},
            "categorization": {"enabled": False},
        }
    )
    assert isinstance(service, _FakePersistence)
    assert not isinstance(service, ProcessingPersistenceService)


def test_categorization_enabled_adds_processor():
    service = _build(
        processing={
            "html_to_markdown": {"enabled": True},
            "categorization": {
                "enabled": True,
                "provider": "local",
                "model": "qwen",
                "categories": ["compute", "storage"],
                "max_chars": 2000,
            },
        },
        providers={
            "local": {
                "base_url": "http://vllm:8001",
                # openai_compat is the value the local provider routes a vLLM
                # endpoint on (see local_provider.py); it is what
                # deploy/fasrc-dev/config.yaml uses. 'vllm' is NOT a routing value
                # and would silently fall back to the Ollama default.
                "mode": "openai_compat",
                "models": ["qwen"],
                "extra_kwargs": {"temperature": 0},
            }
        },
    )
    assert isinstance(service, ProcessingPersistenceService)
    cat = [
        p
        for p in service._pipeline.processors
        if isinstance(p, CategorizationProcessor)
    ]
    assert len(cat) == 1
    processor = cat[0]
    assert processor.categories == ["compute", "storage"]
    assert processor.max_chars == 2000
    # provider_config is sourced from services.chat_app.providers.<provider>
    assert processor.provider_config["base_url"] == "http://vllm:8001"
    assert processor.provider_config["extra_kwargs"]["local_mode"] == "openai_compat"


def test_categorization_enabled_but_provider_missing_skips_categorizer():
    """Fail loud, not silent: when the configured provider is absent from
    services.chat_app.providers, the categorizer is NOT built (an empty config
    would default the local provider to localhost:11434 and mislabel every doc).
    Conversion still runs so ingest proceeds."""
    service = _build(
        processing={
            "html_to_markdown": {"enabled": True},
            "categorization": {
                "enabled": True,
                "provider": "local",  # not present in providers below
                "model": "qwen",
                "categories": ["compute"],
            },
        },
        providers={},  # no 'local' provider configured
    )
    # Still wrapped (conversion runs), but no categorizer.
    assert isinstance(service, ProcessingPersistenceService)
    processors = service._pipeline.processors
    assert any(isinstance(p, HtmlToMarkdownProcessor) for p in processors)
    assert not any(isinstance(p, CategorizationProcessor) for p in processors)


def test_categorization_missing_provider_with_conversion_off_yields_bare_service():
    """If conversion is also off and the provider is missing, nothing is added and
    the bare (unwrapped) service is returned — a true no-op."""
    service = _build(
        processing={
            "html_to_markdown": {"enabled": False},
            "categorization": {
                "enabled": True,
                "provider": "local",
                "model": "qwen",
                "categories": ["compute"],
            },
        },
        providers={},
    )
    assert isinstance(service, _FakePersistence)
    assert not isinstance(service, ProcessingPersistenceService)


def test_conversion_only_when_categorization_disabled():
    service = _build(
        processing={
            "html_to_markdown": {"enabled": True},
            "categorization": {"enabled": False},
        }
    )
    processors = service._pipeline.processors
    assert any(isinstance(p, HtmlToMarkdownProcessor) for p in processors)
    assert not any(isinstance(p, CategorizationProcessor) for p in processors)
