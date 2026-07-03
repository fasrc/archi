"""Regression tests for the request-time provider-config override path.

`_build_provider_config_from_payload` builds the ProviderConfig used when a chat
request overrides the pipeline LLM (provider + model in the request, as the UI
dropdown always sends). It MUST preserve the config's ``extra_kwargs`` — dropping
them silently strips ``extra_body.chat_template_kwargs.enable_thinking`` from the
overridden LLM, so Qwen runs in thinking mode and chain-of-thought bleeds into
answers.
"""

from src.archi.providers.base import ProviderType
from src.interfaces.chat_app.app import (
    _build_provider_config_from_payload,
    _restore_pipeline_llm,
    _swap_pipeline_llm,
)


class _FakePipeline:
    """Minimal stand-in for a ReAct pipeline for override swap/restore tests."""

    def __init__(self, llm):
        self.agent_llm = llm
        self.refreshed = 0

    def refresh_agent(self, force=False):
        self.refreshed += 1


def _cfg(extra_kwargs):
    return {
        "services": {
            "chat_app": {
                "providers": {
                    "local": {
                        "base_url": "http://localhost:8001/v1",
                        "mode": "openai_compat",
                        "default_model": "m",
                        "models": ["m"],
                        "extra_kwargs": extra_kwargs,
                    }
                }
            }
        }
    }


def test_override_provider_config_preserves_extra_kwargs():
    ek = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    pc = _build_provider_config_from_payload(_cfg(ek), ProviderType.LOCAL)
    assert pc is not None
    # the thinking flag must survive the override path
    assert pc.extra_kwargs.get("extra_body") == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    # local_mode is still derived from `mode`
    assert pc.extra_kwargs.get("local_mode") == "openai_compat"


def test_override_provider_config_no_extra_kwargs_still_sets_local_mode():
    pc = _build_provider_config_from_payload(_cfg({}), ProviderType.LOCAL)
    assert pc is not None
    assert pc.extra_kwargs == {"local_mode": "openai_compat"}


def test_swap_pipeline_llm_returns_original_and_refreshes():
    pipeline = _FakePipeline("orig-llm")
    original = _swap_pipeline_llm(pipeline, "override-llm")
    assert original == "orig-llm"
    assert pipeline.agent_llm == "override-llm"
    assert pipeline.refreshed == 1


def test_restore_pipeline_llm_undoes_swap():
    pipeline = _FakePipeline("orig-llm")
    original = _swap_pipeline_llm(pipeline, "override-llm")
    _restore_pipeline_llm(pipeline, original)
    # the shared pipeline is back to its original LLM — no cross-request bleed
    assert pipeline.agent_llm == "orig-llm"
    assert pipeline.refreshed == 2


def test_override_provider_config_does_not_mutate_source():
    ek = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    cfg = _cfg(ek)
    _build_provider_config_from_payload(cfg, ProviderType.LOCAL)
    # building the config must not inject local_mode back into the source dict
    src_extra = cfg["services"]["chat_app"]["providers"]["local"]["extra_kwargs"]
    assert "local_mode" not in src_extra
