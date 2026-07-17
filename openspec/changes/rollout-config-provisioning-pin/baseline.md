# Pre-rollout baseline (captured 2026-07-17)

Corpus counts to verify against after the redeploy (task 5.3).

## Corpus (postgres-dev, documents table)

| source_type | status   | count |
|-------------|----------|-------|
| web         | embedded | 546   |
| web         | failed   | 3     |
| git         | embedded | 724   |
| git         | failed   | 13    |

- Web total: 549 (546 + 3)
- Git failures by suffix: `.ipynb` × 12, `.md` × 1 (the known deferred set — Issue #109)
- `document_parent_nodes`: 17767 (LlamaIndex hierarchical parent count)

**Pass criterion:** post-rollout git embedded ≥ 724 and git failed ≤ 13; web embedded
≈ 546 (±small, re-crawl drift tolerated). A DROP in git embedded = rollout failure.

## Source manifest (config/lists/sources.list, working tree)

- git sources: **2** — `git-https://github.com/fasrc/User_Codes`,
  `git-https://github.com/OSC/ood-documentation/tree/release-4.1`
- KB pages (docs.rc.fas.harvard.edu): 219
- total non-comment, non-blank lines: 370

Pin `990c54c7` `lists/sources.list`: **0 git sources** — confirms the stale-pin trap
(deploying it would drop all 724 git documents).

## Host wiring (deploy/fasrc-dev/config.yaml)

- `input_lists: [config/lists/sources.list]` — already repointed (dead-path fix from #111 in place)
- `agents_dir: deploy/fasrc-dev/agents` — this host stages `fasrc-archi-v12.md` +
  `fasrc-inline-v1.md`; it does NOT consume `config/agents/`. Rendered/live agents dir
  matches. => agent-spec commits in group 2 are repo-fidelity only, no behavior change here.
