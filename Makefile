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
benchmark:
	@test -n "$(SAMPLE)" || { echo 'SAMPLE is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	@test -n "$(FUSION_LEX)" || { echo 'FUSION_LEX is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	@test -n "$(FUSION_SEM)" || { echo 'FUSION_SEM is required, e.g. make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7'; exit 1; }
	BENCHMARK_SAMPLE=$(SAMPLE) \
	BENCHMARK_ALPHA=$(FUSION_LEX) \
	BENCHMARK_BETA=$(FUSION_SEM) \
	poetry run pytest -p no:django -m benchmark --alluredir=allure-results tests/benchmark -v
