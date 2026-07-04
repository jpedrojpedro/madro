# MADRO — Multi-Agent Data Retrieval Orchestrator

MADRO is a multimodal retrieval system that orchestrates specialised agents to answer user demands by fetching, indexing, and ranking text and image data from heterogeneous sources. It is implemented as a Django application backed by PostgreSQL (used as message broker, vector store, and relational database).

---

## Architecture

The system is organised into four operational stages:

```
User Prompt
    │
    ▼
┌─────────────────────────────┐
│  1. Thread / Decomposition  │  InteractiveAgent → DemandCategorizationAgent → Publisher
└─────────────────────────────┘
                │  JobExecution rows (one per agent × topic match)
                ▼
┌─────────────────────────────┐
│  2. AgentRunner             │  invoke() per job → normalize() → persist artifact
└─────────────────────────────┘
                │  broker.job_artifact + job_artifact_document
                ▼
┌─────────────────────────────┐
│  3. Relevance Ranking       │  RelevanceRanker → S_relevance
└─────────────────────────────┘
                │  ranked RankedEntity list
                ▼
┌─────────────────────────────┐
│  4. Response Synthesis      │  LLM summarises ranked evidence → final answer
└─────────────────────────────┘
```

### Modality flows

**Text agents** (`modality=text`): return a JSON payload that is chunked, embedded with `nomic-ai/nomic-embed-text-v1.5`, and stored as `canonical_text` + `tsvector` + chunk embeddings.

**Image agents** (`modality=image`): return image records with raw base64 data. At invoke time, `EnrichmentAgent` runs each image through `Qwen/Qwen2.5-VL-3B-Instruct` twice, producing:
- `img_caption` — a visual scene description
- `ocr_text` — extracted on-image text (markdown-preserving)

These text fields are merged into the record's `text_content` and normalised through the same `MultimodalNormalizer` pipeline as text agents — there's no separate visual embedding model, so text and image artifacts share one embedding space and are scored identically.

### Scoring

```
S_relevance(e) = α · S_lex(e) + β · S_sem(e)
```

`S_lex` comes from Postgres `ts_rank` over the artifact's lexical vector; `S_sem` is cosine similarity between the query embedding and the artifact's best-matching chunk embedding. Weights (`alpha`, `beta`) are configured in `configs/default.yaml`.

---

## Project Structure

```
madro/
├── configs/
│   └── default.yaml               # Model names, ranking weights
├── scripts/
│   ├── smoke_test.py              # End-to-end pipeline test (2 hardcoded prompts)
│   └── db_schema.sql              # PostgreSQL schema
├── tests/
│   └── benchmark/                 # Allure-reported live benchmark (see below)
│       ├── questions.json         # Benchmark prompts + complexity tier
│       ├── conftest.py            # django.setup() before collection
│       └── test_benchmark.py
├── src/madro/
│   ├── retrieval_agents/          # Local retrieval agent implementations
│   │   ├── base.py                # RetrievalAgent ABC
│   │   ├── profile_fetcher_agent.py
│   │   ├── publication_fetcher_agent.py
│   │   ├── semantic_opinion_fetcher.py
│   │   └── image_fetcher_agent.py
│   ├── internal_agents/           # LLM-backed agents (pydantic_ai)
│   │   ├── interactive_agent.py         # Prompt enrichment
│   │   ├── demand_categorization_agent.py  # Demand → sub-demands
│   │   ├── topic_categorization_agent.py   # Agent → topic assignment
│   │   ├── enrichment_agent.py     # Qwen2.5-VL image → img_caption + ocr_text
│   │   └── system_prompts.py
│   ├── broker/
│   │   ├── publisher.py           # Sub-demands → JobExecution rows
│   │   └── agent_runner.py        # invoke() + persist artifacts to broker schema
│   ├── aggregation/                # Ranking & response synthesis (non-LLM math + LLM)
│   │   ├── entities.py             # RankedEntity
│   │   ├── entity_resolver.py      # Joins artifacts into entities by shared key
│   │   ├── relevance_ranker.py     # Lexical + semantic scoring → ranked entities
│   │   └── response_synthesis.py   # LLM summarisation of ranked evidence
│   ├── workflows/
│   │   ├── thread_workflow.py     # Entry point: enrich → decompose → publish
│   │   └── normalizer.py          # MultimodalNormalizer: markdown synthesis, chunk + embed
│   ├── seed_agents.py              # Idempotent RetrievalAgent catalog bootstrap
│   ├── models.py                  # Django ORM models (unmanaged, mapped to schema)
│   ├── data_wrappers.py           # Pydantic I/O models
│   ├── config.py                  # AppConfig (Pydantic), LLM client factory
│   ├── db.py                      # async_cursor() context manager (psycopg3)
│   └── settings.py                # Django settings
├── docker-compose.yml             # madro_db (pgvector/pg17)
├── Makefile
└── pyproject.toml
```

---

## Database Schema

Three PostgreSQL schemas:

- `agents_topics` — agent registry, topic catalogue, agent↔topic assignments
- `flow_control` — conversation threads and messages
- `broker` — job execution queue, job status, artifacts, and chunk embeddings

The `broker.job_artifact_document` table uses `pgvector` with an HNSW index on 768-dimensional embeddings.

---

## Requirements

- Python 3.13+
- [Poetry](https://python-poetry.org/)
- Docker + Docker Compose
- A Google AI Studio API key (Gemini, for LLM agents and response synthesis)
- A Hugging Face token (`HF_TOKEN`) able to download `Qwen/Qwen2.5-VL-3B-Instruct`
- A `RETRIEVAL_DB_URL` pointing to the source data PostgreSQL instance

---

## Running Locally

### 1. Clone and install dependencies

```bash
git clone <repo>
cd madro
poetry install
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill it in:

```env
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
DATABASE_URL=postgresql://madro:madro@localhost:5432/madro
RETRIEVAL_DB_URL=postgresql://<user>:<password>@<host>:<port>/<db>
GOOGLE_API_KEY=<your-google-ai-studio-key>
HF_TOKEN=<your-huggingface-token>
```

### 3. Start the database

```bash
make db
```

### 4. Apply the schema

```bash
docker compose exec madro_db psql -U madro -d madro -f /dev/stdin < scripts/db_schema.sql
```

### 5. Run the server

```bash
make start
```

### 6. Run the smoke test

Exercises the full pipeline end-to-end against real data:

```bash
poetry run python scripts/smoke_test.py
```

The smoke test runs five steps:

| Step | What it does                                                                            |
|------|------------------------------------------------------------------------------------------|
| 0 | Seeds agents (`src/madro/seed_agents.py`) and assigns topics via LLM categorisation          |
| 1 | Submits task prompts, enriches and decomposes demands, publishes jobs                    |
| 2 | Inspects published `JobExecution` rows                                                   |
| 3 | Invokes each agent, runs `EnrichmentAgent` (Qwen2.5-VL) on images, normalizes and persists artifacts |
| 4 | Runs relevance ranking (`RelevanceRanker`) across all completed artifacts                 |
| 5 | Synthesises a natural-language response via LLM (`ResponseSynthesisAgent`)                |

---

## Benchmark (Allure Report)

`tests/benchmark/` runs the same pipeline as the smoke test over a larger, fixed question set (`questions.json`, tagged with a `complexity` tier) and reports each run through [Allure](https://allurereport.org/), so results can be compared across runs instead of just eyeballed in stdout.

```bash
make benchmark
```

This hits the real databases directly (`-p no:django` disables pytest-django's test-database machinery, since the schema is unmanaged and `RETRIEVAL_DB_URL` is real scraped data that can't be recreated by migrations anyway) and writes raw results to `allure-results/`. It's excluded from the default `make test` run.

To view the report locally:

```bash
allure serve allure-results   # requires the Allure commandline (e.g. `brew install allure`)
```

Results are grouped by `complexity` in Allure's Behaviors tab (via `allure.dynamic.feature`) and each question attaches sub-demands, published jobs, per-job artifact summaries, ranked entities, and the final synthesized answer as evidence.

To share a rendered report externally (e.g. with an advisor) without exposing the private source repo, the generated static site is published to a separate public repo (`madro-results`) via GitHub Actions + GitHub Pages, gated behind a simple passcode page — see `allure-gate/` and `.github/workflows/publish-benchmark-report.yml` in that repo.

---

## Other Commands

```bash
make test       # run pytest (excludes the live benchmark suite)
make lint       # ruff check
make migrate    # django migrations
make benchmark  # live end-to-end benchmark, reported via Allure
```

---

## Configuration

`configs/default.yaml` controls model and ranking parameters:

```yaml
model:
  name: gemini-3.1-flash-lite
  temperature: 0
  max_tokens: 65535

image_model:
  name: Qwen/Qwen2.5-VL-3B-Instruct
  temperature: 0
  max_tokens: 4096

fusion:
  alpha: 0.3   # lexical weight
  beta: 0.7    # semantic weight
```

---

## Adding a New Agent

1. Create `src/madro/retrieval_agents/<name>.py` extending `RetrievalAgent`, implementing `run()` returning a JSON string.
2. Register it in `src/madro/seed_agents.py`'s `SEED_AGENTS` list (used by both `scripts/smoke_test.py` and `tests/benchmark/`) or via `POST /agent`, specifying `uri`, `mcp_schema`, `candidate_topics`, and `modality` (`text` or `image`).
3. The topic categorisation agent will automatically assign it to existing or new topics.