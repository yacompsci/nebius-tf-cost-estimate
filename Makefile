.PHONY: test lint format check cov e2e

test:
	python3 -m pytest

e2e:
	python3 -m pytest -m e2e -o addopts=-q

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

format:
	ruff format src tests scripts
	ruff check --fix src tests scripts

cov:
	python3 -m pytest --cov=nebius_tf_cost --cov-report=term-missing

check: test lint
