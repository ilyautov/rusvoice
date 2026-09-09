.PHONY: check lint
check:
	python3 -m pytest tests/ -q
lint:
	python3 -m ruff check rusvoice tests
