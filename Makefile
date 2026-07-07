.PHONY: start migrate test lint db benchmark

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
	poetry run pytest -p no:django -m benchmark --alluredir=allure-results tests/benchmark -v
