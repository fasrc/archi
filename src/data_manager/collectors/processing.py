"""Per-document processing stage at the persistence seam.

This module wraps :class:`PersistenceService` with a configurable pipeline that
runs *before* a resource is written to disk:

1. ``HtmlToMarkdownProcessor`` converts scraped/web HTML to Markdown (so headings,
   lists, tables, and links survive into chunks) and rewrites the resource's suffix
   and path-bearing fields to ``.md``.
2. ``CategorizationProcessor`` optionally assigns an LLM-chosen label from a
   configured list, stored under ``metadata["llm_category"]``.

Both stages are best-effort: a failure never raises and never blocks ingest. When
all processors are disabled the wrapper behaves identically to the bare service.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from markdownify import markdownify

from src.data_manager.collectors.resource_base import BaseResource
from src.utils.logging import get_logger

logger = get_logger(__name__)

_HTML_SUFFIXES = {"html", "htm"}
UNCATEGORIZED = "uncategorized"

ModelFactory = Callable[[str, str, Dict[str, Any]], Any]


@runtime_checkable
class ResourceProcessor(Protocol):
    """Protocol for a single per-resource transformation step.

    Mirrors ``collectors/base.py``'s ``Collector`` protocol style. Implementations
    MUST return a resource (the same instance, possibly mutated, or the original on
    failure) and MUST NOT raise — ingest is never blocked on a processing failure.
    """

    def process(self, resource: BaseResource) -> BaseResource:
        """Transform ``resource`` and return it (or the original on failure)."""
        ...


class ResourcePipeline:
    """Runs an ordered list of :class:`ResourceProcessor` over a resource."""

    def __init__(self, processors: Optional[List[ResourceProcessor]] = None) -> None:
        self.processors: List[ResourceProcessor] = list(processors or [])

    def run(self, resource: BaseResource) -> BaseResource:
        for processor in self.processors:
            try:
                resource = processor.process(resource)
            except Exception as exc:  # pragma: no cover - processors guard internally
                logger.warning(
                    "Resource processor %s raised; keeping resource unchanged: %s",
                    type(processor).__name__,
                    exc,
                )
        return resource


class HtmlToMarkdownProcessor:
    """Convert a string-content HTML resource to Markdown before persistence.

    Guards on ``isinstance(content, str)`` and an ``html``/``htm`` suffix so only
    scraped/web HTML is touched; bytes content (local uploads, binaries) and other
    suffixes pass through untouched. On any failure OR a blank/whitespace-only
    conversion the ORIGINAL resource is returned, so persistence's empty-content
    guard cannot block ingest.
    """

    def process(self, resource: BaseResource) -> BaseResource:
        content = getattr(resource, "content", None)
        if not isinstance(content, str):
            return resource

        suffix = getattr(resource, "suffix", "")
        if not isinstance(suffix, str):
            return resource
        if suffix.lstrip(".").lower() not in _HTML_SUFFIXES:
            return resource

        try:
            markdown = markdownify(content, heading_style="ATX")
        except Exception as exc:
            logger.warning(
                "HTML->Markdown conversion failed for %s; keeping original HTML: %s",
                _resource_label(resource),
                exc,
            )
            return resource

        if not markdown or not markdown.strip():
            logger.warning(
                "HTML->Markdown conversion produced blank output for %s; keeping "
                "original HTML to avoid an empty-content persist error.",
                _resource_label(resource),
            )
            return resource

        resource.content = markdown
        resource.suffix = "md"
        _rewrite_path_field(resource, "file_name")
        _rewrite_path_field(resource, "relative_path")
        resource.set_metadata_field("converted_from", "html")
        return resource


def _rewrite_path_field(resource: BaseResource, field_name: str) -> None:
    """Rewrite a path-bearing field's HTML extension to ``.md`` if it is set."""
    value = getattr(resource, field_name, None)
    if not value or not isinstance(value, str):
        return
    lowered = value.lower()
    for suffix in _HTML_SUFFIXES:
        dotted = f".{suffix}"
        if lowered.endswith(dotted):
            setattr(resource, field_name, value[: -len(dotted)] + ".md")
            return


def _resource_label(resource: BaseResource) -> str:
    try:
        return resource.get_hash()
    except Exception:  # pragma: no cover - defensive
        return type(resource).__name__


def _default_model_factory(
    provider: str, model: str, provider_config: Dict[str, Any]
) -> Any:
    """Build a chat model via the provider layer.

    ``get_model`` is imported lazily (inside this function, not at module top) on
    purpose: ``src.archi.providers`` pulls ``langchain_core`` at import time, which
    is NOT a hard runtime dependency of the ingest/persistence path. Importing it at
    module load would make ``processing.py`` — and therefore the cheap, local
    HTML->Markdown conversion path and the whole persistence seam — unimportable
    wherever langchain is absent (e.g. the unit-test/CI environment). Deferring the
    import keeps the conversion-only path dependency-free; langchain is required only
    when categorization is actually enabled and the model is first built.
    """
    from src.archi.providers import get_model

    return get_model(provider, model, provider_config)


class CategorizationProcessor:
    """Assign an LLM-chosen category label to a resource (opt-in).

    The chat model is built lazily on first use via ``model_factory`` (default:
    the provider layer's ``get_model``), so a disabled categorizer is never
    constructed and a configured-but-unused one costs nothing. ``provider_config``
    MUST be sourced from ``services.chat_app.providers.<provider>`` (base_url/mode/
    models/extra_kwargs) so custom local/vLLM endpoints work.

    Any failure — model build error, ``invoke`` raise, an out-of-list label, or an
    empty category list — yields ``"uncategorized"`` and never raises. The result is
    written to ``metadata["llm_category"]``; a source-provided ``metadata["category"]``
    (e.g. the Indico scraper's) is never touched.
    """

    def __init__(
        self,
        *,
        categories: Sequence[str],
        provider: Optional[str],
        model: Optional[str],
        provider_config: Optional[Dict[str, Any]] = None,
        max_chars: int = 4000,
        model_factory: ModelFactory = _default_model_factory,
    ) -> None:
        self.categories: List[str] = [
            str(c) for c in (categories or []) if str(c).strip()
        ]
        self.provider = provider
        self.model = model
        self.provider_config = provider_config or {}
        self.max_chars = (
            max_chars if isinstance(max_chars, int) and max_chars > 0 else 4000
        )
        self._model_factory = model_factory
        self._chat_model: Any = None
        self._model_build_failed = False

    def process(self, resource: BaseResource) -> BaseResource:
        category = self._categorize(resource)
        resource.set_metadata_field("llm_category", category)
        return resource

    def _categorize(self, resource: BaseResource) -> str:
        if not self.categories:
            return UNCATEGORIZED

        chat_model = self._get_chat_model()
        if chat_model is None:
            return UNCATEGORIZED

        content = getattr(resource, "content", None)
        text = content if isinstance(content, str) else _coerce_text(content)
        if not text:
            return UNCATEGORIZED
        truncated = text[: self.max_chars]

        messages = self._build_messages(truncated)
        try:
            response = chat_model.invoke(messages)
        except Exception as exc:
            logger.warning(
                "Categorization model.invoke failed for %s; marking uncategorized: %s",
                _resource_label(resource),
                exc,
            )
            return UNCATEGORIZED

        label = _extract_label(response)
        if label in self.categories:
            return label
        logger.debug(
            "Categorization model returned out-of-list label %r for %s; marking "
            "uncategorized.",
            label,
            _resource_label(resource),
        )
        return UNCATEGORIZED

    def _get_chat_model(self) -> Any:
        if self._chat_model is not None or self._model_build_failed:
            return self._chat_model
        if not self.provider or not self.model:
            self._model_build_failed = True
            return None
        try:
            self._chat_model = self._model_factory(
                self.provider, self.model, self.provider_config
            )
        except Exception as exc:
            self._model_build_failed = True
            logger.warning(
                "Failed to build categorization chat model (%s/%s); categorization "
                "disabled for this run: %s",
                self.provider,
                self.model,
                exc,
            )
            return None
        return self._chat_model

    def _build_messages(self, content: str) -> List[Any]:
        category_list = ", ".join(self.categories)
        system = (
            "You are a document classifier. Choose exactly one category for the "
            "document from this list: "
            f"{category_list}. Respond with only the category name, nothing else."
        )
        human = f"Document:\n{content}\n\nCategory:"
        return [("system", system), ("human", human)]


class ProcessingPersistenceService:
    """Wrap a ``PersistenceService``: run the pipeline, then delegate.

    ``persist_resource`` is the only behavior-changing override — it transforms the
    resource through the pipeline and forwards all three positional args to the
    inner service. Every other method and attribute (``delete_resource``,
    ``delete_by_metadata_filter``, ``reset_directory``, ``flush_index``,
    ``catalog``, ``data_path``, ``pg_config``, ...) falls through to the inner
    instance via ``__getattr__``, so callers see an unchanged surface.
    """

    def __init__(self, inner: Any, pipeline: ResourcePipeline) -> None:
        # Set via the base attribute machinery so __getattr__ is not consulted.
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_pipeline", pipeline)

    def persist_resource(
        self, resource: BaseResource, target_dir: Any, overwrite: bool = False
    ) -> Any:
        processed = self._pipeline.run(resource)
        return self._inner.persist_resource(processed, target_dir, overwrite)

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not found normally; delegate to the inner svc.
        return getattr(object.__getattribute__(self, "_inner"), name)


def _resolve_provider_config(
    provider: Optional[str], providers_config: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Build provider_config from ``services.chat_app.providers.<provider>``.

    Mirrors ``base_react.py``'s ``_build_provider_config`` so a custom local/vLLM
    endpoint (base_url/mode/models/extra_kwargs) is honored rather than defaulting
    to the wrong server.

    Returns ``None`` (NOT ``{}``) when the named provider is missing or empty under
    ``services.chat_app.providers``. The caller MUST treat ``None`` as "provider not
    configured" and refuse to build the categorizer rather than fall through to an
    empty config — an empty config makes e.g. the LOCAL provider default to its
    built-in Ollama endpoint (``http://localhost:11434``), silently categorizing
    every document against the wrong server.
    """
    provider_key = provider.lower() if isinstance(provider, str) else str(provider)
    cfg = (
        providers_config.get(provider_key, {})
        if isinstance(providers_config, dict)
        else {}
    )
    if not isinstance(cfg, dict) or not cfg:
        return None

    extra = dict(cfg.get("extra_kwargs", {}) or {})
    mode = cfg.get("mode")
    if mode and "local_mode" not in extra:
        extra["local_mode"] = mode

    return {
        "base_url": cfg.get("base_url"),
        "models": cfg.get("models", []),
        "default_model": cfg.get("default_model"),
        "extra_kwargs": extra,
        "mode": mode,
    }


def build_persistence(
    config: Dict[str, Any],
    data_path: Any,
    pg_config: Dict[str, Any],
    *,
    persistence_factory: Optional[Callable[..., Any]] = None,
    model_factory: ModelFactory = _default_model_factory,
) -> Any:
    """Construct a (possibly wrapped) persistence service from config.

    ``data_manager.processing`` drives the pipeline:

    * ``html_to_markdown.enabled`` defaults **true** (cheap/local). A MISSING
      ``processing`` block therefore means conversion on / categorization off — the
      shipped default.
    * ``categorization.enabled`` defaults **false** (one LLM call per document).

    When every processor is disabled the bare ``PersistenceService`` is returned, so
    behavior is byte-for-byte identical to today. ``provider_config`` for
    categorization is sourced from ``services.chat_app.providers.<provider>``.
    """
    if persistence_factory is None:
        from src.data_manager.collectors.persistence import PersistenceService

        persistence_factory = PersistenceService

    inner = persistence_factory(data_path, pg_config=pg_config)

    dm_config = config.get("data_manager", {}) if isinstance(config, dict) else {}
    processing = dm_config.get("processing", {}) if isinstance(dm_config, dict) else {}
    if not isinstance(processing, dict):
        processing = {}

    html_cfg = processing.get("html_to_markdown", {}) or {}
    cat_cfg = processing.get("categorization", {}) or {}

    processors: List[ResourceProcessor] = []

    if bool(html_cfg.get("enabled", True)):
        processors.append(HtmlToMarkdownProcessor())

    if bool(cat_cfg.get("enabled", False)):
        services_cfg = config.get("services", {}) if isinstance(config, dict) else {}
        chat_cfg = (
            services_cfg.get("chat_app", {}) if isinstance(services_cfg, dict) else {}
        )
        providers_config = (
            chat_cfg.get("providers", {}) if isinstance(chat_cfg, dict) else {}
        )
        provider = cat_cfg.get("provider")
        provider_config = _resolve_provider_config(provider, providers_config)
        if provider_config is None:
            # Fail loud, not silent: categorization is enabled but the configured
            # provider is absent from services.chat_app.providers. Building the
            # categorizer with an empty config would make the provider fall back to
            # its built-in default endpoint (e.g. local -> http://localhost:11434),
            # silently marking every document "uncategorized" against the wrong
            # server. Skip the categorizer so conversion still runs and ingest
            # proceeds, but make the misconfiguration impossible to miss in logs.
            logger.warning(
                "data_manager.processing.categorization is ENABLED but its provider "
                "%r is not configured under services.chat_app.providers — skipping "
                "categorization (no llm_category will be written). Add the provider "
                "block (base_url/mode/models) to enable it.",
                provider,
            )
        else:
            processors.append(
                CategorizationProcessor(
                    categories=cat_cfg.get("categories", []) or [],
                    provider=provider,
                    model=cat_cfg.get("model"),
                    provider_config=provider_config,
                    max_chars=int(cat_cfg.get("max_chars", 4000) or 4000),
                    model_factory=model_factory,
                )
            )

    if not processors:
        return inner

    return ProcessingPersistenceService(inner, ResourcePipeline(processors))


def _coerce_text(content: Any) -> str:
    if isinstance(content, (bytes, bytearray)):
        try:
            return bytes(content).decode("utf-8", errors="ignore")
        except Exception:  # pragma: no cover - defensive
            return ""
    return ""


def _extract_label(response: Any) -> str:
    raw = getattr(response, "content", response)
    if not isinstance(raw, str):
        raw = str(raw)
    return raw.strip().strip(".").strip()
