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


class TestCorpusParity:
    def test_json_corpus_applies_the_same_retrievability_filter_as_sql(
        self, tmp_path, capsys
    ):
        # `--corpus-json` is the documented way to reproduce a report offline, so
        # it must agree with `--pg-dsn`. A raw `documents` dump carries pending /
        # failed / deleted rows; if only the SQL path filtered them, the offline
        # run would invent gaps the live run never reports.
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/covered"))
        corpus = _write(
            tmp_path,
            "corpus.json",
            [
                {"url": f"{KB}/kb/covered", "ingestion_status": "embedded"},
                {"url": f"{KB}/kb/gap", "ingestion_status": "embedded"},
                {"url": f"{KB}/kb/pending", "ingestion_status": "pending"},
                {"url": f"{KB}/kb/failed", "ingestion_status": "failed"},
                {
                    "url": f"{KB}/kb/gone",
                    "ingestion_status": "embedded",
                    "is_deleted": True,
                },
            ],
        )

        code = script.main(
            ["coverage", "--bank", str(bank), "--corpus-json", str(corpus)]
        )
        out = capsys.readouterr().out

        assert code == 0
        assert f"{KB}/kb/gap" in out
        for hidden in ("pending", "failed", "gone"):
            assert f"{KB}/kb/{hidden}" not in out


class TestCorpusQuery:
    def test_the_corpus_query_reads_only_retrievable_documents(self):
        # `ingestion_status` is one of pending/embedding/embedded/failed, and rows
        # are inserted as `pending`. Only `embedded` has retrievable chunks, so
        # anything else would have coverage ask for a golden question about a page
        # the agent cannot actually retrieve.
        script = _load_script()

        assert "NOT is_deleted" in script.CORPUS_SQL
        assert "ingestion_status = 'embedded'" in script.CORPUS_SQL


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


# --------------------------------------------------------------------------- #
# Group 3 — `--propose` (greenlit-only) and the declines-only ledger
# --------------------------------------------------------------------------- #
def _ledger(tmp_path, *entries):
    return _write(tmp_path, "ledger.json", list(entries))


def _fake_llm(module, payload, calls=None):
    """Replace the CLI's LLM builder with one returning a canned reply."""

    def build(_model):
        def ask(prompt):
            if calls is not None:
                calls.append(prompt)
            return json.dumps(payload)

        return ask

    module.build_ask_llm = build


def _fake_page(module, html="<h1>GPU</h1><p>Add --gpus=1.</p>"):
    module.fetch_page_html = lambda url: html


CANDIDATE = {
    "user_input": "How do I request a GPU?",
    "reference": "Add #SBATCH --gpus=1.",
    "anchor_type": "easy_retrieve",
}


class TestProposeSubcommand:
    def test_drafts_candidates_for_the_greenlit_page(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _fake_page(module)
        bank = _bank(tmp_path, _row(f"{KB}/kb/other"))
        corpus = _corpus(tmp_path, f"{KB}/kb/a")

        code = module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(corpus),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "anthropic/claude-sonnet-5",
            ]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert '"status": "draft"' in out
        assert f'"{KB}/kb/a"' in out

    def test_leaves_the_bank_file_byte_unchanged(self, tmp_path):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _fake_page(module)
        bank = _bank(tmp_path, _row(f"{KB}/kb/other"))
        before = bank.read_bytes()

        module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
            ]
        )

        assert bank.read_bytes() == before

    def test_refuses_a_page_the_corpus_cannot_retrieve(self, tmp_path, capsys):
        # Drafting a question about a page the agent cannot retrieve authors a
        # guaranteed benchmark failure — the same trap the retrievability filter
        # closes for coverage.
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _fake_page(module)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/gone",
                "--model",
                "m/x",
            ]
        )

        assert code == 1
        assert "not in the retrievable corpus" in capsys.readouterr().err

    def test_propose_requires_a_model(self, tmp_path, capsys):
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/a",
            ]
        )

        assert code == 1
        assert "--model" in capsys.readouterr().err

    def test_a_run_where_every_candidate_is_rejected_fails(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [dict(CANDIDATE, anchor_type="trivia")])
        _fake_page(module)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
            ]
        )

        err = capsys.readouterr().err
        assert code == 1
        assert "anchor_type" in err

    def test_proposing_skips_the_gap_report(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _fake_page(module)

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a", f"{KB}/kb/b")),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
            ]
        )

        assert "gaps —" not in capsys.readouterr().out

    def test_a_page_that_cannot_be_fetched_is_an_operational_failure(
        self, tmp_path, capsys
    ):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])

        def boom(url):
            raise module.OperationalError(f"cannot fetch {url}: connection refused")

        module.fetch_page_html = boom

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
            ]
        )

        assert code == 1
        assert "cannot fetch" in capsys.readouterr().err


class TestDeclineLedgerCli:
    def test_declining_a_page_records_it_and_writes_no_bank(self, tmp_path, capsys):
        module = _load_script()
        bank = _bank(tmp_path, _row())
        before = bank.read_bytes()
        ledger = tmp_path / "ledger.json"

        code = module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--decline",
                f"{KB}/kb/a",
                "--reason",
                "thin page",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 0
        assert bank.read_bytes() == before
        entries = json.loads(ledger.read_text(encoding="utf-8"))
        assert entries[0]["url"] == f"{KB}/kb/a"
        assert entries[0]["reason"] == "thin page"
        assert entries[0]["at"]

    def test_a_declined_page_stops_appearing_as_a_gap(self, tmp_path, capsys):
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"})

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a", f"{KB}/kb/b")),
                "--ledger",
                str(ledger),
            ]
        )

        out = capsys.readouterr().out
        assert f"{KB}/kb/b" in out
        assert f"{KB}/kb/a" not in out.split("suppressed")[0]

    def test_the_suppressed_count_is_printed_not_hidden(self, tmp_path, capsys):
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"})

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--ledger",
                str(ledger),
            ]
        )

        assert "1 declined" in capsys.readouterr().out

    def test_declining_requires_a_ledger_path(self, tmp_path, capsys):
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--decline",
                f"{KB}/kb/a",
            ]
        )

        assert code == 1
        assert "--ledger" in capsys.readouterr().err

    def test_a_missing_ledger_file_reads_as_no_declines(self, tmp_path, capsys):
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--ledger",
                str(tmp_path / "nope.json"),
            ]
        )

        assert code == 0
        assert f"{KB}/kb/a" in capsys.readouterr().out

    def test_a_malformed_ledger_is_an_operational_failure(self, tmp_path, capsys):
        # Silently reading a corrupt ledger as "no declines" would resurface
        # every page an operator ever dismissed.
        module = _load_script()
        bad = tmp_path / "ledger.json"
        bad.write_text("{not json", encoding="utf-8")

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--ledger",
                str(bad),
            ]
        )

        assert code == 1
        assert "ledger" in capsys.readouterr().err

    def test_declining_twice_keeps_one_entry(self, tmp_path):
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a", "reason": "first"})
        args = [
            "coverage",
            "--bank",
            str(_bank(tmp_path, _row())),
            "--corpus-json",
            str(_corpus(tmp_path, f"{KB}/kb/a")),
            "--decline",
            f"{KB}/kb/a",
            "--ledger",
            str(ledger),
        ]

        module.main(args)

        entries = json.loads(ledger.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["reason"] == "first"

    def test_proposing_a_declined_page_still_works_and_says_so(self, tmp_path, capsys):
        # An explicit greenlight overrides an earlier decline: the operator
        # changed their mind, and the CLI names the earlier decision.
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _fake_page(module)
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"})

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 0
        assert "previously declined" in capsys.readouterr().out
