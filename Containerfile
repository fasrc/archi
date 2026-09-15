# Consumer Containerfile — archi's loop sandbox.
#
# FROM the ralph base image (carries the loop machinery on PATH); add archi's
# toolchain. Build the base first: `make -C "$CLAUDE_PLUGIN_ROOT" build-base`
# (or `make build-base` from a keep-on-ralphing checkout).

FROM ralph-base:v1

# --- archi toolchain --------------------------------------------------------
# The gate runs: black, isort, python -m pytest (with pytest-cov). Pins match
# .github/workflows/pr-preview.yml. requirements-base carries archi's runtime
# deps so `tests/unit/` imports resolve. PYTHONPATH puts the bind-mounted repo
# root on sys.path so `src` imports work without an editable install (which the
# /workspace bind mount would shadow at runtime).

# System packages (root). Two groups, one apt layer:
#
#   build-essential, python3-dev — requirements-base includes packages with no
#   3.12 wheels that compile from source — hnswlib (C++), duckdb, lz4 — so they
#   need a compiler and the Python dev headers.
#
#   jq — Loop 2 reaches for it whenever a task touches JSON. The host (1.8.1) and
#   CI runners both have it, so its absence *here* is invisible until a run needs
#   it, and the failure is not a clean halt: on 2026-08-04 a run needed jq, found
#   none, and downloaded a 2.3 MB static binary into ./bin — inside the
#   bind-mounted repo. That left the bot checkout permanently dirty, and a dirty
#   tree is precisely what verify-bot-checkout.sh refuses to start on, so the next
#   six nights aborted in phase 0 on the residue of that one improvisation. A
#   missing CLI tool here does not fail the run that needs it; it fails every run
#   after it. Shipping ~1 MB of jq is cheaper than that.
#
#   Deliberately NOT version-pinned, unlike openspec below. openspec is pinned
#   because its --strict verdict decides whether Loop 1 (host) and Loop 2 (here)
#   agree on what passes, so a gap there is a silent disagreement inside one run.
#   jq gets whatever the base image's Debian ships — 1.7 today, and it will move
#   on a future base bump; do not read "1.7" here as a guarantee.
#
#   The gap runs the risky way round: the container (1.7) is OLDER than the host
#   (1.8.1), so a filter authored or validated on the host is the one that can
#   fail in Loop 2, not the reverse. What bounds that today is the tracked
#   callers — scripts/ci/pr_readiness_labels.sh and its suite use field access
#   and type checks, which 1.7 and 1.8 evaluate identically. It is NOT bounded
#   for an ad-hoc filter a model writes mid-run, and that is the honest limit of
#   this decision rather than an argument that the risk is zero. If it ever
#   bites, pin both sides through a controlled package source; fetching a loose
#   static binary is not the way, per the paragraph above.
#
# Installed as root; the base image's CMD/loop still runs as the non-root
# `claude` user (restored below).
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        jq \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install the gate tools + archi deps globally (as root) so they are on PATH for
# the non-login shell the gate runs in. `pip install --upgrade pip` first so the
# newest wheel index is used (fewer source builds).
COPY requirements/requirements-base.txt /tmp/requirements-base.txt
# duckdb==0.8.1 has no cp312 wheel and its C++ source won't build here; nothing in
# src/ or tests/ imports it (it only appears in generated deployment dockerfiles),
# so drop it from the in-container install. CI runs on Python 3.11, where the
# duckdb wheel exists, so CI keeps the full requirements unfiltered.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      black==24.10.0 \
      isort==6.0.1 \
      pytest \
      pytest-cov \
      diff-cover \
 && grep -ivE '^[[:space:]]*duckdb([=<>!~ ]|$)' /tmp/requirements-base.txt > /tmp/requirements-loop.txt \
 && pip install --no-cache-dir -r /tmp/requirements-loop.txt

# openspec CLI — pinned to the same version as the host (~/.npm-global/bin/openspec)
# so Loop 1 (propose/validate, runs on host) and Loop 2 (implement, runs here)
# cannot silently disagree on what passes --strict. Bump both together.
RUN npm install -g @fission-ai/openspec@1.4.1

ENV PYTHONPATH=/workspace
USER claude
# --- end archi toolchain ----------------------------------------------------
