"""Unit tests for ``archi sources build`` (sources_builder helper + CLI wiring).

Network is mocked via ``unittest.mock.patch`` on ``requests.get`` (repo
convention; there is no ``responses``/``requests-mock`` available). Pure
functions (parse/expand/glob/render/diff) are unit-tested directly; CLI wiring
uses ``click.testing.CliRunner``.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml
from click.testing import CliRunner

from src.cli.tools import sources_builder as sb


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resp(text: str, status: int = 200):
    """Build a fake ``requests`` Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.content = text.encode("utf-8")
    return resp


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://example.com/c</loc></url>
</urlset>"""

EMPTY_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""


# --------------------------------------------------------------------------- #
# 1. Manifest schema
# --------------------------------------------------------------------------- #
class TestManifestSchema:
    def test_valid_mixed_manifest_loads(self, tmp_path):
        manifest = _write(
            tmp_path,
            "m.yaml",
            yaml.safe_dump(
                [
                    {"type": "sitemap", "url": "https://x.test/s.xml"},
                    {"type": "crawl", "url": "https://x.test/i/", "depth": 1},
                    {"type": "literal", "url": "https://x.test/page"},
                ]
            ),
        )
        seeds = sb.load_manifest(str(manifest))
        assert [s["type"] for s in seeds] == ["sitemap", "crawl", "literal"]
        assert seeds[0]["url"] == "https://x.test/s.xml"

    def test_unknown_type_rejected(self, tmp_path):
        manifest = _write(
            tmp_path,
            "m.yaml",
            yaml.safe_dump([{"type": "rss", "url": "https://x.test/feed"}]),
        )
        with pytest.raises(sb.ManifestError) as exc:
            sb.load_manifest(str(manifest))
        assert "rss" in str(exc.value)

    def test_missing_url_rejected(self, tmp_path):
        manifest = _write(tmp_path, "m.yaml", yaml.safe_dump([{"type": "sitemap"}]))
        with pytest.raises(sb.ManifestError) as exc:
            sb.load_manifest(str(manifest))
        assert "url" in str(exc.value)

    def test_non_yaml_rejected(self, tmp_path):
        manifest = _write(tmp_path, "m.yaml", "this: : not: valid: yaml: [")
        with pytest.raises(sb.ManifestError):
            sb.load_manifest(str(manifest))

    def test_non_list_manifest_rejected(self, tmp_path):
        manifest = _write(
            tmp_path, "m.yaml", yaml.safe_dump({"type": "literal", "url": "x"})
        )
        with pytest.raises(sb.ManifestError):
            sb.load_manifest(str(manifest))

    def test_missing_manifest_file_rejected(self, tmp_path):
        with pytest.raises(sb.ManifestError) as exc:
            sb.load_manifest(str(tmp_path / "nope.yaml"))
        assert "not found" in str(exc.value).lower()

    def test_non_mapping_entry_rejected(self, tmp_path):
        manifest = _write(tmp_path, "m.yaml", yaml.safe_dump(["just-a-string"]))
        with pytest.raises(sb.ManifestError) as exc:
            sb.load_manifest(str(manifest))
        assert "mapping" in str(exc.value)


# --------------------------------------------------------------------------- #
# 2. Sitemap expansion (one level of <sitemapindex> nesting)
# --------------------------------------------------------------------------- #
SITEMAPINDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/child-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/child-2.xml</loc></sitemap>
</sitemapindex>"""

CHILD_1 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/1a</loc></url>
  <url><loc>https://example.com/1b</loc></url>
</urlset>"""

CHILD_2 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/2a</loc></url>
</urlset>"""

NESTED_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/grandchild.xml</loc></sitemap>
</sitemapindex>"""


class TestSitemapExpansion:
    def test_flat_urlset(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            urls = sb.expand_sitemap("https://example.com/s.xml")
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        assert get.call_count == 1

    def test_one_level_index(self):
        bodies = {
            "https://example.com/index.xml": SITEMAPINDEX,
            "https://example.com/child-1.xml": CHILD_1,
            "https://example.com/child-2.xml": CHILD_2,
        }

        def fake_get(url, *a, **k):
            return _resp(bodies[url])

        with patch(
            "src.cli.tools.sources_builder.requests.get", side_effect=fake_get
        ) as get:
            urls = sb.expand_sitemap("https://example.com/index.xml")
        # each child fetched exactly once (index + 2 children)
        assert get.call_count == 3
        assert urls == [
            "https://example.com/1a",
            "https://example.com/1b",
            "https://example.com/2a",
        ]

    def test_nested_index_contributes_no_page_urls(self):
        bodies = {
            "https://example.com/index.xml": SITEMAPINDEX,
            "https://example.com/child-1.xml": NESTED_INDEX,  # child is itself an index
            "https://example.com/child-2.xml": CHILD_2,
        }
        fetched = []

        def fake_get(url, *a, **k):
            fetched.append(url)
            return _resp(bodies[url])

        with patch("src.cli.tools.sources_builder.requests.get", side_effect=fake_get):
            urls = sb.expand_sitemap("https://example.com/index.xml")
        # the nested index's grandchild is NOT followed
        assert "https://example.com/grandchild.xml" not in fetched
        # only the urlset child (child-2) contributes page URLs
        assert urls == ["https://example.com/2a"]

    def test_empty_urlset_no_urls_no_error(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(EMPTY_URLSET)
            urls = sb.expand_sitemap("https://example.com/s.xml")
        assert urls == []

    def test_http_503_aborts(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp("Service Unavailable", status=503)
            with pytest.raises(sb.FetchError):
                sb.expand_sitemap("https://example.com/s.xml")

    def test_malformed_xml_aborts(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp("<not-xml <<< broken")
            with pytest.raises(sb.FetchError):
                sb.expand_sitemap("https://example.com/s.xml")

    def test_connection_error_aborts(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.side_effect = requests.exceptions.ConnectionError("boom")
            with pytest.raises(sb.FetchError):
                sb.expand_sitemap("https://example.com/s.xml")

    def test_inline_nested_index_buried_loc_not_emitted_or_fetched(self):
        # A top-level <sitemapindex> whose <sitemap> contains an INLINE nested
        # <sitemapindex><sitemap><loc>buried.xml</loc></sitemap></sitemapindex>.
        # Only the direct <sitemap> child's own <loc> (child.xml) is a fetch
        # target; the buried <loc> must never be emitted or fetched (D8).
        index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/child.xml</loc>
    <sitemapindex>
      <sitemap><loc>https://example.com/buried.xml</loc></sitemap>
    </sitemapindex>
  </sitemap>
</sitemapindex>"""
        child = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page</loc></url>
</urlset>"""
        bodies = {
            "https://example.com/index.xml": index,
            "https://example.com/child.xml": child,
        }
        fetched = []

        def fake_get(url, *a, **k):
            fetched.append(url)
            return _resp(bodies[url])

        with patch("src.cli.tools.sources_builder.requests.get", side_effect=fake_get):
            urls = sb.expand_sitemap("https://example.com/index.xml")
        assert "https://example.com/buried.xml" not in fetched
        assert urls == ["https://example.com/page"]

    def test_unexpected_root_aborts(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp('<?xml version="1.0"?><rss><channel/></rss>')
            with pytest.raises(sb.FetchError) as exc:
                sb.expand_sitemap("https://example.com/feed.xml")
        assert "rss" in str(exc.value).lower()

    def test_doctype_entity_rejected(self):
        # billion-laughs-style payload: a DTD with an <!ENTITY> declaration.
        evil = """<?xml version="1.0"?>
<!DOCTYPE urlset [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;">
]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/&lol2;</loc></url>
</urlset>"""
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(evil)
            with pytest.raises(sb.FetchError) as exc:
                sb.expand_sitemap("https://example.com/s.xml")
        assert "entity" in str(exc.value).lower()

    def test_oversize_body_rejected(self):
        big = "x" * (sb._MAX_FETCH_BYTES + 1)
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(big)
            with pytest.raises(sb.FetchError) as exc:
                sb.expand_sitemap("https://example.com/s.xml")
        assert "cap" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# 3. Glob filtering
# --------------------------------------------------------------------------- #
class TestGlobFiltering:
    def test_no_filters_passthrough(self):
        urls = ["https://x.test/a", "https://x.test/b"]
        assert sb.apply_globs(urls, [], []) == urls

    def test_include_gate_drops_non_matches(self):
        urls = ["https://x.test/docs/a", "https://x.test/blog/b"]
        assert sb.apply_globs(urls, ["*/docs/*"], []) == ["https://x.test/docs/a"]

    def test_exclude_wins_over_include(self):
        urls = ["https://x.test/docs/author/a", "https://x.test/docs/b"]
        kept = sb.apply_globs(urls, ["*/docs/*"], ["*/author/*"])
        assert kept == ["https://x.test/docs/b"]

    def test_exclude_only(self):
        urls = ["https://x.test/a", "https://x.test/skip"]
        assert sb.apply_globs(urls, [], ["*/skip"]) == ["https://x.test/a"]

    def test_include_with_no_matches_drops_all(self):
        urls = ["https://x.test/a", "https://x.test/b"]
        assert sb.apply_globs(urls, ["*/docs/*"], []) == []


# --------------------------------------------------------------------------- #
# 4. Deterministic same-host crawl
# --------------------------------------------------------------------------- #
CRAWL_HTML = """
<html><body>
  <a href="https://slurm.test/archive/srun.html">srun</a>
  <a href="man/sbatch.html">sbatch relative</a>
  <a href="https://other.test/away.html">external</a>
  <a href="https://slurm.test/archive/srun.html">srun dup</a>
  <a href="#section">fragment-only</a>
  <a>no href</a>
</body></html>
"""


class TestCrawl:
    def test_off_host_dropped_and_same_host_kept(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            urls = sb.crawl_same_host(
                "https://slurm.test/archive/", depth=1, include=[], exclude=[]
            )
        assert "https://other.test/away.html" not in urls
        assert "https://slurm.test/archive/srun.html" in urls

    def test_relative_link_resolved(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            urls = sb.crawl_same_host(
                "https://slurm.test/archive/", depth=1, include=[], exclude=[]
            )
        assert "https://slurm.test/archive/man/sbatch.html" in urls

    def test_deterministic_order(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            first = sb.crawl_same_host(
                "https://slurm.test/archive/", depth=1, include=[], exclude=[]
            )
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            second = sb.crawl_same_host(
                "https://slurm.test/archive/", depth=1, include=[], exclude=[]
            )
        assert first == second
        assert first == sorted(first)

    def test_globs_applied_to_crawl(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            urls = sb.crawl_same_host(
                "https://slurm.test/archive/",
                depth=1,
                include=["*srun*"],
                exclude=[],
            )
        assert urls == ["https://slurm.test/archive/srun.html"]

    def test_depth_two_follows_same_host_children(self):
        index = '<html><body><a href="https://slurm.test/a/">a</a></body></html>'
        page_a = (
            '<html><body><a href="https://slurm.test/a/leaf.html">leaf</a>'
            "</body></html>"
        )
        bodies = {
            "https://slurm.test/": index,
            "https://slurm.test/a/": page_a,
        }

        def fake_get(url, *a, **k):
            return _resp(bodies[url])

        with patch("src.cli.tools.sources_builder.requests.get", side_effect=fake_get):
            urls = sb.crawl_same_host(
                "https://slurm.test/", depth=2, include=[], exclude=[]
            )
        # trailing slash collapsed by normalization (D6)
        assert "https://slurm.test/a" in urls
        assert "https://slurm.test/a/leaf.html" in urls

    def test_fetch_failure_aborts(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp("nope", status=500)
            with pytest.raises(sb.FetchError):
                sb.crawl_same_host(
                    "https://slurm.test/", depth=1, include=[], exclude=[]
                )

    def test_cycle_each_page_fetched_once(self):
        # A -> B and B -> A. Even with depth deep enough to revisit, the
        # visited-page guard fetches each page exactly once (no infinite loop).
        page_a = '<html><body><a href="https://slurm.test/b">b</a></body></html>'
        page_b = '<html><body><a href="https://slurm.test/">a</a></body></html>'
        bodies = {
            "https://slurm.test/": page_a,
            "https://slurm.test/b": page_b,
        }
        counts = {}

        def fake_get(url, *a, **k):
            counts[url] = counts.get(url, 0) + 1
            return _resp(bodies[url])

        with patch("src.cli.tools.sources_builder.requests.get", side_effect=fake_get):
            urls = sb.crawl_same_host(
                "https://slurm.test/", depth=5, include=[], exclude=[]
            )
        # Each page fetched at most once despite the A<->B cycle.
        assert all(c == 1 for c in counts.values()), counts
        assert "https://slurm.test/b" in urls


# --------------------------------------------------------------------------- #
# 5. Literal passthrough + the seed dispatcher
# --------------------------------------------------------------------------- #
class TestLiteralAndDispatch:
    def test_literal_not_fetched(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            urls = sb.expand_seed({"type": "literal", "url": "https://x.test/page"})
        assert urls == ["https://x.test/page"]
        get.assert_not_called()

    def test_literal_normalized(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            urls = sb.expand_seed(
                {
                    "type": "literal",
                    "url": "https://EXAMPLE.com/Page/#frag",
                }
            )
        get.assert_not_called()
        # host lowercased, fragment dropped, trailing slash collapsed
        assert urls == ["https://example.com/Page"]

    def test_dispatch_sitemap_applies_globs(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            urls = sb.expand_seed(
                {
                    "type": "sitemap",
                    "url": "https://example.com/s.xml",
                    "include": ["*/a"],
                }
            )
        assert urls == ["https://example.com/a"]

    def test_dispatch_sitemap_normalizes(self):
        body = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://EXAMPLE.com/Doc/#x</loc></url>
</urlset>"""
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(body)
            urls = sb.expand_seed(
                {"type": "sitemap", "url": "https://example.com/s.xml"}
            )
        assert urls == ["https://example.com/Doc"]

    def test_dispatch_crawl(self):
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(CRAWL_HTML)
            urls = sb.expand_seed(
                {"type": "crawl", "url": "https://slurm.test/archive/"}
            )
        assert "https://slurm.test/archive/srun.html" in urls
        assert "https://other.test/away.html" not in urls


# --------------------------------------------------------------------------- #
# 6. render / normalize / dedupe / extras
# --------------------------------------------------------------------------- #
class TestNormalize:
    def test_drop_fragment(self):
        assert sb.normalize_url("https://x.test/a#section") == "https://x.test/a"

    def test_lowercase_scheme_host(self):
        assert sb.normalize_url("HTTPS://X.TEST/a") == "https://x.test/a"

    def test_collapse_trailing_slash(self):
        assert sb.normalize_url("https://x.test/a/") == "https://x.test/a"

    def test_root_slash_preserved(self):
        assert sb.normalize_url("https://x.test/") == "https://x.test/"

    def test_path_case_preserved(self):
        assert (
            sb.normalize_url("https://x.test/CaseSensitive")
            == "https://x.test/CaseSensitive"
        )

    def test_query_preserved(self):
        # The query string is part of resource identity and must survive
        # normalization (only the fragment is dropped).
        assert (
            sb.normalize_url("HTTPS://X.test/p?id=7&q=A#frag")
            == "https://x.test/p?id=7&q=A"
        )


class TestRenderDedupe:
    def test_cross_seed_dedupe_first_seen_order(self, tmp_path):
        out = tmp_path / "sources.list"  # no manual-extras sibling
        lines = sb.append_manual_extras(
            [
                "https://x.test/b",
                "https://x.test/a",
                "https://x.test/b",  # dup
            ],
            str(out),
        )
        assert lines == ["https://x.test/b", "https://x.test/a"]


class TestManualExtras:
    def test_prefixed_extras_preserved(self, tmp_path):
        out = tmp_path / "sources.list"
        extras = tmp_path / "manual-extras.list"
        extras.write_text(
            "# a comment\n"
            "git-https://github.com/org/repo\n"
            "sso-https://internal.test/portal\n"
            "\n"
        )
        lines = sb.append_manual_extras(["https://x.test/a"], str(out))
        assert "git-https://github.com/org/repo" in lines
        assert "sso-https://internal.test/portal" in lines
        assert "# a comment" not in lines

    def test_duplicate_extras_dropped_for_generated(self, tmp_path):
        out = tmp_path / "sources.list"
        extras = tmp_path / "manual-extras.list"
        extras.write_text("https://x.test/a\nhttps://x.test/extra\n")
        lines = sb.append_manual_extras(["https://x.test/a"], str(out))
        # generated URL appears once (in generated position), not again
        assert lines.count("https://x.test/a") == 1
        assert lines.index("https://x.test/a") == 0
        assert "https://x.test/extra" in lines

    def test_no_extras_file(self, tmp_path):
        out = tmp_path / "sources.list"
        lines = sb.append_manual_extras(["https://x.test/a"], str(out))
        assert lines == ["https://x.test/a"]


# --------------------------------------------------------------------------- #
# 7. Output path resolution
# --------------------------------------------------------------------------- #
def _config_with_input_lists(tmp_path, entries):
    cfg = tmp_path / "config.yaml"
    body = {
        "name": "dep",
        "data_manager": {"sources": {"links": {"input_lists": entries}}},
    }
    cfg.write_text(yaml.safe_dump(body))
    return cfg


class TestOutputResolution:
    def test_single_input_list_resolves(self, tmp_path):
        cfg = _config_with_input_lists(tmp_path, ["weblists/sources.list"])
        resolved = sb.resolve_output_path(output=None, config=str(cfg))
        assert resolved == "weblists/sources.list"

    def test_zero_entries_errors(self, tmp_path):
        cfg = _config_with_input_lists(tmp_path, [])
        with pytest.raises(sb.OutputResolutionError) as exc:
            sb.resolve_output_path(output=None, config=str(cfg))
        assert "--output" in str(exc.value)

    def test_two_entries_errors(self, tmp_path):
        cfg = _config_with_input_lists(tmp_path, ["weblists/a.list", "weblists/b.list"])
        with pytest.raises(sb.OutputResolutionError) as exc:
            sb.resolve_output_path(output=None, config=str(cfg))
        assert "--output" in str(exc.value)

    def test_output_override_wins_regardless(self, tmp_path):
        cfg = _config_with_input_lists(tmp_path, ["weblists/a.list", "weblists/b.list"])
        resolved = sb.resolve_output_path(output="/explicit/out.list", config=str(cfg))
        assert resolved == "/explicit/out.list"

    def test_output_override_without_config(self):
        # --output given, no config needed
        resolved = sb.resolve_output_path(output="/explicit/out.list", config=None)
        assert resolved == "/explicit/out.list"

    def test_no_output_no_config_errors(self):
        with pytest.raises(sb.OutputResolutionError):
            sb.resolve_output_path(output=None, config=None)


# --------------------------------------------------------------------------- #
# 8. Dry-run diff
# --------------------------------------------------------------------------- #
class TestDryRunDiff:
    def test_diff_against_existing(self, tmp_path):
        out = tmp_path / "sources.list"
        out.write_text("https://x.test/old\n")
        diff = sb.compute_diff(["https://x.test/new"], str(out))
        assert "+https://x.test/new" in diff
        assert "-https://x.test/old" in diff

    def test_diff_against_missing_is_empty_file(self, tmp_path):
        out = tmp_path / "does-not-exist.list"
        diff = sb.compute_diff(["https://x.test/new"], str(out))
        assert "+https://x.test/new" in diff


# --------------------------------------------------------------------------- #
# 9. CLI wiring (click.testing.CliRunner)
# --------------------------------------------------------------------------- #
def _cli():
    """Build the click group with the sources command registered."""
    from src.cli.cli_main import cli, sources

    cli.add_command(sources)
    return cli


def _sitemap_manifest(tmp_path):
    return _write(
        tmp_path,
        "m.yaml",
        yaml.safe_dump([{"type": "sitemap", "url": "https://example.com/s.xml"}]),
    )


class TestCliWiring:
    def test_happy_path_writes_list(self, tmp_path):
        manifest = _sitemap_manifest(tmp_path)
        out = tmp_path / "sources.list"
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "--output",
                    str(out),
                ],
            )
        assert result.exit_code == 0, result.output
        assert out.read_text().splitlines() == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]

    def test_literal_only_creates_parent_dir(self, tmp_path):
        # A literal-only manifest issues no HTTP and exercises the parent-dir
        # creation branch when the output lives in a missing subdirectory.
        manifest = _write(
            tmp_path,
            "m.yaml",
            yaml.safe_dump([{"type": "literal", "url": "https://x.test/page"}]),
        )
        out = tmp_path / "nested" / "dir" / "sources.list"
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            result = runner.invoke(
                _cli(),
                ["sources", "build", str(manifest), "--output", str(out)],
            )
        assert result.exit_code == 0, result.output
        get.assert_not_called()
        assert out.read_text().splitlines() == ["https://x.test/page"]

    def test_dry_run_writes_nothing(self, tmp_path):
        manifest = _sitemap_manifest(tmp_path)
        out = tmp_path / "sources.list"
        out.write_text("https://example.com/old\n")
        before = out.read_text()
        before_mtime = out.stat().st_mtime_ns
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "--output",
                    str(out),
                    "--dry-run",
                ],
            )
        assert result.exit_code == 0, result.output
        assert out.read_text() == before
        assert out.stat().st_mtime_ns == before_mtime
        # a diff is printed
        assert "+https://example.com/a" in result.output

    def test_dry_run_against_missing_file(self, tmp_path):
        manifest = _sitemap_manifest(tmp_path)
        out = tmp_path / "nope.list"
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "--output",
                    str(out),
                    "--dry-run",
                ],
            )
        assert result.exit_code == 0, result.output
        assert not out.exists()

    def test_malformed_manifest_nonzero(self, tmp_path):
        manifest = _write(tmp_path, "m.yaml", "bad: : yaml: [")
        out = tmp_path / "sources.list"
        runner = CliRunner()
        result = runner.invoke(
            _cli(),
            ["sources", "build", str(manifest), "--output", str(out)],
        )
        assert result.exit_code != 0
        assert not out.exists()

    def test_unknown_type_nonzero_no_write(self, tmp_path):
        manifest = _write(
            tmp_path,
            "m.yaml",
            yaml.safe_dump([{"type": "rss", "url": "https://x.test/feed"}]),
        )
        out = tmp_path / "sources.list"
        runner = CliRunner()
        result = runner.invoke(
            _cli(),
            ["sources", "build", str(manifest), "--output", str(out)],
        )
        assert result.exit_code != 0
        assert "rss" in result.output
        assert not out.exists()

    def test_sitemap_503_nonzero_no_overwrite(self, tmp_path):
        manifest = _sitemap_manifest(tmp_path)
        out = tmp_path / "sources.list"
        out.write_text("https://example.com/keep\n")
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp("down", status=503)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "--output",
                    str(out),
                ],
            )
        assert result.exit_code != 0
        # existing list unchanged
        assert out.read_text() == "https://example.com/keep\n"

    def test_default_output_from_single_input_list(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        with patch("src.cli.tools.sources_builder.requests.get") as get:
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                ],
            )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_ambiguous_default_output_nonzero(self, tmp_path):
        cfg = _config_with_input_lists(tmp_path, ["weblists/a.list", "weblists/b.list"])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            _cli(),
            ["sources", "build", str(manifest), "-c", str(cfg)],
        )
        assert result.exit_code != 0
        assert "--output" in result.output


# --------------------------------------------------------------------------- #
# 10. Import trigger
# --------------------------------------------------------------------------- #
class TestImportTrigger:
    def test_import_shells_create_once(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        with (
            patch("src.cli.tools.sources_builder.requests.get") as get,
            patch(
                "src.cli.utils.command_runner.CommandRunner.run_simple",
                return_value=("ok", "", 0),
            ) as run,
        ):
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                    "--name",
                    "dev",
                    "--import",
                ],
            )
        assert result.exit_code == 0, result.output
        assert run.call_count == 1
        command = run.call_args[0][0]
        assert "archi create" in command
        assert "--name dev" in command
        assert "--force" in command
        # a non-empty --services must be passed (default chatbot)
        assert "--services chatbot" in command

    def test_import_forwards_env_file(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        env = _write(tmp_path, "secrets.env", "X=1\n")
        runner = CliRunner()
        with (
            patch("src.cli.tools.sources_builder.requests.get") as get,
            patch(
                "src.cli.utils.command_runner.CommandRunner.run_simple",
                return_value=("ok", "", 0),
            ) as run,
        ):
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                    "--name",
                    "dev",
                    "--env-file",
                    str(env),
                    "--import",
                ],
            )
        assert result.exit_code == 0, result.output
        command = run.call_args[0][0]
        assert "--env-file" in command
        assert str(env) in command

    def test_import_without_name_nonzero_before_write(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        with patch("src.cli.utils.command_runner.CommandRunner.run_simple") as run:
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                    "--import",
                ],
            )
        assert result.exit_code != 0
        run.assert_not_called()
        assert not out.exists()

    def test_import_without_config_nonzero_before_write(self, tmp_path):
        manifest = _sitemap_manifest(tmp_path)
        out = tmp_path / "the-list.list"
        runner = CliRunner()
        with patch("src.cli.utils.command_runner.CommandRunner.run_simple") as run:
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "--output",
                    str(out),
                    "--name",
                    "dev",
                    "--import",
                ],
            )
        assert result.exit_code != 0
        run.assert_not_called()

    def test_import_with_dry_run_nonzero_no_refresh(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        with patch("src.cli.utils.command_runner.CommandRunner.run_simple") as run:
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                    "--name",
                    "dev",
                    "--import",
                    "--dry-run",
                ],
            )
        assert result.exit_code != 0
        run.assert_not_called()

    def test_import_refresh_failure_nonzero(self, tmp_path):
        out = tmp_path / "the-list.list"
        cfg = _config_with_input_lists(tmp_path, [str(out)])
        manifest = _sitemap_manifest(tmp_path)
        runner = CliRunner()
        with (
            patch("src.cli.tools.sources_builder.requests.get") as get,
            patch(
                "src.cli.utils.command_runner.CommandRunner.run_simple",
                return_value=("", "boom", 1),
            ),
        ):
            get.return_value = _resp(URLSET)
            result = runner.invoke(
                _cli(),
                [
                    "sources",
                    "build",
                    str(manifest),
                    "-c",
                    str(cfg),
                    "--name",
                    "dev",
                    "--import",
                ],
            )
        assert result.exit_code != 0
        # the list was still written before the refresh failed
        assert out.exists()
