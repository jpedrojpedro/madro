# MADRO — Multi-Agent Data Retrieval Orchestrator

MADRO is a multimodal retrieval system that orchestrates specialised agents to answer user demands by fetching, indexing, and fusing text and image data from heterogeneous sources. It is implemented as a Django application backed by PostgreSQL (used as message broker, vector store, and relational database).

---

## Architecture

The system is organised into five operational stages:

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
│  2. AgentRunner             │  invoke() per job → normalise() → persist artifact
└─────────────────────────────┘
                │  broker.job_artifact + job_artifact_document
                ▼
┌─────────────────────────────┐
│  3. Fusion                  │  aggregate_text() + aggregate_image() → S_fusion
└─────────────────────────────┘
                │  ranked FusedEntity list
                ▼
┌─────────────────────────────┐
│  4. Response Synthesis      │  LLM summarises ranked evidence → final answer
└─────────────────────────────┘
```

### Modality flows

**Text agents** (`modality=text`): return a JSON payload that is chunked, embedded with `nomic-ai/nomic-embed-text-v1.5`, and stored as `canonical_text` + `tsvector` + chunk embeddings.

**Image agents** (`modality=image`): return image records with raw base64 data. At invoke time, each image is processed by two HuggingFace models before normalisation:
- `Salesforce/blip-image-captioning-base` — visual scene captioning
- `microsoft/trocr-base-printed` — OCR text extraction

The enriched records `{publication_id, caption, ocr_text}` are stored as `canonical_text`, enabling the same vector scoring pipeline as text artifacts.

### Scoring

```
S_text(e)   = α · S_lex  + β · S_sem       (text artifacts)
S_image(e)  = γ · S_sem  + δ · S_sem       (image artifacts, caption+OCR embeddings)
S_fusion(e) = w_t · S_text + w_i · S_image
```

Weights are configured in `configs/default.yaml`.

---

## Project Structure

```
madro/
├── configs/
│   └── default.yaml               # Model name, fusion weights
├── scripts/
│   └── smoke_test.py              # End-to-end pipeline test
│   └── db_schema.sql              # PostgreSQL schema
├── src/madro/
│   ├── retrieval_agents/          # Local retrieval agent implementations
│   │   ├── base.py                # RetrievalAgent ABC
│   │   ├── profile_fetcher_agent.py
│   │   ├── publication_fetcher_agent.py
│   │   ├── semantic_opinion_fetcher.py
│   │   └── image_fetcher_agent.py
│   ├── workflows/
│   │   ├── thread_workflow.py     # Entry point: enrich → decompose → publish
│   │   ├── interactive_agent.py   # Prompt enrichment (LLM)
│   │   ├── demand_categorization_agent.py  # Demand → sub-demands (LLM)
│   │   ├── topic_categorization_agent.py   # Agent → topic assignment (LLM)
│   │   ├── publisher.py           # Sub-demands → JobExecution rows
│   │   ├── retrieval_agent.py     # invoke(): transport + modality post-processing
│   │   ├── normalizer.py          # chunk + embed → NormalisedArtifact
│   │   ├── agent_runner.py        # Persist artifacts to broker schema
│   │   ├── system_prompts.py      # All LLM system prompts
│   │   └── aggregation/
│   │       ├── text_agent.py      # Lexical + semantic scoring for text artifacts
│   │       ├── image_agent.py     # BLIP + TrOCR inference; vector scoring for image artifacts
│   │       ├── fusion.py          # Cross-modality fusion → FusedEntity list
│   │       └── response_synthesis.py  # LLM summarisation of ranked evidence
│   ├── models.py                  # Django ORM models (unmanaged, mapped to schema)
│   ├── data_wrappers.py           # Pydantic I/O models
│   ├── config.py                  # AppConfig (Pydantic), LLM client factory
│   ├── db.py                      # async_cursor() context manager (psycopg3)
│   └── settings.py                # Django settings
├── tests/
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
- Azure OpenAI deployment (for LLM agents and response synthesis)
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

Create a `.env` file at the project root:

```env
DATABASE_URL=postgresql://madro:madro@localhost:5432/madro
RETRIEVAL_DB_URL=postgresql://<user>:<password>@<host>:<port>/<db>

AZURE_ENDPOINT=https://<your-resource>.openai.azure.com/
API_KEY=<your-azure-openai-key>
API_VERSION=2025-04-01-preview
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

| Step | What it does |
|------|-------------|
| 0 | Seeds agents and assigns topics via LLM categorisation |
| 1 | Submits task prompts, enriches and decomposes demands, publishes jobs |
| 2 | Inspects published `JobExecution` rows |
| 3 | Invokes each agent, runs image inference (BLIP + TrOCR) if needed, normalises and persists artifacts |
| 4 | Runs fusion ranking across text and image modalities |
| 5 | Synthesises a natural-language response via LLM |

---

## Other Commands

```bash
make test     # run pytest
make lint     # ruff check
make migrate  # django migrations
```

---

## Configuration

`configs/default.yaml` controls model and fusion parameters:

```yaml
model:
  name: gpt-4o
  temperature: 0
  max_tokens: 4096

fusion:
  text:
    alpha: 0.3   # lexical weight
    beta: 0.7    # semantic weight
  image:
    gamma: 0.5
    delta: 0.5
  modality_weights:
    w_t: 0.7     # text modality
    w_i: 0.3     # image modality
```

---

## Adding a New Agent

1. Create `src/madro/retrieval_agents/<name>.py` extending `RetrievalAgent`, implementing `run()` returning a JSON string.
2. Register it via the smoke test `SEED_AGENTS` list or `POST /agent`, specifying `uri`, `mcp_schema`, `candidate_topics`, and `modality` (`text` or `image`).
3. The topic categorisation agent will automatically assign it to existing or new topics.
