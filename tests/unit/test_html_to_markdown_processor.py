"""Tests for HtmlToMarkdownProcessor (HTML->Markdown conversion at persist time)."""

import sys
from pathlib import Path

import pytest
from markdownify import STRIP, STRIP_ONE
from markdownify import markdownify as library_markdownify

from src.data_manager.collectors.localfile_resource import LocalFileResource
from src.data_manager.collectors.processing import (
    _FENCE_LANGUAGES,
    _PROMOTED_ATTR,
    HtmlToMarkdownProcessor,
    ResourcePipeline,
    _fence_language,
    _markdownify,
    _promote_block_code,
    _promoted_fence_language,
    _slice_kb_article,
    html_to_markdown,
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

    monkeypatch.setattr("src.data_manager.collectors.processing._markdownify", _boom)
    resource = _html_resource(content="<h1>Title</h1>")
    out = HtmlToMarkdownProcessor().process(resource)
    assert out.content == "<h1>Title</h1>"
    assert out.suffix == "html"
    assert "converted_from" not in out.get_metadata().as_dict()


def test_blank_output_keeps_original(monkeypatch):
    monkeypatch.setattr(
        "src.data_manager.collectors.processing._markdownify",
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


# --- KB landmark slice (improve-fasrc-kb-ingestion) --------------------------
# Echo-KB pages bound the article between "Table of Contents" and either
# "Bookmarkable Links" or (fallback) "Last Updated". The slice keeps only the body
# between those landmarks; non-KB pages (no landmarks) keep the full conversion.

_KB_HTML = (
    "<html><body>"
    "<nav>Filter by categories Affiliates AI Applications</nav>"
    "<div class='eckb-article-toc__title'>Table of Contents</div>"
    "<h1>Running Jobs</h1>"
    "<p>UNIQUEBODYMARKER real article content.</p>"
    "<p class='toc_title'>Bookmarkable Links</p>"
    "<ul><li>1 Introduction</li><li>2 Getting Started</li></ul>"
    "<div>Last Updated May 26 2026</div>"
    "</body></html>"
)


def test_kb_article_sliced_between_landmarks():
    out = HtmlToMarkdownProcessor().process(_html_resource(content=_KB_HTML))
    md = out.get_content()
    # body kept
    assert "UNIQUEBODYMARKER" in md
    assert "Running Jobs" in md
    # pre-"Table of Contents" nav dropped
    assert "Filter by categories" not in md
    assert "Affiliates" not in md
    # "Bookmarkable Links" and everything after dropped
    assert "Bookmarkable Links" not in md
    assert "1 Introduction" not in md
    assert "Last Updated" not in md
    assert out.suffix == "md"
    assert out.get_metadata().as_dict()["converted_from"] == "html"


def test_end_landmark_falls_back_to_last_updated():
    html = (
        "<html><body>"
        "<nav>PRENAV Affiliates</nav>"
        "<div class='eckb-article-toc__title'>Table of Contents</div>"
        "<p>BODYMARKER content.</p>"
        "<div>Last Updated May 26 2026</div>"
        "</body></html>"
    )
    md = HtmlToMarkdownProcessor().process(_html_resource(content=html)).get_content()
    assert "BODYMARKER" in md
    assert "PRENAV" not in md
    assert "Last Updated" not in md
    assert "May 26" not in md


def test_non_kb_page_with_landmarks_not_sliced():
    """A non-KB page (no Echo-KB markup) that happens to contain the landmark
    phrases must NOT be sliced — the slice is gated on the Echo-KB signature, so an
    arbitrary scraped page with a `Table of Contents` and a `Last Updated` footer is
    never silently truncated."""
    html = (
        "<html><body>"
        "<h2>Table of Contents</h2>"
        "<p>KEEPBODY real content.</p>"
        "<footer>Last Updated 2026</footer>"
        "</body></html>"
    )
    md = HtmlToMarkdownProcessor().process(_html_resource(content=html)).get_content()
    assert "KEEPBODY" in md
    # Not gated as KB -> full page kept, including the landmark lines.
    assert "Table of Contents" in md
    assert "Last Updated" in md


def test_no_landmarks_keeps_full_page():
    html = "<html><body><h1>Plain</h1><p>FULLBODY hello world.</p></body></html>"
    md = HtmlToMarkdownProcessor().process(_html_resource(content=html)).get_content()
    assert "FULLBODY" in md
    assert "# Plain" in md


def test_start_landmark_without_end_keeps_full_page():
    html = (
        "<html><body><nav>NAVTEXT keep me</nav>"
        "<div>Table of Contents</div><p>BODY here.</p></body></html>"
    )
    md = HtmlToMarkdownProcessor().process(_html_resource(content=html)).get_content()
    # No end landmark -> do not slice; the pre-TOC nav is retained.
    assert "NAVTEXT" in md
    assert "BODY" in md


def test_slice_helper_returns_body_between_landmarks():
    md = "junk\nTable of Contents\nBODY LINE\nBookmarkable Links\nfooter"
    assert _slice_kb_article(md).strip() == "BODY LINE"


def test_slice_helper_blank_between_keeps_original():
    md = "Table of Contents\nBookmarkable Links\n"
    assert _slice_kb_article(md) == md


def test_slice_helper_no_start_keeps_original():
    md = "some\nmarkdown with Bookmarkable Links only"
    assert _slice_kb_article(md) == md


# --------------------------------------------------------------------------- #
# The extraction seam (openspec `maintain-ragas-goldenset`, task 4.1)
# --------------------------------------------------------------------------- #
# Drift detection hashes the *extracted* text of a re-fetched page, and design
# D6's sign-off condition is that the live signal be measured exactly the way the
# corpus was built. `html_to_markdown` is that shared rule: the processor and the
# drift pass both call it, so an extraction change can never make the two
# disagree about what a page "says".


def test_extraction_seam_matches_what_the_processor_persists():
    html = "<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>"

    persisted = HtmlToMarkdownProcessor().process(_html_resource(content=html))

    assert html_to_markdown(html) == persisted.get_content()


def test_extraction_seam_slices_a_kb_article_like_the_processor():
    html = (
        "<html><body><div class='eckb-article-toc'>chrome</div>"
        "<div>Table of Contents</div><p>BODY LINE</p>"
        "<div>Bookmarkable Links</div><p>footer</p></body></html>"
    )

    extracted = html_to_markdown(html)

    assert "BODY LINE" in extracted
    assert "footer" not in extracted


def test_extraction_seam_reports_blank_conversion_as_empty():
    # The processor keeps the original HTML on a blank conversion; the seam says
    # so by returning "" rather than inventing text to hash.
    assert html_to_markdown("<!-- nothing -->") == ""


# --- _promote_block_code (issue #399) ----------------------------------------
# Multi-line bare <code> elements (no <pre> parent, contains <br>) are promoted
# into a <pre><code> block so markdownify renders them as fenced code, not inline.


def test_promote_block_code_wraps_multiline_code_with_class():
    result = _promote_block_code('<p><code class="bash">a<br>b</code></p>')
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(result, "html.parser")
    code = soup.find("code")
    assert code is not None
    pre = code.find_parent("pre")
    assert pre is not None, "expected <code> to be wrapped in <pre>"
    assert pre.get("class") == ["bash"]
    assert code.get_text() == "a\nb"
    assert soup.find("br") is None


def test_promote_block_code_skips_inline_code():
    result = _promote_block_code("<p>Add <code>--gpus=1</code>.</p>")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(result, "html.parser")
    code = soup.find("code")
    assert code is not None
    assert code.find_parent("pre") is None


def test_promote_block_code_skips_code_already_in_pre():
    result = _promote_block_code("<pre><code>a<br>b</code></pre>")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(result, "html.parser")
    assert len(soup.find_all("pre")) == 1
    assert soup.find("br") is not None


def test_promote_block_code_wraps_no_class():
    result = _promote_block_code("<p><code>a<br>b</code></p>")
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(result, "html.parser")
    code = soup.find("code")
    assert code is not None
    pre = code.find_parent("pre")
    assert pre is not None, "expected <code> to be wrapped in <pre>"
    assert pre.get("class") is None


# --- _FENCE_LANGUAGES / _fence_language (issue #399) -------------------------
# Language detection maps the class attribute of a <pre> element to the fenced-
# code language label passed to markdownify's code_language_callback.


def test_fence_languages_set():
    assert _FENCE_LANGUAGES == frozenset(
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


def test_fence_language_exact_match():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup('<pre class="bash">x</pre>', "html.parser").pre
    assert _fence_language(pre) == "bash"


def test_fence_language_compound_class():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup('<pre class="hljs bash">x</pre>', "html.parser").pre
    assert _fence_language(pre) == "bash"


def test_fence_language_case_insensitive():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup('<pre class="Bash">x</pre>', "html.parser").pre
    assert _fence_language(pre) == "bash"


def test_fence_language_unknown_class():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup('<pre class="wp-block-code">x</pre>', "html.parser").pre
    assert _fence_language(pre) == ""


def test_fence_language_no_class():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup("<pre>x</pre>", "html.parser").pre
    assert _fence_language(pre) == ""


# --- wire helpers into html_to_markdown (issue #399, task 2.1) ---------------
# _promote_block_code and _fence_language are wired into _worker() so the full
# html_to_markdown() / HtmlToMarkdownProcessor pipeline produces fenced blocks.

_BASH_CODE_HTML = '<p><code class="bash">#!/bin/bash<br># comment<br>echo hi</code></p>'
_WP_CODE_HTML = '<p><code class="wp-block-code">line1<br>line2</code></p>'


def test_wire_fenced_block_with_language():
    md = html_to_markdown(_BASH_CODE_HTML)
    assert "```bash\n#!/bin/bash\n# comment\necho hi\n```" in md
    for line in md.splitlines():
        assert not (
            line.startswith("#") and line.endswith("  ")
        ), f"trailing two-space line still present: {line!r}"


def test_wire_fenced_block_unknown_language():
    md = html_to_markdown(_WP_CODE_HTML)
    assert "```\nline1\nline2\n```" in md
    assert "wp-block-code" not in md


def test_wire_guard_inline_code_unchanged():
    html = "<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>"
    assert html_to_markdown(html) == "# Title\n\nAdd `--gpus=1`."


def test_wire_guard_pre_code_unchanged():
    html = "<pre><code>#!/bin/bash\n# c\necho hi</code></pre>"
    assert html_to_markdown(html) == "```\n#!/bin/bash\n# c\necho hi\n```"


def test_wire_processor_produces_fenced_block():
    out = HtmlToMarkdownProcessor().process(_html_resource(content=_BASH_CODE_HTML))
    assert out.suffix == "md"
    content = out.get_content()
    assert content == html_to_markdown(_BASH_CODE_HTML)
    assert "```bash" in content


# --- source whitespace next to a promoted <br> (issue #399, Codex review) -----
# Formatted HTML (WordPress wpautop emits "<br />\n") carries a newline text node
# next to every break. Inline rendering collapses it; inside the promoted <pre> it
# would survive as a blank line between every code line.


def _promoted_code_text(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_promote_block_code(html), "html.parser")
    return soup.find("code").get_text()


def test_promote_block_code_drops_source_newline_after_br():
    html = (
        '<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>'
    )
    assert _promoted_code_text(html) == "#!/bin/bash\n# comment\necho hi"


def test_promote_block_code_keeps_indentation_after_the_dropped_newline():
    html = "<p><code>if x:<br>\n    y<br>\nz</code></p>"
    assert _promoted_code_text(html) == "if x:\n    y\nz"


def test_promote_block_code_drops_source_newline_before_br():
    assert _promoted_code_text("<p><code>a\n<br>b</code></p>") == "a\nb"


def test_promote_block_code_keeps_blank_line_from_two_breaks():
    assert _promoted_code_text("<p><code>a<br>\n<br>\nb</code></p>") == "a\n\nb"
    assert _promoted_code_text("<p><code>a<br><br>b</code></p>") == "a\n\nb"


def test_wire_fenced_block_wpautop_newlines_have_no_blank_lines():
    html = (
        '<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>'
    )
    assert html_to_markdown(html) == "```bash\n#!/bin/bash\n# comment\necho hi\n```"


# --- fence language only for promoted blocks (issue #399, Codex review) -------
# markdownify calls code_language_callback for every <pre>. A native
# <pre class="bash"> must keep converting to a bare fence, byte-identical to the
# output before #399, so the class lookup is gated on the promotion marker.


def test_promote_block_code_marks_the_new_pre():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        _promote_block_code("<p><code>a<br>b</code></p>"), "html.parser"
    )
    assert soup.find("pre").has_attr(_PROMOTED_ATTR)


def test_promoted_fence_language_ignores_a_native_pre():
    from bs4 import BeautifulSoup

    pre = BeautifulSoup('<pre class="bash">x</pre>', "html.parser").pre
    assert _promoted_fence_language(pre) == ""


def test_promoted_fence_language_reads_a_promoted_pre():
    from bs4 import BeautifulSoup

    html = f'<pre class="bash" {_PROMOTED_ATTR}="">x</pre>'
    pre = BeautifulSoup(html, "html.parser").pre
    assert _promoted_fence_language(pre) == "bash"


def test_wire_guard_native_pre_with_language_class_unchanged():
    # Measured on origin/dev 9d17fd92 (2026-09-02): a native <pre class="bash">
    # converts to a bare fence. The promotion must not add an infostring to it.
    html = '<pre class="bash"><code>echo hi</code></pre>'
    assert html_to_markdown(html) == "```\necho hi\n```"


# --- design trade-offs pinned after the adversarial review (issue #399) --------
# These pin behaviour the design accepts on purpose, so a later change to the
# promotion predicate or the whitespace scrub is deliberate, not accidental.


def test_wire_multiline_code_inside_prose_becomes_its_own_block():
    # Design trade-off (openspec design.md, Risks): a multi-line <code> that shares
    # its paragraph with prose still becomes a fenced block. An inline span cannot
    # carry a line break in CommonMark; dev merged the lines into one. Measured
    # 2026-09-02: 9 of 25 such elements in a 60-page KB sample share a block with
    # prose, and all 9 are terminal transcripts, multi-line commands, or JSON.
    html = '<p>Before <code class="bash">a<br>b</code> after</p>'
    assert html_to_markdown(html) == "Before\n\n```bash\na\nb\n```\n\nafter"


def test_promote_block_code_keeps_a_tab_that_indents_a_code_line():
    # A build-recipe line keeps its leading tab after the dropped source newline.
    # dev collapsed it to nothing: '`all:  \ncc main.c`'.
    html = "<p><code>all:<br>\n\tcc main.c</code></p>"
    assert _promoted_code_text(html) == "all:\n\tcc main.c"


def test_promote_block_code_drops_only_whitespace_beside_a_source_newline():
    # Horizontal whitespace that touches a source newline beside a <br> is dropped:
    # an inline element collapses it in the browser and dev already dropped it
    # ('`cmd\n  \nnext`'). Whitespace with no newline beside it is payload and stays.
    assert _promoted_code_text("<p><code>cmd\t\n<br>next</code></p>") == "cmd\nnext"


# --- fence delimiter sized to the longest backtick run inside the block (#407) ---


def test_fence_widens_past_an_embedded_triple_backtick_run():
    html = "<p><code>a<br>```<br># heading</code></p>"
    assert html_to_markdown(html) == "````\na\n```\n# heading\n````"


def test_fence_widens_past_an_embedded_triple_backtick_run_in_a_native_pre():
    html = "<pre>a\n```\nb</pre>"
    assert html_to_markdown(html) == "````\na\n```\nb\n````"


def test_fence_widens_past_a_four_backtick_run():
    html = "<pre>a\n````\nb</pre>"
    assert html_to_markdown(html) == "`````\na\n````\nb\n`````"


def test_fence_widens_and_keeps_the_infostring():
    html = '<p><code class="bash">x<br>```<br>y</code></p>'
    assert html_to_markdown(html) == "````bash\nx\n```\ny\n````"


def test_fence_widens_for_a_mid_line_backtick_run():
    html = "<pre>use ``` inline</pre>"
    assert html_to_markdown(html) == "````\nuse ``` inline\n````"


def test_fence_empty_block_is_unaffected():
    assert html_to_markdown("<p>x</p><pre></pre><p>y</p>") == "x\n\ny"
    assert _markdownify("<pre></pre>") == ""


@pytest.mark.parametrize("mode", [STRIP, STRIP_ONE, None])
def test_markdownify_matches_the_library_for_every_strip_pre_mode(mode):
    html = "<p>x</p><pre>\n\n  a\n\n</pre><p>y</p>"
    assert _markdownify(html, strip_pre=mode) == library_markdownify(
        html, strip_pre=mode
    )


def test_markdownify_rejects_an_invalid_strip_pre_mode():
    with pytest.raises(ValueError):
        _markdownify("<pre>x</pre>", strip_pre="bogus")
    assert _promoted_code_text("<p><code>a\tb\t<br>c</code></p>") == "a\tb\t\nc"
