"""Lock, verify and archive a rung-0 prompt sweep (change sweep-mode-feature-matrix-wrappers).

The feature-matrix wrappers call this in ``--sweep`` mode; shell keeps docker and
the archi CLI, this module keeps the decisions, so they can be unit-tested:

- ``lock``    — regenerate the arm configs from the manifest and require the sweep
  directory to match byte for byte; hash every input (manifest, configs, prompts,
  bank, anchors, QA dataset and profile, the prepared ``preparation.jsonl``);
  record the r0b exemplar-disjointness check on its arm. Written once.
- ``verify``  — re-hash every locked file; any change is named.
- ``archive`` — for a multi-arm sweep artifact, check every arm before anything is
  written (names match the locked stems one to one, a new artifact, an unused run
  number, stable and equal corpus fingerprints, each arm's recorded prompt equal
  to its locked prompt, a hash-bound snapshot whenever the end map reading was
  usable, the census on run 1, both pins on later runs), then copy the files,
  write the pins and append every ledger row in one write.

Rules come from plan ``docs/docs/proposals/categories-action-plan.md`` §6.1 and
the operator's decisions on #524, #525 and #538.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from scripts.benchmarking.category_census import (  # noqa: E402
    DEFAULT_SIMILARITY,
    exemplar_disjointness,
    parse_exemplars,
)
from scripts.benchmarking.generate_prompt_sweep import (  # noqa: E402
    generate_sweep_configs,
)
from src.utils.benchmark_provenance import prompt_text_sha256  # noqa: E402

UNAVAILABLE = "<unavailable:"
ROUTING_HEADING = "## Category routing"
EXEMPLAR_HEADING = "## Worked examples"


class SweepError(Exception):
    """A sweep step refused; the message names what failed."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _usable(reading: Any) -> bool:
    return (
        isinstance(reading, str)
        and bool(reading)
        and not reading.startswith(UNAVAILABLE)
    )


def generate(manifest: Path, out_dir: Optional[Path] = None) -> List[Path]:
    """Render the sweep configs; with *out_dir*, into it instead of the manifest's."""
    if out_dir is None:
        return generate_sweep_configs(Path(manifest))
    data = yaml.safe_load(Path(manifest).read_text()) or {}
    data["out_dir"] = str(out_dir)
    copy = Path(out_dir).parent / "manifest.regenerated.yaml"
    copy.write_text(yaml.safe_dump(data))
    return generate_sweep_configs(copy)


# --- lock --------------------------------------------------------------------------------


def _regenerated_matches(manifest: Path, sweep_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "configs"
        generate(manifest, fresh)
        want = {p.name: p.read_bytes() for p in fresh.glob("*.yaml")}
    have = {p.name: p.read_bytes() for p in Path(sweep_dir).glob("*.yaml")}
    problems = sorted(set(want) ^ set(have)) + sorted(
        name for name in set(want) & set(have) if want[name] != have[name]
    )
    if problems:
        raise SweepError(
            "the sweep directory does not match the configs regenerated from the "
            f"manifest ({', '.join(problems)}); regenerate it with "
            "generate_prompt_sweep.py, or fix the manifest"
        )


def _load_rows(path: Path) -> List[dict]:
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        raise SweepError(f"{path} is not a JSON list of bank rows")
    return rows


def build_lock(
    *,
    sweep_dir: Path,
    manifest: Path,
    stack: str,
    qa_dataset: Path,
    qa_profile: Path,
    prepared: Path,
    code_sha: str,
    code_tree: str,
    locked: Optional[str] = None,
) -> Dict[str, Any]:
    preparation = Path(prepared) / "preparation.jsonl"
    if not preparation.is_file():
        raise SweepError(
            f"no prepared QA workspace at {prepared} — run qa_prepare.sh --sweep "
            f"{stack} first; the lock pins its preparation.jsonl"
        )
    _regenerated_matches(Path(manifest), Path(sweep_dir))

    configs = sorted(Path(sweep_dir).glob("*.yaml"))
    if not configs:
        raise SweepError(f"{sweep_dir} holds no arm configs")
    first = yaml.safe_load(configs[0].read_text()) or {}
    bench = (first.get("services") or {}).get("benchmarking") or {}
    bank = Path(str(bench.get("queries_path")))
    anchors = Path(str((bench.get("anchors") or {}).get("path")))
    for path in (bank, anchors):
        if not path.is_file():
            raise SweepError(f"locked input not found: {path}")
    reference = _load_rows(bank) + _load_rows(anchors)

    arms: Dict[str, Dict[str, Any]] = {}
    routing_arm = exemplar_arm = None
    for config in configs:
        cfg = yaml.safe_load(config.read_text()) or {}
        prompt = Path(
            str(
                ((cfg.get("services") or {}).get("benchmarking") or {})["agent_md_file"]
            )
        )
        text = prompt.read_text()
        entry: Dict[str, Any] = {
            "config": str(config),
            "config_sha256": _sha(config),
            "prompt": str(prompt),
            "prompt_sha256": _sha(prompt),
            "prompt_text_sha256": prompt_text_sha256(prompt),
        }
        if ROUTING_HEADING in text:
            routing_arm = config.stem
        if EXEMPLAR_HEADING in text:
            exemplar_arm = config.stem
            result = exemplar_disjointness(
                parse_exemplars(text), reference, DEFAULT_SIMILARITY
            )
            if not result["passed"]:
                named = "; ".join(
                    str(c.get("url") or c.get("question")) for c in result["collisions"]
                )
                raise SweepError(
                    f"{config.stem}: exemplars are not disjoint from the bank and "
                    f"anchors ({named})"
                )
            entry["disjointness"] = {
                "prompt_sha256": entry["prompt_sha256"],
                "passed": True,
                "exemplars": result["exemplars"],
                "threshold": result["threshold"],
            }
        arms[config.stem] = entry

    return {
        "kind": "sweep",
        "stack": stack,
        "locked": locked
        or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sweep_dir": str(sweep_dir),
        "manifest": {"path": str(manifest), "sha256": _sha(manifest)},
        "arms": arms,
        "routing_arm": routing_arm,
        "exemplar_arm": exemplar_arm,
        "similarity_threshold": DEFAULT_SIMILARITY,
        "bank": {"path": str(bank), "sha256": _sha(bank)},
        "anchors": {"path": str(anchors), "sha256": _sha(anchors)},
        "qa": {
            "dataset": str(qa_dataset),
            "dataset_sha256": _sha(qa_dataset),
            "profile": str(qa_profile),
            "profile_sha256": _sha(qa_profile),
            "prepared": str(prepared),
            "preparation_sha256": _sha(preparation),
        },
        "code_sha": code_sha,
        "code_tree": code_tree,
    }


# --- verify ------------------------------------------------------------------------------


def _locked_files(lock: Dict[str, Any]) -> List[tuple]:
    files = [
        (lock["manifest"]["path"], lock["manifest"]["sha256"]),
        (lock["bank"]["path"], lock["bank"]["sha256"]),
        (lock["anchors"]["path"], lock["anchors"]["sha256"]),
        (lock["qa"]["dataset"], lock["qa"]["dataset_sha256"]),
        (lock["qa"]["profile"], lock["qa"]["profile_sha256"]),
        (
            str(Path(lock["qa"]["prepared"]) / "preparation.jsonl"),
            lock["qa"]["preparation_sha256"],
        ),
    ]
    for arm in lock["arms"].values():
        files += [
            (arm["config"], arm["config_sha256"]),
            (arm["prompt"], arm["prompt_sha256"]),
        ]
    return files


def verify_lock(lock: Dict[str, Any]) -> List[str]:
    """Every locked file whose content changed or disappeared since the lock."""
    problems: List[str] = []
    for path, want in _locked_files(lock):
        if not Path(path).is_file():
            problems.append(f"{path}: missing (locked sha256 {want[:12]})")
        elif _sha(Path(path)) != want:
            problems.append(
                f"{path}: content changed since the lock (locked sha256 {want[:12]})"
            )
    return problems


def lock_sha(lock: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(lock, sort_keys=True).encode("utf-8")).hexdigest()


# --- archive -------------------------------------------------------------------------------


def _latest_start(rows: List[dict], stack: str) -> dt.datetime:
    starts = [
        (row["started"], index)
        for index, row in enumerate(rows)
        if row.get("kind") == "ragas-start"
        and row.get("stack") == stack
        and row.get("started")
    ]
    if not starts:
        raise SweepError(
            f"no ragas-start row for {stack} in the ledger — start the run with "
            "run_arm.sh --sweep so the artifact is tied to the lock"
        )
    started = max(starts)[0]
    return dt.datetime.fromisoformat(started.replace("Z", "+00:00"))


def _arm_name(entry: dict) -> Optional[str]:
    return (
        ((entry.get("configuration") or {}).get("services") or {}).get("benchmarking")
        or {}
    ).get("name")


def _check_census(
    census: Optional[Path], lock: Dict[str, Any], fingerprint: str
) -> dict:
    if census is None or not Path(census).is_file():
        raise SweepError(
            "run 1 needs --census <json> from category_census.py, run on this stack "
            "after the run and before the rerun"
        )
    report = json.loads(Path(census).read_text())
    if report.get("passed") is not True:
        raise SweepError(
            f"the census at {census} did not pass: {report.get('failures')}"
        )
    if report.get("corpus_fingerprint") != fingerprint:
        raise SweepError(
            f"the census read corpus {report.get('corpus_fingerprint')}, the artifact "
            f"scored {fingerprint}"
        )
    inputs = report.get("inputs") or {}
    expected = {
        "bank_sha256": lock["bank"]["sha256"],
        "anchors_sha256": lock["anchors"]["sha256"],
        "similarity_threshold": lock["similarity_threshold"],
    }
    if lock.get("routing_arm"):
        expected["routing_prompt_sha256"] = lock["arms"][lock["routing_arm"]][
            "prompt_sha256"
        ]
    if lock.get("exemplar_arm"):
        expected["exemplar_prompt_sha256"] = lock["arms"][lock["exemplar_arm"]][
            "prompt_sha256"
        ]
    for key, want in expected.items():
        if inputs.get(key) != want:
            raise SweepError(
                f"the census was run on other inputs: {key} is {inputs.get(key)!r}, "
                f"the sweep lock has {want!r}"
            )
    return report


def archive(
    *,
    lock: Dict[str, Any],
    artifact: Path,
    stack: str,
    run: int,
    ledger: Path,
    pins_dir: Path,
    dest: Path,
    census: Optional[Path] = None,
    finished: Optional[str] = None,
    lock_sha256: Optional[str] = None,
) -> List[dict]:
    """Check every arm, then copy, pin and record the run in one ledger write.

    *lock_sha256* is the sweep lock file's sha256, the value the shell wrappers
    stamp stacks and ledger rows with; it defaults to a digest of *lock*.
    """
    artifact = Path(artifact)
    document = json.loads(artifact.read_text())
    entries = document.get("benchmarking_results") or []
    names = [_arm_name(entry) or "<unnamed>" for entry in entries]

    counts = Counter(names)
    locked = set(lock["arms"])
    problems = [f"{name} appears {n} times" for name, n in counts.items() if n > 1]
    problems += [
        f"{stem} has no arm in the artifact" for stem in sorted(locked - set(names))
    ]
    problems += [f"{name} is not a locked arm" for name in sorted(set(names) - locked)]
    if problems:
        raise SweepError(
            "artifact arms do not match the sweep lock: " + "; ".join(problems)
        )

    rows = json.loads(Path(ledger).read_text()) if Path(ledger).exists() else []
    digest = _sha(artifact)
    if any(row.get("artifact_sha256") == digest for row in rows):
        raise SweepError(f"{artifact} is already archived")
    taken = [
        row
        for row in rows
        if row.get("kind") == "ragas"
        and row.get("stack") == stack
        and row.get("run") == run
        and row.get("arm") in locked
    ]
    if taken:
        raise SweepError(
            f"run {run} on {stack} is already archived; pick the next run number"
        )
    written = dt.datetime.fromtimestamp(artifact.stat().st_mtime, tz=dt.timezone.utc)
    latest = _latest_start(rows, stack)
    if written < latest:
        raise SweepError(
            f"artifact written {written:%Y-%m-%dT%H:%M:%SZ}, before the latest "
            f"ragas-start for {stack} at {latest:%Y-%m-%dT%H:%M:%SZ} — the run "
            "produced no new artifact"
        )

    fingerprints = set()
    for name, entry in zip(names, entries):
        before, after = entry.get("corpus_fingerprint_before"), entry.get(
            "corpus_fingerprint"
        )
        if not (_usable(before) and _usable(after)) or before != after:
            raise SweepError(
                f"{name}: corpus fingerprint not usable and stable at both endpoints"
            )
        fingerprints.add(after)
        if entry.get("agent_md_sha256") != lock["arms"][name]["prompt_text_sha256"]:
            raise SweepError(
                f"{name}: the arm ran prompt {entry.get('agent_md_sha256')}, the lock "
                f"pins {lock['arms'][name]['prompt_text_sha256']}"
            )
        end, snapshot = entry.get("category_map_sha256_end"), entry.get(
            "category_map_file"
        )
        if _usable(end):
            path = artifact.parent / str(snapshot or "")
            if not snapshot or not path.is_file():
                raise SweepError(
                    f"{name}: usable end map reading but no snapshot beside the artifact"
                )
            if f"sha256:{_sha(path)}" != end:
                raise SweepError(
                    f"{name}: snapshot {snapshot} does not match its end digest"
                )
    if len(fingerprints) != 1:
        raise SweepError(f"arms scored different corpora: {sorted(fingerprints)}")
    fingerprint = fingerprints.pop()

    corpus_pin = Path(pins_dir) / f"corpus-pin-{stack}"
    map_pin = Path(pins_dir) / f"category-map-pin-{stack}"
    census_report: Optional[dict] = None
    if run == 1:
        census_report = _check_census(census, lock, fingerprint)
    else:
        if not (corpus_pin.is_file() and map_pin.is_file()):
            raise SweepError(f"no pins for {stack} — archive run 1 first")
        if corpus_pin.read_text().strip() != fingerprint:
            raise SweepError(f"corpus {fingerprint} differs from the corpus pin")
        pinned = map_pin.read_text().strip()
        for name, entry in zip(names, entries):
            for key in ("category_map_sha256_start", "category_map_sha256_end"):
                reading = entry.get(key)
                if _usable(reading) and reading != pinned:
                    raise SweepError(
                        f"{name}: {key} {reading} differs from the map pin {pinned}"
                    )

    out_rows: List[dict] = []
    map_digest = census_report.get("category_map_digest") if census_report else None
    for name, entry in zip(names, entries):
        row = {
            "arm": name,
            "kind": "ragas",
            "sweep": True,
            "stack": stack,
            "run": run,
            "finished": finished,
            "artifact": str(Path(dest) / artifact.name),
            "artifact_sha256": digest,
            "corpus_fingerprint": fingerprint,
            "prompt_sha256": lock["arms"][name]["prompt_sha256"],
            "category_map_sha256_start": entry.get("category_map_sha256_start"),
            "category_map_sha256_end": entry.get("category_map_sha256_end"),
            "category_map_unchanged_at_endpoints": entry.get(
                "category_map_unchanged_at_endpoints"
            ),
            "category_map_file": entry.get("category_map_file"),
            "lock_sha256": lock_sha256 or lock_sha(lock),
        }
        if census_report is not None:
            first = next(
                (
                    entry.get(k)
                    for k in ("category_map_sha256_start", "category_map_sha256_end")
                    if _usable(entry.get(k))
                ),
                None,
            )
            if first is not None and first != map_digest:
                raise SweepError(
                    f"{name}: census map digest {map_digest} differs from the arm's "
                    f"reading {first}"
                )
            row["census_sha256"] = _sha(census)
            row["census_map_digest"] = map_digest
            row["census_map_bound"] = first is not None
        out_rows.append(row)

    # Every check passed: write.
    Path(dest).mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, Path(dest) / artifact.name)
    report = artifact.with_name(f"{artifact.stem}_report.md")
    if report.is_file():
        shutil.copy2(report, Path(dest) / report.name)
    for entry in entries:
        if entry.get("category_map_file"):
            shutil.copy2(
                artifact.parent / entry["category_map_file"],
                Path(dest) / entry["category_map_file"],
            )
    if run == 1:
        corpus_pin.write_text(fingerprint + "\n")
        map_pin.write_text(str(map_digest) + "\n")
    Path(ledger).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(ledger).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows + out_rows, indent=1))
    os.replace(tmp, ledger)
    return out_rows


# --- CLI -----------------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="sweep_tools.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("lock")
    for flag in (
        "--sweep-dir",
        "--manifest",
        "--stack",
        "--qa-dataset",
        "--qa-profile",
        "--prepared",
        "--code-sha",
        "--code-tree",
        "--out",
    ):
        lk.add_argument(flag, required=True)
    vf = sub.add_parser("verify")
    vf.add_argument("--lock", required=True)
    vf.add_argument("--field")
    ar = sub.add_parser("archive")
    for flag in (
        "--lock",
        "--artifact",
        "--stack",
        "--run",
        "--ledger",
        "--pins-dir",
        "--dest",
    ):
        ar.add_argument(flag, required=True)
    ar.add_argument("--census")
    ar.add_argument("--finished")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "lock":
            out = Path(args.out)
            if out.exists():
                raise SweepError(
                    f"sweep already locked at {out}; the lock is written once"
                )
            lock = build_lock(
                sweep_dir=Path(args.sweep_dir),
                manifest=Path(args.manifest),
                stack=args.stack,
                qa_dataset=Path(args.qa_dataset),
                qa_profile=Path(args.qa_profile),
                prepared=Path(args.prepared),
                code_sha=args.code_sha,
                code_tree=args.code_tree,
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(lock, indent=1, sort_keys=True))
            print(f"sweep locked: {out} ({len(lock['arms'])} arms)")
        elif args.cmd == "verify":
            lock = json.loads(Path(args.lock).read_text())
            if args.field:
                value: Any = lock
                for key in args.field.split("."):
                    value = value.get(key) if isinstance(value, dict) else None
                print("" if value is None else value)
                return 0
            problems = verify_lock(lock)
            if problems:
                raise SweepError("locked inputs changed: " + "; ".join(problems))
        else:
            lock = json.loads(Path(args.lock).read_text())
            rows = archive(
                lock=lock,
                artifact=Path(args.artifact),
                stack=args.stack,
                run=int(args.run),
                ledger=Path(args.ledger),
                pins_dir=Path(args.pins_dir),
                dest=Path(args.dest),
                census=Path(args.census) if args.census else None,
                finished=args.finished,
                lock_sha256=_sha(Path(args.lock)),
            )
            print(f"archived run {args.run}: {len(rows)} arm rows")
    except SweepError as exc:
        print(f"sweep: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
