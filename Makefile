.PHONY: setup
setup:
	pip install -e .[dev]

.PHONY: clean
clean:
	rm -rf build dist src/*.egg-info .ruff_cache
	find . -name '__pycache__' -exec rm -rf {} +
