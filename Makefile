.PHONY: help install sync lint format type test up down logs migrate clean

help:
	@echo "Targets:"
	@echo "  install    — uv sync (workspace)"
	@echo "  lint       — ruff check + format check"
	@echo "  format     — ruff format (write)"
	@echo "  type       — mypy strict"
	@echo "  test       — pytest"
	@echo "  up         — docker compose up --build"
	@echo "  down       — docker compose down"
	@echo "  logs       — docker compose logs -f"
	@echo "  migrate    — alembic upgrade head в каждом сервисе"
	@echo "  clean      — снести кэши"

install sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy .

test:
	uv run pytest

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose exec core alembic upgrade head
	docker compose exec notifications alembic upgrade head
	docker compose exec sheets-sync alembic upgrade head

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
