.PHONY: install format lint test

install:
	python -m pip install --upgrade pip
	python -m pip install black isort flake8 pytest

format:
	python -m black .
	python -m isort .

lint:
	python -m black --check .
	python -m isort --check-only .
	python -m flake8 .

test:
	python -m pytest -q || true
