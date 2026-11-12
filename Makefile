.PHONY: install lint format typecheck test e2e cov check secrets all serve dry-run

install:            ## create .venv and install runtime + dev dependencies
	uv sync
	uv run pre-commit install

lint:               ## ruff lint + formatting check
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format:             ## auto-format and auto-fix
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

typecheck:          ## mypy --strict
	uv run mypy

test:               ## unit + integration tests (fast, no network)
	uv run pytest

e2e:                ## end-to-end test with a throw-away Label Studio (~2 min)
	uv run pytest -m e2e

cov:                ## tests with coverage report
	uv run pytest --cov --cov-report=term-missing

secrets:            ## fail if a secret from .env appears in tracked files
	uv run python scripts/check_no_secrets.py $$(git ls-files)

all: lint typecheck test secrets

check:              ## verify .env, Label Studio token, ERDA login, disk space
	uv run wildcam-bridge check

serve:              ## run the API in the foreground (production uses systemd)
	uv run wildcam-bridge serve

dry-run:            ## make dry-run CSV=path/to/query.csv
	uv run wildcam-bridge dry-run $(CSV)
