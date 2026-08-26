# Glossary

Plain-language definitions of the terms and shorthand you'll meet around archi. **No
background in retrieval systems, machine learning, statistics, or this project's workflow is
assumed** — every entry is written to be understood cold.

How to read it:

- The first three sections (**Product & domain**, **Retrieval & ranking**, **Benchmarking &
  evaluation**) are for anyone — a stakeholder, an operator, or a new engineer.
- The last two (**Development workflow**, **Infrastructure & deployment**) are day-to-day
  jargon for people *building and running* archi. A non-engineer can stop after Benchmarking.
- Acronyms are also defined as hover tooltips wherever they appear across these docs — hover a
  dotted term like RAG to see its meaning without leaving the page.

!!! note "Three things named almost the same — don't mix them up"
    - **archi** is the product. **a2rchi** is the username and file path on the deployment
      host (a historical spelling). **Cannon** is a compute cluster — nothing to do with
      "canonical".
    - **category**, **llm_category**, and the **Echo-KB breadcrumb** are three *different*
      notions of "category". See each entry below.

---

## Product & domain

### archi
The product this whole repository builds: **A**I **A**ugmented **R**esearch **C**hat
**I**ntelligence — a framework for building chat assistants that answer from a curated set of
documents, for research and education groups. Developed at MIT; the FASRC fork is the active
line of work.

### RAG
**R**etrieval-**A**ugmented **G**eneration — the core idea behind archi. Instead of trusting a
language model to know an answer, you first *search* a document collection for relevant
passages, then hand those passages to the model and ask it to write the answer from them. This
keeps answers grounded in real sources.

### retrieval
The "search" half of RAG: finding the pieces of the document collection whose meaning is
closest to the user's question, so the model has the right material to answer from.

### ingest / ingestion
The one-time process of loading source documents into archi: downloading them, splitting them
into chunks, turning each chunk into an embedding, and storing it. For the FASRC document set
this takes roughly 50 minutes.

### chunk
A small slice of a document (a few paragraphs), not the whole page. Retrieval works on chunks,
because a focused passage matches a question better than an entire article does.

### corpus
The full collection of documents archi has ingested and can search. For the FASRC deployment
the corpus is the FASRC Knowledge Base plus the Slurm scheduler documentation.

### embedding
A list of numbers that captures the *meaning* of a piece of text, arranged so that texts about
similar things have similar numbers. Comparing embeddings is how archi finds passages "about
the same thing" as a question, even when they share no exact words.

### vectorstore
The database that holds every chunk's embedding and finds the closest ones to a question.
archi's is PostgreSQL with the `pgvector` extension. (`VectorStore` is also the name of the
archi component that wraps it.)

### pipeline
A configured recipe for answering, combining retrieval and a language model in a particular
way. archi ships several (question-answering, grading, agent); a deployment picks which one it
runs.

### orchestrator
The central archi component (`archi.py`) that wires a pipeline to a vectorstore and is the
entry point for answering a query. Think of it as the conductor.

### provider
A pluggable connection to a language-model backend — OpenAI, Anthropic, Gemini, a local
server, etc. Models are named `"provider/model"` (e.g. `openai/gpt-4o`), so a deployment can
swap backends by changing a string.

### KB / Knowledge Base
The set of documents an archi assistant answers from. For FASRC this is their user
documentation site (the "FASRC KB").

### FASRC
Harvard's **F**aculty of **A**rts and **S**ciences **R**esearch **C**omputing group — the
organization archi is deployed for here. The active fork lives at `fasrc/archi`.

### Cannon
FASRC's main large compute cluster. It's a frequent *topic* of user questions, so the assistant
needs good documentation about it — unrelated to the word "canonical".

### FASSE
A separate, security-hardened FASRC cluster (**F**AS **S**ecure **E**nvironment) for sensitive
data. It's also one of the FASRC KB's category labels.

### Slurm
The job scheduler that decides which work runs on which machines on FASRC's clusters. Its
official documentation (`slurm.schedmd.com`) is part of archi's corpus, so the assistant can
answer scheduling questions.

---

## Retrieval & ranking

### pgvector
A PostgreSQL extension that stores embeddings and finds the nearest ones inside the database,
so archi doesn't need a separate vector database.

### hybrid search
Blending two ways of ranking results — meaning-based (embeddings) and keyword-based (BM25) —
into one score, so a passage can win either by being *about* the right thing or by containing
the right words. Both components are min-max normalized to 0–1 before weighting, so neither
raw scale dominates. archi weights these by default.

### BM25
A long-standing keyword-relevance formula: it scores a passage higher when it contains the
question's words, especially rarer ones. It's the keyword half of hybrid search.

### semantic search
Finding text by *meaning* rather than exact words, by comparing embeddings. The opposite pole
from keyword (BM25) search.

### cross-encoder / reranker
A slower but sharper model that re-reads the top handful of candidate passages *together with*
the question and re-scores them for relevance. archi uses one called **FlashRank**. It runs
after the fast first-pass search to reorder the shortlist.

### FlashRank
The specific reranker archi uses — a compact cross-encoder that runs on CPU. See
**cross-encoder / reranker**.

### hierarchical retrieval
A retrieval strategy that matches on small, precise "child" chunks but then returns their
larger "parent" section for context — so the model gets a focused hit *and* enough surrounding
text to answer well. This is the retriever the FASRC deployment runs.

### parent / child chunks
Two sizes of the same content used by hierarchical retrieval: small **child** chunks are what
get matched against the question; the bigger **parent** section they belong to is what gets
handed to the model.

### rerank score / hybrid score
The number a retriever attaches to a passage to rank it. The *hybrid score* comes from blending
semantic and BM25; the *rerank score* is the cross-encoder's later, sharper judgment. Higher
means more relevant.

### category
A subject label that comes *from the source itself* (for example, a tag the source website
already publishes). Distinct from `llm_category` below. On the FASRC KB it's the 19-item
website taxonomy read from the page breadcrumb.

### llm_category
A subject label assigned *by a language model* at ingest time, chosen from a fixed short list —
as opposed to `category`, which comes from the source. The two are stored separately and never
overwrite each other.

### Echo-KB breadcrumb
FASRC's documentation runs on the Echo Knowledge Base WordPress plugin ("Echo-KB" / "EPKB").
Each article page shows a breadcrumb trail (Home › Category › Article); archi reads that trail
to capture the article's website **category**.

### soft boost / category boost
A proposed (and, after evaluation, shelved) ranking tweak: nudge a passage's score up when its
category matches the question's category. "Soft" because it only nudges rank rather than
filtering anything out.

### oracle mode
An evaluation trick: feed the system the *perfect* answer to some sub-decision to measure the
best case a feature could ever achieve. If even the perfect version doesn't help, the real
version won't either — so you can kill an idea cheaply.

### gold source
The known-correct source page for a benchmark question — the answer key. Retrieval is scored by
whether it surfaces the gold source. (Also called the reference source.)

### hit@k / hit-rate@k
The fraction of benchmark questions for which the gold source appears somewhere in the top *k*
retrieved results. "hit@5" asks: did the right page make the top five?

### MRR
**M**ean **R**eciprocal **R**ank — a single number for how *high up* the gold source lands,
averaged over all questions. Rank 1 scores 1.0, rank 2 scores 0.5, rank 3 scores 0.33, and so
on; higher is better.

---

## Benchmarking & evaluation

A deeper, benchmarking-specific glossary lives in
[Interpreting Results §7](interpreting_benchmark_results.md#7-glossary); the entries here are
the ones you'll meet most often.

### RAGAS
The evaluation library archi uses to score answer *quality* — how relevant, faithful, and
well-grounded an answer is — using a language model as the grader. Run via `archi evaluate`.

### judge
The separate language model that grades the assistant's answers during a benchmark (archi pins
a specific model so scores stay comparable run to run).

### SUT
**S**ystem **U**nder **T**est — the exact archi configuration being benchmarked in a given
round.

### arm
One competing variant of the system in a benchmark comparison — the word is borrowed from
clinical trials, where each treatment group is an "arm". Every arm answers the same question
bank under otherwise identical conditions (same corpus, same judge), so any score difference
can be pinned on the one thing that varies — for example, the current code as a *baseline*
arm against a proposed fix as a *treatment* arm.

### anchor / anchor questions
A small set of fixed benchmark questions, never edited and never tuned against, run every round
to catch regressions. If an anchor's score moves, something changed.

### bank / question bank
The file of benchmark questions, each with a reference answer and its gold source(s). The
evaluation runs the assistant over the bank and scores the results.

### noise floor
How much a score wobbles when you re-run the identical benchmark and change nothing. A
difference smaller than the noise floor isn't real — it's just run-to-run jitter.

### pre-registration
Writing down the hypothesis, the metric that decides it, and the pass/fail rule *before*
running an evaluation — so you can't rationalize the result afterward.

### ceiling / floor effect
When scores are already so high (or so low) that a genuine improvement can't show up in the
number, because there's no room left to move. A benchmark pinned at the ceiling can't detect
progress.

---

## Development workflow

*Engineer-facing.* These are terms for people changing archi's code. A stakeholder or operator
can skip this section.

### the gate
The single quality check every change must pass before it's committed: formatting, linting,
and tests (`scripts/gate.sh`). If the gate is red, the change doesn't land. "Passing the gate"
is the bar for done.

### diff coverage
A rule the gate enforces: at least 80% of the *lines your change touched* must be covered by
tests — measured on the change itself, not the whole codebase. It stops new code from arriving
untested without demanding the entire project be covered first.

### TDD (red / green)
**T**est-**D**riven **D**evelopment: write a failing test first (**red**), write the least code
to make it pass (**green**), then clean up. The discipline is never writing implementation
before a failing test exists.

### OpenSpec
The spec-driven workflow archi uses to plan changes before coding them. Each change gets a
folder under `openspec/changes/<name>/` holding its written artifacts (below).

### change / proposal / design / tasks / archive
The pieces of one OpenSpec unit of work: the **change** is the folder; **proposal.md** says
*what and why*, **design.md** says *how*, **tasks.md** is the checklist; when it's done and
merged the change is moved to **archive/**.

### Loop 1 / Loop 2
The two halves of the workflow: **Loop 1** is planning and specification (write the OpenSpec
artifacts); **Loop 2** is implementation, test-first. Plan, then build.

### Ralph loop / nightly automation
An unattended agent loop that works through opted-in GitHub issues overnight, turning them into
pull requests without a human driving each step. It stops at the PR — it never merges on its
own.

---

## Infrastructure & deployment

*Engineer/operator-facing.* Terms for running and updating a live archi deployment.

### dev deploy
The local `dev` deployment of archi, backed by the FASRC model server, managed by the scripts
in `deploy/scripts/`. Where changes are tried before anything goes further.

### redeploy
Rebuilding and restarting a deployment so code or config edits take effect. archi's deployment
container installs the code in a non-editable way, so edits are invisible until you redeploy —
copying files into a running container does *nothing*.

### re-ingest
Re-running ingestion so the search index reflects new or changed source documents. Needed after
anything that changes how documents are chunked, embedded, or captured.

### nuke
The destructive full teardown of the `dev` deployment — containers, stored data (database *and*
ingested corpus), and images. Irreversible; used when a plain re-ingest isn't enough and you
need a clean slate.

### vLLM
The high-performance server that hosts the actual chat language model on FASRC's GPUs,
separately from archi. archi talks to it over an OpenAI-compatible interface.

### deploy/fasrc-dev
The folder holding the FASRC `dev` deployment's own config and agent prompts. `dev` is the
GPU host. The real config and secrets are deliberately kept out of version control
(git-ignored); a sanitized example config *is* tracked and reviewed like any other code.

### deploy/scripts
The management scripts that stand a deployment up, restart it, check it, and tear it down.
They are host-neutral: every host runs the same scripts, and each host says which deployment
it is through a small `host.env` file (git-ignored) that the scripts read. The scripts are
tracked and reviewed like any other code.

### archi-config
A separate, private repository holding the operational configuration a deployment needs —
which websites to ingest, environment settings, the assistant's instructions. Kept apart from
the public archi code so internal details never appear in the open-source repo. Deploy scripts
fetch it automatically (see [config pin](#config-pin)).

### config pin
The exact, named version of the [archi-config](#archi-config) repository that deployments
install — like ordering a specific edition of a book rather than "whatever's newest". Every
deploy checks the version it got against a recorded fingerprint and refuses to proceed if
someone has quietly swapped what the name points to.
