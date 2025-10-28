.PHONY: setup test lint serve clean help

help:  ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup:  ## Install dependencies in a new virtual environment
	pip install uv
	uv venv --python 3.12
	@echo "Run 'source .venv/bin/activate' to activate the virtual environment"
	@echo "Then run 'make install' to install dependencies"

install:  ## Install all dependencies (requires active venv)
	uv pip install -r requirements.txt -r requirements-dev.txt
	uv pip install -e .

test:  ## Run tests with pytest
	AUTH_ENABLED=false pytest tests/ -v

lint:  ## Run linter (ruff) on src and tests
	ruff check src/ tests/

lint-fix:  ## Run linter and auto-fix issues
	ruff check --fix src/ tests/

format:  ## Format code with ruff
	ruff format src/ tests/

check:  ## Run static syntax check
	python -m compileall src tests

serve:  ## Start the MCP server
	python main.py

clean:  ## Remove Python cache files and artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f pytest.xml

all: lint check test  ## Run all quality checks
