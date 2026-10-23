#!/usr/bin/env python3
"""The judge queue: which bundles a judge job can take (judge offload D4a, D5, D5a).

    python scripts/benchmarking/judge/judge_queue.py --queue <dir> \
        --judge-env <judge.env> --sidecar <scorer-identity.json> (--status | --count)

Standard library only: the submitter runs this on a login node with no project
environment. ``src/utils/exclusive_link.py`` is loaded by path for the same
reason. The file is not called ``queue.py``: a script folder that holds a
``queue.py`` hides the standard library's ``queue`` module from every script in
that folder, and ``concurrent.futures`` (so httpx and ragas) then fail to import.

**judge.env** holds the judge settings of the job, one ``KEY=VALUE`` per line.
Blank lines and lines that start with ``#`` are skipped, and one pair of matching
quotes around a value is removed. No shell expansion is done. Required keys:

- ``JUDGE_MODEL_ID``: the model the job serves (vLLM ``--served-model-name``);
- ``JUDGE_MODEL_REVISION``: its pinned Hugging Face revision;
- ``JUDGE_PRECISION``: the weight precision, for example ``bf16`` or ``fp8``;
- ``JUDGE_BOOTSTRAP_ROWS_PER_MINUTE``: a positive number, the rate the time
  budget uses before the job has scored a row (D5).

Optional keys, recorded in the judge identity (D10) and ``None`` when absent:
``JUDGE_IMAGE_DIGEST`` (the vLLM image) and ``JUDGE_SERVER_ARGS`` (the
output-relevant ``vllm serve`` arguments, for example ``--max-model-len``).

**scorer-identity.json** (the sidecar) is written beside the scorer image by
``score.py write-identity``. It holds at least ``code_digest`` and
``package_digest``. Its digest is the SHA-256 of its bytes, and its token (the
hex part) names the ``SCORER_STALE.<token>`` and ``INCOMPATIBLE.<token>``
records.

A bundle **matches** a job when its manifest names the judge model and revision
of judge.env, its code digest and package digest equal the sidecar's, and no
``<queue>/SCORER_STALE.<token>`` or ``<bundle>/INCOMPATIBLE.<token>`` record names
the sidecar. A claim is a ``claim.<gen>`` file that holds the job id; the current
claim is the highest ``gen``. It is stale when the bundle has no ``SCORED`` or
``FAILED``, the job is not in ``squeue``, and ``sacct`` reports it in a terminal
state. A takeover links ``claim.<gen+1>`` and never removes or renames a claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple, TypeVar

_REPO = Path(__file__).resolve().parents[3]

REQUIRED_ENV_KEYS = (
    "JUDGE_MODEL_ID",
    "JUDGE_MODEL_REVISION",
    "JUDGE_PRECISION",
    "JUDGE_BOOTSTRAP_ROWS_PER_MINUTE",
)
OPTIONAL_ENV_KEYS = ("JUDGE_IMAGE_DIGEST", "JUDGE_SERVER_ARGS")
TERMINAL_MARKERS = ("SCORED", "FAILED")
# sacct states after which a job can never run again under its id.
TERMINAL_JOB_STATES = frozenset(
    {
        "BOOT_FAIL",
        "CANCELLED",
        "COMPLETED",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "TIMEOUT",
    }
)
_CLAIM_NAME = re.compile(r"^claim\.(\d+)$")
_TIME_LEFT = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$")

T = TypeVar("T")


def _load_link_exclusive():
    path = _REPO / "src" / "utils" / "exclusive_link.py"
    spec = importlib.util.spec_from_file_location("archi_exclusive_link", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.link_exclusive


link_exclusive = _load_link_exclusive()


class Sidecar(NamedTuple):
    identity: Dict[str, object]
    digest: str
    token: str


class Claim(NamedTuple):
    gen: int
    job_id: str


def read_judge_env(path) -> Dict[str, Optional[str]]:
    """The settings of a judge.env file; raises ValueError naming the problem."""
    values: Dict[str, Optional[str]] = {}
    text = Path(path).read_text()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}: line {number} is not KEY=VALUE: {raw!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    for key in REQUIRED_ENV_KEYS:
        if not values.get(key):
            raise ValueError(f"{path}: {key} is missing or empty")
    rate = values["JUDGE_BOOTSTRAP_ROWS_PER_MINUTE"]
    try:
        number = float(rate)
    except ValueError:
        number = math.nan
    if not math.isfinite(number) or number <= 0:
        raise ValueError(
            f"{path}: JUDGE_BOOTSTRAP_ROWS_PER_MINUTE must be a positive number, "
            f"not {rate!r}"
        )
    for key in OPTIONAL_ENV_KEYS:
        values[key] = values.get(key) or None
    return values


def read_sidecar(path) -> Sidecar:
    """The scorer sidecar and its digest; raises ValueError if a digest is missing."""
    data = Path(path).read_bytes()
    identity = json.loads(data)
    if not isinstance(identity, dict):
        raise ValueError(f"{path}: the scorer identity is not a JSON object")
    for key in ("code_digest", "package_digest"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise ValueError(f"{path}: the scorer identity has no {key}")
    token = hashlib.sha256(data).hexdigest()
    return Sidecar(identity, f"sha256:{token}", token)


def read_manifest(bundle: Path) -> Optional[dict]:
    try:
        manifest = json.loads((Path(bundle) / "manifest.json").read_bytes())
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def is_terminal(bundle: Path) -> bool:
    return any((Path(bundle) / name).exists() for name in TERMINAL_MARKERS)


def ready_bundles(queue: Path) -> List[Path]:
    """The bundle folders with ``READY``, oldest ``READY`` first."""
    found = [p for p in Path(queue).iterdir() if (p / "READY").is_file()]
    return sorted(found, key=lambda p: ((p / "READY").stat().st_mtime, p.name))


def match_reason(
    bundle: Path, judge_env: Dict[str, Optional[str]], sidecar: Sidecar, queue: Path
) -> Optional[str]:
    """None when the job of *judge_env* and *sidecar* can score *bundle*, else why not."""
    manifest = read_manifest(bundle)
    if manifest is None:
        return "manifest.json does not load"
    judge = manifest.get("judge_model") or {}
    wanted = f"{judge.get('id')}@{judge.get('revision')}"
    loaded = f"{judge_env['JUDGE_MODEL_ID']}@{judge_env['JUDGE_MODEL_REVISION']}"
    if wanted != loaded:
        return f"waits for judge {wanted}; the configured judge is {loaded}"
    code = (manifest.get("code_version") or {}).get("digest")
    package = manifest.get("package_digest")
    scorer_code = sidecar.identity["code_digest"]
    scorer_package = sidecar.identity["package_digest"]
    if code != scorer_code or package != scorer_package:
        return (
            f"waits for a scorer with code digest {code} and package digest "
            f"{package}; the configured scorer has code digest {scorer_code} and "
            f"package digest {scorer_package}"
        )
    if (Path(queue) / f"SCORER_STALE.{sidecar.token}").exists():
        return (
            f"SCORER_STALE.{sidecar.token}: the configured scorer sidecar does not "
            f"describe the live scorer environment"
        )
    incompatible = Path(bundle) / f"INCOMPATIBLE.{sidecar.token}"
    if incompatible.exists():
        return f"{incompatible.name}: {incompatible.read_text().strip()}"
    return None


def current_claim(bundle: Path) -> Optional[Claim]:
    """The claim with the highest ``gen``, or None when the bundle has no claim."""
    claims = []
    for path in Path(bundle).iterdir():
        found = _CLAIM_NAME.match(path.name)
        if found:
            claims.append((int(found.group(1)), path))
    if not claims:
        return None
    gen, path = max(claims)
    return Claim(gen, path.read_text().strip())


def _run(command: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            list(command), capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def job_in_squeue(job_id: str) -> Optional[bool]:
    """True when squeue lists the job, False when it does not, None when unknown."""
    proc = _run(["squeue", "-h", "-j", job_id, "-o", "%i"])
    if proc is None:
        return None
    if proc.returncode == 0:
        return bool(proc.stdout.strip())
    if "invalid job id" in proc.stderr.lower():
        return False
    return None


def job_ended(job_id: str) -> bool:
    """True only when sacct reports the job in a terminal state."""
    proc = _run(["sacct", "-n", "-X", "-P", "-j", job_id, "-o", "State"])
    if proc is None or proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if line.strip():
            return line.split()[0].upper() in TERMINAL_JOB_STATES
    return False


def claim_is_stale(bundle: Path, claim: Claim) -> bool:
    """Stale: no terminal marker, the job not in squeue, and terminal in sacct.

    A claim with no job id can only come from a manual edit; no owner of it can
    exist, so it is stale. When Slurm cannot be asked, a claim is never stale.
    """
    if is_terminal(bundle):
        return False
    if not claim.job_id:
        return True
    if job_in_squeue(claim.job_id) is not False:
        return False
    return job_ended(claim.job_id)


def try_claim(bundle: Path, job_id: str) -> bool:
    """Claim *bundle* for *job_id*; True when the job holds the current claim."""
    claim = current_claim(bundle)
    if claim is None:
        gen = 1
    elif claim.job_id == job_id:
        return True
    elif claim_is_stale(bundle, claim):
        gen = claim.gen + 1
    else:
        return False
    return link_exclusive(Path(bundle) / f"claim.{gen}", f"{job_id}\n".encode())


def _claimable(bundle: Path) -> bool:
    claim = current_claim(bundle)
    return claim is None or claim_is_stale(bundle, claim)


def available(
    queue: Path, judge_env: Dict[str, Optional[str]], sidecar: Sidecar
) -> List[Path]:
    """READY bundles with no terminal marker that match and are free to claim."""
    return [
        bundle
        for bundle in ready_bundles(queue)
        if not is_terminal(bundle)
        and match_reason(bundle, judge_env, sidecar, queue) is None
        and _claimable(bundle)
    ]


def own_work(queue: Path, job_id: str) -> List[Path]:
    """Bundles whose current claim is *job_id*'s, with no terminal marker (requeue)."""
    found = []
    for bundle in ready_bundles(queue):
        claim = current_claim(bundle)
        if claim is not None and claim.job_id == job_id and not is_terminal(bundle):
            found.append(bundle)
    return found


def status_lines(
    queue: Path, judge_env: Dict[str, Optional[str]], sidecar: Sidecar
) -> List[str]:
    """One line for each READY bundle without SCORED, then each SCORER_STALE record."""
    lines = []
    for bundle in ready_bundles(queue):
        if (bundle / "SCORED").exists():
            continue
        if (bundle / "FAILED").exists():
            reason = (bundle / "FAILED").read_text().strip()
            lines.append(f"{bundle.name}: FAILED: {reason}")
            continue
        reason = match_reason(bundle, judge_env, sidecar, queue)
        claim = current_claim(bundle)
        if reason is not None:
            lines.append(f"{bundle.name}: not matched: {reason}")
        elif claim is None:
            lines.append(f"{bundle.name}: available")
        elif claim_is_stale(bundle, claim):
            owner = claim.job_id or "<no job id>"
            lines.append(
                f"{bundle.name}: stale claim of job {owner} (claim.{claim.gen}); "
                f"available"
            )
        else:
            lines.append(
                f"{bundle.name}: claimed by job {claim.job_id} (claim.{claim.gen})"
            )
    for record in sorted(Path(queue).glob("SCORER_STALE.*")):
        lines.append(f"{record.name}: {record.read_text().strip()}")
    return lines


def parse_time_left(text: str) -> Optional[float]:
    """Seconds from a ``squeue -o %L`` value.

    ``UNLIMITED`` is ``math.inf``. Anything else that does not parse
    (``NOT_SET``, ``INVALID``, an empty answer) is None: unknown, not unlimited.
    """
    if text.strip() == "UNLIMITED":
        return math.inf
    found = _TIME_LEFT.match(text.strip())
    if not found:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def time_left(job_id: Optional[str]) -> Optional[float]:
    """Seconds left in the job's allocation; ``math.inf`` when unlimited, None
    when squeue cannot tell (no job id, a failed query, an unreadable answer)."""
    if not job_id:
        return None
    proc = _run(["squeue", "-h", "-j", job_id, "-o", "%L"])
    if proc is None or proc.returncode != 0:
        return None
    lines = proc.stdout.strip().splitlines()
    return parse_time_left(lines[0]) if lines else None


def plan_claims(
    candidates: Sequence[Tuple[T, int]],
    *,
    seconds_left: Optional[float],
    rows_per_minute: float,
    first_is_free: bool,
) -> List[T]:
    """The candidates to claim now, in order, under the time budget (D5).

    *candidates* are ``(bundle, row_count)``, oldest first. The time that the
    bundles already picked need is counted against *seconds_left*, so a job
    claims at start only what the rate says it can finish. With
    *first_is_free*, the first candidate is picked whatever it needs (a job
    always scores at least one bundle). ``math.inf`` seconds is no limit.
    ``None`` seconds (squeue could not tell) picks at most the first
    candidate: a failed query must not let one job claim the whole queue.
    """
    if seconds_left is None:
        return [item for item, _ in candidates[:1]]
    picked: List[T] = []
    committed = 0.0
    for item, rows in candidates:
        need = rows / rows_per_minute * 60
        free = first_is_free and not picked
        if free or committed + need <= seconds_left:
            picked.append(item)
            committed += need
    return picked


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="List or count the judge bundles a judge job can take."
    )
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--judge-env", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true")
    action.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)
    try:
        judge_env = read_judge_env(args.judge_env)
        sidecar = read_sidecar(args.sidecar)
    except (OSError, ValueError) as exc:
        print(f"judge_queue: {exc}", file=sys.stderr)
        return 2
    if args.count:
        print(len(available(args.queue, judge_env, sidecar)))
    else:
        for line in status_lines(args.queue, judge_env, sidecar):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
