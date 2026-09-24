"""The three preflight censuses and the r0b exemplar check (plan §6.2, W7).

Each is a gate the rung-0 sweep must pass before a verdict is read. The pure
functions are tested on stubbed rows; the CLI is tested with a stub connection
that proves every reading comes from one database state.
"""

import hashlib
import json

import pytest

from scripts.benchmarking import category_census as cc
from src.utils.benchmark_provenance import category_map_digest, category_map_records

KB = "https://docs.rc.fas.harvard.edu/kb"
LABELS = ["Storage", "Software", "Cluster Usage", "Data Transfer", "AI", "VPN"]

ROUTING_PROMPT = """---
name: FASRC Docs
---

## Answering

- Ground every claim.

## Category routing

The documentation is organised into these categories:

- Storage
- Software
- Cluster Usage
- Data Transfer
- AI
- VPN

When a question names a category, narrow the search.
"""

EXEMPLAR_PROMPT = """---
name: FASRC Docs
---

## Worked examples

These examples show the shape of a good answer.

Question: We've been approved to work with a DSL3 dataset. Can we run the analysis on Cannon?

Answer: No — Cannon is rated only for DSL 1 and DSL 2 data
[Data Security Levels – FASRC DOCS](https://docs.rc.fas.harvard.edu/kb/data-security-levels).

Question: Which of our lab's two grant accounts should I charge this job to?

Answer: That is a decision for whoever administers your lab's allocations.

What these have in common: the answer never asserts anything unsupported.
"""


def _docs(n_categorized, n_empty, category="Storage"):
    docs = [(f"{KB}/c{i}", category) for i in range(n_categorized)]
    docs += [(f"{KB}/e{i}", None) for i in range(n_empty)]
    return docs


# --- coverage ----------------------------------------------------------------------


def test_coverage_counts_only_kb_documents():
    docs = _docs(9, 1) + [("https://indico.cern.ch/event/1", None)]
    result = cc.coverage_census(docs)
    assert result["total"] == 10
    assert result["share"] == pytest.approx(0.9)
    assert result["passed"] is True


def test_coverage_below_ninety_percent_fails():
    result = cc.coverage_census(_docs(85, 15))
    assert result["passed"] is False


# --- vocabulary --------------------------------------------------------------------


def test_routing_labels_are_the_bullets_under_the_heading():
    assert cc.parse_routing_labels(ROUTING_PROMPT) == LABELS


def test_routing_prompt_without_the_section_is_rejected():
    with pytest.raises(ValueError):
        cc.parse_routing_labels("## Answering\n\n- x\n")


def test_matching_vocabulary_passes():
    docs = [(f"{KB}/{i}", label) for i, label in enumerate(LABELS)]
    result = cc.vocabulary_census(docs, LABELS)
    assert result == {"prompt_only": [], "corpus_only": [], "passed": True}


def test_drift_names_each_side():
    docs = [(f"{KB}/{i}", label) for i, label in enumerate(LABELS[:-1] + ["Training"])]
    result = cc.vocabulary_census(docs, LABELS)
    assert result["prompt_only"] == ["VPN"]
    assert result["corpus_only"] == ["Training"]
    assert result["passed"] is False


def test_uncategorized_documents_do_not_cause_drift():
    """95 % coverage: both checks pass; an empty category is not a label."""
    docs = [(f"{KB}/{i}", LABELS[i % len(LABELS)]) for i in range(95)]
    docs += [(f"{KB}/e{i}", None) for i in range(4)] + [(f"{KB}/blank", "")]
    assert cc.coverage_census(docs)["passed"] is True
    assert cc.vocabulary_census(docs, LABELS)["passed"] is True


def test_non_kb_categories_are_out_of_scope():
    docs = [(f"{KB}/{i}", label) for i, label in enumerate(LABELS)]
    docs.append(("https://indico.cern.ch/event/1", "Conference"))
    assert cc.vocabulary_census(docs, LABELS)["passed"] is True


# --- bank coverage and concentration -----------------------------------------------


def _bank(n_per_category, categories=LABELS):
    rows, docs = {}, []
    for c, label in enumerate(categories):
        for i in range(n_per_category):
            url = f"{KB}/{c}-{i}"
            rows[f"q{c}-{i}"] = [url]
            docs.append((url, label))
    return rows, docs


def test_bank_with_six_covered_categories_passes():
    rows, docs = _bank(3)
    result = cc.bank_census(rows, docs)
    assert result["categories_with_coverage"] == 6
    assert result["passed"] is True
    storage = next(r for r in result["table"] if r["category"] == "Storage")
    assert storage == {
        "category": "Storage",
        "gold_rows": 3,
        "coverage": 3,
        "underpowered": False,
    }


def test_five_categories_fail():
    rows, docs = _bank(3, LABELS[:5])
    result = cc.bank_census(rows, docs)
    assert result["passed"] is False
    assert "5 categories" in result["failures"][0]


def test_one_article_above_ten_percent_fails():
    rows, docs = _bank(2)
    rows.update({f"hot{i}": [f"{KB}/0-0"] for i in range(2)})  # 4 of 14 rows
    result = cc.bank_census(rows, docs)
    assert result["passed"] is False
    assert f"{KB}/0-0" in " ".join(result["failures"])


def test_side_lines_are_reported_and_no_row_is_dropped():
    rows, docs = _bank(3)
    rows["cross"] = [f"{KB}/0-0", f"{KB}/1-0"]
    rows["uncat"] = [f"{KB}/blank"]
    rows["lost"] = [f"{KB}/missing"]
    docs.append((f"{KB}/blank", None))
    result = cc.bank_census(rows, docs)
    assert result["cross_category"] == ["cross"]
    assert result["uncategorized"] == ["uncat"]
    assert result["unresolved"] == [["lost", f"{KB}/missing"]]
    assert result["rows"] == len(rows)


# --- exemplars ---------------------------------------------------------------------


def test_exemplars_are_parsed_with_their_cited_urls():
    exemplars = cc.parse_exemplars(EXEMPLAR_PROMPT)
    assert [e["question"][:20] for e in exemplars] == [
        "We've been approved ",
        "Which of our lab's t",
    ]
    assert exemplars[0]["urls"] == [f"{KB}/data-security-levels"]
    assert exemplars[1]["urls"] == []


def test_clean_exemplars_pass():
    bank = [{"user_input": "How do I submit a job?", "sources": [f"{KB}/running-jobs"]}]
    result = cc.exemplar_disjointness(cc.parse_exemplars(EXEMPLAR_PROMPT), bank, 0.5)
    assert result == {
        "passed": True,
        "exemplars": 2,
        "threshold": 0.5,
        "collisions": [],
    }


def test_exact_question_collides():
    bank = [
        {
            "user_input": "Which of our lab's two grant accounts should I charge this job to",
            "sources": [],
        }
    ]
    result = cc.exemplar_disjointness(cc.parse_exemplars(EXEMPLAR_PROMPT), bank, 0.5)
    assert result["passed"] is False
    assert result["collisions"][0]["kind"] == "question"


def test_paraphrased_question_collides():
    bank = [
        {
            "user_input": "Which of our two grant accounts should I charge this job to?",
            "sources": [],
        }
    ]
    result = cc.exemplar_disjointness(cc.parse_exemplars(EXEMPLAR_PROMPT), bank, 0.5)
    assert [c["kind"] for c in result["collisions"]] == ["question"]


def test_shared_url_collides_across_a_trailing_slash():
    bank = [{"user_input": "Unrelated?", "sources": [f"{KB}/data-security-levels/"]}]
    result = cc.exemplar_disjointness(cc.parse_exemplars(EXEMPLAR_PROMPT), bank, 0.5)
    assert result["collisions"] == [
        {"kind": "url", "exemplar": 1, "url": f"{KB}/data-security-levels"}
    ]


# --- CLI: one connection, one database state ----------------------------------------


MAP_ROWS = [
    (f"{KB}/{c}-{i}", label) for c, label in enumerate(LABELS) for i in range(3)
]


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.conn.queries.append(query)
        if "extra_json->>'category'" in query:
            self._rows = MAP_ROWS
        elif self.conn.fail_fingerprint:
            raise RuntimeError("fingerprint query failed")
        else:
            self._rows = [("doc:x", "1")]

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, fail_fingerprint=False):
        self.queries = []
        self.session = None
        self.fail_fingerprint = fail_fingerprint
        self.closed = False

    def set_session(self, **kwargs):
        self.session = kwargs

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _files(tmp_path):
    bank = tmp_path / "bank.json"
    bank.write_text(
        json.dumps(
            [
                {"user_input": f"q{c}-{i}?", "sources": [url]}
                for (url, _), (c, i) in zip(
                    MAP_ROWS, [(c, i) for c in range(len(LABELS)) for i in range(3)]
                )
            ]
        )
    )
    anchors = tmp_path / "anchors.json"
    anchors.write_text(json.dumps([{"user_input": "Anchor?", "sources": []}]))
    routing = tmp_path / "r0a.md"
    routing.write_text(ROUTING_PROMPT)
    exemplar = tmp_path / "r0b.md"
    exemplar.write_text(EXEMPLAR_PROMPT)
    return bank, anchors, routing, exemplar


def _argv(tmp_path, out):
    bank, anchors, routing, exemplar = _files(tmp_path)
    return [
        "--pg-dsn",
        "postgresql://stub",
        "--bank",
        str(bank),
        "--anchors",
        str(anchors),
        "--routing-prompt",
        str(routing),
        "--exemplar-prompt",
        str(exemplar),
        "--json",
        str(out),
    ]


def test_cli_reads_everything_on_one_readonly_repeatable_read_connection(
    tmp_path, capsys
):
    conns = []

    def connect(dsn):
        conns.append(dsn)
        conn = _Conn()
        connect.conn = conn
        return conn

    out = tmp_path / "census.json"
    code = cc.main(_argv(tmp_path, out), connect=connect)

    assert code == 0, capsys.readouterr()
    assert conns == ["postgresql://stub"]
    conn = connect.conn
    assert conn.session == {"readonly": True, "isolation_level": "REPEATABLE READ"}
    assert any("document_chunks" in q for q in conn.queries)
    assert conn.closed

    report = json.loads(out.read_text())
    assert report["passed"] is True
    assert report["category_map_digest"] == category_map_digest(
        category_map_records(MAP_ROWS)
    )
    assert report["corpus_fingerprint"].startswith("sha256:")
    bank, _, routing, _ = _files(tmp_path)
    assert (
        report["inputs"]["bank_sha256"] == hashlib.sha256(bank.read_bytes()).hexdigest()
    )
    assert (
        report["inputs"]["routing_prompt_sha256"]
        == hashlib.sha256(routing.read_bytes()).hexdigest()
    )
    assert report["inputs"]["similarity_threshold"] == 0.5
    assert "| Storage |" in capsys.readouterr().out


def test_cli_exits_2_when_the_fingerprint_reading_fails(tmp_path):
    out = tmp_path / "census.json"
    code = cc.main(
        _argv(tmp_path, out), connect=lambda dsn: _Conn(fail_fingerprint=True)
    )

    assert code == 2
    assert json.loads(out.read_text())["passed"] is False


def test_cli_exits_1_on_a_missing_input(tmp_path):
    argv = _argv(tmp_path, tmp_path / "c.json")
    argv[argv.index("--bank") + 1] = str(tmp_path / "missing.json")
    assert cc.main(argv, connect=lambda dsn: _Conn()) == 1
