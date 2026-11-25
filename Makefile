.PHONY: test lint format check cov

test:
	python3 -m pytest

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

format:
	ruff format src tests scripts
	ruff check --fix src tests scripts

cov:
	python3 -m pytest --cov=nebius_tf_cost --cov-report=term-missing

check: test lint
