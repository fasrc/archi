"""Unit tests for the golden-set maintenance CLI
`scripts/benchmarking/goldenset_maintenance.py` (openspec change
`maintain-ragas-goldenset`, group 2).

The script is a thin wrapper over `src.utils.goldenset_maintenance`: it loads the
bank through `benchmark_schema`, reads the corpus (or a JSON dump of it), expands
the live source list, and prints the coverage / orphan work lists. These tests
drive `main()` so the exit contract is pinned — **findings never fail the run**,
only operational failure does — and so the detection passes are proven read-only
end to end.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "benchmarking"
    / "goldenset_maintenance.py"
)

KB = "https://docs.rc.fas.harvard.edu"


def _load_script():
    spec = importlib.util.spec_from_file_location("goldenset_maintenance_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bank(tmp_path, *rows):
    return _write(tmp_path, "bank.json", list(rows))


def _row(*sources, **extra):
    row = {"user_input": "q?", "reference": "a", "sources": list(sources)}
    row.update(extra)
    return row


def _corpus(tmp_path, *urls):
    return _write(
        tmp_path, "corpus.json", [{"url": u, "source_type": "web"} for u in urls]
    )


class TestCoverageSubcommand:
    def test_reports_an_uncovered_page_and_still_exits_zero(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        corpus = _corpus(tmp_path, f"{KB}/kb/a", f"{KB}/kb/b")

        code = script.main(
            ["coverage", "--bank", str(bank), "--corpus-json", str(corpus)]
        )
        out = capsys.readouterr().out

        # Findings are work to do, not a failed run — the cron must not page.
        assert code == 0
        assert f"{KB}/kb/b" in out
        assert f"{KB}/kb/a" not in out.split("gaps")[-1]

    def test_a_fully_covered_corpus_says_so(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        corpus = _corpus(tmp_path, f"{KB}/kb/a")

        assert (
            script.main(["coverage", "--bank", str(bank), "--corpus-json", str(corpus)])
            == 0
        )
        assert "0 gaps" in capsys.readouterr().out

    def test_a_slug_near_miss_prints_in_its_own_bucket(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        corpus = _corpus(tmp_path, f"{KB}/docs/a")

        script.main(["coverage", "--bank", str(bank), "--corpus-json", str(corpus)])
        out = capsys.readouterr().out

        assert "reconcil" in out.lower()
        assert "0 gaps" in out

    def test_filters_narrow_the_report(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        corpus = _corpus(tmp_path, f"{KB}/kb/b", f"{KB}/other/c")

        script.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(corpus),
                "--path-glob",
                f"{KB}/kb/*",
            ]
        )
        out = capsys.readouterr().out

        assert f"{KB}/kb/b" in out
        assert f"{KB}/other/c" not in out

    def test_leaves_the_bank_file_byte_unchanged(self, tmp_path):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        corpus = _corpus(tmp_path, f"{KB}/kb/b")
        before = bank.read_bytes()

        script.main(["coverage", "--bank", str(bank), "--corpus-json", str(corpus)])

        assert bank.read_bytes() == before

    def test_an_unreadable_corpus_is_an_operational_failure(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))

        code = script.main(
            ["coverage", "--bank", str(bank), "--corpus-json", str(tmp_path / "nope")]
        )

        assert code == 1
        assert "error" in capsys.readouterr().err.lower()


class TestOrphansSubcommand:
    def test_flags_a_row_whose_page_left_the_live_inventory(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/removed"))
        sources = tmp_path / "sources.list"
        sources.write_text(f"{KB}/kb/still-here\n", encoding="utf-8")

        code = script.main(["orphans", "--bank", str(bank), "--sources", str(sources)])
        out = capsys.readouterr().out

        assert code == 0
        assert f"{KB}/kb/removed" in out

    def test_an_incomplete_inventory_abstains_and_exits_nonzero(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/removed"))
        sources = tmp_path / "sources.list"
        sources.write_text("", encoding="utf-8")

        code = script.main(["orphans", "--bank", str(bank), "--sources", str(sources)])
        captured = capsys.readouterr()

        # Abstention is an OPERATIONAL failure, not a healthy run: a cron reading
        # exit 0 would treat "no analysis happened" as "nothing is wrong" and hide
        # a broken inventory indefinitely. Findings still exit zero; this is not a
        # finding.
        assert code != 0
        assert "abstain" in captured.err.lower()
        assert f"{KB}/kb/removed" not in captured.out

    def test_a_sitemap_source_without_an_explicit_floor_fails_fast(
        self, tmp_path, capsys
    ):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))
        sources = tmp_path / "sources.list"
        sources.write_text(f"sitemap-{KB}/sitemap.xml\n", encoding="utf-8")

        code = script.main(["orphans", "--bank", str(bank), "--sources", str(sources)])

        # Without an explicit floor the library default is min_pages=1, so a
        # truncated sitemap reads as complete and every unlisted bank row looks
        # deleted. Refuse to guess rather than emit false orphans.
        assert code == 1
        assert "min-pages" in capsys.readouterr().err.lower()

    def test_a_truncated_sitemap_below_the_configured_floor_abstains(
        self, tmp_path, capsys, monkeypatch
    ):
        from src.data_manager.collectors.scrapers import sitemap_source

        script = _load_script()
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{KB}/kb/only-one</loc></url>"
            "</urlset>"
        )
        monkeypatch.setattr(sitemap_source, "fetch_sitemap_text", lambda url: xml)

        bank = _bank(tmp_path, _row(f"{KB}/kb/removed"))
        sources = tmp_path / "sources.list"
        sources.write_text(f"sitemap-{KB}/sitemap.xml\n", encoding="utf-8")

        code = script.main(
            [
                "orphans",
                "--bank",
                str(bank),
                "--sources",
                str(sources),
                "--min-pages",
                "150",
            ]
        )
        captured = capsys.readouterr()

        # The deployment floor is 150; this sitemap returned 1 page. That is an
        # incomplete inventory, so the bank row must NOT be called an orphan.
        assert code != 0
        assert f"{KB}/kb/removed" not in captured.out

    def test_an_out_of_scope_host_is_reported_separately(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row("https://slurm.schedmd.com/sbatch.html"))
        sources = tmp_path / "sources.list"
        sources.write_text(f"{KB}/kb/a\n", encoding="utf-8")

        script.main(["orphans", "--bank", str(bank), "--sources", str(sources)])
        out = capsys.readouterr().out

        assert "0 orphans" in out
        assert "out of scope" in out.lower()

    def test_a_missing_sources_file_is_an_operational_failure(self, tmp_path, capsys):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/a"))

        code = script.main(
            ["orphans", "--bank", str(bank), "--sources", str(tmp_path / "nope")]
        )

        assert code == 1
        assert "error" in capsys.readouterr().err.lower()


class TestCli:
    def test_no_subcommand_exits_non_zero(self, capsys):
        script = _load_script()

        assert script.main([]) == 2

    def test_a_malformed_bank_is_an_operational_failure(self, tmp_path, capsys):
        script = _load_script()
        bank = tmp_path / "bank.json"
        bank.write_text("{not json", encoding="utf-8")
        corpus = _corpus(tmp_path, f"{KB}/kb/a")

        code = script.main(
            ["coverage", "--bank", str(bank), "--corpus-json", str(corpus)]
        )

        assert code == 1
        assert "error" in capsys.readouterr().err.lower()
