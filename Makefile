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

# --userns=keep-id is PODMAN-SPECIFIC (host UID/GID mapping). If RUNTIME=docker,
# drop it or replace with `--user $$(id -u):$$(id -g)`.
RUN_FLAGS := \
  --userns=keep-id \
  -e RALPH_MODEL \
  -e GH_TOKEN \
  -e GH_REPO=fasrc/archi \
  -v $(WORKSPACE):/workspace \
  -v $(CLAUDE_DIR):/home/claude/.claude

.PHONY: help hooks build check-base login loop loop-headless loop-once shell clean

help:
	@echo "Targets:"
	@echo "  hooks      install the pre-commit gate hook (git config core.hooksPath hooks)"
	@echo "  build      build $(IMAGE) FROM $(BASE_IMAGE) (build the base first)"
	@echo "  login      one-time: authenticate Claude Code"
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
