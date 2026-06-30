## ADDED Requirements

### Requirement: Hierarchical-rerank retrieval is enabled by default

A deployment configuration rendered from the CLI config template SHALL enable
hierarchical-rerank retrieval by default — i.e. when the deployment's source config does not
set `data_manager.retrievers.hierarchical_rerank.enabled`, the rendered config SHALL contain
`enabled: true`. Operators SHALL retain the ability to opt out by setting
`hierarchical_rerank.enabled: false` in their deployment config, which renders `false` and
falls back to the existing `HybridRetriever`.

#### Scenario: Default render enables the reranker

- **WHEN** a deployment config is rendered and the source config omits
  `data_manager.retrievers.hierarchical_rerank.enabled`
- **THEN** the rendered config sets `hierarchical_rerank.enabled: true`, so retrieval uses the
  hierarchical cross-encoder reranker

#### Scenario: Explicit opt-out is honored

- **WHEN** a deployment config sets `data_manager.retrievers.hierarchical_rerank.enabled: false`
- **THEN** the rendered config sets `enabled: false` and retrieval falls back to
  `HybridRetriever`

### Requirement: Default chunking strategy pairs with the default reranker

A deployment config rendered with hierarchical-rerank enabled by default SHALL also render a
hierarchical chunking strategy by default: when the source config omits
`data_manager.chunking.strategy`, the rendered config SHALL set `strategy: sentence` (not the
legacy `character` strategy). The hierarchical-rerank retriever only returns parent context
when ingestion has built parent/child nodes (a `sentence` or `markdown` strategy); a
`character` strategy produces flat chunks with no `parent_id`, so the reranker would pay its
cost without the parent-context benefit. Pairing the defaults keeps the out-of-the-box
configuration coherent with the benchmarked package.

#### Scenario: Default render uses a hierarchical chunking strategy

- **WHEN** a deployment config is rendered and the source config omits
  `data_manager.chunking.strategy`
- **THEN** the rendered config sets `chunking.strategy: sentence`, so ingestion builds the
  parent/child nodes the default-on reranker needs

#### Scenario: Default chunking and reranker are enabled together

- **WHEN** a deployment config is rendered with neither chunking nor retriever settings
- **THEN** both `chunking.strategy: sentence` and `hierarchical_rerank.enabled: true` are
  rendered, matching the ADR 0003 treatment configuration
