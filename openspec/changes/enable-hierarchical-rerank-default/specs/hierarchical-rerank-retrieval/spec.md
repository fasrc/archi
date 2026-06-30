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
