"""Sweep lock, verify and archive for the rung-0 prompt sweep (change sweep-mode).

The wrappers' shell handles docker and the archi CLI; these helpers decide what a
lock holds, whether the locked inputs still hold, and whether a multi-arm
artifact may be archived — checked for every arm before anything is written.
"""

import datetime as dt
import hashlib
import json
import os

import pytest
import yaml

from scripts.benchmarking.feature_matrix import sweep_tools as st
from src.utils.benchmark_provenance import (
    category_map_digest,
    category_map_records,
    category_map_text,
)

KB = "https://docs.rc.fas.harvard.edu/kb"
STEMS = ["fasrc-docs", "fasrc-docs-r0a-category", "fasrc-docs-r0b-icl"]
ROUTING = "## Category routing\n\n- A\n- B\n"
EXEMPLARS = (
    "## Worked examples\n\nQuestion: How do I request FASSE access?\n\n"
    f"Answer: See [x]({KB}/fasse).\n"
)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture()
def sweep(tmp_path, monkeypatch):
    """A generated three-arm sweep, its manifest, bank, anchors and prepared QA dir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bank.json").write_text(
        json.dumps(
            [{"user_input": "How do I submit a job?", "sources": [f"{KB}/jobs"]}]
        )
    )
    (tmp_path / "anchors.json").write_text("[]")
    base = {
        "name": "claw",
        "services": {
            "benchmarking": {
                "queries_path": "bank.json",
                "anchors": {"path": "anchors.json"},
                "agent_md_file": "prompts/fasrc-docs.md",
            }
        },
        "data_manager": {"chunking": {"strategy": "sentence"}},
    }
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(base))
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "fasrc-docs.md").write_text("control\n")
    (prompts / "fasrc-docs-r0a-category.md").write_text("control\n" + ROUTING)
    (prompts / "fasrc-docs-r0b-icl.md").write_text("control\n" + EXEMPLARS)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "base_config": "base.yaml",
                "out_dir": "sweep",
                "primary_metric": "context_precision",
                "prompts": [f"prompts/{stem}.md" for stem in STEMS],
            }
        )
    )
    st.generate(manifest)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "preparation.jsonl").write_text('{"item_id": "x"}\n')
    (tmp_path / "qa.json").write_text("{}")
    (tmp_path / "profile.yaml").write_text("judge: x\n")
    return tmp_path


def _lock(sweep, **overrides):
    kwargs = dict(
        sweep_dir=sweep / "sweep",
        manifest=sweep / "manifest.yaml",
        stack="r0",
        qa_dataset=sweep / "qa.json",
        qa_profile=sweep / "profile.yaml",
        prepared=sweep / "prepared",
        code_sha="c0ffee",
        code_tree="src=abc",
    )
    kwargs.update(overrides)
    return st.build_lock(**kwargs)


# --- lock ------------------------------------------------------------------------------


def test_lock_records_every_arm_config_and_prompt(sweep):
    lock = _lock(sweep)
    assert sorted(lock["arms"]) == sorted(STEMS)
    r0a = lock["arms"]["fasrc-docs-r0a-category"]
    assert r0a["prompt_sha256"] == _sha(sweep / "prompts/fasrc-docs-r0a-category.md")
    assert r0a["config_sha256"] == _sha(sweep / "sweep/fasrc-docs-r0a-category.yaml")
    assert lock["bank"]["sha256"] == _sha(sweep / "bank.json")
    assert lock["qa"]["preparation_sha256"] == _sha(
        sweep / "prepared/preparation.jsonl"
    )
    assert lock["qa"]["dataset_sha256"] == _sha(sweep / "qa.json")
    assert lock["code_tree"] == "src=abc"


def test_disjointness_recorded_only_on_the_exemplar_arm(sweep):
    lock = _lock(sweep)
    record = lock["arms"]["fasrc-docs-r0b-icl"]["disjointness"]
    assert record["passed"] is True and record["exemplars"] == 1
    assert record["prompt_sha256"] == _sha(sweep / "prompts/fasrc-docs-r0b-icl.md")
    assert "disjointness" not in lock["arms"]["fasrc-docs-r0a-category"]


def test_contaminated_exemplar_refuses_the_lock(sweep):
    (sweep / "prompts/fasrc-docs-r0b-icl.md").write_text(
        f"control\n## Worked examples\n\nQuestion: Unrelated?\n\nAnswer: [x]({KB}/jobs/)\n"
    )
    with pytest.raises(st.SweepError, match=f"{KB}/jobs"):
        _lock(sweep)


def test_manifest_edited_after_generation_refuses_the_lock(sweep):
    manifest = yaml.safe_load((sweep / "manifest.yaml").read_text())
    (sweep / "prompts/other.md").write_text("other\n")
    manifest["prompts"][1] = "prompts/other.md"
    (sweep / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    with pytest.raises(st.SweepError, match="regenerated"):
        _lock(sweep)


def test_hand_edited_arm_refuses_the_lock(sweep):
    path = sweep / "sweep/fasrc-docs-r0a-category.yaml"
    config = yaml.safe_load(path.read_text())
    config["data_manager"]["chunking"]["strategy"] = "token"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    with pytest.raises(st.SweepError):
        _lock(sweep)


def test_missing_prepared_workspace_refuses_the_lock(sweep):
    with pytest.raises(st.SweepError, match="prepare"):
        _lock(sweep, prepared=sweep / "missing")


# --- verify ------------------------------------------------------------------------------


def test_verify_passes_on_an_untouched_sweep(sweep):
    assert st.verify_lock(_lock(sweep)) == []


@pytest.mark.parametrize(
    "path",
    [
        "bank.json",
        "anchors.json",
        "qa.json",
        "profile.yaml",
        "prepared/preparation.jsonl",
        "prompts/fasrc-docs-r0b-icl.md",
        "sweep/fasrc-docs.yaml",
        "manifest.yaml",
    ],
)
def test_verify_names_any_changed_locked_file(sweep, path):
    lock = _lock(sweep)
    with open(sweep / path, "a") as f:
        f.write("\n# edited\n")
    problems = st.verify_lock(lock)
    assert problems and path.split("/")[-1] in " ".join(problems)


# --- archive -------------------------------------------------------------------------------


def _artifact(
    sweep,
    *,
    names=STEMS,
    fingerprint="sha256:corpus",
    map_pairs=None,
    prompt_override=None,
    unchanged=True,
):
    out = sweep / "bench_out"
    out.mkdir(exist_ok=True)
    stem = "benchmarking-r0-20260924_120000"
    records = category_map_records(map_pairs or [(f"{KB}/jobs", "Cluster Usage")])
    digest = category_map_digest(records)
    entries = []
    for index, name in enumerate(names, 1):
        tsv = f"{stem}_category_map_{index}.tsv"
        (out / tsv).write_text(category_map_text(records))
        prompt = (
            sweep / f"prompts/{name}.md"
            if (sweep / f"prompts/{name}.md").exists()
            else None
        )
        entries.append(
            {
                "configuration": {"services": {"benchmarking": {"name": name}}},
                "corpus_fingerprint_before": fingerprint,
                "corpus_fingerprint": fingerprint,
                "corpus_unchanged_at_endpoints": True,
                "category_map_sha256_start": digest,
                "category_map_sha256_end": digest,
                "category_map_unchanged_at_endpoints": unchanged,
                "category_map_file": tsv,
                "agent_md_sha256": (prompt_override or {}).get(name)
                or (_prompt_text_sha(prompt) if prompt else None),
            }
        )
    path = out / f"{stem}.json"
    path.write_text(json.dumps({"benchmarking_results": entries, "metadata": {}}))
    (out / f"{stem}_report.md").write_text("# report\n")
    return path, digest


def _prompt_text_sha(path):
    return hashlib.sha256(path.read_text().encode("utf-8")).hexdigest()


def _census(sweep, digest, lock, **overrides):
    report = {
        "passed": True,
        "corpus_fingerprint": "sha256:corpus",
        "category_map_digest": digest,
        "inputs": {
            "bank_sha256": lock["bank"]["sha256"],
            "anchors_sha256": lock["anchors"]["sha256"],
            "routing_prompt_sha256": lock["arms"]["fasrc-docs-r0a-category"][
                "prompt_sha256"
            ],
            "exemplar_prompt_sha256": lock["arms"]["fasrc-docs-r0b-icl"][
                "prompt_sha256"
            ],
            "similarity_threshold": 0.5,
        },
    }
    report.update(overrides)
    path = sweep / "census.json"
    path.write_text(json.dumps(report))
    return path


def _ledger(sweep, started="2026-09-24T11:00:00Z"):
    ledger = sweep / "out/ledger.json"
    ledger.parent.mkdir(exist_ok=True)
    ledger.write_text(
        json.dumps([{"kind": "ragas-start", "stack": "r0", "started": started}])
    )
    return ledger


def _archive(sweep, lock, artifact, run, census=None):
    return st.archive(
        lock=lock,
        artifact=artifact,
        stack="r0",
        run=run,
        ledger=sweep / "out/ledger.json",
        pins_dir=sweep / "out",
        dest=sweep / "out/archive",
        census=census,
        finished="2026-09-24T12:30:00Z",
    )


def test_run_one_archives_every_arm_and_writes_both_pins(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    _ledger(sweep)
    census = _census(sweep, digest, lock)

    rows = _archive(sweep, lock, artifact, 1, census)

    assert [row["arm"] for row in rows] == STEMS
    assert all(row["census_sha256"] == _sha(census) for row in rows)
    assert all(row["census_map_bound"] is True for row in rows)
    ledger = json.loads((sweep / "out/ledger.json").read_text())
    assert [r["kind"] for r in ledger] == ["ragas-start", "ragas", "ragas", "ragas"]
    assert (sweep / "out/corpus-pin-r0").read_text().strip() == "sha256:corpus"
    assert (sweep / "out/category-map-pin-r0").read_text().strip() == digest
    copied = sorted(p.name for p in (sweep / "out/archive").iterdir())
    assert len(copied) == 5  # artifact, report, three snapshots


def test_run_one_requires_a_passing_census(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    _ledger(sweep)
    with pytest.raises(st.SweepError, match="census"):
        _archive(sweep, lock, artifact, 1, None)
    failed = _census(sweep, digest, lock, passed=False)
    with pytest.raises(st.SweepError, match="census"):
        _archive(sweep, lock, artifact, 1, failed)


def test_census_from_an_older_map_is_refused(sweep):
    lock = _lock(sweep)
    artifact, _ = _artifact(sweep)
    _ledger(sweep)
    census = _census(sweep, "sha256:older", lock)
    with pytest.raises(st.SweepError, match="map digest"):
        _archive(sweep, lock, artifact, 1, census)


def test_census_on_other_inputs_is_refused(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    _ledger(sweep)
    census = _census(sweep, digest, lock)
    report = json.loads(census.read_text())
    report["inputs"]["routing_prompt_sha256"] = "0" * 64
    census.write_text(json.dumps(report))
    with pytest.raises(st.SweepError, match="routing_prompt_sha256"):
        _archive(sweep, lock, artifact, 1, census)


def test_arm_names_must_match_the_lock(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep, names=[STEMS[0], STEMS[0], STEMS[2]])
    _ledger(sweep)
    with pytest.raises(st.SweepError, match="fasrc-docs-r0a-category"):
        _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))
    assert not (sweep / "out/archive").exists()


def test_prompt_changed_since_the_lock_is_refused(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep, prompt_override={STEMS[1]: "f" * 64})
    _ledger(sweep)
    with pytest.raises(st.SweepError, match="prompt"):
        _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))


def test_a_bad_snapshot_on_the_third_arm_writes_nothing(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    _ledger(sweep)
    (artifact.parent / "benchmarking-r0-20260924_120000_category_map_3.tsv").write_text(
        "x"
    )
    with pytest.raises(st.SweepError, match="fasrc-docs-r0b-icl"):
        _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))
    ledger = json.loads((sweep / "out/ledger.json").read_text())
    assert len(ledger) == 1
    assert not (sweep / "out/archive").exists()


def test_a_changed_map_is_archived_with_its_state(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep, unchanged=False)
    _ledger(sweep)
    rows = _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))
    assert all(row["category_map_unchanged_at_endpoints"] is False for row in rows)


def test_stale_artifact_is_refused(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    old = dt.datetime(2026, 9, 24, 10, tzinfo=dt.timezone.utc).timestamp()
    os.utime(artifact, (old, old))
    _ledger(sweep, started="2026-09-24T11:00:00Z")
    with pytest.raises(st.SweepError, match="no new artifact"):
        _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))


def test_run_two_checks_both_pins_and_refuses_a_reused_run(sweep):
    lock = _lock(sweep)
    artifact, digest = _artifact(sweep)
    _ledger(sweep)
    _archive(sweep, lock, artifact, 1, _census(sweep, digest, lock))

    with pytest.raises(st.SweepError, match="already archived"):
        _archive(sweep, lock, artifact, 1)

    moved, _ = _artifact(sweep, map_pairs=[(f"{KB}/jobs", "Software")])
    ledger = json.loads((sweep / "out/ledger.json").read_text())
    ledger.append(
        {"kind": "ragas-start", "stack": "r0", "started": "2026-09-24T12:00:00Z"}
    )
    (sweep / "out/ledger.json").write_text(json.dumps(ledger))
    with pytest.raises(st.SweepError, match="map pin"):
        _archive(sweep, lock, moved, 2)
