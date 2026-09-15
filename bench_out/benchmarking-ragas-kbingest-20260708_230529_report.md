# Benchmark Results Comparison

**Configuration:** configs/config.yaml  
**Timestamp:** 2026-07-08 23:05:29.870301+00:00  
**Questions Processed:** 0

## Run provenance

⚠️ Whether the run used the selected configuration was **not recorded**: this artifact predates configuration provenance, so no comparison was made.

⚠️ Corpus stability is **unknown**: it was not observed both before and after the run (` None ` → ` None `).

⏱️ Time to ingest is **not recorded**: this artifact predates the field.

- Code version: *not recorded — this artifact predates version stamping*
- Deploy-time commit: ` b6d9a87cb7420d243ea81901641a5297e6707270 ` — frozen by `archi create`; it identifies the deploy, not the image this run used
- Config version: ` sha256:2e158e4ffbbbd6cd1c5f25ce14746d44246427ec11f0e061c8a2c4f00dceb13e `
- Config basis: reconstructed from the configuration file recorded in this artifact; the configuration the agent read was never captured, so this may not describe the run

Settings that define this arm:

| Setting | Value |
|---|---|
| data\_manager.chunk\_overlap | 0 |
| data\_manager.chunk\_size | 1000 |
| data\_manager.chunking | {"strategy": "sentence"} |
| data\_manager.distance\_metric | cosine |
| data\_manager.embedding\_name | HuggingFaceEmbeddings |
| data\_manager.retrievers | {"bm25\_retriever": {"num\_documents\_to\_retrieve": 5}, "hierarchical\_rerank": {"candidate\_pool\_size": 20, "enabled": true, "num\_documents\_to\_retrieve": 5, "reranker": {"model": "ms-marco-MiniLM-L-12-v2"}}, "hybrid\_retriever": {"bm25\_weight": 0.6, "num\_documents\_to\_retrieve": 5, "semantic\_weight": 0.4}, "semantic\_retriever": {"num\_documents\_to\_retrieve": 5}} |
| data\_manager.stemming | {"enabled": false} |
| services.benchmarking.agent\_class | FASRCDocsAgent |
| services.benchmarking.agent\_md\_file | /root/archi/agents/fasrc-inline-v1.md |
| services.benchmarking.mode\_settings | {"ragas\_settings": {"batch\_size": false, "embedding\_model": "HuggingFace", "enabled\_metrics": \["answer\_relevancy", "faithfulness", "context\_precision", "context\_recall"\], "evaluator\_model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "evaluator\_ollama\_url": null, "evaluator\_provider": "huit\_bedrock", "timeout": 180}, "sources\_settings": {"default\_match\_field": "url"}} |
| services.benchmarking.model | palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4 |
| services.benchmarking.modes | \["RAGAS", "SOURCES"\] |
| services.benchmarking.provider | openai |
| services.chat\_app.agent\_class | CMSCompOpsAgent |
| services.chat\_app.default\_model | llama3.2 |
| services.chat\_app.default\_provider | local |
| services.chat\_app.recursion\_limit | 50 |
| services.vectorstore.backend | postgres |
| services.vectorstore.distance\_metric | cosine |

## 📊 Aggregate RAGAS Metrics

| Metric | Score |
|---|---|
| Answer Relevancy | n/a (unscored) |
| Faithfulness | n/a (unscored) |
| Context Precision | n/a (unscored) |
| Context Recall | n/a (unscored) |
