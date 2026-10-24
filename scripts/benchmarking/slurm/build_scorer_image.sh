#!/usr/bin/env bash
# Build the scorer image of the offloaded RAGAS judge and its scorer-identity.json (#733;
# design D10 in openspec/changes/archive/2026-10-23-offload-ragas-judge-to-slurm).
#
#   build_scorer_image.sh <benchmark image tar> <out dir>
#   build_scorer_image.sh --dry-run <benchmark image tar> <out dir>
#
# <benchmark image tar> is `docker save` of the benchmark image that ran the deferred run,
# on the dev host:  docker save <benchmarking_image>:<tag> -o archi-benchmark.tar
# The scorer matches a bundle only when the run's code digest and package digest equal the
# scorer's, so build from the image of the run. <out dir> is the judge home
# ($HOME/archi-judge by default): the jobs read <out dir>/archi-scorer.sif and
# <out dir>/scorer-identity.json beside it.
#
# The build writes both files under a .new name first, computes the image digest (the
# SHA-256 of the SIF file), writes the sidecar with `score.py write-identity` inside the new
# image (the code digest of the image's own src, the package digest, the ragas version),
# and then moves the image and the sidecar into place, the image first: a judge job reads
# the sidecar first and the image second, so a job that starts during the two moves can
# only pair the old sidecar with the new image, which its image-digest check refuses as
# SCORER_STALE for the old sidecar (archi_judge.sbatch). A new sidecar has a new digest, so
# bundles that an old scorer recorded as INCOMPATIBLE or SCORER_STALE become available
# again with no clean-up. --dry-run prints the commands and runs nothing.
set -euo pipefail

die() {
  echo "build_scorer_image: refused: $*" >&2
  exit 1
}

DRY_RUN=false
if [ "${1:-}" = --dry-run ]; then
  DRY_RUN=true
  shift
fi
[ $# -eq 2 ] || die "usage: build_scorer_image.sh [--dry-run] <benchmark image tar> <out dir>"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
RECIPE="$REPO/scripts/benchmarking/slurm/archi-scorer.def"
if [ "$DRY_RUN" = true ]; then
  TAR="$1"
else
  [ -f "$1" ] || die "$1 is not a file; make it with docker save on the dev host"
  TAR="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
fi
OUT="$2"
IMAGE="$OUT/archi-scorer.sif"
SIDECAR="$OUT/scorer-identity.json"

BUILD=(apptainer build --force --build-arg "benchmark_image=$TAR" --build-arg "repo=$REPO"
  "$IMAGE.new" "$RECIPE")
identity() { # $1 = image digest
  IDENTITY=(apptainer exec --cleanenv --bind "$OUT" "$IMAGE.new"
    python3 /root/archi/scripts/benchmarking/judge/score.py write-identity
    --out "$SIDECAR.new" --image-digest "$1")
}

show() {
  printf '%q' "$1"
  shift
  printf ' %q' "$@"
  printf '\n'
}

if [ "$DRY_RUN" = true ]; then
  show "${BUILD[@]}"
  identity "sha256:<sha256 of $IMAGE.new>"
  show "${IDENTITY[@]}"
  show mv "$IMAGE.new" "$IMAGE"
  show mv "$SIDECAR.new" "$SIDECAR"
  exit 0
fi

command -v apptainer >/dev/null || die "apptainer is not on PATH"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
IMAGE="$OUT/archi-scorer.sif"
SIDECAR="$OUT/scorer-identity.json"
BUILD=(apptainer build --force --build-arg "benchmark_image=$TAR" --build-arg "repo=$REPO"
  "$IMAGE.new" "$RECIPE")
rm -f "$IMAGE.new" "$SIDECAR.new"

echo "build_scorer_image: building $IMAGE from $TAR" >&2
"${BUILD[@]}"
DIGEST="sha256:$(sha256sum "$IMAGE.new" | cut -d' ' -f1)"
identity "$DIGEST"
"${IDENTITY[@]}"
[ -s "$SIDECAR.new" ] || die "score.py write-identity wrote no $SIDECAR.new (see above)"
mv -f "$IMAGE.new" "$IMAGE"
mv -f "$SIDECAR.new" "$SIDECAR"
echo "build_scorer_image: wrote $IMAGE ($DIGEST) and $SIDECAR" >&2
