.PHONY: setup
setup:
	@# A Python version is not explicitly specified via the --python flag because it is picked up from
	@# the special .python-version file.
	uv sync --dev

.PHONY: lint
lint:
	uv run ruff check src tests
	uv run mypy src tests

.PHONY: fmt
fmt:
	# Running isort ahead of ruff because it is more comprehensive than `ruff format`.
	uv run isort src tests
	uv run ruff format src tests

.PHONY: test
test:
	uv run pytest tests/ --cov=src/ --cov-report=term-missing

.PHONY: clean
clean:
	rm -rf build dist src/*.egg-info .ruff_cache
	find . -name '__pycache__' -exec rm -rf {} +
