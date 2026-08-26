.PHONY: start migrate test lint db benchmark benchmark-baseline benchmark-ground-truth compare-baseline delete-suite

db:
	docker compose up -d

start:
	poetry run python manage.py runserver

migrate:
	poetry run python manage.py migrate

test:
	poetry run pytest

lint:
	poetry run ruff check src tests

# Live end-to-end benchmark against the real DBs, reported via Allure.
# pytest-django is disabled (-p no:django) since this suite manages its own
# django.setup() and must hit the real dev DB, not a Django test database.
# View the report with the Allure commandline tool (e.g. `brew install allure`):
#   allure serve allure-results
#
# SAMPLE, FUSION_LEX, and FUSION_SEM are mandatory — they parametrize the run
# and the run's Allure label is built from them, e.g.:
#   make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7
#
# To resume a run that was killed partway through (e.g. OOM), select only the
# remaining question ids via K="<start> to <end>" (question number, inclusive)
# and pass the original run's RUN_TIMESTAMP (as printed in its Allure
# parent_suite label) so the results land back in the same run instead of
# starting a new one:
#   make benchmark SAMPLE=10 FUSION_LEX=0.5 FUSION_SEM=0.5 \
#     RUN_TIMESTAMP=2026-07-07T04:08:32Z K="34 to 50"
benchmark:
	@test -n "$(SAMPLE)" || { echo 'SAMPLE is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	@test -n "$(FUSION_LEX)" || { echo 'FUSION_LEX is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	@test -n "$(FUSION_SEM)" || { echo 'FUSION_SEM is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	BENCHMARK_SAMPLE=$(SAMPLE) \
	BENCHMARK_ALPHA=$(FUSION_LEX) \
	BENCHMARK_BETA=$(FUSION_SEM) \
	$(if $(RUN_TIMESTAMP),BENCHMARK_RUN_TIMESTAMP=$(RUN_TIMESTAMP)) \
	$(if $(K),BENCHMARK_QUESTION_RANGE="$(K)") \
	poetry run pytest -p no:django -m benchmark --alluredir=allure-results tests/benchmark --ignore=tests/benchmark/test_baseline.py --ignore=tests/benchmark/test_ground_truth.py -v

# Naive one-shot SQL baseline against dowser — decoupled from `benchmark`
# above so it can be (re)run without re-executing MADRO's full pipeline
# (thread workflow, agent runner, VLM enrichment, ranking). Writes into the
# same allure-results/ dir, each model getting its own
# "Baseline_{model} @ {timestamp}" parent_suite (same "{label} @ {timestamp}"
# convention `benchmark` above uses for its RUN_ID). Runs against every model
# in test_baseline.py's BASELINE_MODELS (currently Gemini, plus Qwen2.5-Coder
# and Llama 3.1 8B served locally via Ollama) unless narrowed with MODEL.
# Compare against an
# existing `make benchmark` run with
# scripts/compare_baseline.py. test_benchmark.py is --ignore'd because its
# module-level BENCHMARK_SAMPLE/ALPHA/BETA env var lookups raise KeyError at
# collection time regardless of marker filtering, and this target
# intentionally doesn't set them.
#   make benchmark-baseline
#   make benchmark-baseline K="1 to 3"
#   make benchmark-baseline MODEL=qwen2.5-coder
benchmark-baseline:
	$(if $(K),BENCHMARK_QUESTION_RANGE="$(K)") \
	poetry run pytest -p no:django -m baseline --alluredir=allure-results tests/benchmark --ignore=tests/benchmark/test_benchmark.py --ignore=tests/benchmark/test_ground_truth.py -v $(if $(MODEL),-k "$(MODEL)")

# Ground Truth: the authoritative answer for each question, resolved via the
# same zero-shot SQL resolver as Baseline but scoped to only the tables
# named in that question's `hint` (see tests/benchmark/baselines/hint_schema.py)
# and Gemini only. Run this before `make benchmark-baseline` if you want
# Baseline steered by Ground Truth's resolved identity column (see
# tests/benchmark/baselines/ground_truth_reference.py).
#   make benchmark-ground-truth
#   make benchmark-ground-truth K="1 to 3"
benchmark-ground-truth:
	$(if $(K),BENCHMARK_QUESTION_RANGE="$(K)") \
	poetry run pytest -p no:django -m ground_truth --alluredir=allure-results tests/benchmark --ignore=tests/benchmark/test_benchmark.py --ignore=tests/benchmark/test_baseline.py -v

# Compares an existing `make benchmark` run's Allure results against the
# `make benchmark-baseline` run's Allure results — read-only, no pipeline
# re-execution. See scripts/compare_baseline.py for what it produces
# (comparison_results.json + comparison_grid.xlsx).
#
# APPROACH is mandatory (the MADRO run label to compare against, e.g. the
# value printed as "approach" in that run's Allure parameters). BASELINE_MODEL,
# RUN_AT, BASELINE_RUN_AT, OUT, and GRID_OUT are optional overrides.
# BASELINE_MODEL selects which baseline (gemini or qwen2.5-coder) the --out
# JSON is computed against; the --grid-out spreadsheet always includes both
# regardless of this setting:
#   make compare-baseline APPROACH=sample-10_alpha-0.0_beta-1.0
#   make compare-baseline APPROACH=sample-10_alpha-0.0_beta-1.0 BASELINE_MODEL=qwen2.5-coder
#   make compare-baseline APPROACH=sample-10_alpha-0.0_beta-1.0 \
#     RUN_AT=2026-07-09T17:35:04Z BASELINE_RUN_AT=2026-07-10T12:00:00Z \
#     OUT=my_comparison.json GRID_OUT=my_grid.xlsx
compare-baseline:
	@test -n "$(APPROACH)" || { echo 'APPROACH is required, e.g. make compare-baseline APPROACH=sample-10_alpha-0.0_beta-1.0'; exit 1; }
	poetry run python scripts/compare_baseline.py \
	  --approach $(APPROACH) \
	  $(if $(BASELINE_MODEL),--baseline-model $(BASELINE_MODEL)) \
	  $(if $(RUN_AT),--run-at $(RUN_AT)) \
	  $(if $(BASELINE_RUN_AT),--baseline-run-at $(BASELINE_RUN_AT)) \
	  $(if $(OUT),--out $(OUT)) \
	  $(if $(GRID_OUT),--grid-out $(GRID_OUT))

# Deletes every Allure result + attachment file belonging to one suite,
# matched by its exact parentSuite label (e.g. as shown in `allure serve` or
# a result file's "parentSuite" label — a full benchmark run's
# "{approach} @ {timestamp}" label, or a baseline run's
# "Baseline_{model} @ {timestamp}" label). Dry-run by
# default — only lists what would be deleted, and won't touch attachments
# shared with another suite. Pass CONFIRM=1 to actually delete. See
# scripts/delete_allure_suite.py.
#   make delete-suite SUITE="sample-10_alpha-0.5_beta-0.5 @ 2026-07-07T04:08:32Z"
#   make delete-suite SUITE="sample-10_alpha-0.5_beta-0.5 @ 2026-07-07T04:08:32Z" CONFIRM=1
delete-suite:
	@test -n "$(SUITE)" || { echo 'SUITE is required, e.g. make delete-suite SUITE="sample-10_alpha-0.5_beta-0.5 @ 2026-07-07T04:08:32Z"'; exit 1; }
	poetry run python scripts/delete_allure_suite.py --suite "$(SUITE)" $(if $(CONFIRM),--yes)
