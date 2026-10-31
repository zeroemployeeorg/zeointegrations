.PHONY: setup verify

setup:
	uv sync --frozen --all-extras

verify:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen python -m unittest discover -s tests -v
	uv run --frozen python -m unittest discover -s tests_installed -v
	uv run --frozen python tools/verify_provider_tests.py
	uv run --frozen python tools/verify_python_floor.py
	uv build --offline --no-sources
	uv run --frozen python tools/verify_loops_wheel.py
	$(MAKE) -C packages/loops verify
	$(MAKE) -C packages/kit verify
	$(MAKE) -C packages/hubspot verify
	git diff --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD
