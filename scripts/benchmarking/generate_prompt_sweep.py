"""Generate a RAGAS prompt-sweep: one benchmarking config per prompt variant.

A prompt sweep runs N agent prompts through the existing RAGAS harness with
everything but the prompt held fixed, so the only moving part is
`services.benchmarking.agent_md_file`. This script takes one base config plus a
list of prompt files (a sweep manifest) and writes one rendered config per
prompt into a sweep directory. Each generated config is byte-for-byte identical
to the base except for:

  - services.benchmarking.agent_md_file  -> the prompt's path
  - services.benchmarking.name           -> the prompt's filename stem
  - services.benchmarking.primary_metric -> manifest primary_metric (if set)

Holding everything else constant is what makes the resulting leaderboard an
apples-to-apples comparison (the leaderboard's shared_context cross-check will
flag any drift). Run the sweep with:

    archi evaluate --config-dir <sweep_dir> --hostmode ...

Manifest format (YAML):

    base_config: config/benchmarking/ragas.yaml
    out_dir: bench_out/sweep_configs        # optional, this is the default
    primary_metric: faithfulness            # optional, default faithfulness
    prompts:
      - config/agents/fasrc-cannon-v1-strict.md
      - config/agents/fasrc-cannon-v2-lean.md
      - config/agents/fasrc-cannon-v3-cited.md
      - config/agents/fasrc-cannon-v4-linked.md

Usage:
    python scripts/benchmarking/generate_prompt_sweep.py --manifest config/benchmarking/prompt_sweep.yaml
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULT_OUT_DIR = "bench_out/sweep_configs"
DEFAULT_PRIMARY_METRIC = "faithfulness"
KNOWN_METRICS = {
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
}


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a YAML mapping at {path}, got {type(data).__name__}"
        )
    return data


def generate_sweep_configs(manifest_path: Path) -> List[Path]:
    """Render one benchmarking config per prompt listed in the manifest.

    Validates that every prompt file exists BEFORE writing anything, so a bad
    manifest never leaves a partial set of configs in the sweep directory.
    Returns the list of written config paths.
    """
    manifest = _load_yaml(manifest_path)

    base_config_raw = manifest.get("base_config")
    if not base_config_raw:
        raise ValueError("Manifest missing required key 'base_config'.")
    prompts_raw = manifest.get("prompts")
    if not isinstance(prompts_raw, list) or not prompts_raw:
        raise ValueError(
            "Manifest 'prompts' must be a non-empty list of prompt file paths."
        )

    primary_metric = str(manifest.get("primary_metric", DEFAULT_PRIMARY_METRIC))
    if primary_metric not in KNOWN_METRICS:
        raise ValueError(
            f"Manifest primary_metric '{primary_metric}' is not a known RAGAS metric "
            f"{sorted(KNOWN_METRICS)}."
        )

    # Relative paths resolve against the current working directory (the repo
    # root — the documented place to run this from, and how the prompts in the
    # example manifest are written). Absolute paths are used as-is.
    repo_root = Path.cwd()

    def _resolve(p: str) -> Path:
        candidate = Path(p)
        return candidate if candidate.is_absolute() else repo_root / candidate

    base_config_path = _resolve(str(base_config_raw))
    if not base_config_path.is_file():
        raise ValueError(f"base_config not found: '{base_config_path}'")

    prompt_paths = [_resolve(str(p)) for p in prompts_raw]
    missing = [str(p) for p in prompt_paths if not p.is_file()]
    if missing:
        # Atomic: refuse before writing any config.
        raise ValueError(
            f"Prompt file(s) not found, aborting (no configs written): {missing}"
        )

    out_dir = _resolve(str(manifest.get("out_dir", DEFAULT_OUT_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)

    base_config = _load_yaml(base_config_path)
    if "services" not in base_config or "benchmarking" not in base_config.get(
        "services", {}
    ):
        raise ValueError(
            f"base_config '{base_config_path}' has no services.benchmarking section to sweep."
        )

    written: List[Path] = []
    for prompt_path in prompt_paths:
        cfg = copy.deepcopy(base_config)
        bench = cfg["services"]["benchmarking"]
        stem = prompt_path.stem
        bench["agent_md_file"] = str(prompt_path)
        bench["name"] = stem
        bench["primary_metric"] = primary_metric

        out_path = out_dir / f"{stem}.yaml"
        with open(out_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        written.append(out_path)

    return written


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest",
        "-m",
        required=True,
        help="Path to a sweep manifest YAML (base_config + prompts).",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        written = generate_sweep_configs(manifest_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    out_dir = written[0].parent
    manifest = _load_yaml(manifest_path)
    primary_metric = str(manifest.get("primary_metric", DEFAULT_PRIMARY_METRIC))

    print(f"Wrote {len(written)} sweep config(s) to {out_dir}/:")
    for p in written:
        print(f"  - {p.name}")
    print()
    print("Next: run the sweep (leaderboard ranks by " f"'{primary_metric}'):")
    print(f"  archi evaluate --config-dir {out_dir} --hostmode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
