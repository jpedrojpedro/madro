.PHONY: start migrate test lint db

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
