"""
Tests for catalog_reconcile: scope_for, CollectionPass, find_uncollected, log_reconcile_report.

Spec: openspec/changes/fix-issue-534-report-uncollected-catalog-rows/specs/catalog-reconcile-report/spec.md
Design: openspec/changes/fix-issue-534-report-uncollected-catalog-rows/design.md (D1, D2, D4)
"""

import logging
import threading

import pytest

from src.data_manager.collectors.utils.catalog_reconcile import (
    CollectionPass,
    ReconcileReport,
    find_uncollected,
    log_reconcile_report,
    scope_for,
)

# ---------------------------------------------------------------------------
# D1: scope_for
# ---------------------------------------------------------------------------


class TestScopeFor:
    def test_git_uses_parent(self):
        md = {"source_type": "git", "parent": "User_Codes", "git_repo": "ignored"}
        assert scope_for(md) == ("git", "User_Codes")

    def test_git_falls_back_to_git_repo(self):
        md = {"source_type": "git", "git_repo": "User_Codes"}
        assert scope_for(md) == ("git", "User_Codes")

    def test_git_both_absent_returns_none(self):
        md = {"source_type": "git"}
        assert scope_for(md) == ("git", None)

    def test_web_lowercased_host(self):
        md = {"source_type": "web", "url": "https://DOCS.Example.COM/page"}
        assert scope_for(md) == ("web", "docs.example.com")

    def test_web_indico(self):
        md = {
            "source_type": "web",
            "url": "https://indico.example.com/event/1",
            "scraper": "indico",
        }
        assert scope_for(md) == ("web", "indico")

    def test_web_elog(self):
        md = {
            "source_type": "web",
            "url": "https://elog.example.com/log/1",
            "scraper": "elog",
        }
        assert scope_for(md) == ("web", "elog")

    def test_sso_lowercased_host(self):
        md = {"source_type": "sso", "url": "https://SSO.Example.ORG/login"}
        assert scope_for(md) == ("sso", "sso.example.org")

    def test_local_files_wildcard(self):
        md = {"source_type": "local_files", "path": "/data/file.py"}
        assert scope_for(md) == ("local_files", "*")

    def test_ticket_jira(self):
        md = {"source_type": "ticket", "ticket_provider": "jira"}
        assert scope_for(md) == ("ticket", "jira")

    def test_ticket_redmine(self):
        md = {"source_type": "ticket", "ticket_provider": "redmine"}
        assert scope_for(md) == ("ticket", "redmine")

    def test_ticket_no_provider(self):
        md = {"source_type": "ticket"}
        assert scope_for(md) == ("ticket", None)

    def test_unknown_source_type(self):
        md = {"source_type": "magic_beans"}
        assert scope_for(md) == ("magic_beans", None)


# ---------------------------------------------------------------------------
# D2: CollectionPass – thread safety
# ---------------------------------------------------------------------------


def test_concurrent_record_collected_loses_no_hash():
    """8 threads each record one hash; all 8 must appear in collected."""
    cp = CollectionPass()
    hashes = [f"hash_{i}" for i in range(8)]
    metadata = {"source_type": "git", "parent": "repo"}

    threads = [
        threading.Thread(target=cp.record_collected, args=(h, metadata)) for h in hashes
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    scope = ("git", "repo")
    assert cp.collected[scope] == set(hashes)


# ---------------------------------------------------------------------------
# D4: find_uncollected + log_reconcile_report
# ---------------------------------------------------------------------------


def _git_meta(repo: str, path: str = "file.py") -> dict:
    return {"source_type": "git", "parent": repo, "path": path}


def _web_meta(url: str) -> dict:
    return {"source_type": "web", "url": url}


class TestFindUncollected:
    def test_successful_git_scope_yields_uncollected(self):
        """Rows a,b collected; c,d in catalog → candidates are exactly c,d."""
        cp = CollectionPass()
        cp.record_collected("a", _git_meta("User_Codes", "a.py"))
        cp.record_collected("b", _git_meta("User_Codes", "b.py"))

        rows = [
            ("a", _git_meta("User_Codes", "a.py")),
            ("b", _git_meta("User_Codes", "b.py")),
            ("c", _git_meta("User_Codes", "c.py")),
            ("d", _git_meta("User_Codes", "d.py")),
        ]
        report = find_uncollected(rows, cp)
        assert {c[0] for c in report.candidates} == {"c", "d"}

    def test_scope_failure_yields_zero_and_one_warning(self, caplog):
        """A failed scope produces zero candidates and exactly one WARNING."""
        cp = CollectionPass()
        cp.record_failure("git", "Bad_Repo", "clone failed")

        rows = [("h1", _git_meta("Bad_Repo", "x.py"))]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "Bad_Repo" in warnings[0].getMessage()

    def test_whole_type_failure_skips_all_scopes_of_that_type(self, caplog):
        """scope_key=None failure skips every scope of that source type."""
        cp = CollectionPass()
        cp.record_failure("web", None, "selenium missing")

        rows = [
            ("h1", _web_meta("https://alpha.example.com/p")),
            ("h2", _web_meta("https://beta.example.com/p")),
        ]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        # one WARNING per skipped scope (two distinct web scopes)
        assert len(warnings) == 2

    def test_zero_collected_yields_warning_with_row_count(self, caplog):
        """Scope ran but collected nothing while the catalog holds rows → WARNING names count."""
        cp = CollectionPass()
        # make "web" appear in ran via another host
        cp.record_collected("other", _web_meta("https://other.example.com/"))

        rows = [
            ("h1", _web_meta("https://docs.example.org/p1")),
            ("h2", _web_meta("https://docs.example.org/p2")),
            ("h3", _web_meta("https://docs.example.org/p3")),
            ("h4", _web_meta("https://docs.example.org/p4")),
            ("h5", _web_meta("https://docs.example.org/p5")),
        ]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "5" in warnings[0].getMessage()

    def test_source_type_not_ran_yields_no_candidates_no_warning(self, caplog):
        """A source type that never ran produces no candidates and no WARNING."""
        cp = CollectionPass()
        cp.record_collected("x", _git_meta("some_repo"))

        rows = [("t1", {"source_type": "ticket", "ticket_provider": "jira"})]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []

    def test_indico_always_skipped_with_warning(self, caplog):
        """Indico rows are always skipped, even when web ran, with a WARNING."""
        cp = CollectionPass()
        cp.record_collected("w1", _web_meta("https://other.example.com/"))

        rows = [
            (
                "i1",
                {
                    "source_type": "web",
                    "url": "https://indico.example.com/event/1",
                    "scraper": "indico",
                },
            )
        ]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "indico" in warnings[0].getMessage().lower()

    def test_elog_always_skipped_with_warning(self, caplog):
        """ELOG rows are always skipped, even when web ran, with a WARNING."""
        cp = CollectionPass()
        cp.record_collected("w1", _web_meta("https://other.example.com/"))

        rows = [
            (
                "e1",
                {
                    "source_type": "web",
                    "url": "https://elog.example.com/log/1",
                    "scraper": "elog",
                },
            )
        ]
        with caplog.at_level(logging.WARNING):
            report = find_uncollected(rows, cp)
            log_reconcile_report(report)

        assert report.candidates == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_none_scope_key_counted_as_unscoped_never_candidate(self):
        """Rows with no resolvable scope key are unscoped and never candidates."""
        cp = CollectionPass()
        cp.record_collected("x", _git_meta("some_repo"))

        rows = [
            ("h1", {"source_type": "git"}),  # no parent or git_repo → None scope key
        ]
        report = find_uncollected(rows, cp)
        assert report.candidates == []
        assert report.unscoped_count == 1


# ---------------------------------------------------------------------------
# D4: log_reconcile_report
# ---------------------------------------------------------------------------


def _candidate(hash_, path, source_type, suffix, scope):
    return (hash_, path, source_type, suffix, scope)


class TestLogReconcileReport:
    def test_info_summary_reports_total_and_suffix_counts(self, caplog):
        """4 py + 2 sbatch + 2 md → INFO reports 8 with those suffix counts."""
        candidates = (
            [
                _candidate(f"h{i}", f"file{i}.py", "git", "py", ("git", "repo"))
                for i in range(4)
            ]
            + [
                _candidate(f"s{i}", f"job{i}.sbatch", "git", "sbatch", ("git", "repo"))
                for i in range(2)
            ]
            + [
                _candidate(f"m{i}", f"doc{i}.md", "git", "md", ("git", "repo"))
                for i in range(2)
            ]
        )
        report = ReconcileReport(candidates=candidates, skipped=[], unscoped_count=0)

        with caplog.at_level(logging.INFO):
            log_reconcile_report(report)

        info = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info) == 1
        msg = info[0].getMessage()
        assert "8" in msg
        assert "py" in msg
        assert "sbatch" in msg
        assert "md" in msg

    def test_each_candidate_logged_at_debug(self, caplog):
        """Each candidate produces exactly one DEBUG log line."""
        candidates = [
            _candidate("h1", "file1.py", "git", "py", ("git", "repo")),
            _candidate("h2", "file2.py", "git", "py", ("git", "repo")),
        ]
        report = ReconcileReport(candidates=candidates, skipped=[], unscoped_count=0)

        with caplog.at_level(logging.DEBUG):
            log_reconcile_report(report)

        debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debug) == 2
