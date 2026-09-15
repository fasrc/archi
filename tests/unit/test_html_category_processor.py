"""Tests for HtmlCategoryProcessor — capture the source breadcrumb category.

Runs at persist time, BEFORE HtmlToMarkdownProcessor (which strips the breadcrumb).
Echo-KB pages render ``Home › <Category> › <Article>`` as a server-side breadcrumb
(``span.eckb-breadcrumb-link``); the immediate category is written to
``metadata["category"]`` — the source-provided key, distinct from ``llm_category``.
"""

from src.data_manager.collectors.processing import (
    HtmlCategoryProcessor,
    HtmlToMarkdownProcessor,
    ResourcePipeline,
)
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource


def _html_resource(content, suffix="html", **kwargs):
    return ScrapedResource(
        url="https://docs.rc.fas.harvard.edu/kb/running-jobs/",
        content=content,
        suffix=suffix,
        source_type="web",
        **kwargs,
    )


def _breadcrumb(*crumbs):
    spans = "".join(f'<span class="eckb-breadcrumb-link">{c}</span>' for c in crumbs)
    return f"<html><body>{spans}<h1>{crumbs[-1]}</h1><p>body</p></body></html>"


class TestCategoryCaptured:

    def test_breadcrumb_category_is_extracted(self):
        html = _breadcrumb("Home", "Cluster Usage", "Running Jobs")
        out = HtmlCategoryProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("category") == "Cluster Usage"

    def test_nested_breadcrumb_returns_immediate_parent(self):
        html = _breadcrumb("Home", "Software", "Languages", "Python")
        out = HtmlCategoryProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("category") == "Languages"


class TestNoCategory:

    def test_no_breadcrumb_writes_nothing(self):
        html = "<html><body><h1>Plain</h1><p>hi</p></body></html>"
        out = HtmlCategoryProcessor().process(_html_resource(html))
        assert "category" not in out.get_metadata().as_dict()

    def test_too_few_crumbs_writes_nothing(self):
        html = _breadcrumb("Home", "Running Jobs")  # only two crumbs
        out = HtmlCategoryProcessor().process(_html_resource(html))
        assert "category" not in out.get_metadata().as_dict()


class TestGuards:

    def test_does_not_overwrite_existing_category(self):
        html = _breadcrumb("Home", "Cluster Usage", "Running Jobs")
        resource = _html_resource(html, metadata={"category": "Preset"})
        out = HtmlCategoryProcessor().process(resource)
        assert out.get_metadata().as_dict().get("category") == "Preset"

    def test_non_html_suffix_passes_through(self):
        html = _breadcrumb("Home", "Cluster Usage", "Running Jobs")
        out = HtmlCategoryProcessor().process(_html_resource(html, suffix="md"))
        assert "category" not in out.get_metadata().as_dict()

    def test_non_string_content_passes_through(self):
        out = HtmlCategoryProcessor().process(
            _html_resource(b"<span class='eckb-breadcrumb-link'>x</span>", suffix="pdf")
        )
        assert "category" not in out.get_metadata().as_dict()


class TestPipelineOrder:

    def test_category_captured_before_markdown_conversion(self):
        html = _breadcrumb("Home", "Cluster Usage", "Running Jobs")
        pipeline = ResourcePipeline(
            [HtmlCategoryProcessor(), HtmlToMarkdownProcessor()]
        )
        out = pipeline.run(_html_resource(html))
        assert out.get_metadata().as_dict().get("category") == "Cluster Usage"
        assert out.suffix == "md"
