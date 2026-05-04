.PHONY: help up down ingest-openmeteo ingest-openaq ingest-bronze ingest-silver ingest-gold ingest test lint format monitoring-up monitoring-down clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ── Local dev stack ───────────────────────────────────────────────────────────
up: ## Start local stack (MinIO + observability) via docker-compose
	docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml up -d

down: ## Stop local stack
	docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml down

# ── Data pipeline ─────────────────────────────────────────────────────────────
ingest-openmeteo: ## Run bronze ingestion — Open-Meteo Air Quality API
	docker compose -f docker/docker-compose.yml --profile jobs run --rm ingestion python -m data.ingestion.bronze.openmeteo

ingest-openaq: ## Run bronze ingestion — OpenAQ v3
	docker compose -f docker/docker-compose.yml --profile jobs run --rm ingestion python -m data.ingestion.bronze.openaq

ingest-bronze: ingest-openmeteo ingest-openaq ## Run all bronze ingestors

ingest-silver: ## Run silver transformation
	docker compose -f docker/docker-compose.yml --profile jobs run --rm ingestion python -m data.ingestion.silver.transformer

ingest-gold: ## Build gold marts
	docker compose -f docker/docker-compose.yml --profile jobs run --rm ingestion python -m data.ingestion.gold.marts

ingest: ingest-bronze ingest-silver ingest-gold ## Run full pipeline

# ── Tests ─────────────────────────────────────────────────────────────────────
test: ## Run all Python tests
	pytest tests/ -v --tb=short

# ── Code quality ──────────────────────────────────────────────────────────────
lint: ## Lint Python code
	ruff check data/ tests/
	black --check data/ tests/

format: ## Format Python code
	black data/ tests/
	ruff check --fix data/ tests/

# ── Local observability stack ─────────────────────────────────────────────────
monitoring-up: ## Start local Prometheus + Alertmanager via docker-compose
	docker compose -f docker/docker-compose.yml up -d prometheus alertmanager pushgateway

monitoring-down: ## Stop local observability stack
	docker compose -f docker/docker-compose.yml stop prometheus alertmanager pushgateway

# ── Utilities ─────────────────────────────────────────────────────────────────
clean: ## Remove Python cache and build artefacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} +
