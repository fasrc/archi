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
import os
import stat
import threading
import time
from pathlib import Path

import pytest

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
        corpus = _corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))

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
        corpus = _corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))

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


def _persisted(tmp_path, name="web/a.md", text="Add #SBATCH --gpus=1 for one GPU."):
    """Write the persisted document the retriever actually serves."""
    path = tmp_path / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _corpus_with_files(tmp_path, *pairs):
    return _write(
        tmp_path,
        "corpus.json",
        [{"url": u, "source_type": "web", "file_path": f} for u, f in pairs],
    )


CANDIDATE = {
    "user_input": "How do I request a GPU?",
    "reference": "Add #SBATCH --gpus=1.",
    "anchor_type": "easy_retrieve",
}


class TestProposeSubcommand:
    def test_drafts_candidates_for_the_greenlit_page(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)
        bank = _bank(tmp_path, _row(f"{KB}/kb/other"))
        corpus = _corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))

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
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert '"status": "draft"' in out
        assert f'"{KB}/kb/a"' in out

    def test_leaves_the_bank_file_byte_unchanged(self, tmp_path):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)
        bank = _bank(tmp_path, _row(f"{KB}/kb/other"))
        before = bank.read_bytes()

        module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert bank.read_bytes() == before

    def test_refuses_a_page_the_corpus_cannot_retrieve(self, tmp_path, capsys):
        # Drafting a question about a page the agent cannot retrieve authors a
        # guaranteed benchmark failure — the same trap the retrievability filter
        # closes for coverage.
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/gone",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
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
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
            ]
        )

        assert code == 1
        assert "--model" in capsys.readouterr().err

    def test_a_run_where_every_candidate_is_rejected_fails(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [dict(CANDIDATE, anchor_type="trivia")])
        _persisted(tmp_path)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        err = capsys.readouterr().err
        assert code == 1
        assert "anchor_type" in err

    def test_proposing_skips_the_gap_report(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(
                    _corpus_with_files(
                        tmp_path, (f"{KB}/kb/a", "web/a.md"), (f"{KB}/kb/b", "web/b.md")
                    )
                ),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert "gaps —" not in capsys.readouterr().out

    def test_grounds_in_the_persisted_document_not_the_live_page(self, tmp_path):
        # The retriever serves the PERSISTED text. Ingestion is URL-keyed and
        # skips the content write for a page it already holds, so the live page
        # can be ahead of the index; a question grounded in live-only text would
        # be unanswerable — exactly the failure the retrievability guard exists
        # to prevent. Nothing in this path touches the network.
        module = _load_script()
        prompts = []
        _fake_llm(module, [CANDIDATE], calls=prompts)
        _persisted(tmp_path, text="ONLY IN THE PERSISTED DOCUMENT")

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert "ONLY IN THE PERSISTED DOCUMENT" in prompts[0]

    def test_propose_requires_a_data_path(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
            ]
        )

        assert code == 1
        assert "--data-path" in capsys.readouterr().err

    def test_a_missing_persisted_document_is_an_operational_failure(
        self, tmp_path, capsys
    ):
        # The catalog row says the page is embedded but its file is gone: the
        # tool refuses rather than falling back to a live fetch, because that
        # fallback is precisely how an unanswerable question gets authored.
        module = _load_script()
        _fake_llm(module, [CANDIDATE])

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/gone.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert code == 1
        assert "persisted document" in capsys.readouterr().err

    def test_a_corpus_row_without_a_file_path_cannot_be_proposed_against(
        self, tmp_path, capsys
    ):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])

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
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert code == 1
        assert "file_path" in capsys.readouterr().err

    def test_an_empty_persisted_document_is_refused(self, tmp_path, capsys):
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path, text="   ")

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert code == 1
        assert capsys.readouterr().err


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

    def test_proposing_a_declined_page_is_refused_until_it_is_undeclined(
        self, tmp_path, capsys
    ):
        # Drafting while the decline stands would leave the page suppressed
        # forever: the candidates are unapplied, so nothing covers the page, and
        # the stale entry keeps hiding it from every later run. Refuse, and name
        # the recovery.
        module = _load_script()
        prompts = []
        _fake_llm(module, [CANDIDATE], calls=prompts)
        _persisted(tmp_path)
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"})

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 1
        assert prompts == []
        assert "--undecline" in capsys.readouterr().err


class TestUndecline:
    """The supported reversal — without it a decline is permanent."""

    def test_undeclining_removes_the_entry_and_the_page_is_a_gap_again(
        self, tmp_path, capsys
    ):
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"}, {"url": f"{KB}/kb/keep"})
        bank = _bank(tmp_path, _row())
        corpus = _corpus(tmp_path, f"{KB}/kb/a", f"{KB}/kb/keep")

        code = module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(corpus),
                "--undecline",
                f"{KB}/kb/a",
                "--ledger",
                str(ledger),
            ]
        )
        assert code == 0
        assert {e["url"] for e in json.loads(ledger.read_text("utf-8"))} == {
            f"{KB}/kb/keep"
        }

        module.main(
            [
                "coverage",
                "--bank",
                str(bank),
                "--corpus-json",
                str(corpus),
                "--ledger",
                str(ledger),
            ]
        )

        assert f"{KB}/kb/a" in capsys.readouterr().out

    def test_a_declined_then_undeclined_then_greenlit_page_stays_a_gap(
        self, tmp_path, capsys
    ):
        # The end-to-end invariant: drafting candidates never marks a page
        # covered, and no ledger state left over from the decline hides it.
        module = _load_script()
        _fake_llm(module, [CANDIDATE])
        _persisted(tmp_path)
        bank = _bank(tmp_path, _row())
        corpus = _corpus_with_files(tmp_path, (f"{KB}/kb/a", "web/a.md"))
        ledger = tmp_path / "ledger.json"
        common = ["coverage", "--bank", str(bank), "--corpus-json", str(corpus)]

        module.main(common + ["--decline", f"{KB}/kb/a", "--ledger", str(ledger)])
        module.main(common + ["--undecline", f"{KB}/kb/a", "--ledger", str(ledger)])
        assert (
            module.main(
                common
                + [
                    "--propose",
                    f"{KB}/kb/a",
                    "--model",
                    "m/x",
                    "--data-path",
                    str(tmp_path / "data"),
                    "--ledger",
                    str(ledger),
                ]
            )
            == 0
        )
        capsys.readouterr()

        module.main(common + ["--ledger", str(ledger)])

        out = capsys.readouterr().out
        assert "1 gaps" in out
        assert f"{KB}/kb/a" in out

    def test_undeclining_a_page_that_was_never_declined_says_so(self, tmp_path, capsys):
        module = _load_script()
        ledger = _ledger(tmp_path)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--undecline",
                f"{KB}/kb/a",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 0
        assert "not declined" in capsys.readouterr().out

    def test_undeclining_requires_a_ledger(self, tmp_path, capsys):
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--undecline",
                f"{KB}/kb/a",
            ]
        )

        assert code == 1
        assert "--ledger" in capsys.readouterr().err


class TestDeclineTargetsGapsOnly:
    """A decline is a disposition of a reviewed gap, not a free-text note."""

    def test_declining_a_covered_page_is_refused(self, tmp_path, capsys):
        module = _load_script()
        ledger = tmp_path / "ledger.json"

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row(f"{KB}/kb/a"))),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--decline",
                f"{KB}/kb/a",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 1
        assert not ledger.exists()
        assert "already covered" in capsys.readouterr().err

    def test_declining_a_slug_near_miss_is_refused(self, tmp_path, capsys):
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row(f"{KB}/docs/a"))),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--decline",
                f"{KB}/kb/a",
                "--ledger",
                str(tmp_path / "ledger.json"),
            ]
        )

        assert code == 1
        assert "reconcil" in capsys.readouterr().err

    def test_declining_a_url_the_corpus_does_not_hold_is_refused(
        self, tmp_path, capsys
    ):
        # A typo'd or not-yet-ingested URL would sit in the ledger and silently
        # suppress that page if it ever became a real gap.
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--decline",
                f"{KB}/kb/typoo",
                "--ledger",
                str(tmp_path / "ledger.json"),
            ]
        )

        assert code == 1
        assert "retrievable corpus" in capsys.readouterr().err


class TestLedgerDurability:
    """The ledger is the only durable record of a decline — and the only file
    this tool writes. A half-written one loses decisions permanently."""

    def test_replacing_the_ledger_keeps_the_permissions_it_already_had(self, tmp_path):
        """Same `mkstemp` 0600 clobber as the summary file, one commit point over.

        Found while verifying the `--summary-json` finding on #151: `write_ledger`
        replaces through the identical temp-then-`os.replace` path, so a ledger an
        operator widened for a teammate loses that access on the next decline.
        """
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a", "reason": "keep me"})
        ledger.chmod(0o664)
        url = f"{KB}/kb/b"

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, url)),
                "--decline",
                url,
                "--ledger",
                str(ledger),
                "--reason",
                "not worth a question",
            ]
        )

        assert stat.S_IMODE(ledger.stat().st_mode) == 0o664

    def test_an_interrupted_write_leaves_the_previous_ledger_intact(
        self, tmp_path, monkeypatch
    ):
        # Truncate-then-write would leave a mangled file here; an atomic replace
        # leaves the previous ledger byte-identical.
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a", "reason": "keep me"})
        before = ledger.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(
                    _corpus(
                        tmp_path,
                        f"{KB}/kb/a",
                        f"{KB}/kb/b",
                        f"{KB}/kb/first",
                        f"{KB}/kb/second",
                        f"{KB}/kb/mine",
                        f"{KB}/kb/other",
                        f"{KB}/kb/new",
                    )
                ),
                "--decline",
                f"{KB}/kb/b",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 1
        assert ledger.read_bytes() == before

    def test_a_failed_write_leaves_no_temp_file_behind(self, tmp_path, monkeypatch):
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"})

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(
                    _corpus(
                        tmp_path,
                        f"{KB}/kb/a",
                        f"{KB}/kb/b",
                        f"{KB}/kb/first",
                        f"{KB}/kb/second",
                        f"{KB}/kb/mine",
                        f"{KB}/kb/other",
                        f"{KB}/kb/new",
                    )
                ),
                "--decline",
                f"{KB}/kb/b",
                "--ledger",
                str(ledger),
            ]
        )

        assert [
            p.name for p in tmp_path.iterdir() if p.name.startswith(".ledger")
        ] == []

    def test_the_decline_is_merged_against_the_ledger_as_it_is_at_write_time(
        self, tmp_path
    ):
        # The read-modify-write reads immediately before writing, so a decline
        # another session recorded in between is not silently clobbered.
        module = _load_script()
        ledger = _ledger(tmp_path)
        bank = _bank(tmp_path, _row())
        corpus = _corpus(tmp_path, f"{KB}/kb/mine", f"{KB}/kb/other")
        real_read = module.read_ledger

        def racing_read(path):
            entries = real_read(path)
            if not entries:
                # Another operator's decline lands between our two reads.
                module.write_ledger(str(ledger), [{"url": f"{KB}/kb/other"}])
                module.read_ledger = real_read
                return real_read(path)
            return entries

        module.read_ledger = racing_read
        try:
            module.main(
                [
                    "coverage",
                    "--bank",
                    str(bank),
                    "--corpus-json",
                    str(corpus),
                    "--decline",
                    f"{KB}/kb/mine",
                    "--ledger",
                    str(ledger),
                ]
            )
        finally:
            module.read_ledger = real_read

        urls = {e["url"] for e in json.loads(ledger.read_text(encoding="utf-8"))}
        assert urls == {f"{KB}/kb/other", f"{KB}/kb/mine"}

    def test_declining_needs_a_corpus(self, tmp_path, capsys):
        # A decline means "I reviewed this GAP and it earns no question" — a
        # claim the operator can only make about a page the corpus shows as a
        # gap. Same rule `--propose` obeys; they are the two dispositions of one
        # decision.
        module = _load_script()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--decline",
                f"{KB}/kb/a",
                "--ledger",
                str(tmp_path / "ledger.json"),
            ]
        )

        assert code == 1
        assert "corpus" in capsys.readouterr().err

    def test_two_declines_that_race_both_survive(self, tmp_path):
        # The lost-update case: a second writer starts while the first still
        # holds the ledger. Without a lock covering read-merge-write, the second
        # reads the pre-write state and its atomic replace erases the first
        # decline -- silently, with both commands reporting success.
        module = _load_script()
        ledger = tmp_path / "ledger.json"
        bank = _bank(tmp_path, _row())
        corpus = _corpus(tmp_path, f"{KB}/kb/first", f"{KB}/kb/second")
        result = {}

        def second_writer():
            result["code"] = module.main(
                [
                    "coverage",
                    "--bank",
                    str(bank),
                    "--corpus-json",
                    str(corpus),
                    "--decline",
                    f"{KB}/kb/second",
                    "--ledger",
                    str(ledger),
                ]
            )

        with module.ledger_lock(str(ledger)):
            worker = threading.Thread(target=second_writer)
            worker.start()
            time.sleep(0.2)
            # The second writer is parked on the lock, so nothing landed yet.
            assert not ledger.exists()
            module.write_ledger(str(ledger), [{"url": f"{KB}/kb/first"}])

        worker.join(timeout=10)
        assert not worker.is_alive()
        assert result["code"] == 0
        urls = {e["url"] for e in json.loads(ledger.read_text(encoding="utf-8"))}
        assert urls == {f"{KB}/kb/first", f"{KB}/kb/second"}

    def test_ledger_mutation_is_refused_without_locking_support(
        self, tmp_path, monkeypatch, capsys
    ):
        # Warning and proceeding is not a mitigation: the lost-update race is
        # exactly what the lock exists to stop, and a decline cannot be
        # reconstructed from the bank. Refuse the mutation instead.
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/keep"})
        before = ledger.read_bytes()
        monkeypatch.setattr(module, "fcntl", None)

        code = module.main(
            [
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
        )

        assert code == 1
        assert ledger.read_bytes() == before
        assert "lock" in capsys.readouterr().err

    def test_read_only_passes_still_work_without_locking_support(
        self, tmp_path, monkeypatch, capsys
    ):
        # Only ledger *mutation* needs the lock; refusing the whole tool would
        # be a bigger loss than the guarantee is worth.
        module = _load_script()
        monkeypatch.setattr(module, "fcntl", None)

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, f"{KB}/kb/a")),
                "--ledger",
                str(_ledger(tmp_path)),
            ]
        )

        assert code == 0
        assert f"{KB}/kb/a" in capsys.readouterr().out

    def test_the_lock_is_a_sidecar_not_the_ledger_itself(self, tmp_path):
        # `write_ledger` swaps the ledger's inode via os.replace, so a lock held
        # on the ledger file would be a lock on a file the next writer never
        # opens -- the lock must live on a path that is never replaced.
        module = _load_script()
        ledger = tmp_path / "ledger.json"

        with module.ledger_lock(str(ledger)):
            module.write_ledger(str(ledger), [{"url": f"{KB}/kb/a"}])

        assert (tmp_path / "ledger.json.lock").exists()


class TestProposePathContainment:
    """A poisoned `file_path` must never reach a read, let alone a provider."""

    def test_a_traversal_path_is_refused_before_the_model_is_called(
        self, tmp_path, capsys
    ):
        module = _load_script()
        prompts = []
        _fake_llm(module, [CANDIDATE], calls=prompts)
        (tmp_path / "secret.md").write_text("SECRET", encoding="utf-8")
        (tmp_path / "data").mkdir()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", "../secret.md"))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert code == 1
        assert prompts == []
        assert "outside the data root" in capsys.readouterr().err

    def test_an_absolute_escape_is_refused_before_the_model_is_called(
        self, tmp_path, capsys
    ):
        module = _load_script()
        prompts = []
        _fake_llm(module, [CANDIDATE], calls=prompts)
        secret = tmp_path / "secret.md"
        secret.write_text("SECRET", encoding="utf-8")
        (tmp_path / "data").mkdir()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus_with_files(tmp_path, (f"{KB}/kb/a", str(secret)))),
                "--propose",
                f"{KB}/kb/a",
                "--model",
                "m/x",
                "--data-path",
                str(tmp_path / "data"),
            ]
        )

        assert code == 1
        assert prompts == []
        assert "SECRET" not in capsys.readouterr().out


class TestLedgerDirectoryDurability:
    def test_the_parent_directory_is_fsynced_after_the_replace(
        self, tmp_path, monkeypatch
    ):
        # fsyncing only the temp file leaves the *rename* unpersisted on
        # filesystems that need a directory sync — a crash after the command
        # reports success would resurrect every dismissed page.
        module = _load_script()
        synced = []
        real_fsync = os.fsync
        monkeypatch.setattr(
            os, "fsync", lambda fd: (synced.append(os.fstat(fd)), real_fsync(fd))[1]
        )

        module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(
                    _corpus(
                        tmp_path,
                        f"{KB}/kb/a",
                        f"{KB}/kb/b",
                        f"{KB}/kb/first",
                        f"{KB}/kb/second",
                        f"{KB}/kb/mine",
                        f"{KB}/kb/other",
                        f"{KB}/kb/new",
                    )
                ),
                "--decline",
                f"{KB}/kb/a",
                "--ledger",
                str(tmp_path / "ledger.json"),
            ]
        )

        import stat

        assert any(stat.S_ISDIR(st.st_mode) for st in synced)

    def test_a_failed_directory_sync_reports_the_write_as_committed(
        self, tmp_path, capsys
    ):
        # `os.replace` has already committed by the time the directory is
        # synced. Reporting "cannot write ledger" here would tell the operator
        # nothing happened and invite a retry, when in fact the decline is
        # recorded and only its durability is unconfirmed.
        module = _load_script()
        ledger = tmp_path / "ledger.json"
        real_fsync = os.fsync

        def picky(fd):
            import stat

            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("cannot sync directory")
            return real_fsync(fd)

        original = os.fsync
        os.fsync = picky
        try:
            code = module.main(
                [
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
            )
        finally:
            os.fsync = original

        assert code == 1
        err = capsys.readouterr().err
        assert "WAS updated" in err
        assert "not retry" in err.lower()
        # The claim in the message has to be true: the decline really landed.
        assert json.loads(ledger.read_text("utf-8"))[0]["url"] == f"{KB}/kb/a"

    def test_an_unsyncable_directory_fails_before_anything_is_mutated(
        self, tmp_path, capsys
    ):
        # The directory handle is taken BEFORE the replace, so a directory that
        # cannot be synced at all fails while the ledger is still untouched --
        # a clean "nothing happened", which is the only honest thing to retry.
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/keep"})
        before = ledger.read_bytes()
        real_open = os.open

        def picky(path, flags, *args, **kwargs):
            if os.path.isdir(path):
                raise OSError("cannot open directory")
            return real_open(path, flags, *args, **kwargs)

        os.open = picky
        try:
            code = module.main(
                [
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
            )
        finally:
            os.open = real_open

        assert code == 1
        assert ledger.read_bytes() == before
        assert "cannot write ledger" in capsys.readouterr().err

    def test_a_semantically_corrupt_ledger_is_an_operational_failure(
        self, tmp_path, capsys
    ):
        # Syntactically valid JSON, one broken entry: the run must stop rather
        # than report a clean coverage pass built on a partial decline set.
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a"}, {"ur1": f"{KB}/kb/b"})

        code = module.main(
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

        assert code == 1
        err = capsys.readouterr().err
        assert "ledger" in err and "1" in err

    def test_declining_into_a_corrupt_ledger_is_refused(self, tmp_path, capsys):
        # Appending here would carry the broken entry forward while presenting
        # the readable subset as the authoritative decline set.
        module = _load_script()
        ledger = _ledger(tmp_path, {"url": ""})
        before = ledger.read_bytes()

        code = module.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(
                    _corpus(
                        tmp_path,
                        f"{KB}/kb/a",
                        f"{KB}/kb/b",
                        f"{KB}/kb/first",
                        f"{KB}/kb/second",
                        f"{KB}/kb/mine",
                        f"{KB}/kb/other",
                        f"{KB}/kb/new",
                    )
                ),
                "--decline",
                f"{KB}/kb/new",
                "--ledger",
                str(ledger),
            ]
        )

        assert code == 1
        assert ledger.read_bytes() == before


# --------------------------------------------------------------------------- #
# `drift` — hash tripwire, then LLM diff (group 4)
# --------------------------------------------------------------------------- #

from src.utils.goldenset_maintenance import page_digest  # noqa: E402

GPU_HTML = "<html><body><p>Add #SBATCH --gpus=1 for one GPU.</p></body></html>"
GPU_HTML_CHANGED = "<html><body><p>Add #SBATCH --gpus=2 for one GPU.</p></body></html>"


KB_HOST = "docs.rc.fas.harvard.edu"


def _drift_head(bank, *hosts):
    """`drift` argv with its required allowlist — an empty one authorizes nothing."""
    return [
        "drift",
        "--bank",
        str(bank),
        "--allowed-hosts",
        *(hosts or (KB_HOST, "slurm.schedmd.com")),
    ]


def _locked_row(*sources, hashes=None, **extra):
    row = {
        "user_input": "How many GPUs?",
        "reference": "Add #SBATCH --gpus=1.",
        "sources": list(sources),
        "status": "locked",
    }
    if hashes is not None:
        row["source_hashes"] = hashes
    row.update(extra)
    return row


def _fake_pages(module, pages, errors=None):
    """Replace the CLI's page fetcher with a canned one (no network)."""

    def build():
        def fetch(url):
            if errors and url in errors:
                raise RuntimeError(errors[url])
            return pages[url]

        return fetch

    module.build_fetch_html = build


class TestDriftSubcommand:
    """4.1 / 4.4 — the tripwire, wired, and provably read-only."""

    def test_reports_a_changed_source_and_exits_zero(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        # A finding is work to do, not a broken run (the cron contract).
        assert code == 0
        assert url in out

    def test_leaves_the_bank_byte_unchanged(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        before = bank.read_bytes()
        _fake_pages(script, {url: GPU_HTML_CHANGED})

        script.main([*_drift_head(bank)])

        assert bank.read_bytes() == before

    def test_a_matching_hash_reports_no_drift(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML})

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        assert code == 0
        assert "0 drifted" in out

    def test_a_missing_baseline_is_named_not_silently_passed(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url))
        _fake_pages(script, {url: GPU_HTML})

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        assert code == 0
        assert "no baseline" in out.lower()
        assert url in out

    def test_all_sources_unreachable_abstains_and_exits_nonzero(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {}, errors={url: "connection timed out"})

        code = script.main([*_drift_head(bank)])
        captured = capsys.readouterr()

        # Nothing was read, so "no drift" would be a false clean over the bank.
        assert code == 1
        assert "abstain" in captured.err.lower()

    def test_a_bank_with_no_locked_rows_says_so_rather_than_reading_clean(
        self, tmp_path, capsys
    ):
        script = _load_script()
        bank = _bank(tmp_path, _row(f"{KB}/kb/gpu"))
        _fake_pages(script, {})

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        assert code == 0
        assert "0 checked" in out

    def test_print_hashes_emits_a_paste_ready_baseline(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url))
        _fake_pages(script, {url: GPU_HTML})

        script.main([*_drift_head(bank), "--print-hashes"])
        out = capsys.readouterr().out

        # Without a way to obtain a hash, `source_hashes` could never be filled in
        # and drift would sit inert forever.
        assert page_digest(GPU_HTML) in out
        assert '"source_hashes"' in out

    def test_print_hashes_says_why_it_emitted_nothing(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, status="draft"))
        _fake_pages(script, {url: GPU_HTML})

        script.main([*_drift_head(bank), "--print-hashes"])
        out = capsys.readouterr().out

        # Baselines are produced for `locked` rows only — declaring the lock is
        # the human act, and the hash records what was vouched for. An operator
        # asking for a draft row's baseline is in the right place at the wrong
        # step, and silence reads as "the tool is broken".
        assert "no `source_hashes` blocks" in out
        assert "status: locked" in out

    def test_a_bank_with_nothing_to_print_still_exits_zero(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, status="draft"))
        _fake_pages(script, {url: GPU_HTML})

        assert script.main([*_drift_head(bank), "--print-hashes"]) == 0


class TestDriftVerdictCli:
    """4.3 — the model diff is advisory and fires only on a moved hash."""

    def test_the_model_is_not_called_when_nothing_moved(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML})
        calls = []
        _fake_llm(script, {"verdict": "broken"}, calls=calls)

        script.main([*_drift_head(bank), "--model", "anthropic/x"])

        assert calls == []

    def test_a_verdict_is_printed_beside_the_finding(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})
        _fake_llm(script, {"verdict": "broken", "explanation": "it says 2 now"})

        code = script.main([*_drift_head(bank), "--model", "anthropic/x"])
        out = capsys.readouterr().out

        assert code == 0
        assert "broken" in out
        assert "it says 2 now" in out

    def test_without_a_model_the_finding_still_stands(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        # Hash-only is the cheap cron mode: the tripwire alone is a real finding.
        assert code == 0
        assert url in out

    def test_a_holds_verdict_does_not_hide_the_row(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})
        _fake_llm(script, {"verdict": "holds", "explanation": "unrelated edit"})

        script.main([*_drift_head(bank), "--model", "anthropic/x"])
        out = capsys.readouterr().out

        assert url in out
        assert "holds" in out


class TestDriftFetchPolicyCli:
    """A bank-controlled URL must not become an unrestricted outbound request."""

    def test_a_loopback_source_is_refused_and_reported(self, tmp_path, capsys):
        script = _load_script()
        url = "http://127.0.0.1:9000/admin"
        # A second, readable row so this exercises a refusal inside an otherwise
        # normal run rather than the all-refused abstention path.
        ok = f"{KB}/kb/gpu"
        bank = _bank(
            tmp_path,
            _locked_row(url, hashes={url: "sha256:" + "0" * 64}),
            _locked_row(ok, hashes={ok: page_digest(GPU_HTML)}),
        )
        fetched = []

        def build():
            def fetch(target):
                fetched.append(target)
                return GPU_HTML

            return fetch

        script.build_fetch_html = build

        code = script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        assert fetched == [ok]
        assert code == 0
        assert "refused" in out.lower()
        assert url in out

    def test_allowed_hosts_narrows_what_drift_will_contact(self, tmp_path, capsys):
        script = _load_script()
        listed, other = f"{KB}/kb/gpu", "https://slurm.schedmd.com/mpi"
        bank = _bank(
            tmp_path,
            _locked_row(listed, hashes={listed: page_digest(GPU_HTML)}),
            _locked_row(other, hashes={other: page_digest(GPU_HTML)}),
        )
        _fake_pages(script, {listed: GPU_HTML, other: GPU_HTML})

        code = script.main(_drift_head(bank, "docs.rc.fas.harvard.edu"))
        out = capsys.readouterr().out

        assert code == 0
        assert "slurm.schedmd.com" in out
        assert "refused" in out.lower()

    def test_the_allowlist_is_required(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))

        # No allow-everything default: this tool already refuses to guess a
        # sitemap floor for the same reason — a convenient default that quietly
        # produces the wrong answer is worse than a required flag.
        with pytest.raises(SystemExit):
            script.main(["drift", "--bank", str(bank)])


class TestDriftEvidence:
    """A verdict about text the operator cannot see is not reviewable."""

    def test_show_text_prints_the_page_the_model_judged(self, tmp_path, capsys):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})
        _fake_llm(script, {"verdict": "broken", "explanation": "says 2"})

        script.main([*_drift_head(bank), "--model", "anthropic/x", "--show-text"])
        out = capsys.readouterr().out

        assert "--gpus=2" in out

    def test_the_default_report_points_at_the_flag_instead_of_dumping_pages(
        self, tmp_path, capsys
    ):
        script = _load_script()
        url = f"{KB}/kb/gpu"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML_CHANGED})

        script.main([*_drift_head(bank)])
        out = capsys.readouterr().out

        assert "--gpus=2" not in out
        assert "--show-text" in out


class TestDriftTransport:
    """What the drift fetch actually does on the wire."""

    def test_the_fetcher_verifies_tls(self, monkeypatch):
        script = _load_script()
        from src.data_manager.collectors.scrapers import sitemap_source

        seen = {}

        def fake(url, **kwargs):
            seen.update(kwargs)
            return GPU_HTML

        monkeypatch.setattr(sitemap_source, "fetch_sitemap_text", fake)
        script.build_fetch_html()(f"{KB}/kb/gpu")

        # The ingest defaults to verify=False. Inheriting that here would let a
        # network attacker manufacture drift findings and put chosen text into
        # the prompt sent to the model provider.
        assert seen["verify"] is True

    def test_the_fetcher_caps_the_body_below_the_ingest_ceiling(self, monkeypatch):
        script = _load_script()
        from src.data_manager.collectors.scrapers import sitemap_source

        seen = {}

        def fake(url, **kwargs):
            seen.update(kwargs)
            return GPU_HTML

        monkeypatch.setattr(sitemap_source, "fetch_sitemap_text", fake)
        script.build_fetch_html()(f"{KB}/kb/gpu")

        assert seen["max_bytes"] == script.MAX_PAGE_BYTES

    def test_the_fetcher_refuses_to_leave_https(self, monkeypatch):
        script = _load_script()
        from src.data_manager.collectors.scrapers import sitemap_source

        seen = {}

        def fake(url, **kwargs):
            seen.update(kwargs)
            return GPU_HTML

        monkeypatch.setattr(sitemap_source, "fetch_sitemap_text", fake)
        script.build_fetch_html()(f"{KB}/kb/gpu")

        # `verify=True` is only worth anything while the connection stays TLS.
        # A same-host https -> http redirect passes the fetcher's host check, so
        # the downgrade has to be refused at the transport itself.
        assert seen["require_https"] is True

    def test_an_all_refused_run_exits_nonzero(self, tmp_path, capsys):
        script = _load_script()
        url = "http://127.0.0.1:9000/admin"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        _fake_pages(script, {url: GPU_HTML})

        code = script.main([*_drift_head(bank)])
        captured = capsys.readouterr()

        # A mistyped --allowed-hosts must not read as a clean bill of health.
        assert code == 1
        assert "abstain" in captured.err.lower()


class TestPrintHashesNeverLosesABaseline:
    """`--print-hashes` is labelled paste-ready, so it has to be complete.

    A row with one readable source and one unreachable one emitted a block
    holding only the readable URL. Pasting that over the row's `source_hashes`
    silently drops the other baseline — the next run calls it unbaselined and
    the confirmation history is gone, from an output that invited the paste.
    """

    def test_an_unreadable_source_keeps_its_existing_baseline(self, tmp_path, capsys):
        script = _load_script()
        good, dead = f"{KB}/kb/gpu", f"{KB}/kb/dead"
        stored_dead = "sha256:" + "a" * 64
        bank = _bank(
            tmp_path,
            _locked_row(
                good,
                dead,
                hashes={good: page_digest(GPU_HTML), dead: stored_dead},
            ),
        )

        def build():
            def fetch(url):
                if url == dead:
                    raise RuntimeError("timeout")
                return GPU_HTML

            return fetch

        script.build_fetch_html = build

        script.main([*_drift_head(bank), "--print-hashes"])
        out = capsys.readouterr().out
        block = json.loads(out[out.index("{") :])["source_hashes"]

        assert block[good] == page_digest(GPU_HTML)
        assert block[dead] == stored_dead

    def test_a_block_missing_a_source_entirely_is_flagged_incomplete(
        self, tmp_path, capsys
    ):
        script = _load_script()
        good, dead = f"{KB}/kb/gpu", f"{KB}/kb/dead"
        # `dead` is both unreadable AND unbaselined — nothing to carry forward,
        # so the block genuinely cannot be complete. Say so instead of implying
        # it is safe to paste.
        bank = _bank(
            tmp_path, _locked_row(good, dead, hashes={good: page_digest(GPU_HTML)})
        )

        def build():
            def fetch(url):
                if url == dead:
                    raise RuntimeError("timeout")
                return GPU_HTML

            return fetch

        script.build_fetch_html = build

        script.main([*_drift_head(bank), "--print-hashes"])
        out = capsys.readouterr().out

        assert "INCOMPLETE" in out
        assert dead in out


def _report_argv(bank, corpus, sources, *extra):
    """`report` argv: one cron line covering all three passes."""
    return [
        "report",
        "--bank",
        str(bank),
        "--corpus-json",
        str(corpus),
        "--sources",
        str(sources),
        "--allowed-hosts",
        KB_HOST,
        *extra,
    ]


def _sources_list(tmp_path, *urls):
    path = tmp_path / "sources.list"
    path.write_text("".join(f"{u}\n" for u in urls), encoding="utf-8")
    return path


class TestReportSubcommand:
    """5.1 — one unattended pass emitting all three work lists."""

    def test_prints_all_three_work_lists(self, tmp_path, capsys):
        script = _load_script()
        live, gone, uncovered = f"{KB}/kb/live", f"{KB}/kb/gone", f"{KB}/kb/uncovered"
        bank = _bank(
            tmp_path,
            _locked_row(live, hashes={live: page_digest(GPU_HTML)}),
            _row(gone),
        )
        corpus = _corpus(tmp_path, live, uncovered)
        sources = _sources_list(tmp_path, live, uncovered)
        _fake_pages(script, {live: GPU_HTML_CHANGED})

        code = script.main(_report_argv(bank, corpus, sources))
        out = capsys.readouterr().out

        assert code == 0
        assert uncovered in out  # coverage gap
        assert gone in out  # orphan
        assert live in out  # drift

    def test_findings_do_not_fail_the_cron_job(self, tmp_path, capsys):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live, f"{KB}/kb/uncovered")
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})

        # A gap and a drifted row are work to do, not a broken run. A cron that
        # exits non-zero on findings trains its reader to ignore the alert.
        assert script.main(_report_argv(bank, corpus, sources)) == 0

    def test_modifies_no_file(self, tmp_path):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live, f"{KB}/kb/uncovered")
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})
        before = (bank.read_bytes(), corpus.read_bytes(), sources.read_bytes())

        script.main(_report_argv(bank, corpus, sources))

        assert (bank.read_bytes(), corpus.read_bytes(), sources.read_bytes()) == before

    def test_an_unreachable_corpus_exits_nonzero(self, tmp_path, capsys):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})

        # The one thing that MUST page someone: the report could not read what it
        # is reporting on.
        code = script.main(_report_argv(bank, tmp_path / "nope.json", sources))

        assert code == 1
        assert "corpus" in capsys.readouterr().err.lower()

    def test_a_broken_pass_does_not_suppress_the_others(self, tmp_path, capsys):
        script = _load_script()
        live, gone = f"{KB}/kb/live", f"{KB}/kb/gone"
        bank = _bank(
            tmp_path,
            _locked_row(live, hashes={live: page_digest(GPU_HTML)}),
            _row(gone),
        )
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})

        # Corpus unreadable, so coverage cannot run — but a nightly report that
        # stops at its first broken pass tells its reader strictly less than one
        # that says which passes did work.
        code = script.main(_report_argv(bank, tmp_path / "nope.json", sources))
        captured = capsys.readouterr()

        assert code == 1
        assert gone in captured.out  # orphans still ran
        assert live in captured.out  # drift still ran

    def test_an_abstaining_inventory_exits_nonzero(self, tmp_path, capsys):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path)
        _fake_pages(script, {live: GPU_HTML})

        # An empty inventory means orphan detection never happened; reporting
        # "no orphans" would be a clean bill of health for a check that did not run.
        code = script.main(_report_argv(bank, corpus, sources))

        assert code == 1
        assert "ABSTAINED" in capsys.readouterr().err

    def test_no_model_is_called_unless_one_is_named(self, tmp_path):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})
        calls = []
        _fake_llm(script, {"verdict": "broken"}, calls=calls)

        script.main(_report_argv(bank, corpus, sources))

        # Unattended by definition. The hash tripwire is the finding; paying a
        # provider per drifted row every night is opt-in.
        assert calls == []


class TestReportSummaryJson:
    """5.1 — a machine-readable signal, so a cron can tell three states apart.

    The exit code only carries two (`0` ran, `1` broke), and the spec pins
    findings to zero. A wrapper that keys notification on the exit code
    therefore cannot distinguish "clean" from "there is work to do" — which is
    the state the whole job exists to surface.
    """

    def _run(self, tmp_path, script, bank, corpus, sources, out):
        return script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )

    def test_counts_the_findings_of_every_pass(self, tmp_path):
        script = _load_script()
        live, gone, uncovered = f"{KB}/kb/live", f"{KB}/kb/gone", f"{KB}/kb/uncovered"
        bank = _bank(
            tmp_path,
            _locked_row(live, hashes={live: page_digest(GPU_HTML)}),
            _row(gone),
        )
        corpus = _corpus(tmp_path, live, uncovered)
        sources = _sources_list(tmp_path, live, uncovered)
        _fake_pages(script, {live: GPU_HTML_CHANGED})
        out = tmp_path / "summary.json"

        code = self._run(tmp_path, script, bank, corpus, sources, out)
        summary = json.loads(out.read_text())

        assert code == 0
        assert summary["gaps"] == 1  # uncovered
        assert summary["orphans"] == 1  # gone
        assert summary["drifted"] == 1  # live moved
        assert summary["failed_passes"] == []

    def test_a_clean_bank_reports_zero_findings(self, tmp_path):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        self._run(tmp_path, script, bank, corpus, sources, out)
        summary = json.loads(out.read_text())

        # Distinguishable from the case above by counts alone — no text parsing.
        assert summary["gaps"] == 0
        assert summary["orphans"] == 0
        assert summary["drifted"] == 0
        assert summary["failed_passes"] == []

    def test_a_broken_pass_is_named_in_the_summary(self, tmp_path):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        code = self._run(tmp_path, script, bank, tmp_path / "nope.json", sources, out)
        summary = json.loads(out.read_text())

        assert code == 1
        assert any("coverage" in f for f in summary["failed_passes"])

    def test_the_summary_is_written_even_when_a_pass_broke(self, tmp_path):
        # The wrapper reads this file to decide what to say; if a broken run
        # left no file, the failure path would look like a clean one.
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        self._run(tmp_path, script, bank, tmp_path / "nope.json", sources, out)

        assert out.exists()

    def test_no_summary_file_is_written_unless_asked(self, tmp_path):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})

        script.main(_report_argv(bank, corpus, sources))

        assert list(tmp_path.glob("*summary*")) == []

    def test_malformed_bank_overwrites_healthy_summary_with_failure_state(
        self, tmp_path, capsys
    ):
        script = _load_script()
        out = tmp_path / "summary.json"
        # Pre-existing healthy summary from a previous good run.
        out.write_text(
            json.dumps(
                {
                    "census": {"total": 5, "locked": 5, "draft": 0, "anchor_type": {}},
                    "gaps": 0,
                    "orphans": 0,
                    "drifted": 0,
                    "failed_passes": [],
                    "notify": False,
                }
            ),
            encoding="utf-8",
        )
        bank = tmp_path / "bank.json"
        bank.write_text("{not valid json", encoding="utf-8")
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )
        summary = json.loads(out.read_text())

        assert code == 1
        assert any("bank" in f for f in summary["failed_passes"])
        assert summary["census"] is None
        assert summary["notify"] is True
        # Old success values must be gone — a monitor reading this file alone sees failure.
        assert (
            summary.get("gaps") != 0
            or summary.get("drifted") != 0
            or (summary["failed_passes"] != [] and summary["census"] is None)
        )

    def test_a_schema_malformed_bank_writes_the_failure_summary_too(
        self, tmp_path, capsys
    ):
        """A row whose `anchor_type` is a list is valid JSON and a valid list.

        So it survives `load_bank`, and only dies inside the census, which uses
        that value as a dict key — `TypeError: unhashable type`. That is not an
        `OperationalError`, so it escapes both the report handler and `main`,
        killing the process with a traceback BEFORE the summary is written. The
        monitor is then left reading the previous run's healthy file.
        """
        script = _load_script()
        out = tmp_path / "summary.json"
        out.write_text(json.dumps({"notify": False}), encoding="utf-8")
        bank = tmp_path / "bank.json"
        bank.write_text(
            json.dumps([{"user_input": "q", "anchor_type": ["heading", "table"]}]),
            encoding="utf-8",
        )
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )
        summary = json.loads(out.read_text())

        assert code == 1
        assert summary["census"] is None
        assert summary["notify"] is True
        assert any("bank" in f for f in summary["failed_passes"])
        # The message has to name the field, or the operator has a traceback's
        # worth of information ("unhashable type") and no idea which row.
        assert "anchor_type" in " ".join(summary["failed_passes"])

    def test_a_numeric_anchor_type_mixed_with_text_writes_the_failure_summary(
        self, tmp_path
    ):
        """A number is hashable, so it survives the census and dies at the sort.

        `bank_status_counts` keys the distribution by whatever `anchor_type`
        holds, and a number is a perfectly good dict key — so the unhashable
        guard never sees it. The run instead dies on
        `sorted(census["anchor_type"].items())`, comparing `int` with `str`,
        which is a `TypeError` raised AFTER the bank handler and BEFORE the
        summary write: traceback, no file, monitor still reading last night's
        healthy snapshot.
        """
        script = _load_script()
        out = tmp_path / "summary.json"
        out.write_text(json.dumps({"notify": False}), encoding="utf-8")
        bank = _bank(
            tmp_path,
            _row(f"{KB}/kb/a", anchor_type=3),
            _row(f"{KB}/kb/b", anchor_type="faq"),
        )
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )
        summary = json.loads(out.read_text())

        assert code == 1
        assert summary["census"] is None
        assert summary["notify"] is True
        assert "anchor_type" in " ".join(summary["failed_passes"])

    def test_a_bank_whose_anchor_types_are_all_numeric_is_rejected(self, tmp_path):
        """No sort to crash here — which is exactly why the type must be checked.

        One numeric `anchor_type` and nothing to compare it against sorts fine,
        and `json.dump` then stringifies the key, so the census reports a bucket
        `"3"` that is nowhere in the bank. Accepting it means the health file
        quietly disagrees with the file it is describing; the tool has to refuse
        the input rather than lean on a downstream crash to notice.
        """
        script = _load_script()
        live = f"{KB}/kb/live"
        out = tmp_path / "summary.json"
        bank = _bank(
            tmp_path,
            _locked_row(live, hashes={live: page_digest(GPU_HTML)}, anchor_type=3),
        )
        _fake_pages(script, {live: GPU_HTML})

        code = script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--summary-json",
                str(out),
            ]
        )
        summary = json.loads(out.read_text())

        assert code == 1
        assert summary["census"] is None
        assert "anchor_type" in " ".join(summary["failed_passes"])

    def test_replacing_a_summary_keeps_the_readers_it_already_had(self, tmp_path):
        """`mkstemp` creates 0600 and `os.replace` installs that over the target.

        A summary an operator made group-readable for a monitor running as
        another Unix user therefore becomes unreadable on the first report run —
        the health file goes silent in exactly the way a monitor cannot
        distinguish from "nothing to report".
        """
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"
        out.write_text("{}", encoding="utf-8")
        out.chmod(0o644)

        self._run(tmp_path, script, bank, corpus, sources, out)

        assert stat.S_IMODE(out.stat().st_mode) == 0o644

    def test_missing_bank_writes_failure_summary_and_exits_nonzero(
        self, tmp_path, capsys
    ):
        script = _load_script()
        out = tmp_path / "summary.json"
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")
        # Bank path does not exist.
        bank = tmp_path / "no_such_bank.json"

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )

        assert code == 1
        assert out.exists(), "summary must be written even when the bank is missing"
        summary = json.loads(out.read_text())
        assert any("bank" in f for f in summary["failed_passes"])
        assert summary["census"] is None
        assert summary["notify"] is True

    def test_healthy_bank_still_writes_normal_summary(self, tmp_path):
        # Regression: the bank-load-failure path must not break the happy path.
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        code = self._run(tmp_path, script, bank, corpus, sources, out)
        summary = json.loads(out.read_text())

        assert code == 0
        assert summary["census"] is not None
        assert summary["failed_passes"] == []
        # `notify` is derived from _NOTIFY_ON buckets; a clean bank gives False.
        assert summary["notify"] is False

    def test_write_interrupted_before_commit_leaves_prior_file_intact(
        self, tmp_path, monkeypatch, capsys
    ):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"
        healthy = json.dumps(
            {"status": "healthy", "failed_passes": [], "notify": False}
        )
        out.write_text(healthy, encoding="utf-8")
        before = out.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )

        assert code == 1
        assert "cannot write" in capsys.readouterr().err.lower()
        assert out.read_bytes() == before, "pre-existing file must be left intact"
        assert [
            p for p in tmp_path.iterdir() if p.suffix == ".tmp"
        ] == [], "no temp file litter may remain after a failed write"

    def test_a_failed_refresh_still_names_the_bank_error(
        self, tmp_path, monkeypatch, capsys
    ):
        """Two failures at once must not hide the one the operator can act on.

        The bank is unreadable AND the summary refresh fails. The write error is
        raised from inside the bank handler, so left alone it replaces the bank
        error as the exception `main` prints — the operator is told the disk is
        full and never that their bank is malformed. Both have to be on stderr,
        and the prior summary is left stale (which is why the exit code, not the
        file, is the authoritative signal).
        """
        script = _load_script()
        out = tmp_path / "summary.json"
        healthy = json.dumps({"failed_passes": [], "notify": False})
        out.write_text(healthy, encoding="utf-8")
        bank = tmp_path / "bank.json"
        bank.write_text("{not valid json", encoding="utf-8")
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")
        monkeypatch.setattr(
            os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        )

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out)]
        )
        err = capsys.readouterr().err.lower()

        assert code == 1
        assert "cannot read" in err and str(bank).lower() in err
        assert "cannot write" in err
        assert out.read_text() == healthy, "a failed refresh leaves the file stale"


def _supplementary_gid():
    """A group this process belongs to that is NOT its default group, or None.

    The whole point of the ownership fix is a target whose GROUP carries the
    grant, so the test needs a second group to move the file into. An
    unprivileged run cannot invent one.
    """
    for gid in os.getgroups():
        if gid != os.getgid():
            return gid
    return None


#: A POSIX ACL granting group 4242 read, in the on-disk format `system.
#: posix_acl_access` uses (acl(5)): u32 version 2, then {u16 tag, u16 perm,
#: u32 id} entries ordered by tag. Built here rather than shelled out to
#: `setfacl`, so the test needs no binary that CI may not carry.
def _acl_granting_group_read(gid=4242):
    import struct

    undefined = 0xFFFFFFFF
    entries = [
        (0x01, 6, undefined),  # ACL_USER_OBJ  rw-
        (0x04, 4, undefined),  # ACL_GROUP_OBJ r--
        (0x08, 4, gid),  # ACL_GROUP     r--  <- the grant under test
        (0x10, 4, undefined),  # ACL_MASK      r--
        (0x20, 0, undefined),  # ACL_OTHER     ---
    ]
    return struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, perm, ident) for tag, perm, ident in entries
    )


def _set_acl_or_skip(path):
    if not hasattr(os, "setxattr"):
        pytest.skip("extended attributes are a Linux thing")
    acl = _acl_granting_group_read()
    try:
        os.setxattr(str(path), "system.posix_acl_access", acl)
    except OSError as exc:
        pytest.skip(f"filesystem carries no POSIX ACLs: {exc}")
    return acl


class TestReplacementKeepsTheAccessItsTargetHad:
    """`os.replace` installs the temp file's metadata, not just its bytes.

    `mkstemp` makes a 0600 file owned by the writer with no extended
    attributes, so every grant an operator put on the target — mode bits, a
    group that could read it, a named-user ACL — is revoked by the next
    successful write. For the `--summary-json` health file that is the worst
    kind of breakage: the monitor stops being able to read it and cannot tell
    that apart from "nothing to report".
    """

    def _report(self, script, tmp_path, out):
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        _fake_pages(script, {live: GPU_HTML})
        return script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--summary-json",
                str(out),
            ]
        )

    def _decline(self, script, tmp_path, ledger):
        url = f"{KB}/kb/b"
        return script.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(_corpus(tmp_path, url)),
                "--decline",
                url,
                "--ledger",
                str(ledger),
                "--reason",
                "not worth a question",
            ]
        )

    def test_summary_keeps_the_group_that_could_read_it(self, tmp_path):
        gid = _supplementary_gid()
        if gid is None:
            pytest.skip("no supplementary group to hand the file to")
        script = _load_script()
        out = tmp_path / "summary.json"
        out.write_text("{}", encoding="utf-8")
        os.chown(out, -1, gid)
        out.chmod(0o640)

        assert self._report(script, tmp_path, out) == 0

        # 0640 alone is not the grant: the group owning the file is.
        assert out.stat().st_gid == gid
        assert stat.S_IMODE(out.stat().st_mode) == 0o640

    def test_summary_keeps_an_acl_that_named_a_reader(self, tmp_path):
        script = _load_script()
        out = tmp_path / "summary.json"
        out.write_text("{}", encoding="utf-8")
        acl = _set_acl_or_skip(out)

        assert self._report(script, tmp_path, out) == 0

        assert os.getxattr(str(out), "system.posix_acl_access") == acl

    def test_ledger_keeps_the_group_that_could_read_it(self, tmp_path):
        """Same commit step, second call site — the ledger replaces the same way."""
        gid = _supplementary_gid()
        if gid is None:
            pytest.skip("no supplementary group to hand the file to")
        script = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a", "reason": "keep me"})
        os.chown(ledger, -1, gid)
        ledger.chmod(0o640)

        self._decline(script, tmp_path, ledger)

        assert ledger.stat().st_gid == gid
        assert stat.S_IMODE(ledger.stat().st_mode) == 0o640

    def test_ledger_keeps_an_acl_that_named_a_reader(self, tmp_path):
        script = _load_script()
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/a", "reason": "keep me"})
        acl = _set_acl_or_skip(ledger)

        self._decline(script, tmp_path, ledger)

        assert os.getxattr(str(ledger), "system.posix_acl_access") == acl


class TestOutputPathsMayNotAliasInputs:
    """A file this tool WRITES must never be one it has to READ.

    `os.replace` installs the replacement over the target's directory entry, so
    a `--summary-json` pointed at the bank does not "also write the summary" —
    it destroys the bank. The bank-load-failure summary made that reachable on
    the one run where the operator most needs the input preserved: the bank is
    malformed, they are about to go repair it, and the failure summary lands on
    top of it. The refusal is up front, before any pass runs and before any
    write, so nothing is half-done when the run is rejected.
    """

    def _malformed_bank(self, tmp_path):
        bank = tmp_path / "bank.json"
        bank.write_text("{not valid json", encoding="utf-8")
        return bank

    def test_summary_json_pointed_at_a_malformed_bank_does_not_eat_it(
        self, tmp_path, capsys
    ):
        script = _load_script()
        bank = self._malformed_bank(tmp_path)
        before = bank.read_bytes()
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(bank)]
        )
        err = capsys.readouterr().err

        assert code == 1
        assert bank.read_bytes() == before, "the bank the operator must repair is gone"
        assert "--summary-json" in err and "--bank" in err

    def test_summary_json_pointed_at_a_healthy_bank_is_refused_too(self, tmp_path):
        """The success path replaces the target as thoroughly as the failure one."""
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        before = bank.read_bytes()
        _fake_pages(script, {live: GPU_HTML})

        code = script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--summary-json",
                str(bank),
            ]
        )

        assert code == 1
        assert bank.read_bytes() == before

    def test_a_different_spelling_of_the_same_file_is_still_the_same_file(
        self, tmp_path
    ):
        """Refusal is on file identity, not on the string the operator typed.

        A deployment reaches its data through a symlinked directory
        (`/srv/current -> /srv/releases/42`), so the two flags can name one file
        without matching character for character. `os.replace` resolves the path
        and eats the bank either way.
        """
        script = _load_script()
        real = tmp_path / "releases"
        real.mkdir()
        bank = self._malformed_bank(real)
        before = bank.read_bytes()
        link = tmp_path / "current"
        link.symlink_to(real, target_is_directory=True)
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [
                *_report_argv(bank, corpus, sources),
                "--summary-json",
                str(link / "bank.json"),
            ]
        )

        assert code == 1
        assert bank.read_bytes() == before

    def test_a_hard_link_to_an_input_is_the_same_file(self, tmp_path):
        """Identity is the inode, not the name.

        A hard link survives `rename(2)` where a second name for the same
        directory entry (a bind-mounted path, which no test can create
        unprivileged) does not — but both are one file wearing two names, and
        the flags have conflated an input with an output either way.
        """
        script = _load_script()
        bank = self._malformed_bank(tmp_path)
        alias = tmp_path / "summary.json"
        os.link(bank, alias)
        corpus = _corpus(tmp_path, f"{KB}/kb/live")
        sources = _sources_list(tmp_path, f"{KB}/kb/live")

        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(alias)]
        )

        assert code == 1
        assert alias.read_text() == "{not valid json"

    def test_summary_json_pointed_at_the_ledger_is_refused(self, tmp_path, capsys):
        """The ledger is the one record that cannot be re-derived from the bank."""
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        ledger = _ledger(tmp_path, {"url": f"{KB}/kb/gone", "reason": "retired"})
        before = ledger.read_bytes()
        _fake_pages(script, {live: GPU_HTML})

        code = script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--ledger",
                str(ledger),
                "--summary-json",
                str(ledger),
            ]
        )
        err = capsys.readouterr().err

        assert code == 1
        assert ledger.read_bytes() == before
        assert "--ledger" in err and "--summary-json" in err

    def test_two_outputs_on_one_path_are_refused_before_either_exists(self, tmp_path):
        """Identity has to hold for a file that is not there yet.

        `samefile` needs both ends on disk, and on a first run neither output is
        — a missing ledger legitimately means "nothing declined yet". Left
        unchecked the collision surfaces a night later, when the next run reads
        the summary it wrote as if it were the ledger and refuses that instead.
        """
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        _fake_pages(script, {live: GPU_HTML})
        shared = tmp_path / "nested" / "state.json"
        shared.parent.mkdir()

        code = script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--ledger",
                str(shared),
                "--summary-json",
                str(tmp_path / "nested" / ".." / "nested" / "state.json"),
            ]
        )

        assert code == 1
        assert not shared.exists(), "the run must be refused before anything is written"

    def test_ledger_pointed_at_the_corpus_dump_is_refused(self, tmp_path, capsys):
        """The same defect one output over — and this one is not caught by luck.

        A `--ledger` aliasing the `--bank` happens to bounce off decline-entry
        validation (bank rows carry no `url`). A corpus dump is a list of objects
        that each DO have one, so it validates as a ledger: `--undecline` reads
        the corpus as a decline list, drops the named row, and writes the
        remainder back as the ledger. Verified before the guard existed — the
        run printed `undeclined: … -> corpus.json`, exited zero, and left the
        dump one row short and reindented.
        """
        script = _load_script()
        url = f"{KB}/kb/b"
        corpus = _corpus(tmp_path, url, f"{KB}/kb/c")
        before = corpus.read_bytes()

        code = script.main(
            [
                "coverage",
                "--bank",
                str(_bank(tmp_path, _row())),
                "--corpus-json",
                str(corpus),
                "--undecline",
                url,
                "--ledger",
                str(corpus),
            ]
        )
        err = capsys.readouterr().err

        assert code == 1
        assert corpus.read_bytes() == before
        assert "--ledger" in err and "--corpus-json" in err

    def test_distinct_paths_in_one_directory_still_run(self, tmp_path):
        """The guard must not fire on the ordinary layout: one dir, several files."""
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        code = script.main(
            [
                *_report_argv(
                    bank, _corpus(tmp_path, live), _sources_list(tmp_path, live)
                ),
                "--ledger",
                str(_ledger(tmp_path)),
                "--summary-json",
                str(out),
            ]
        )

        assert code == 0
        assert json.loads(out.read_text())["failed_passes"] == []


class TestReportNotifiesOnDegradedRuns:
    """A pass that half-ran must not summarise as clean.

    `find_drift` abstains only when *nothing* was read. One readable source is
    enough to make the pass "succeed", so a run where 1 page is unchanged and 49
    were unreachable exits zero with `drifted == 0`. Counting only drifted rows
    would make that indistinguishable from a healthy bank — the exact false
    clean the per-bucket reporting exists to prevent, reintroduced one layer up.
    """

    def _summary(self, tmp_path, script, bank, corpus, sources, extra=()):
        out = tmp_path / "summary.json"
        code = script.main(
            [*_report_argv(bank, corpus, sources), "--summary-json", str(out), *extra]
        )
        return code, json.loads(out.read_text())

    def test_an_unreachable_source_counts_as_unchecked(self, tmp_path):
        script = _load_script()
        ok_url, dead = f"{KB}/kb/ok", f"{KB}/kb/dead"
        bank = _bank(
            tmp_path,
            _locked_row(ok_url, hashes={ok_url: page_digest(GPU_HTML)}),
            _locked_row(dead, hashes={dead: page_digest(GPU_HTML)}),
        )
        corpus = _corpus(tmp_path, ok_url, dead)
        sources = _sources_list(tmp_path, ok_url, dead)
        _fake_pages(script, {ok_url: GPU_HTML}, errors={dead: "connection reset"})

        code, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert code == 0  # the pass ran; the cron contract holds
        assert summary["drifted"] == 0
        assert summary["unchecked_sources"] == 1
        assert summary["notify"] is True

    def test_a_missing_baseline_counts_as_unchecked(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/ok"
        bank = _bank(tmp_path, _locked_row(url))  # locked, never baselined
        corpus = _corpus(tmp_path, url)
        sources = _sources_list(tmp_path, url)
        _fake_pages(script, {url: GPU_HTML})

        _, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert summary["unchecked_sources"] == 1
        assert summary["notify"] is True

    def test_a_malformed_baseline_counts_as_unchecked(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/ok"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: "sha256:truncated"}))
        corpus = _corpus(tmp_path, url)
        sources = _sources_list(tmp_path, url)
        _fake_pages(script, {url: GPU_HTML})

        _, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert summary["unchecked_sources"] == 1
        assert summary["notify"] is True

    def test_a_policy_refusal_notifies_too(self, tmp_path):
        # An absent host is OMISSION, not intent: the operator listed the hosts
        # they thought of, and a row added later citing a new host is refused
        # forever without anyone saying so. Reading "not listed" as "do not
        # check" is silence-by-omission — and for drift there is no reason not
        # to allowlist every host the bank cites, so a refusal always means
        # either a stale allowlist or a permanently unverifiable row.
        script = _load_script()
        url, off_host = f"{KB}/kb/ok", "https://slurm.schedmd.com/mpi"
        bank = _bank(
            tmp_path,
            _locked_row(url, hashes={url: page_digest(GPU_HTML)}),
            _locked_row(off_host, hashes={off_host: page_digest(GPU_HTML)}),
        )
        corpus = _corpus(tmp_path, url)
        sources = _sources_list(tmp_path, url)
        _fake_pages(script, {url: GPU_HTML})

        _, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert summary["refused_sources"] == 1
        assert summary["unchecked_sources"] == 0
        assert summary["notify"] is True

    def test_a_clean_run_does_not_notify(self, tmp_path):
        script = _load_script()
        url = f"{KB}/kb/ok"
        bank = _bank(tmp_path, _locked_row(url, hashes={url: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, url)
        sources = _sources_list(tmp_path, url)
        _fake_pages(script, {url: GPU_HTML})

        _, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert summary["notify"] is False

    def test_an_orphan_near_miss_notifies(self, tmp_path):
        # Recorded under its own key: coverage reports a near-miss bucket too,
        # and one `update()` overwriting the other would lose a finding.
        script = _load_script()
        url = f"{KB}/kb/fairshare-2"
        bank = _bank(tmp_path, _row(url))
        corpus = _corpus(tmp_path, f"{KB}/kb/fairshare")
        sources = _sources_list(tmp_path, f"{KB}/kb/fairshare")

        _, summary = self._summary(tmp_path, script, bank, corpus, sources)

        assert summary["orphans_needs_reconciliation"] == 1
        assert summary["notify"] is True


class TestReportConfirmationCensus:
    """Spec: "WHEN the tool reports on the bank THEN it prints the count of
    `locked` vs `draft` rows and the `anchor_type` distribution, from the field
    rather than by parsing `notes`."

    `bank_status_counts` has existed since group 1 but no CLI ever printed it,
    so the scenario was satisfied nowhere. `report` is the reporting surface.
    """

    def _bank_and_run(self, tmp_path, script, extra=()):
        live = f"{KB}/kb/live"
        bank = _bank(
            tmp_path,
            _locked_row(live, hashes={live: page_digest(GPU_HTML)}, anchor_type="easy"),
            _row(f"{KB}/kb/live", anchor_type="reasoning"),
        )
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        code = script.main([*_report_argv(bank, corpus, sources), *extra])
        return code

    def test_prints_locked_versus_draft(self, tmp_path, capsys):
        script = _load_script()

        self._bank_and_run(tmp_path, script)
        out = capsys.readouterr().out

        assert "1 locked" in out
        assert "1 draft" in out

    def test_prints_the_anchor_type_distribution(self, tmp_path, capsys):
        script = _load_script()

        self._bank_and_run(tmp_path, script)
        out = capsys.readouterr().out

        assert "easy" in out
        assert "reasoning" in out

    def test_the_census_is_in_the_summary_json(self, tmp_path):
        script = _load_script()
        out = tmp_path / "summary.json"

        self._bank_and_run(tmp_path, script, ("--summary-json", str(out)))
        summary = json.loads(out.read_text())

        assert summary["census"]["locked"] == 1
        assert summary["census"]["draft"] == 1
        assert summary["census"]["anchor_type"]["easy"] == 1

    def test_the_census_alone_does_not_wake_anyone(self, tmp_path):
        # Composition, not a finding: a bank that is 90% draft is a project
        # status, not a nightly alarm.
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _row(live))  # all draft, and fully covered
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})
        out = tmp_path / "summary.json"

        script.main([*_report_argv(bank, corpus, sources), "--summary-json", str(out)])
        summary = json.loads(out.read_text())

        assert summary["census"]["draft"] == 1
        assert summary["notify"] is False


class TestReportSaysWhenItOnlyRanTheTripwire:
    """A hash-only drift run must not read as a completed staleness check.

    `--model` is optional by design (the mismatch is the fact; the verdict only
    triages urgency), and the nightly cron deliberately runs without one. But a
    report that lists drifted rows and says nothing else implies the reference
    was compared against the new page. It was not — only the bytes were.
    """

    def _drifted_run(self, tmp_path, script, extra=()):
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})
        return script.main([*_report_argv(bank, corpus, sources), *extra])

    def test_a_hash_only_run_says_the_reference_was_not_compared(
        self, tmp_path, capsys
    ):
        script = _load_script()

        self._drifted_run(tmp_path, script)
        out = capsys.readouterr().out

        assert "NOT compared" in out
        assert "--model" in out

    def test_the_summary_records_which_kind_of_drift_check_ran(self, tmp_path):
        script = _load_script()
        out = tmp_path / "summary.json"

        self._drifted_run(tmp_path, script, ("--summary-json", str(out)))

        assert json.loads(out.read_text())["drift_check"] == "hash-only"

    def test_a_configured_model_records_the_full_check(self, tmp_path):
        script = _load_script()
        _fake_llm(script, {"verdict": "holds", "explanation": "same number"})
        out = tmp_path / "summary.json"

        self._drifted_run(
            tmp_path, script, ("--summary-json", str(out), "--model", "anthropic/x")
        )

        assert json.loads(out.read_text())["drift_check"] == "reference-compared"

    def test_a_clean_run_does_not_nag_about_the_model(self, tmp_path, capsys):
        # The caveat belongs beside a finding it qualifies, not on every run.
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})

        script.main(_report_argv(bank, corpus, sources))

        assert "NOT compared" not in capsys.readouterr().out


class TestReportKeepsTheDriftEvidence:
    """Spec: a drifted row is flagged "with the re-fetched content for review".

    Interactively that is `--show-text`, opt-in because the report is a work
    list. Unattended there is nobody to re-run it: the log IS the artifact, and
    by the time anyone reads "3 drifted" the page may have moved again. So the
    evidence has to be captured at detection time. The cron wrapper's size cap
    is what makes that safe to do every night.
    """

    def _drifted(self, tmp_path, script, extra=()):
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML_CHANGED})
        return script.main([*_report_argv(bank, corpus, sources), *extra])

    def test_the_changed_page_text_is_in_the_report(self, tmp_path, capsys):
        script = _load_script()

        self._drifted(tmp_path, script)
        out = capsys.readouterr().out

        # The fact that moved, not just the fact that something moved.
        assert "--gpus=2" in out

    def test_the_evidence_names_the_url_it_came_from(self, tmp_path, capsys):
        script = _load_script()

        self._drifted(tmp_path, script)
        out = capsys.readouterr().out

        assert f"{KB}/kb/live (as fetched now)" in out

    def test_a_clean_run_carries_no_page_text(self, tmp_path, capsys):
        script = _load_script()
        live = f"{KB}/kb/live"
        bank = _bank(tmp_path, _locked_row(live, hashes={live: page_digest(GPU_HTML)}))
        corpus = _corpus(tmp_path, live)
        sources = _sources_list(tmp_path, live)
        _fake_pages(script, {live: GPU_HTML})

        script.main(_report_argv(bank, corpus, sources))

        assert "as fetched now" not in capsys.readouterr().out
