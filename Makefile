.PHONY: precheck setup-dev lint format test typecheck build ci clean

precheck:
	uv run reddit-zst-to-parquet precheck

setup-dev:
	uv sync --all-groups
	uv run pre-commit install --install-hooks
	uv run pre-commit install --hook-type pre-push

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest --cov --cov-report=term-missing

typecheck:
	uv run mypy .

build:
	uv build

ci:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy .
	uv run pytest tests/
	uv build
	uv run reddit-zst-to-parquet --help
	uv run reddit-zst-to-parquet precheck --method local --skip-connection

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov build dist
	find commands core engines tests transfer -type d -name "__pycache__" -exec rm -rf {} +
