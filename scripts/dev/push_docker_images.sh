#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/docker_common.sh"

RUNTIME="${CONTAINER_RUNTIME:-docker}"
GHCR_NAMESPACE="${GHCR_NAMESPACE:-ghcr.io/fasrc}"
TAG_INPUT=""
IMAGE_FILTER=""

usage() {
  cat <<USAGE
Usage: push_docker_images.sh [OPTIONS] [TAG]

Pushes previously built base Docker images to one or both registries:
- ${GHCR_NAMESPACE}/...   (always; assumes caller has already logged in via docker/login-action)
- docker.io/...           (only when DOCKERHUB_USERNAME and DOCKERHUB_TOKEN are both set to
                          non-whitespace values; otherwise skipped with a notice, NOT a failure)

Arguments:
- TAG (optional): image tag to use; defaults to the project version from pyproject.toml.

Options:
- -i, --image NAME        Only push this image (e.g. a2rchi/a2rchi-python-base); defaults to all.
- -h, --help              Show this help and exit.

Environment:
- CONTAINER_RUNTIME=docker|podman    override the container CLI (defaults to docker)
- GHCR_NAMESPACE                     override the ghcr namespace prefix (defaults to ghcr.io/fasrc)
- PUSH_LATEST=true|false             also push the :latest tag (defaults to true)
- DOCKERHUB_USERNAME, DOCKERHUB_TOKEN    if both set, also push to docker.io
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--image)
      if [[ $# -lt 2 ]]; then
        echo "Error: --image requires a value." >&2
        usage
        exit 1
      fi
      IMAGE_FILTER="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Error: unknown option '$1'." >&2
      usage
      exit 1
      ;;
    *)
      if [[ -n "$TAG_INPUT" ]]; then
        echo "Error: multiple tag values provided: '$TAG_INPUT' and '$1'." >&2
        usage
        exit 1
      fi
      TAG_INPUT="$1"
      shift
      ;;
  esac
done

# Returns 0 if the argument is non-empty AND contains at least one non-whitespace character.
_nonempty() {
  local val="${1:-}"
  [[ "$val" =~ [^[:space:]] ]]
}

TAG="$(resolve_tag "$TAG_INPUT" || true)"
if [[ -z "$TAG" ]]; then
  echo "Error: unable to determine image tag." >&2
  usage
  exit 1
fi

ensure_runtime "$RUNTIME"

echo "Using container runtime: $RUNTIME"
echo "Image tag: $TAG"
echo "GHCR namespace: $GHCR_NAMESPACE"

PUSH_LATEST="${PUSH_LATEST:-true}"
TAGS=("$TAG")
if [[ "${PUSH_LATEST,,}" == "true" ]]; then
  TAGS+=("latest")
fi

# Resolve the set of images to push (filter or all).
if [[ -n "$IMAGE_FILTER" ]]; then
  if [[ -z "${IMAGE_DIRS[$IMAGE_FILTER]:-}" ]]; then
    echo "Error: image '$IMAGE_FILTER' not found in IMAGE_DIRS. Known images:" >&2
    for image in "${!IMAGE_DIRS[@]}"; do
      echo "  - $image" >&2
    done
    exit 1
  fi
  IMAGES_TO_PUSH=("$IMAGE_FILTER")
else
  IMAGES_TO_PUSH=("${!IMAGE_DIRS[@]}")
fi

# Verify all selected images are present locally before any push attempt.
for image in "${IMAGES_TO_PUSH[@]}"; do
  for tag in "${TAGS[@]}"; do
    if ! "$RUNTIME" image inspect "$image:$tag" >/dev/null 2>&1; then
      echo "Error: image $image:$tag not found locally. Build it before pushing." >&2
      exit 1
    fi
  done
done

# --- Push to ghcr.io (always; auth handled by caller via docker/login-action) ---
echo
echo "Pushing to ${GHCR_NAMESPACE}/..."
for image in "${IMAGES_TO_PUSH[@]}"; do
  # image looks like "a2rchi/a2rchi-python-base"; strip namespace to get the bare image name.
  image_name="${image##*/}"
  for tag in "${TAGS[@]}"; do
    src="$image:$tag"
    dst="${GHCR_NAMESPACE}/${image_name}:${tag}"
    echo "  Tagging $src -> $dst"
    "$RUNTIME" tag "$src" "$dst"
    echo "  Pushing $dst"
    "$RUNTIME" push "$dst"
  done
done

# --- Push to docker.io (only if both DockerHub credentials are non-empty/non-whitespace) ---
DOCKERHUB_SECRET="${DOCKERHUB_TOKEN:-${DOCKERHUB_PASSWORD:-}}"
if _nonempty "${DOCKERHUB_USERNAME:-}" && _nonempty "$DOCKERHUB_SECRET"; then
  echo
  echo "Pushing to docker.io/..."
  docker_login "$RUNTIME"
  for image in "${IMAGES_TO_PUSH[@]}"; do
    for tag in "${TAGS[@]}"; do
      echo "  Pushing $image:$tag"
      "$RUNTIME" push "$image:$tag"
    done
  done
else
  echo
  echo "DockerHub credentials not configured; skipping docker.io publish."
fi

if [[ "${PUSH_LATEST,,}" == "true" ]]; then
  echo "All push targets processed for tags $TAG and latest."
else
  echo "All push targets processed for tag $TAG."
fi
