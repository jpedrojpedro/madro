# MADRO — Multi-Agent Data Retrieval Orchestrator

*Multi-Agent Retrieval over Heterogeneous Multimodal Data*

MADRO is a multi-agent architecture that answers natural-language requests over heterogeneous, multimodal data sources. A request is decomposed into sub-demands, routed to the specialised retrieval agents able to answer each one, and the text and image-derived evidence they return is ranked and synthesised into a single answer. It is implemented as a Django application backed by PostgreSQL, used as message broker, vector store, and relational database.

This repository is the reference implementation of the PhD thesis *MADRO: Multi-Agent Retrieval over Heterogeneous Multimodal Data* (Department of Informatics, PUC-Rio, 2026) and of the ICEIS 2026 paper listed under [Citation](#citation). The proof-of-concept instantiation — the *MADRO Social Media Search Tool* — runs over scraped Instagram data about restaurants, influencers, and brands.

---

## Release status

| Component | Status | License |
|---|---|---|
| Source code | ✅ Available | MIT |
| Benchmark questions (`tests/benchmark/questions.json`) | ✅ Available | CC BY-NC 4.0 |
| Thesis results — Chapter 6 (`results/thesis/`) | ✅ Available | CC BY-NC 4.0 |
| Benchmark corpus (Parquet) | 🚧 Work in progress — data wrangling underway | CC BY-NC 4.0 |
| Follow-up paper results | 🚧 On hold until publication | CC BY-NC 4.0 |

Until the corpus is released, the pipeline and benchmark can be read and inspected but not re-executed end to end — see [Running locally](#running-locally).

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

Each retrieval agent generates its own SQL on the fly over a scoped view of the source schema, so a new data source is added by registering an agent rather than changing the pipeline.

### Modality flows

**Text agents** (`modality=text`) return a JSON payload that is chunked, embedded with `nomic-ai/nomic-embed-text-v1.5`, and stored as `canonical_text` + `tsvector` + chunk embeddings.

**Image agents** (`modality=image`) return image records with raw base64 data. `EnrichmentAgent` runs each image through `qwen2.5vl:7b` (served locally via Ollama) twice, producing:

- `img_caption` — a visual scene description
- `ocr_text` — extracted on-image text (markdown-preserving)

These fields are merged into the record's `text_content` and normalised through the same `MultimodalNormalizer` pipeline as text agents. There is no separate visual embedding model, so text and image artifacts share one embedding space and are scored identically.

### Scoring

```
S_relevance(e) = α · S_lex(e) + β · S_sem(e)
```

`S_lex` is Postgres `ts_rank` over the entity's lexical vector; `S_sem` is cosine similarity between the sub-demand embedding and the entity's own embedding. Ranking is scoped per sub-demand: each sub-demand's top-k is unioned into the final result rather than re-ranked globally. Weights are set in `configs/default.yaml`.

### Database schemas

- `agents_topics` — agent registry, topic catalogue, agent↔topic assignments
- `flow_control` — conversation threads and messages
- `broker` — job execution queue, job status, artifacts, and chunk embeddings (`pgvector`, HNSW index on 768-dimensional embeddings)

---

## Benchmark

`tests/benchmark/questions.json` holds 50 questions across four complexity tiers — low (single-agent, textual), medium (multi-agent, textual), high (multi-modal, temporal), and challenging (`out-of-scope` in the file — questions outside what the agents were built for). Each question is answered three ways and reported through [Allure](https://allurereport.org/):

| Side | What it is |
|---|---|
| **Ground Truth** | A Gemini-generated SQL query over only the tables named in the question's `hint` — the reference set precision/recall are measured against |
| **Baseline** | A zero-shot SQL query over the full source schema, with no hints (Gemini, `qwen2.5-coder:7b`, `llama3.1:8b`) |
| **MADRO** | The full pipeline, labelled by its α/β weights and retrieval sampling depth |

Results are compared by P@k / R@k (k = 1, 5, 10) over entity identities (`profile_id`, `publication_id`, …).

### Thesis results

`results/thesis/` holds the scored comparison behind Chapter 6 of the thesis. The scoring code changed after the thesis defense, so those numbers are reproduced from the [`thesis-results`](https://github.com/jpedrojpedro/madro/tree/thesis-results) tag, not from `main` — see [`results/thesis/README.md`](results/thesis/README.md) for source runs and the exact command.

### Running the benchmark

Requires the corpus (see [Release status](#release-status)). Ground Truth runs first, since Baseline is steered by its identity choices:

```bash
make benchmark-ground-truth
make benchmark-baseline
make benchmark SAMPLE=25 FUSION_LEX=0.7 FUSION_SEM=0.3
make compare-baseline APPROACH=sample-25_alpha-0.7_beta-0.3
allure serve allure-results   # requires the Allure commandline, e.g. `brew install allure`
```

The benchmark suites hit the real databases directly (`-p no:django`) and are excluded from `make test`.

---

## Running locally

### Requirements

- Python 3.13+ and [Poetry](https://python-poetry.org/)
- Docker + Docker Compose
- [Ollama](https://ollama.com/) with `qwen2.5vl:7b` pulled (plus `qwen2.5-coder:7b` and `llama3.1:8b` for the baselines)
- A Google AI Studio API key (Gemini, for the LLM agents, SQL generation, and response synthesis)
- The source corpus loaded into the `dowser_db` container — 🚧 not yet released

### Setup

```bash
git clone https://github.com/jpedrojpedro/madro.git
cd madro
poetry install
cp .env.example .env   # then fill it in
make db                # starts madro_db (:5432) and dowser_db (:5433)
docker compose exec -T madro_db psql -U madro -d madro < scripts/db_schema.sql
```

`.env`:

```env
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
DATABASE_URL=postgresql://madro:madro@localhost:5432/madro
RETRIEVAL_DB_URL=postgresql://dowser:dowser@localhost:5433/dowser
GOOGLE_API_KEY=<your-google-ai-studio-key>
```

The source schema is in `configs/dowser_schema.sql` (annotated version in `configs/dowser_schema.md`).

### Run

```bash
make start                                   # Django server
poetry run python scripts/smoke_test.py      # end-to-end run over a few hardcoded prompts
```

### Other commands

```bash
make test       # pytest (excludes the live benchmark suites)
make lint       # ruff check
```

---

## Configuration

`configs/default.yaml` sets the models and ranking weights:

| Key | Default | Role |
|---|---|---|
| `model.name` | `gemini-3.1-flash-lite` | Internal agents, SQL generation, response synthesis |
| `image_model.name` | `qwen2.5vl:7b` | Image captioning + OCR (Ollama) |
| `qwen_baseline_model`, `llama_baseline_model` | `qwen2.5-coder:7b`, `llama3.1:8b` | Additional zero-shot SQL baselines (Ollama) |
| `retrieval_sql_backend` | `gemini` | SQL resolver used by retrieval agents (`gemini` or `arctic`) |
| `fusion.alpha` / `fusion.beta` | `0.3` / `0.7` | Lexical / semantic ranking weights |

---

## Project structure

```
madro/
├── configs/                 # Model + ranking config, source schema
├── results/thesis/          # Scored benchmark results behind the thesis
├── scripts/                 # Smoke test, DB schema, result comparison
├── tests/benchmark/         # Questions, Ground Truth / Baseline / MADRO suites
└── src/madro/
    ├── internal_agents/     # LLM-backed agents: prompt enrichment, decomposition, topics, image enrichment
    ├── retrieval_agents/    # RetrievalAgent ABC + one agent per data source
    ├── broker/              # Job publishing and execution (PostgreSQL as Pub-Sub)
    ├── aggregation/         # Entity resolution, relevance ranking, response synthesis
    ├── workflows/           # Thread workflow entry point, multimodal normalizer
    └── models.py            # Django ORM models (unmanaged, mapped to scripts/db_schema.sql)
```

---

## Adding a new agent

1. Create `src/madro/retrieval_agents/<name>.py` extending `RetrievalAgent`, implementing `run()` returning a JSON string.
2. Register it in `SEED_AGENTS` in `src/madro/seed_agents.py` (used by both `scripts/smoke_test.py` and `tests/benchmark/`) or via `POST /agent`, specifying `uri`, `mcp_schema`, `candidate_topics`, and `modality` (`text` or `image`).
3. The topic categorisation agent assigns it to existing or new topics automatically.

---

## Citation

If you use MADRO or its benchmark in your research, please cite:

```bibtex
@inproceedings{iceis26,
  author       = {João Pinheiro and Yenier Izquierdo and Luiz Leme and Antonio Furtado and Marco Casanova},
  title        = {A Proposal for an LLM-Based Multi-Agent Architecture for Orchestrating Access to Multiple Data Sources},
  booktitle    = {Proceedings of the 28th International Conference on Enterprise Information Systems - Volume 1: ICEIS},
  year         = {2026},
  pages        = {322-329},
  publisher    = {SciTePress},
  organization = {INSTICC},
  doi          = {10.5220/0014894200004018},
  isbn         = {978-989-758-834-1},
  issn         = {2184-4992}
}
```

<!-- Thesis entry — uncomment once the Maxwell (PUC-Rio) DOI is issued:
```bibtex
@phdthesis{pinheiro2026madro,
  author = {João Pedro Valladão Pinheiro},
  title  = {MADRO: Multi-Agent Retrieval over Heterogeneous Multimodal Data},
  school = {Pontifical Catholic University of Rio de Janeiro (PUC-Rio)},
  year   = {2026},
  doi    = {TBD}
}
```
-->

---

## License

- **Code** — [MIT](LICENSE).
- **Benchmark questions, results, and corpus** — [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). The corpus derives from publicly available social media content and is released for non-commercial research use only.

---

## Acknowledgements

This work was developed as part of a PhD thesis at the Department of Informatics, PUC-Rio, advised by Prof. José Alberto Rodrigues Pereira Sardinha and co-advised by Prof. Marco Antonio Casanova.

This study was financed in part by the Coordenação de Aperfeiçoamento de Pessoal de Nível Superior – Brasil (CAPES) – Finance Code 001.
