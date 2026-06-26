"""Tests for HtmlToMarkdownProcessor (HTML->Markdown conversion at persist time)."""

import sys
from pathlib import Path

from src.data_manager.collectors.localfile_resource import LocalFileResource
from src.data_manager.collectors.processing import (
    HtmlToMarkdownProcessor,
    ResourcePipeline,
)
from src.data_manager.collectors.scrapers.scraped_resource import ScrapedResource


def _html_resource(content="<h1>Title</h1>", suffix="html", **kwargs):
    return ScrapedResource(
        url="https://example.com/doc",
        content=content,
        suffix=suffix,
        source_type="web",
        **kwargs,
    )


def test_converts_html_to_atx_markdown_and_flips_suffix():
    resource = _html_resource(content="<h1>Title</h1>")

    out = HtmlToMarkdownProcessor().process(resource)

    assert "# Title" in out.get_content()
    assert out.suffix == "md"
    assert out.get_metadata().as_dict()["converted_from"] == "html"


def test_structure_survives_conversion():
    html = "<h2>Sec</h2><ul><li>a</li><li>b</li></ul><a href='http://x.io'>link</a>"
    out = HtmlToMarkdownProcessor().process(_html_resource(content=html))
    md = out.get_content()
    assert "## Sec" in md
    assert "* a" in md or "- a" in md
    assert "http://x.io" in md


def test_table_structure_survives():
    html = "<table><tr><th>H</th></tr><tr><td>V</td></tr></table>"
    out = HtmlToMarkdownProcessor().process(_html_resource(content=html))
    assert "|" in out.get_content()


def test_rewrites_path_fields_to_md():
    resource = _html_resource(
        file_name="page.html",
        relative_path="sub/page.html",
    )

    out = HtmlToMarkdownProcessor().process(resource)

    assert out.file_name == "page.md"
    assert out.relative_path == "sub/page.md"
    assert out.get_file_path(Path("/data")) == Path("/data/sub/page.md")
    assert out.get_filename() == "page.md"


def test_htm_suffix_also_converted():
    out = HtmlToMarkdownProcessor().process(_html_resource(suffix="htm"))
    assert out.suffix == "md"


def test_hash_unchanged_after_conversion():
    resource = _html_resource()
    before = resource.get_hash()
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.get_hash() == before


def test_bytes_content_passthrough():
    resource = _html_resource(content=b"<h1>x</h1>")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == b"<h1>x</h1>"
    assert out.suffix == "html"


def test_non_html_suffix_passthrough():
    resource = _html_resource(content="print('x')", suffix="py")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == "print('x')"
    assert out.suffix == "py"


def test_local_file_resource_passthrough(tmp_path):
    """LocalFileResource is bytes with no suffix field -> untouched."""
    source = tmp_path / "a.html"
    source.write_text("<h1>x</h1>")
    resource = LocalFileResource(
        file_name="a.html", source_path=source, content=b"<h1>x</h1>"
    )
    out = HtmlToMarkdownProcessor().process(resource)
    assert out is resource
    assert out.content == b"<h1>x</h1>"


def test_already_markdown_is_noop():
    resource = _html_resource(content="# Already", suffix="md")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == "# Already"
    assert out.suffix == "md"
    assert "converted_from" not in out.get_metadata().as_dict()


def test_converter_raises_keeps_original(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("markdownify failed")

    monkeypatch.setattr("src.data_manager.collectors.processing.markdownify", _boom)
    resource = _html_resource(content="<h1>Title</h1>")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == "<h1>Title</h1>"
    assert out.suffix == "html"
    assert "converted_from" not in out.get_metadata().as_dict()


def test_blank_output_keeps_original(monkeypatch):
    monkeypatch.setattr(
        "src.data_manager.collectors.processing.markdownify",
        lambda *_a, **_k: "   \n  ",
    )
    resource = _html_resource(content="<script>noop()</script>")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == "<script>noop()</script>"
    assert out.suffix == "html"
    assert "converted_from" not in out.get_metadata().as_dict()


def test_deeply_nested_html_is_converted_not_recursion_fallback():
    """A pathologically deep HTML tree (~2000 nested <div>s) must still CONVERT.

    Before issue #40's fix, markdownify recursed per nesting level and hit
    RecursionError, which the broad ``except Exception`` swallowed into the
    raw-HTML fallback (suffix stayed ``html``). Such pages should be converted to
    Markdown like any other, not silently kept as raw HTML.
    """
    depth = 2000
    html = "<div>" * depth + "deep" + "</div>" * depth

    out = HtmlToMarkdownProcessor().process(_html_resource(content=html))

    assert out.suffix == "md"
    assert out.get_metadata().as_dict()["converted_from"] == "html"
    markdown = out.get_content()
    assert markdown and markdown.strip()
    assert "deep" in markdown


def test_no_recursion_limit_raise_when_enlarged_stack_unavailable(monkeypatch):
    """Segfault-safety (#48 review, Copilot): if the enlarged thread stack cannot be
    set, the process-wide recursion limit must NOT be raised — a deep recursion on the
    default C stack would overflow it and crash the process. The conversion should fall
    back gracefully instead."""
    import src.data_manager.collectors.processing as proc

    def _stack_fails(*_a, **_k):
        raise RuntimeError("stack size unsupported on this platform")

    monkeypatch.setattr(proc.threading, "stack_size", _stack_fails)

    baseline = sys.getrecursionlimit()
    raised_to = []
    real_set = sys.setrecursionlimit

    def _spy(n):
        raised_to.append(n)
        return real_set(n)

    monkeypatch.setattr(proc.sys, "setrecursionlimit", _spy)
    try:
        proc._markdownify_deep_safe("<p>hi</p>")
    except RecursionError:
        pass  # acceptable: failing safely is the point
    assert all(
        n <= baseline for n in raised_to
    ), f"recursion limit raised to {raised_to} without an enlarged stack (segfault risk)"


def test_recursion_limit_restored_after_conversion():
    """Invariant (#48 review, Codex): the process-global recursion limit is always
    restored, so a conversion never leaks a raised limit to the rest of the process."""
    import src.data_manager.collectors.processing as proc

    before = sys.getrecursionlimit()
    proc._markdownify_deep_safe("<div>" * 1500 + "x" + "</div>" * 1500)
    assert sys.getrecursionlimit() == before


def test_conversion_limits_are_bounded():
    """#48 review (Copilot): the stack size and recursion limit must be sized for the
    real ~2000-depth pages, not pathologically large (256 MiB / 100k risks thread-
    creation failure and is far past need)."""
    import src.data_manager.collectors.processing as proc

    assert proc._CONVERSION_RECURSION_LIMIT <= 20_000
    assert proc._CONVERSION_STACK_SIZE <= 128 * 1024 * 1024


def test_pipeline_runs_processors_in_order():
    pipeline = ResourcePipeline([HtmlToMarkdownProcessor()])
    out = pipeline.run(_html_resource(content="<h1>Title</h1>"))
    assert "# Title" in out.get_content()
    assert out.suffix == "md"
