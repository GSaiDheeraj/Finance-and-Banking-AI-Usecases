.PHONY: help install test lint build deploy clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies
	pip install -r requirements.txt

test: ## Run tests
	pytest tests/ -v

lint: ## Run linting
	ruff check .
	ruff format --check .

lint-fix: ## Fix linting issues
	ruff check --fix .
	ruff format .

security: ## Run security checks
	bandit -r fin_doc_intel/ -f json -o bandit-report.json

build: ## Build Docker image
	docker build -t findoc-api:latest .

run: ## Run the application locally
	uvicorn fin_doc_intel.api.main:app --reload --host 0.0.0.0 --port 8000

run-streamlit: ## Run the Streamlit UI
	streamlit run app_streamlit/ui.py

clean: ## Clean up generated files
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf bandit-report.json

init-db: ## Initialize the database
	python -c "from fin_doc_intel.db.session import init_db; init_db()"
