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
benchmark:
	poetry run pytest -p no:django -m benchmark --alluredir=allure-results tests/benchmark -v
