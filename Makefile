.PHONY: setup
bootstrap:
	pip install -e .[dev]

.PHONY: lint
lint:
	ruff check src tests
	mypy src tests

.PHONY: fmt
fmt:
  # Running isort ahead of ruff because it is more comprehensive than `ruff format`.
	isort src tests
	ruff format src tests

.PHONY: test
test:
	pytest

.PHONY: clean
clean:
	rm -rf build dist src/*.egg-info .ruff_cache
	find . -name '__pycache__' -exec rm -rf {} +
