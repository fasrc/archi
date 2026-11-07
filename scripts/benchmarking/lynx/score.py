"""Score one judge bundle with Lynx (design D3, D5, D6, D7).

    python -m scripts.benchmarking.lynx.score <bundle folder> --model <hf id> \
        --revision <sha> --out-dir <folder> [--backend vllm|stub]

The bundle folder is a run's own bundle (``BUNDLE_COMPLETE``) or its queue copy
(``READY``). The scorer checks it before it builds the backend, never changes a
file in it, and writes ``<out-dir>/<result_basename>-arm<arm_index>.lynx.json``.
Give the folder of the result JSON as ``--out-dir``: ``compare_runs.py`` reads
the Lynx file beside the result. An existing Lynx file is never replaced.

A row whose prompt tokens plus 600 is more than 8,000 is ``skipped_overflow``;
it is never truncated and never sent. A row whose output has no strict verdict
is ``parse_error``. ``lynx_faithfulness`` is ``lynx_pass / lynx_scored``.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from scripts.benchmarking.lynx.backends import (
    CONTEXT_TOKENS,
    MAX_NEW_TOKENS,
    make_backend,
)
from scripts.benchmarking.lynx.parse import parse_output
from scripts.benchmarking.lynx.prompt import build_prompt

COMPLETE_MARKERS = ("BUNDLE_COMPLETE", "READY")
STATUSES = ("scored", "skipped_overflow", "parse_error")
EXIT_OK = 0
EXIT_REFUSED = 1


class Refused(Exception):
    """The bundle or the arguments cannot give a Lynx file; nothing is written."""


@dataclasses.dataclass(frozen=True)
class Bundle:
    folder: Path
    result_basename: str
    arm_index: int
    rows_sha256: str
    rows: List[Dict[str, Any]]


def _check_row(number: int, line: str) -> Dict[str, Any]:
    try:
        row = json.loads(line)
    except ValueError as exc:
        raise Refused(f"rows.jsonl line {number} is not JSON: {exc}") from None
    contexts = row.get("retrieved_contexts") if isinstance(row, dict) else None
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("key"), str)
        or not isinstance(row.get("user_input"), str)
        or not isinstance(row.get("response"), str)
        or not isinstance(contexts, list)
        or not all(isinstance(text, str) for text in contexts)
    ):
        raise Refused(
            f"rows.jsonl line {number} is not a row with a string key, user_input "
            f"and response and a list of string retrieved_contexts"
        )
    return row


def read_bundle(folder: Path) -> Bundle:
    """Check one bundle folder and read its rows (design D5)."""
    if not folder.is_dir():
        raise Refused(f"{folder} is not a folder")
    if not any((folder / marker).is_file() for marker in COMPLETE_MARKERS):
        raise Refused(f"{folder} has neither BUNDLE_COMPLETE nor READY")
    try:
        manifest = json.loads((folder / "manifest.json").read_bytes())
        data = (folder / "rows.jsonl").read_bytes()
    except (OSError, ValueError) as exc:
        raise Refused(f"{folder}: manifest.json or rows.jsonl does not load: {exc}")
    if not isinstance(manifest, dict):
        raise Refused(f"{folder}: manifest.json is not an object")
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if digest != manifest.get("rows_sha256"):
        raise Refused(
            f"{folder}: rows.jsonl has {digest}, but the manifest's rows_sha256 is "
            f"{manifest.get('rows_sha256')!r}"
        )
    basename = manifest.get("result_basename")
    arm_index = manifest.get("arm_index")
    if (
        not isinstance(basename, str)
        or not basename
        or not isinstance(arm_index, int)
        or isinstance(arm_index, bool)
        or arm_index < 0
    ):
        raise Refused(
            f"{folder}: the manifest has no valid result_basename and arm_index"
        )
    if folder.name != f"{basename}-arm{arm_index}":
        raise Refused(
            f"{folder}: the folder name is not {basename}-arm{arm_index}, as its "
            f"manifest says"
        )
    try:
        lines = data.decode("utf-8").split("\n")
    except UnicodeDecodeError as exc:
        raise Refused(f"{folder}: rows.jsonl is not UTF-8: {exc}") from None
    if lines[-1] == "":
        lines.pop()
    rows: List[Dict[str, Any]] = []
    first_line: Dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        row = _check_row(number, line)
        if row["key"] in first_line:
            raise Refused(
                f"rows.jsonl line {number}: key {row['key']!r} repeats line "
                f"{first_line[row['key']]}"
            )
        first_line[row["key"]] = number
        rows.append(row)
    if manifest.get("row_count") != len(rows):
        raise Refused(
            f"{folder}: rows.jsonl has {len(rows)} rows, but the manifest's "
            f"row_count is {manifest.get('row_count')!r}"
        )
    return Bundle(folder, basename, arm_index, digest, rows)


def lynx_file_name(result_basename: str, arm_index: int) -> str:
    return f"{result_basename}-arm{arm_index}.lynx.json"


def check_out_dir(out_dir: Path, bundle: Bundle) -> Path:
    """The Lynx file path in *out_dir*, which must be outside the bundle (D6)."""
    if not out_dir.is_dir():
        raise Refused(f"--out-dir {out_dir} is not a folder")
    out = out_dir.resolve()
    folder = bundle.folder.resolve()
    if out == folder or folder in out.parents:
        raise Refused(
            f"--out-dir {out_dir} is the bundle folder or is inside the bundle "
            f"folder {bundle.folder}; the bundle must stay unchanged"
        )
    target = out_dir / lynx_file_name(bundle.result_basename, bundle.arm_index)
    if target.exists():
        raise Refused(f"{target} exists; remove it to score the bundle again")
    return target


def count_rows(rows: Mapping[str, Mapping[str, Any]]) -> Dict[str, int]:
    """The four Lynx counts of a ``rows`` mapping (design D7)."""
    entries = list(rows.values())
    return {
        "lynx_scored": sum(1 for e in entries if e.get("status") == "scored"),
        "lynx_pass": sum(
            1 for e in entries if e.get("status") == "scored" and e["verdict"] == "PASS"
        ),
        "lynx_skipped_overflow": sum(
            1 for e in entries if e.get("status") == "skipped_overflow"
        ),
        "lynx_parse_errors": sum(
            1 for e in entries if e.get("status") == "parse_error"
        ),
    }


def faithfulness(counts: Mapping[str, int]) -> Optional[float]:
    """PASS over scored rows only; ``None`` when no row was scored."""
    if not counts["lynx_scored"]:
        return None
    return counts["lynx_pass"] / counts["lynx_scored"]


def score_bundle(
    bundle: Bundle,
    backend: Any,
    *,
    model_id: str,
    model_revision: str,
    backend_name: str,
) -> Dict[str, Any]:
    """The Lynx document of one bundle (design D3, D7)."""
    rows: Dict[str, Dict[str, Any]] = {}
    pending = []
    for row in bundle.rows:
        prompt = build_prompt(row)
        tokens = backend.count_tokens(prompt)
        if tokens + MAX_NEW_TOKENS > CONTEXT_TOKENS:
            rows[row["key"]] = {
                "verdict": None,
                "status": "skipped_overflow",
                "reasoning": None,
                "prompt_tokens": tokens,
            }
        else:
            pending.append((row["key"], prompt, tokens))
    prompts = [prompt for _, prompt, _ in pending]
    outputs = backend.generate(prompts) if prompts else []
    if len(outputs) != len(prompts):
        raise RuntimeError(
            f"the backend returned {len(outputs)} outputs for {len(prompts)} prompts"
        )
    for (key, _, tokens), text in zip(pending, outputs):
        parsed = parse_output(text)
        if parsed.verdict is None:
            rows[key] = {
                "verdict": None,
                "status": "parse_error",
                "reasoning": None,
                "prompt_tokens": tokens,
                "error": parsed.error,
                "output": text,
            }
        else:
            rows[key] = {
                "verdict": parsed.verdict,
                "status": "scored",
                "reasoning": parsed.reasoning,
                "prompt_tokens": tokens,
            }
    counts = count_rows(rows)
    return {
        "model_id": model_id,
        "model_revision": model_revision,
        "backend": backend_name,
        "result_basename": bundle.result_basename,
        "arm_index": bundle.arm_index,
        "bundle_rows_sha256": bundle.rows_sha256,
        "token_budget": {
            "context_tokens": CONTEXT_TOKENS,
            "max_new_tokens": MAX_NEW_TOKENS,
        },
        "rows": rows,
        "counts": counts,
        "lynx_faithfulness": faithfulness(counts),
    }


def write_lynx_file(target: Path, document: Mapping[str, Any]) -> None:
    """Create *target* with canonical JSON; never replace an existing file."""
    data = (
        json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise Refused(
                f"{target} exists; remove it to score the bundle again"
            ) from None
    finally:
        os.unlink(temporary)


def main(
    argv: Optional[Sequence[str]] = None,
    backend_factory: Callable[[str, str, str], Any] = make_backend,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.benchmarking.lynx.score",
        description="Score one judge bundle with PatronusAI Lynx.",
    )
    parser.add_argument("bundle", type=Path, help="the bundle folder")
    parser.add_argument("--model", required=True, help="the Hugging Face model id")
    parser.add_argument("--revision", required=True, help="the model weight revision")
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="the folder of the result JSON; must be outside the bundle folder",
    )
    parser.add_argument("--backend", choices=("vllm", "stub"), default="vllm")
    args = parser.parse_args(argv)
    try:
        for flag, value in (("--model", args.model), ("--revision", args.revision)):
            if not value.strip():
                raise Refused(f"{flag} must not be empty")
        bundle = read_bundle(args.bundle)
        target = check_out_dir(args.out_dir, bundle)
        backend = backend_factory(args.backend, args.model, args.revision)
        document = score_bundle(
            bundle,
            backend,
            model_id=args.model,
            model_revision=args.revision,
            backend_name=args.backend,
        )
        write_lynx_file(target, document)
    except Refused as exc:
        print(f"lynx: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    counts = ", ".join(f"{name} {value}" for name, value in document["counts"].items())
    print(
        f"lynx: wrote {target}: {counts}, "
        f"lynx_faithfulness {document['lynx_faithfulness']}"
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
