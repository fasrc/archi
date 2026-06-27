"""Tests for HtmlTitleProcessor — capture a clean <title> for citation text.

Runs at persist time, BEFORE HtmlToMarkdownProcessor (which rewrites content to
Markdown). Gives HTML docs a human-readable ``metadata["title"]`` so the agent
can cite them as ``[title](url)`` instead of a bare index. The selenium/SSO path
and the PDF loader already set a title; this closes the plain-HTML gap.
"""

from src.data_manager.collectors.processing import (
    HtmlTitleProcessor,
    HtmlToMarkdownProcessor,
    ResourcePipeline,
)
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource


def _html_resource(content, suffix="html", **kwargs):
    return ScrapedResource(
        url="https://docs.rc.fas.harvard.edu/kb/page/",
        content=content,
        suffix=suffix,
        source_type="web",
        **kwargs,
    )


class TestTitleCaptured:

    def test_title_tag_is_extracted_into_metadata(self):
        html = "<html><head><title>Foo</title></head><body><p>hi</p></body></html>"
        out = HtmlTitleProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == "Foo"

    def test_title_is_trimmed(self):
        html = "<html><head><title>  Spaced Out  </title></head><body></body></html>"
        out = HtmlTitleProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == "Spaced Out"


class TestTitleFallbacks:

    def test_falls_back_to_h1_when_no_title(self):
        html = "<html><head></head><body><h1>Heading One</h1></body></html>"
        out = HtmlTitleProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == "Heading One"

    def test_falls_back_to_og_title(self):
        html = (
            '<html><head><meta property="og:title" content="Social Title"></head>'
            "<body></body></html>"
        )
        out = HtmlTitleProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == "Social Title"

    def test_titleless_page_yields_empty_title_and_does_not_raise(self):
        html = "<html><head></head><body><p>no title here</p></body></html>"
        out = HtmlTitleProcessor().process(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == ""


class TestGuards:

    def test_does_not_clobber_existing_title(self):
        # selenium/PDF paths already set a good title; never overwrite it.
        html = "<html><head><title>Raw HTML Title</title></head></html>"
        resource = _html_resource(html, metadata={"title": "Rendered Title"})
        out = HtmlTitleProcessor().process(resource)
        assert out.get_metadata().as_dict().get("title") == "Rendered Title"

    def test_non_html_suffix_passes_through_untouched(self):
        resource = _html_resource("<title>X</title>", suffix="md")
        out = HtmlTitleProcessor().process(resource)
        assert "title" not in out.get_metadata().as_dict()

    def test_non_string_content_passes_through(self):
        resource = _html_resource(b"<title>bytes</title>", suffix="pdf")
        out = HtmlTitleProcessor().process(resource)
        assert "title" not in out.get_metadata().as_dict()


class TestPipelineOrder:

    def test_title_captured_before_markdown_conversion(self):
        # In the real pipeline, title extraction must precede markdown conversion
        # (which strips <title>). Order: HtmlTitleProcessor -> HtmlToMarkdownProcessor.
        html = "<html><head><title>Doc Title</title></head><body><h1>Body</h1></body></html>"
        pipeline = ResourcePipeline([HtmlTitleProcessor(), HtmlToMarkdownProcessor()])
        out = pipeline.run(_html_resource(html))
        assert out.get_metadata().as_dict().get("title") == "Doc Title"
        assert out.suffix == "md"
        assert "# Body" in out.get_content()
