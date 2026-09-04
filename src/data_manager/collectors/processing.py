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

import re
import sys
import threading
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

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag
from markdownify import MarkdownConverter

from src.data_manager.collectors.resource_base import BaseResource
from src.utils.logging import get_logger

logger = get_logger(__name__)

_HTML_SUFFIXES = {"html", "htm"}
UNCATEGORIZED = "uncategorized"

# FASRC KB (Echo Knowledge Base) article-body landmarks. Every article page wraps a
# small body in category-filter nav (before) and a bookmark/tags/date footer (after).
# The body is bounded by a unique "Table of Contents" heading and a "Bookmarkable
# Links" section — or, when that is absent, the always-present "Last Updated" footer.
# Slicing between them drops the chrome; non-KB pages lack these and are left whole.
_KB_SLICE_START = "Table of Contents"
_KB_SLICE_END = "Bookmarkable Links"
_KB_SLICE_END_FALLBACK = "Last Updated"

# Echo-KB (EPKB) structural markers in the raw page HTML. The article-body slice is
# gated on these so an arbitrary non-KB page that merely contains "Table of Contents"
# and "Last Updated" is never truncated (per PR #97 review).
_ECHO_KB_MARKERS = ("eckb-breadcrumb-link", "eckb-article-toc", "eckb-article-content")

# Headroom for converting pathologically deep HTML (issue #40). markdownify parses
# with BeautifulSoup and converts recursively, so a tree nested thousands of levels
# deep overflows the default 1000-frame recursion limit. Merely raising the limit is
# not enough: deep Python recursion can overflow the C stack and segfault, so the
# conversion runs in a dedicated worker thread created with an enlarged stack while
# the (process-global) recursion limit is temporarily raised and then restored.
# Sized for the real worst case (~2000-deep KB pages, a few stack frames per level),
# with headroom — not pathologically large (a 256 MiB/thread stack can fail to allocate).
_CONVERSION_RECURSION_LIMIT = 16_000
_CONVERSION_STACK_SIZE = 64 * 1024 * 1024  # 64 MiB
# `sys.setrecursionlimit` and `threading.stack_size` are process-global; serialize the
# block that mutates+restores them so concurrent conversions (e.g. multiple uploader
# handlers) cannot interleave and leak/clobber each other's interpreter-wide settings.
_CONVERSION_LOCK = threading.Lock()

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


def _extract_html_title(html: str) -> str:
    """Best-available page title for citation text: <title> -> <h1> -> og:title.

    Returns a trimmed string, or "" when none is present. Never raises — a missing
    title must not block ingest (citation falls back to display_name downstream).
    """
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        if soup.title and soup.title.string and soup.title.string.strip():
            return soup.title.string.strip()
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()
    except Exception:
        logger.debug("Failed to extract HTML title", exc_info=True)
    return ""


def _extract_kb_category(html: str) -> Optional[str]:
    """Source category from an Echo-KB breadcrumb, or ``None``.

    KB pages render ``Home › <Category> › <Article>`` as a server-side breadcrumb
    (``span.eckb-breadcrumb-link``). Return the immediate category — the span between
    ``Home`` and the article title (``crumbs[-2]``), which yields the most specific
    parent for nested categories. Returns ``None`` for non-KB / crumbless pages and
    never raises — a missing category must not block ingest.
    """
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        crumbs = [
            s.get_text(strip=True)
            for s in soup.select("span.eckb-breadcrumb-link")
            if s.get_text(strip=True)
        ]
        if len(crumbs) >= 3:
            return crumbs[-2]
    except Exception:
        logger.debug("Failed to extract KB category", exc_info=True)
    return None


class HtmlTitleProcessor:
    """Capture a clean ``metadata["title"]`` from HTML before Markdown conversion.

    Guards on ``isinstance(content, str)`` and an ``html``/``htm`` suffix so only
    scraped/web HTML is touched. Must run BEFORE ``HtmlToMarkdownProcessor`` (which
    rewrites content to Markdown, stripping ``<title>``). Never overwrites a
    non-empty title already set by the selenium/SSO path or the PDF loader.
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

        existing = resource.get_metadata().as_dict().get("title")
        if isinstance(existing, str) and existing.strip():
            return resource

        resource.set_metadata_field("title", _extract_html_title(content))
        return resource


class HtmlCategoryProcessor:
    """Capture a source ``metadata["category"]`` from an HTML breadcrumb.

    Guards on ``isinstance(content, str)`` and an ``html``/``htm`` suffix so only
    scraped/web HTML is touched. Must run BEFORE ``HtmlToMarkdownProcessor`` (which
    rewrites content to Markdown, stripping the breadcrumb). Never overwrites a
    non-empty ``category`` already set by a source scraper (e.g. Indico), and writes
    nothing when no breadcrumb is found. Distinct from the LLM ``llm_category``.
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

        existing = resource.get_metadata().as_dict().get("category")
        if isinstance(existing, str) and existing.strip():
            return resource

        category = _extract_kb_category(content)
        if category:
            resource.set_metadata_field("category", category)
        return resource


def html_to_markdown(html: str) -> str:
    """Convert page HTML to the Markdown the ingest persists.

    The extraction rule as a **pure function**, so anything that needs to know
    what a page "says" measures it the same way the corpus was built. The golden
    set's drift pass hashes the text of a re-fetched page, and design D6's
    sign-off condition is exactly this: the live signal must come through the
    ingest's own extraction rather than a second implementation that could drift
    from it and turn a markup change into a phantom content change.

    Returns ``""`` when the conversion is blank — the caller decides what that
    means (persistence keeps the original HTML; drift refuses to hash it).
    Conversion failures propagate, so a caller can tell "converted to nothing"
    from "could not convert".
    """
    markdown = _markdownify_deep_safe(html)
    if not markdown or not markdown.strip():
        return ""
    # Strip KB page chrome to the article body — ONLY for Echo-KB pages (gated on
    # their raw-HTML signature), so an arbitrary non-KB page that merely contains
    # "Table of Contents"/"Last Updated" is never truncated.
    if _is_echo_kb_page(html):
        markdown = _slice_kb_article(markdown)
    return markdown


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
            markdown = html_to_markdown(content)
        except Exception as exc:
            logger.warning(
                "HTML->Markdown conversion failed for %s; keeping original HTML: %s",
                _resource_label(resource),
                exc,
            )
            return resource

        if not markdown:
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


# Source whitespace beside a ``<br>`` inside a promoted code block (issue #399 review).
# Formatted HTML — WordPress ``wpautop`` emits ``<br />\n`` — carries a newline text node
# next to every break. Inline rendering collapses it, but inside the promoted ``<pre>``
# it would survive as a blank line between every code line (106 of 107 breaks in a
# 60-page KB sample, 2026-09-02). Horizontal whitespace after the newline is kept so an
# indented code line stays indented.
_BR_TRAILING_WS = re.compile(r"(?:[ \t]*\r?\n[ \t]*)+$")
_BR_LEADING_WS = re.compile(r"^(?:[ \t]*\r?\n)+")

# Marker set on every ``<pre>`` that ``_promote_block_code`` creates (issue #399 review).
# ``markdownify`` calls ``code_language_callback`` for every ``<pre>``, so without the
# marker a native ``<pre class="bash">`` would gain a ``bash`` infostring and stop
# converting byte-identically to the output before #399. Only promoted blocks are ours
# to label.
_PROMOTED_ATTR = "data-archi-promoted"


def _strip_break_whitespace(br) -> None:
    """Drop the source newlines that sit beside a ``<br>`` about to become ``"\\n"``."""
    for node, pattern in (
        (br.previous_sibling, _BR_TRAILING_WS),
        (br.next_sibling, _BR_LEADING_WS),
    ):
        if type(node) is not NavigableString:
            continue
        stripped = pattern.sub("", str(node))
        if stripped == str(node):
            continue
        if stripped:
            node.replace_with(stripped)
        else:
            node.extract()


def _promote_block_code(html: str) -> str:
    """Promote bare multi-line ``<code>`` elements to ``<pre><code>`` blocks (issue #399).

    A ``<code>`` tag that is not already under a ``<pre>`` and that contains at least
    one ``<br>`` is treated as a block-level code listing rather than inline code.
    The source newlines beside each ``<br>`` are dropped first (they are formatting,
    not content — see ``_strip_break_whitespace``), then each ``<br>`` is replaced with
    a newline, and the element is wrapped in a new ``<pre>`` that inherits the
    ``class`` attribute of the ``<code>`` (if present) so that downstream language
    detection by ``_fence_language`` can fire on the ``<pre>``. The new ``<pre>`` is
    marked with ``_PROMOTED_ATTR`` so ``_promoted_fence_language`` labels only it.
    """
    soup = BeautifulSoup(html, "html.parser")
    for code in soup.find_all("code"):
        if code.find_parent("pre") is not None:
            continue
        brs = code.find_all("br")
        if not brs:
            continue
        # Two passes: strip the source whitespace beside every break while the
        # neighbours are still the original text nodes, then insert the newlines.
        # Interleaving would let the "\n" inserted for one break be read as source
        # whitespace of the next and stripped, collapsing an intended blank line.
        for br in brs:
            _strip_break_whitespace(br)
        for br in brs:
            br.replace_with("\n")
        pre = soup.new_tag("pre")
        pre[_PROMOTED_ATTR] = ""
        if code.get("class"):
            pre["class"] = code["class"]
        code.wrap(pre)
    return str(soup)


_FENCE_LANGUAGES: frozenset = frozenset(
    {
        "bash",
        "sh",
        "spec",
        "lua",
        "python",
        "c",
        "cpp",
        "fortran",
        "r",
        "perl",
        "json",
        "yaml",
        "text",
    }
)


def _fence_language(pre) -> str:
    """Return the fenced-code language label for a ``<pre>`` element (issue #399).

    Iterates the element's ``class`` list, lowercases each token, and returns the
    first that is a member of ``_FENCE_LANGUAGES``.  Returns ``""`` when no match
    is found or when the element carries no ``class`` attribute.
    """
    for token in pre.get("class") or []:
        token_lower = token.lower()
        if token_lower in _FENCE_LANGUAGES:
            return token_lower
    return ""


def _promoted_fence_language(pre) -> str:
    """``code_language_callback`` that labels only promoted blocks (issue #399 review).

    Returns ``_fence_language(pre)`` when ``pre`` carries ``_PROMOTED_ATTR`` and ``""``
    otherwise, so a native ``<pre>`` keeps the bare fence it had before #399.
    """
    if not pre.has_attr(_PROMOTED_ATTR):
        return ""
    return _fence_language(pre)


_SELF_SEPARATING_FOLLOWERS: frozenset = frozenset(
    {
        "article",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)
"""Block-level elements whose markdownify converter emits a leading newline (issue #410).

Measured on markdownify 1.2.2: each element in this set already starts on a new line when
it follows a nested list inside a list item, so no extra newline is needed.  ``ul`` and
``ol`` both produce ``'\\n' + text.rstrip()`` for nested lists.  The failure direction is
safe: an element missing from the set yields one extra blank line (harmless Markdown),
while an element wrongly included would leave a glue join in place.
"""


def _next_content_sibling(el):
    """Return the first meaningful sibling after *el* (issue #410).

    Walks ``el.next_sibling`` and returns:
    * the first ``Tag`` found, or
    * the first ``NavigableString`` that is not a ``Comment`` or ``Doctype`` and has
      at least one non-blank character.

    Returns ``None`` when all remaining siblings are whitespace-only text nodes,
    ``Comment`` nodes, or ``Doctype`` nodes.
    """
    sib = el.next_sibling
    while sib is not None:
        if isinstance(sib, Tag):
            return sib
        if isinstance(sib, NavigableString) and not isinstance(sib, (Comment, Doctype)):
            if str(sib).strip():
                return sib
        sib = sib.next_sibling
    return None


def _nested_list_needs_break(el, text: str) -> bool:
    """Return ``True`` when a trailing ``\\n`` must be appended to *el*'s output (issue #410).

    The predicate is ``False`` when:
    * ``text.strip()`` is empty — an empty nested list contributes nothing (design D4);
    * there is no meaningful sibling after *el*; or
    * the next content sibling is a ``Tag`` whose name is in ``_SELF_SEPARATING_FOLLOWERS``
      — such elements already start on a new line in markdownify output.

    Returns ``True`` for text nodes and inline elements (``a``, ``code``, ``span``, …)
    and for tags with no markdownify converter (``figure``, ``nav``, …) because their
    output is glued onto the nested list's last line without the extra newline.
    """
    if not text.strip():
        return False
    nxt = _next_content_sibling(el)
    if nxt is None:
        return False
    return not (isinstance(nxt, Tag) and nxt.name in _SELF_SEPARATING_FOLLOWERS)


class _ArchiMarkdownConverter(MarkdownConverter):
    """MarkdownConverter subclass with the nested-list newline fix (issue #410).

    markdownify binds ``convert_ul`` and ``convert_ol`` to the base
    ``convert_list`` at class-definition time, so overriding ``convert_list``
    alone would never be called for list elements.  The two class-level
    rebindings below re-point those attributes at this override (design D2).
    """

    def convert_list(self, el, text, parent_tags):
        """Append a trailing newline when inline content follows a nested list."""
        out = super().convert_list(el, text, parent_tags)
        if "li" in parent_tags and _nested_list_needs_break(el, text):
            return out + "\n"
        return out

    convert_ul = convert_list
    convert_ol = convert_list


def markdownify(html: str, **options) -> str:
    """Convert *html* to Markdown via ``_ArchiMarkdownConverter`` (issue #410).

    The function name is kept rather than delegating to ``MarkdownConverter``
    directly because two existing tests monkeypatch
    ``src.data_manager.collectors.processing.markdownify``; renaming would
    silently break that patching.
    """
    return _ArchiMarkdownConverter(**options).convert(html)


def _markdownify_deep_safe(content: str) -> str:
    """Convert HTML to Markdown with headroom for deeply-nested input.

    Runs ``markdownify`` inside a worker thread created with an enlarged stack and
    a temporarily raised recursion limit, so a pathologically deep HTML tree
    (issue #40) is converted rather than overflowing the recursion limit / C stack.
    The process-global recursion limit and the thread stack size are restored in a
    ``finally`` block regardless of outcome. Any exception raised inside the worker
    is re-raised to the caller, which keeps the existing raise->fallback behavior.
    """
    result: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = markdownify(
                _promote_block_code(content),
                heading_style="ATX",
                code_language_callback=_promoted_fence_language,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised to caller below
            result["error"] = exc

    # Serialize: these settings are interpreter-wide, so concurrent conversions must not
    # interleave their mutate/restore of the recursion limit and thread stack size.
    with _CONVERSION_LOCK:
        previous_limit = sys.getrecursionlimit()
        previous_stack: Optional[int] = None
        try:
            try:
                previous_stack = threading.stack_size(_CONVERSION_STACK_SIZE)
            except (ValueError, RuntimeError):  # pragma: no cover - platform-dependent
                previous_stack = None
            # Only raise the recursion limit if the enlarged stack was actually set;
            # raising it on the default C stack would overflow it and crash the process.
            if previous_stack is not None:
                sys.setrecursionlimit(max(previous_limit, _CONVERSION_RECURSION_LIMIT))
            worker = threading.Thread(target=_worker)
            worker.start()
            worker.join()
        finally:
            sys.setrecursionlimit(previous_limit)
            if previous_stack is not None:
                try:
                    threading.stack_size(previous_stack)
                except (
                    ValueError,
                    RuntimeError,
                ):  # pragma: no cover - platform-dependent
                    pass

    if "error" in result:
        raise result["error"]
    return result["value"]


def _slice_kb_article(markdown: str) -> str:
    """Return the KB article body sliced from full-page Markdown by its landmarks.

    Drops everything up to and including the ``Table of Contents`` line and everything
    from ``Bookmarkable Links`` (or, when absent, ``Last Updated``) onward. Slicing is
    applied ONLY when both a start and an end landmark are found and the result is
    non-blank; otherwise ``markdown`` is returned unchanged, so non-KB pages and edge
    cases keep the full-page conversion (no article is ever dropped).
    """
    start = markdown.find(_KB_SLICE_START)
    if start == -1:
        return markdown
    # Drop through the end of the start-landmark line (up to & including it).
    body_start = markdown.find("\n", start)
    if body_start == -1:
        return markdown
    end = markdown.find(_KB_SLICE_END)
    if end == -1:
        end = markdown.find(_KB_SLICE_END_FALLBACK)
    if end == -1 or end <= body_start:
        return markdown
    sliced = markdown[body_start:end].strip()
    return sliced if sliced else markdown


def _is_echo_kb_page(html: str) -> bool:
    """True when the raw HTML carries an Echo Knowledge Base signature.

    Gates the article-body slice to real KB pages so an arbitrary scraped page that
    merely contains the landmark phrases is never truncated.
    """
    return any(marker in html for marker in _ECHO_KB_MARKERS)


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

    ``max_concurrency`` bounds how many documents may be in ``invoke`` at once, and
    defaults to 1. That default is not arbitrary caution: ``process`` runs inside
    ``persist_resource``, which the scrape phase now calls from a pool sized by
    ``data_manager.scrape_workers`` (issue #136). Without an independent bound, a
    knob chosen for *fetch* politeness would silently set the LLM request rate too —
    and because every provider rejection is swallowed as ``"uncategorized"``, hitting
    a rate limit degrades metadata quietly instead of failing. Serializing by default
    reproduces the pre-parallel behavior exactly; operators who know their provider's
    ceiling raise it via ``categorization.max_concurrency``.
    """

    def __init__(
        self,
        *,
        categories: Sequence[str],
        provider: Optional[str],
        model: Optional[str],
        provider_config: Optional[Dict[str, Any]] = None,
        max_chars: int = 4000,
        max_concurrency: Any = 1,
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
        # Coerce like max_chars: anything not a positive int falls back to serial.
        # The fallback is deliberately 1 rather than the raw value — a bad config
        # value must never resolve to "unbounded" against a live provider.
        self._max_concurrency = (
            max_concurrency
            if isinstance(max_concurrency, int)
            and not isinstance(max_concurrency, bool)
            and max_concurrency > 0
            else 1
        )
        self._slots = threading.Semaphore(self._max_concurrency)
        # The lazy model build is a read-modify-write over _chat_model and
        # _model_build_failed. It was single-threaded until the scrape pool landed;
        # this lock keeps it to one build even when the first N documents arrive
        # together, so a broken endpoint is still probed exactly once (#136).
        self._build_lock = threading.Lock()

    def process(self, resource: BaseResource) -> BaseResource:
        category = self._categorize(resource)
        resource.set_metadata_field("llm_category", category)
        return resource

    def _categorize(self, resource: BaseResource) -> str:
        if not self.categories:
            return UNCATEGORIZED

        # Hold a provider slot across the build-and-invoke pair, so the bound counts
        # requests actually in flight, and release it before the purely local
        # response parsing below — parsing one document must not block the next
        # document's request.
        with self._slots:
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

        label = _select_category(response, self.categories)
        if label:
            return label
        raw_response = getattr(response, "content", response)
        raw_len = len(
            raw_response if isinstance(raw_response, str) else str(raw_response)
        )
        logger.debug(
            "Categorization model returned no in-list category for %s "
            "(response_len=%d, tail=%r); marking uncategorized.",
            _resource_label(resource),
            raw_len,
            _truncate_for_log(raw_response),
        )
        return UNCATEGORIZED

    def _get_chat_model(self) -> Any:
        # Double-checked: the common case (model already built, or the build already
        # known to have failed) stays lock-free, and only the one-time build path
        # serializes. Without the lock, N concurrent first documents would each see
        # _chat_model is None and build their own — N clients against an endpoint
        # that is often the very thing we are trying not to overload.
        if self._chat_model is not None or self._model_build_failed:
            return self._chat_model
        with self._build_lock:
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

        # Build real langchain message objects (matching base_react.py) rather than
        # ("system", ...)/("human", ...) role/content tuples: tuple auto-conversion is
        # not honored by every BaseChatModel. Imported lazily here — never at module
        # top — for the same reason as get_model: langchain is only required on the
        # categorization path, not the conversion-only/persistence path. If the import
        # is somehow unavailable (e.g. a test that stubs langchain_core), fall back to
        # role/content tuples so categorization never raises.
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            return [SystemMessage(content=system), HumanMessage(content=human)]
        except Exception:  # pragma: no cover - exercised only without real langchain
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
        # Title and category capture must precede markdown conversion (which strips
        # the <title> and the breadcrumb).
        processors.append(HtmlTitleProcessor())
        processors.append(HtmlCategoryProcessor())
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
                    max_concurrency=cat_cfg.get("max_concurrency", 1),
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


_LOG_TAIL_CHARS = 200


def _truncate_for_log(value: Any, limit: int = _LOG_TAIL_CHARS) -> str:
    """Return at most ``limit`` trailing characters of ``value`` (stringified), so a
    debug log never dumps a full, possibly very large, reasoning-model response. The
    tail is kept because the model's answer is at the end of its output."""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


# How many non-empty trailing lines of a response to treat as the model's "answer".
# Bounding the scan stops a category token that appears only in earlier chain-of-thought
# from being returned when the final answer names no category.
_ANSWER_LOOKBACK_LINES = 3

# A *blanket* refusal in the final answer (all named labels fall under a negation, e.g.
# "None of compute, storage, or policy applies") must map to uncategorized rather than
# the last label it happens to mention. Plain negation ("not storage, it is compute") is
# deliberately NOT matched here, so an assertion that negates one label and names another
# still resolves to the asserted label.
_REFUSAL_RE = re.compile(
    r"\b(?:none of|neither|not applicable|n/?a\b"
    r"|no (?:matching |valid |suitable )?categor"
    r"|does not (?:apply|fit|match)|cannot (?:be )?categor"
    r"|unable to (?:categor|classif))",
    re.IGNORECASE,
)


def _select_category(response: Any, categories: Sequence[str]) -> str:
    """Resolve the chosen category from a model response, tolerant of reasoning
    models that "think out loud" before answering.

    A reasoning model (e.g. Qwen3) emits its chain-of-thought in ``content`` ahead
    of the answer, so an exact ``content in categories`` check marks every document
    "uncategorized" even when the model reasoned to the right label. The model's
    FINAL answer is the reliable signal, so resolution is:

    1. Exact match on the whole cleaned response — the bare single-token reply from
       a non-reasoning model (unchanged behavior).
    2. Otherwise scan only the last ``_ANSWER_LOOKBACK_LINES`` non-empty lines (the
       model's answer) bottom-up and, within the lowest line that mentions any
       category, return the LAST-occurring category token. This recovers a bare
       trailing label (``"storage"``), a prefixed final line (``"Category: storage"``),
       and a negated-then-asserted line (``"not storage, it is compute"`` ->
       ``compute``). A category named only in earlier reasoning is NOT consulted, and a
       blanket refusal (``"None of compute, storage, or policy applies"``) yields
       ``uncategorized`` instead of its last-mentioned label.

    Matching is case-insensitive and word-boundaried (so ``compute`` never matches
    inside ``computer``); the canonical category as configured is returned. Returns
    ``""`` when no category is found, so the caller maps it to ``uncategorized``
    rather than inventing a label.
    """
    raw = getattr(response, "content", response)
    if not isinstance(raw, str):
        raw = str(raw)

    canon = {c.lower(): c for c in categories}
    if not canon:
        return ""

    cleaned = raw.strip().strip(".").strip()
    if cleaned.lower() in canon:
        return canon[cleaned.lower()]

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    for line in reversed(lines[-_ANSWER_LOOKBACK_LINES:]):
        if _REFUSAL_RE.search(line):
            # A blanket refusal in the model's final answer must not be mined for a
            # category; the model explicitly declined, so map it to uncategorized.
            return ""
        low = line.lower()
        best: Optional[tuple] = None  # (position, canonical) — last token in line wins
        for cat_low, cat in canon.items():
            for match in re.finditer(rf"(?<![\w-]){re.escape(cat_low)}(?![\w-])", low):
                if best is None or match.start() > best[0]:
                    best = (match.start(), cat)
        if best is not None:
            return best[1]
    return ""
