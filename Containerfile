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

# Build toolchain (root): requirements-base includes packages with no 3.12 wheels
# that compile from source — hnswlib (C++), duckdb, lz4 — so they need a compiler
# and the Python dev headers. Installed as root; the base image's CMD/loop still
# runs as the non-root `claude` user (restored below).
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
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
 && grep -ivE '^[[:space:]]*duckdb([=<>!~ ]|$)' /tmp/requirements-base.txt > /tmp/requirements-loop.txt \
 && pip install --no-cache-dir -r /tmp/requirements-loop.txt

ENV PYTHONPATH=/workspace
USER claude
# --- end archi toolchain ----------------------------------------------------
