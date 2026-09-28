# Consumer Makefile — build the loop image and drive the loop.
#
# The loop machinery lives in the base image (on PATH); this repo carries no
# scripts/ralph.sh. Status is the /ralph-status Claude Code skill.

IMAGE      ?= archi-loop
RUNTIME    ?= podman
BASE_IMAGE ?= ralph-base:v1
WORKSPACE  := $(CURDIR)
CLAUDE_DIR := $(WORKSPACE)/.ralph/claude-home

# The review gate (ON by default) runs `gh` in the RUNNER, inside the container, so
# it needs a GitHub token there. The host authenticates via the keyring (no token in
# ~/.config/gh), so we derive one with `gh auth token` and forward it as GH_TOKEN.
# `export` puts it in the recipe env; `-e GH_TOKEN` (no inline value) forwards it
# without exposing the token in `ps`/argv. A pre-set GH_TOKEN (e.g. CI) wins.
# Not needed when RALPH_REVIEW_GATE=0 (offline loop).
export GH_TOKEN ?= $(shell gh auth token 2>/dev/null)

# Claude auth for the container, two ways. PREFERRED: CLAUDE_CODE_OAUTH_TOKEN, a
# one-year subscription token from `claude setup-token`, kept in the unattended
# unit's EnvironmentFile and forwarded here exactly like GH_TOKEN (bare `-e NAME`:
# never inlined, not passed when unset). FALLBACK: the interactive `make login`
# credential in $(CLAUDE_DIR), which stopped refreshing after a few weeks twice
# (2026-08-24, 2026-09-27) and killed every nightly turn with a 401 until a human
# logged in again. `make auth-check` probes whichever is in effect.
#
# --userns=keep-id is PODMAN-SPECIFIC (host UID/GID mapping). If RUNTIME=docker,
# drop it or replace with `--user $$(id -u):$$(id -g)`.
RUN_FLAGS := \
  --userns=keep-id \
  -e RALPH_MODEL \
  -e RALPH_TASKS \
  -e GH_TOKEN \
  -e CLAUDE_CODE_OAUTH_TOKEN \
  -e GH_REPO=fasrc/archi \
  -v $(WORKSPACE):/workspace \
  -v $(CLAUDE_DIR):/home/claude/.claude

.PHONY: help hooks build check-base login auth-check loop loop-headless loop-once shell clean

help:
	@echo "Targets:"
	@echo "  hooks      install the pre-commit gate hook (git config core.hooksPath hooks)"
	@echo "  build      build $(IMAGE) FROM $(BASE_IMAGE) (build the base first)"
	@echo "  login      fallback: interactive Claude login into $(CLAUDE_DIR) (prefer CLAUDE_CODE_OAUTH_TOKEN from 'claude setup-token')"
	@echo "  auth-check probe that the container can authenticate to Claude (no TTY; exit 1 on a dead login)"
	@echo "  loop       run the Ralph Loop in the foreground (Ctrl-C to stop)"
	@echo "  loop-once  run exactly one turn"
	@echo "  shell      interactive shell in the container"
	@echo "  clean      remove $(IMAGE)"
	@echo "  (status:   use the /ralph-status skill in Claude Code)"

# Point git at the tracked hooks/ dir so the pre-commit gate is active in the
# loop container (which shares /workspace/.git). Idempotent — a prerequisite of
# build/loop/loop-once so it is never forgotten. NOTE: core.hooksPath overrides
# ALL of .git/hooks; if you have other hooks, consolidate them into hooks/.
hooks:
	@git config core.hooksPath hooks

build: hooks
	$(RUNTIME) build \
	  --build-arg USER_UID=$$(id -u) \
	  --build-arg USER_GID=$$(id -g) \
	  -t $(IMAGE) -f Containerfile .

# Refuse to run a loop image built on a SUPERSEDED base. $(IMAGE) inherits
# $(BASE_IMAGE)'s org.ralph.* LABELs; if the base-version OR the baked UID/GID
# differ from the base image now on the machine, the base was rebuilt without
# `make build` here — so the loop would run a stale runner or wrong-owner image.
# Detect + instruct only (rebuilding the BASE needs the plugin; that is
# /ralph-build-base's job). Skips silently if either image is unstamped (legacy)
# or the runtime is absent.
# NOTE: the `{{ index ... }}` below are Go/podman template literals, NOT ralph-init
# {{PLACEHOLDER}} tokens — do not substitute them.
check-base:
	@command -v $(RUNTIME) >/dev/null 2>&1 || exit 0; \
	  fmt='{{ index .Config.Labels "org.ralph.base-version" }}:{{ index .Config.Labels "org.ralph.user-uid" }}:{{ index .Config.Labels "org.ralph.user-gid" }}'; \
	  img=$$($(RUNTIME) image inspect $(IMAGE) --format "$$fmt" 2>/dev/null); \
	  base=$$($(RUNTIME) image inspect $(BASE_IMAGE) --format "$$fmt" 2>/dev/null); \
	  if [ -n "$${img%%:*}" ] && [ -n "$${base%%:*}" ] && [ "$$img" != "$$base" ]; then \
	    echo "ERROR: $(IMAGE) was built on a stale/mismatched $(BASE_IMAGE) ($$img != $$base)."; \
	    echo "       Run 'make build' to rebuild it on the current base before looping."; \
	    exit 1; \
	  fi

login:
	@mkdir -p $(CLAUDE_DIR)
	$(RUNTIME) run --rm -it $(RUN_FLAGS) --name $(IMAGE)-login $(IMAGE) claude login

# Pre-run auth probe, fail-closed and TTY-free: one minimal `--print` call proves
# the container can authenticate with whatever RUN_FLAGS carries (the forwarded
# CLAUDE_CODE_OAUTH_TOKEN, else the `make login` credential in $(CLAUDE_DIR)). A
# dead login exits non-zero here, so a systemd ExecStartPre that runs this stops
# the unit BEFORE a nightly drain spends its slot on 401s. Deliberately NOT
# `--bare`: bare mode skips the CLAUDE_CODE_OAUTH_TOKEN auth path and answers
# "Not logged in · Please run /login" even with a valid token (measured 2026-09-28
# on the image's claude 2.1.173 and the host's 2.1.283), which is exactly the
# message an expired login prints — a probe that used it reported a healthy token
# as dead. Only `-p` is used; it is in 2.1.173 (`claude --help`), while
# `--max-turns` is NOT — an unknown flag would refuse every night as a usage
# error, so do not add flags without checking that help first.
# Bounded: a hung pull, container start or API call must not hold the unit for
# its whole TimeoutStartSec (5h), so `timeout` kills the client after
# AUTH_CHECK_TIMEOUT seconds and the named container is removed if it lingers.
AUTH_CHECK_TIMEOUT ?= 120
auth-check: check-base
	@mkdir -p $(CLAUDE_DIR)
	@if out=$$(timeout -k 10 $(AUTH_CHECK_TIMEOUT) \
	    $(RUNTIME) run --rm $(RUN_FLAGS) --name $(IMAGE)-auth-check $(IMAGE) \
	    claude -p "Reply with exactly: OK" 2>&1); then \
	  echo "auth-check: the loop container can authenticate to Claude."; \
	else \
	  rc=$$?; $(RUNTIME) rm -f $(IMAGE)-auth-check >/dev/null 2>&1 || true; \
	  printf '%s\n' "$$out" | tail -n 5 | sed 's/^/auth-check: claude said: /' >&2; \
	  if [ "$$rc" -eq 124 ] || [ "$$rc" -eq 137 ]; then \
	    echo "auth-check: the probe timed out after $(AUTH_CHECK_TIMEOUT)s (container start, pull, or API hang)." >&2; \
	  fi; \
	  echo "auth-check: the loop container CANNOT authenticate to Claude (expired 'make login'" >&2; \
	  echo "            credential, or no CLAUDE_CODE_OAUTH_TOKEN). Fix: run 'claude setup-token' and" >&2; \
	  echo "            put CLAUDE_CODE_OAUTH_TOKEN in the unit's EnvironmentFile, or 'make login'." >&2; \
	  exit 1; \
	fi

loop: hooks check-base
	@mkdir -p $(CLAUDE_DIR)
	$(RUNTIME) run --rm -it $(RUN_FLAGS) --name $(IMAGE) $(IMAGE) ralph.sh

# Headless loop for unattended/cron runs (no -it): same as `loop` without a TTY,
# so it works when launched by cron or a scheduled agent with no terminal.
# Used by the archi-nightly skill. Ctrl-C still stops a foreground invocation.
loop-headless: hooks check-base
	@mkdir -p $(CLAUDE_DIR)
	$(RUNTIME) run --rm $(RUN_FLAGS) --name $(IMAGE)-headless $(IMAGE) ralph.sh

loop-once: hooks check-base
	@mkdir -p $(CLAUDE_DIR)
	$(RUNTIME) run --rm -it $(RUN_FLAGS) --name $(IMAGE)-once $(IMAGE) ralph.sh --once

shell:
	@mkdir -p $(CLAUDE_DIR)
	$(RUNTIME) run --rm -it $(RUN_FLAGS) --name $(IMAGE)-shell $(IMAGE)

clean:
	$(RUNTIME) rmi $(IMAGE) || true
