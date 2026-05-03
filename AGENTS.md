# Agent Guide

## Operating Principles

- Be autonomous: inspect the code, make the smallest correct change, and verify it before stopping.
- Do not read or print `config.local.toml`; it may contain local credentials.
- Do not delete remote data unless the user explicitly authorizes the exact files or test scenario.
- Prefer real conversion tests after pipeline changes; small unit tests are necessary but not sufficient for this project.

## Development Commands

- `uv sync --all-groups` installs all runtime and development dependencies.
- `uv run ruff format .` formats Python files.
- `uv run ruff check .` runs lint checks.
- `uv run mypy .` runs type checks.
- `uv run pytest tests/` runs the regression suite.
- `uv build` verifies package metadata and wheel/sdist builds.
- `uv run reddit-zst-to-parquet precheck` validates local host readiness.

## Testing Notes

- Use `uv run reddit-zst-to-parquet precheck --method local --skip-connection` for CI-safe smoke checks.
- For transfer or conversion changes, run at least one real FTP conversion smoke test when credentials and network are available.
- Keep generated logs, captures, and scratch files in ignored directories such as `out/`, `tmp/`, or `scratch/`.
