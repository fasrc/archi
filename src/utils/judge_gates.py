"""The judge gates G11 and G12 (judge offload design D9), and how judge
identities are compared (D10).

Standard library only: ``scripts/benchmarking/compare_runs.py`` loads this file
by path on a host with no database driver (``src/utils/__init__.py`` imports
psycopg2), and ``scripts/benchmarking/feature_matrix/archive_run.sh`` runs it as
a script. ``src/utils/judge_identity.py`` re-exports the comparison helpers, so
there is one ``canonical_identity`` for every reader.

- **G11, judged.** An arm with ``judge_status: pending`` has no RAGAS scores:
  its deferred judge has not run yet. It is refused, and the refusal names the
  judged file to wait for, ``<basename>.judged.json`` beside the pending file.
- **G12, one judge.** Arms whose judge identities differ are refused, and the
  refusal names the fields that differ. The identity of an arm is its
  ``ragas_judge_identity_effective`` when the key is present: #618's four
  fields, or the record that #732 extends (a null value means no judge ran).
  Only when the key is absent (an artifact from before 2026-10-13) is it
  ``(evaluator_provider, evaluator_model)`` from the arm's recorded
  configuration. A missing field never equals a recorded one, so the three
  generations of artifact never match each other.

The campaign archiver applies G12 across runs too: each ledger row records its
arm's identity, and a new arm whose identity differs from an earlier RAGAS row
is refused. An earlier row with no identity never equals a recorded one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

IDENTITY_KEY = "ragas_judge_identity_effective"
PENDING = "pending"
JUDGED_SUFFIX = ".judged.json"
#: Lynx writes ``<basename>-arm<N>.lynx.json`` beside the result (#734).
_NOT_RESULTS = (".lynx.json",)
_SHOWN_LABELS = 3
_SHOWN_CHARS = 160


class _NotRecorded:
    """An identity field, or a ledger row's identity, that was never recorded."""

    def __repr__(self) -> str:
        return "<not recorded>"


NOT_RECORDED = _NotRecorded()


class JudgeGateRefusal(Exception):
    """The archiver cannot take this result; the message says why."""


# --- comparing identities ------------------------------------------------------


def normalize_numbers(value: Any) -> Any:
    """A copy of *value* in which every integral float is the equal ``int``.

    Goes into dicts, lists and tuples. A ``bool`` stays a ``bool``: it is a
    subclass of ``int``, but ``True`` is not the setting ``1``. A NaN or an
    infinity is left as it is.
    """
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_numbers(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_identity(identity: Any) -> str:
    """The one comparable text of a judge identity: sorted keys, normalized numbers.

    The separators are the ``json.dumps`` defaults, so a #618 identity (four
    strings) reads the same as the ``json.dumps(identity, sort_keys=True)`` that
    the leaderboard showed before this helper existed.
    """
    return json.dumps(normalize_numbers(identity), sort_keys=True, default=str)


def differing_fields(identities: Sequence[Mapping[str, Any]]) -> List[str]:
    """The top-level fields whose values are not equal in all *identities*.

    A field that one identity has and another lacks differs too: a missing field
    never equals a recorded one, also when the recorded value is ``None``.
    """
    names = sorted({name for identity in identities for name in identity})
    found: List[str] = []
    for name in names:
        if any(name not in identity for identity in identities):
            found.append(name)
        elif len({canonical_identity(identity[name]) for identity in identities}) > 1:
            found.append(name)
    return found


# --- G11 -------------------------------------------------------------------------


def is_pending(record: Mapping[str, Any]) -> bool:
    return isinstance(record, Mapping) and record.get("judge_status") == PENDING


def judged_path(path: Path) -> Path:
    """``<basename>.judged.json`` beside a pending ``<basename>.json``."""
    path = Path(path)
    if path.name.endswith(JUDGED_SUFFIX):
        return path
    stem = path.name[: -len(".json")] if path.name.endswith(".json") else path.name
    return path.with_name(stem + JUDGED_SUFFIX)


def _for(scope: Optional[str]) -> str:
    return f" for {scope}" if scope else ""


def pending_refusal(
    arms: Sequence[Tuple[str, Mapping[str, Any], Path]], *, scope: Optional[str] = None
) -> Optional[str]:
    """G11 over ``(label, arm record, artifact path)``: the refusal, or None."""
    pending = [(label, path) for label, record, path in arms if is_pending(record)]
    if not pending:
        return None
    named = "; ".join(
        f"{label} (wait for {judged_path(path)})" for label, path in pending
    )
    return (
        f"G11 refused{_for(scope)}: judge_status is pending for {named}. The deferred RAGAS "
        "judge has not scored these arms yet, so they have no RAGAS scores. Use "
        "the judged file: the merge writes it, and the publish step copies it "
        "beside the pending file."
    )


# --- G12 -------------------------------------------------------------------------


def _get(mapping: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(mapping, Mapping):
            return None
        mapping = mapping.get(key)
    return mapping


def judge_identity_of(record: Mapping[str, Any]) -> Any:
    """The identity G12 compares for one arm record.

    The recorded ``ragas_judge_identity_effective`` when the key is present,
    also when it is null. Else ``(evaluator_provider, evaluator_model)`` from
    the recorded configuration, by the rule of the harness's
    ``ragas_judge_identity``: the ``ragas_settings`` values win, and the
    benchmarking ``provider`` and ``model`` are the fallback.
    """
    if IDENTITY_KEY in record:
        return record[IDENTITY_KEY]
    bench = _get(record, "configuration", "services", "benchmarking") or {}
    ragas = _get(bench, "mode_settings", "ragas_settings") or {}
    provider = _get(ragas, "evaluator_provider") or _get(bench, "provider")
    model = _get(ragas, "evaluator_model") or _get(bench, "model")
    return {
        "evaluator_provider": None if provider is None else str(provider).lower(),
        "evaluator_model": model,
    }


def _key(identity: Any) -> str:
    if identity is NOT_RECORDED:
        return "<not recorded>"
    return canonical_identity(identity)


def _shown(value: Any) -> str:
    if value is NOT_RECORDED:
        return "<not recorded>"
    if value is None:
        return "null (no judge ran)"
    text = canonical_identity(value)
    return text if len(text) <= _SHOWN_CHARS else text[: _SHOWN_CHARS - 3] + "..."


def _labels(labels: Sequence[str]) -> str:
    shown = ", ".join(labels[:_SHOWN_LABELS])
    if len(labels) > _SHOWN_LABELS:
        shown += f" and {len(labels) - _SHOWN_LABELS} more"
    return shown


def identity_refusal(
    arms: Sequence[Tuple[str, Any]], *, scope: Optional[str] = None
) -> Optional[str]:
    """G12 over ``(label, identity)``: the refusal when the identities differ.

    The arms are grouped by identity, and each group's value of each differing
    field is shown. A null identity (no judge ran) and an identity that was
    never recorded differ from every judged identity.
    """
    groups: Dict[str, Tuple[Any, List[str]]] = {}
    for label, identity in arms:
        groups.setdefault(_key(identity), (identity, []))[1].append(label)
    if len(groups) <= 1:
        return None
    values = [identity for identity, _ in groups.values()]
    if all(isinstance(value, Mapping) for value in values):
        fields = differing_fields(values)
        detail = "; ".join(
            f"{name} ("
            + ", ".join(
                f"{_labels(labels)}: {_shown(identity.get(name, NOT_RECORDED))}"
                for identity, labels in groups.values()
            )
            + ")"
            for name in fields
        )
        what = f"These identity fields differ: {detail}"
    else:
        what = "The identities differ: " + "; ".join(
            f"{_labels(labels)}: {_shown(identity)}"
            for identity, labels in groups.values()
        )
    return (
        f"G12 refused{_for(scope)}: the arms were scored by different RAGAS "
        f"judges, so their scores are not one measurement. {what}. Score every "
        "arm with one judge."
    )


def _row_label(row: Mapping[str, Any]) -> str:
    return f"ledger row {row.get('stack')} {row.get('arm')} run {row.get('run')}"


def ledger_refusal(
    rows: Sequence[Mapping[str, Any]], arms: Sequence[Tuple[str, Any]]
) -> Optional[str]:
    """G12 for the archiver: the new *arms* against the campaign's RAGAS rows.

    Each earlier ``kind: ragas`` row of the ledger records its arm's identity.
    A row with no identity never equals a recorded one, so a campaign whose
    rows predate the record cannot take a new judged run.
    """
    earlier = [
        (_row_label(row), row.get(IDENTITY_KEY, NOT_RECORDED))
        for row in rows
        if row.get("kind") == "ragas"
    ]
    message = identity_refusal(earlier + list(arms))
    if message is None:
        return None
    if any(identity is NOT_RECORDED for _, identity in earlier):
        message += (
            " An earlier row of this campaign recorded no judge identity, and an "
            "identity that was never recorded never equals a recorded one: start "
            "a new campaign for this judge."
        )
    return message


# --- the archiver's artifact choice ------------------------------------------------


class Selection(NamedTuple):
    """The file to archive, and the file whose time dates the run."""

    artifact: Path
    dated_by: Path


def _load(path: Path) -> Dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text().replace("NaN", "null"))
    except (OSError, ValueError) as exc:
        raise JudgeGateRefusal(f"{path} does not load: {exc}") from exc
    if not isinstance(document, dict):
        raise JudgeGateRefusal(f"{path} does not load: not a JSON object")
    return document


def _arms(path: Path, document: Mapping[str, Any]) -> List[Tuple[str, Any, Path]]:
    records = document.get("benchmarking_results") or []
    return [
        (f"{path.name} arm {index}", record, path)
        for index, record in enumerate(records, 1)
    ]


def select_result(folder: Path, prefix: str) -> Selection:
    """The result the archiver takes from *folder*: the newest ``<prefix>*.json``.

    A result is ``<basename>.json``, with its judged file
    ``<basename>.judged.json`` when a deferred judge scored it; Lynx files are
    not results. The newest basename is chosen by the time of its own file, the
    one the run wrote, so a judged file copied back later does not make an
    older run look new. A result with a pending arm is refused (G11) unless its
    judged file exists, and a judged file with a pending arm is refused too.
    """
    groups: Dict[str, Dict[str, Path]] = {}
    for path in Path(folder).glob(f"{prefix}*.json"):
        name = path.name
        if any(name.endswith(suffix) for suffix in _NOT_RESULTS):
            continue
        if name.endswith(JUDGED_SUFFIX):
            groups.setdefault(name[: -len(JUDGED_SUFFIX)], {})["judged"] = path
        else:
            groups.setdefault(name[: -len(".json")], {})["result"] = path
    if not groups:
        raise JudgeGateRefusal(f"no artifact {prefix}*.json under {folder}")

    def dated_by(files: Dict[str, Path]) -> Path:
        return files.get("result") or files["judged"]

    newest = max(
        groups.values(),
        key=lambda files: (dated_by(files).stat().st_mtime, str(dated_by(files))),
    )
    result = newest.get("result")
    if result is not None:
        refusal = pending_refusal(_arms(result, _load(result)))
        if refusal is None:
            return Selection(result, result)
        if "judged" not in newest:
            raise JudgeGateRefusal(refusal)
    judged = newest["judged"]
    refusal = pending_refusal(_arms(judged, _load(judged)))
    if refusal is not None:
        raise JudgeGateRefusal(refusal)
    return Selection(judged, dated_by(newest))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="judge_gates.py",
        description="Choose the result the campaign archiver takes (G11).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--dir", required=True, type=Path)
    select.add_argument("--prefix", required=True)
    args = parser.parse_args(argv)
    try:
        choice = select_result(args.dir, args.prefix)
    except JudgeGateRefusal as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(choice.artifact)
    print(choice.dated_by)
    return 0


if __name__ == "__main__":
    sys.exit(main())
