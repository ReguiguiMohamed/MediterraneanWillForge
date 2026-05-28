.PHONY: help up down ingest-openmeteo ingest-openaq ingest-waqi ingest-bronze ingest-silver ingest-gold ingest-anomaly ingest quality report test lint format monitoring-up monitoring-down clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE_BASE := docker compose -f docker/docker-compose.yml
COMPOSE_LOCAL := docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml
JOB := --profile jobs run --rm

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# Local dev stack
up: ## Start local stack with MinIO and observability
	$(COMPOSE_LOCAL) up -d

down: ## Stop local stack
	$(COMPOSE_LOCAL) down

# Data pipeline
ingest-openmeteo: ## Run Bronze ingestion for Open-Meteo Air Quality
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.bronze.copernicus_ingestor

ingest-openaq: ## Run Bronze ingestion for OpenAQ v3
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.bronze.openaq_ingestor

ingest-waqi: ## Run Bronze ingestion for WAQI
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.bronze.waqi_ingestor

ingest-bronze: ingest-openmeteo ingest-openaq ingest-waqi ## Run all Bronze ingestors

ingest-silver: ## Run Silver transformation
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.silver.transformer

ingest-gold: ## Build Gold marts
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.gold.marts

ingest-anomaly: ## Build Gold anomaly alerts
	$(COMPOSE_LOCAL) $(JOB) ingestion python -m data.ingestion.gold.anomaly

ingest: ingest-bronze ingest-silver ingest-gold ingest-anomaly ## Run the full local pipeline

quality: ## Run recent-partition data quality checks
	$(COMPOSE_LOCAL) $(JOB) quality python -m data.quality.run_checks

report: ## Generate static report PNGs from configured Gold storage
	python -m data.reporting.static_report

# Tests
test: ## Run all Python tests
	pytest tests/ -v --tb=short

# Code quality
lint: ## Lint Python code
	ruff check data/ tests/
	black --check data/ tests/

format: ## Format Python code
	black data/ tests/
	ruff check --fix data/ tests/

# Local observability stack
monitoring-up: ## Start local Prometheus, Alertmanager, and Pushgateway
	$(COMPOSE_BASE) up -d prometheus alertmanager pushgateway

monitoring-down: ## Stop local observability services
	$(COMPOSE_BASE) stop prometheus alertmanager pushgateway

# Utilities
clean: ## Remove Python cache and test artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} +
