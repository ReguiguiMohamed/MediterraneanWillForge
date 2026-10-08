.PHONY: help up down ingest-openmeteo ingest-openaq ingest-waqi ingest-bronze ingest-silver ingest-gold ingest-anomaly ingest quality test test-unit test-integration lint format sqlmesh-test sqlmesh-plan validate monitoring-up monitoring-down clean

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

# Tests
test: ## Run all tests with coverage
	pytest tests/ -v --tb=short --cov=data --cov-report=term-missing

test-unit: ## Run unit tests with coverage
	pytest tests/unit/ -v --tb=short --cov=data --cov-report=term-missing

test-integration: ## Run deterministic integration tests against local MinIO
	$(COMPOSE_LOCAL) --profile jobs up -d minio
	MINIO_ENDPOINT=http://localhost:9000 \
	MINIO_ACCESS_KEY=minioadmin \
	MINIO_SECRET_KEY=minioadmin \
	pytest tests/integration/ -v --tb=short

# Code quality
lint: ## Lint Python code
	ruff check data/ tests/
	black --check data/ tests/

format: ## Format Python code
	black data/ tests/
	ruff check --fix data/ tests/

# SQLMesh (isolated environment, it pins its own sqlglot)
sqlmesh-test: .sqlmesh-venv/bin/sqlmesh ## Run SQLMesh unit tests without reading lake data
	.sqlmesh-venv/bin/sqlmesh -p data/sqlmesh test

sqlmesh-plan: .sqlmesh-venv/bin/sqlmesh ## Build and audit SQLMesh models from MinIO Silver data
	.sqlmesh-venv/bin/sqlmesh -p data/sqlmesh plan --auto-apply --no-prompts

.sqlmesh-venv/bin/sqlmesh: requirements-sqlmesh.txt
	python -m venv .sqlmesh-venv
	.sqlmesh-venv/bin/pip install -r requirements-sqlmesh.txt

# Configuration
validate: ## Validate base and local Compose configuration
	$(COMPOSE_BASE) config --quiet
	$(COMPOSE_LOCAL) config --quiet

# Local observability stack
monitoring-up: ## Start MinIO, Prometheus, Alertmanager, Pushgateway, and cAdvisor
	$(COMPOSE_LOCAL) up -d minio pushgateway prometheus alertmanager cadvisor

monitoring-down: ## Stop local observability services
	$(COMPOSE_LOCAL) stop minio prometheus alertmanager pushgateway cadvisor

# Utilities
clean: ## Remove Python cache and test artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} +
	rm -f .coverage coverage.xml coverage.json
