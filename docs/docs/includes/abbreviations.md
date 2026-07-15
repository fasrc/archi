<!--
Shared abbreviation definitions, auto-appended to EVERY docs page by
pymdownx.snippets (see docs/mkdocs.yml). Each `*[TERM]: definition` line makes the
`abbr` extension render every whole-word occurrence of TERM as an <abbr> hover tooltip.

Keep this to acronyms and unambiguous short tokens only. Multi-word concept phrases
("the gate", "gold source") are handled as glossary links, not tooltips, because abbr
matches literally and would over- or under-match a phrase. Do NOT add ambiguous tokens
(e.g. "KB" also means kilobytes; "QA" is overloaded). Definitions are one plain line.
Fuller definitions live in glossary.md.
-->
*[RAG]: Retrieval-Augmented Generation — search a document collection, then have a language model write the answer from what it found.
*[LLM]: Large Language Model — the AI that writes answers (e.g. GPT, Claude, a local model).
*[MRR]: Mean Reciprocal Rank — how high up the correct source lands in the results, averaged across questions.
*[BM25]: A classic keyword-relevance score — ranks a passage higher when it contains the question's (rarer) words.
*[HNSW]: The approximate-nearest-neighbor index type archi uses to find similar embeddings quickly.
*[FASRC]: Harvard's Faculty of Arts and Sciences Research Computing group — archi's deployment target.
*[FASSE]: FAS Secure Environment — a security-hardened FASRC cluster for sensitive data.
*[OOD]: Open OnDemand — a web portal for using FASRC clusters from a browser.
*[RAGAS]: The library archi uses to score answer quality with a language-model judge.
*[SUT]: System Under Test — the exact archi configuration being benchmarked.
*[TDD]: Test-Driven Development — write a failing test first, then the code to pass it.
*[ADR]: Architecture Decision Record — a short doc capturing one hard-to-reverse decision and why.
*[BYOK]: Bring Your Own Key — users supply their own language-model API keys.
*[vLLM]: The high-performance server hosting the chat model on GPUs, separate from archi.
