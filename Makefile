PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
ifeq ($(OS),Windows_NT)
BIN := $(VENV)/Scripts
endif

.PHONY: setup demo serve test lint eval samples clean

setup:  ## Create the virtual environment and install everything
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt

demo:  ## Start the web app with the bundled sample ready at http://127.0.0.1:5000/
	$(BIN)/python -m backlink_lens demo

serve:
	$(BIN)/python -m backlink_lens serve

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy .

eval:  ## Score column detection and cleaning on the labelled sets (AI step only with AI_API_KEY)
	$(BIN)/python -m backlink_lens evaluate

samples:  ## Regenerate sample_data/ (deterministic)
	$(BIN)/python scripts/make_samples.py

clean:
	rm -rf var .pytest_cache .mypy_cache .ruff_cache
