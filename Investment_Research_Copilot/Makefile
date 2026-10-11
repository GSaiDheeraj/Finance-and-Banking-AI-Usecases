.PHONY: help install test lint lint-fix security build run run-worker run-streamlit clean init-db

help: ## Show this help message
	@echo 'Usage: make [target]'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies with pip
	pip install -r requirements.txt

install-uv: ## Install dependencies with uv
	uv pip install -r requirements.txt

test: ## Run tests
	pytest tests/ -v

lint: ## Run ruff lint and format check
	ruff check .
	ruff format --check .

lint-fix: ## Auto-fix lint issues
	ruff check --fix .
	ruff format .

security: ## Run bandit security scan
	bandit -r portfolio_monitor/ -ll -f json -o bandit-report.json || true

build: ## Build Docker image
	docker build -t investment-research-api:latest .

run: ## Run FastAPI locally
	uvicorn portfolio_monitor.api.main:app --reload --host 0.0.0.0 --port 8000

run-worker: ## Run Celery worker (requires Redis)
	celery -A portfolio_monitor.celery_app worker --loglevel=info

run-streamlit: ## Run Streamlit dev UI
	streamlit run app_streamlit/ui.py

init-db: ## Create Postgres tables
	python -c "from portfolio_monitor.db.models import init_schema; init_schema()"

clean: ## Remove caches and reports
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache bandit-report.json
