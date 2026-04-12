.PHONY: help up down provision deploy ingest quality test lint clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ── Environment ───────────────────────────────────────────────────────────────
up: ## Start the Vagrant VM
	cd vagrant && vagrant up

down: ## Halt the Vagrant VM
	cd vagrant && vagrant halt

destroy: ## Destroy the Vagrant VM (irreversible)
	cd vagrant && vagrant destroy -f

# ── Infrastructure ────────────────────────────────────────────────────────────
tf-init: ## Initialise Terraform
	cd terraform/environments/local && terraform init

tf-plan: ## Terraform plan
	cd terraform/environments/local && terraform plan

tf-apply: ## Apply Terraform configuration
	cd terraform/environments/local && terraform apply -auto-approve

tf-destroy: ## Destroy Terraform resources
	cd terraform/environments/local && terraform destroy -auto-approve

provision: ## Run Ansible site playbook
	ansible-playbook -i ansible/inventory/hosts.ini ansible/site.yml

# ── Data pipeline ─────────────────────────────────────────────────────────────
ingest-openmeteo: ## Run bronze ingestion — Open-Meteo (gridded CAMS model data, 12 cities)
	docker compose -f docker/docker-compose.yml run --rm ingestion python -m ingestion.bronze.copernicus_ingestor

ingest-openaq: ## Run bronze ingestion — OpenAQ v2 (station observations, North Africa + Med)
	docker compose -f docker/docker-compose.yml run --rm ingestion python -m ingestion.bronze.openaq_ingestor

ingest-bronze: ingest-openmeteo ingest-openaq ## Run all bronze ingestors

ingest-silver: ## Run silver transformation
	docker compose -f docker/docker-compose.yml run --rm ingestion python -m ingestion.silver.transformer

ingest-gold: ## Build gold marts
	docker compose -f docker/docker-compose.yml run --rm ingestion python -m ingestion.gold.marts

ingest: ingest-bronze ingest-silver ingest-gold ## Run full pipeline

quality: ## Run data quality checks
	docker compose -f docker/docker-compose.yml run --rm quality

dbt-run: ## Run dbt models
	cd data/dbt && dbt run

dbt-test: ## Run dbt tests
	cd data/dbt && dbt test

# ── Tests ─────────────────────────────────────────────────────────────────────
test: ## Run all Python tests
	pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	pytest tests/unit/ -v

test-integration: ## Run integration tests only
	pytest tests/integration/ -v

# ── Code quality ──────────────────────────────────────────────────────────────
lint: ## Lint Python code
	ruff check data/ tests/
	black --check data/ tests/

format: ## Format Python code
	black data/ tests/
	ruff check --fix data/ tests/

# ── Observability stack ───────────────────────────────────────────────────────
monitoring-up: ## Start Prometheus + Grafana + Alertmanager via compose
	docker compose -f docker/docker-compose.yml up -d prometheus grafana alertmanager pushgateway

monitoring-down: ## Stop observability stack
	docker compose -f docker/docker-compose.yml stop prometheus grafana alertmanager pushgateway

# ── Utilities ─────────────────────────────────────────────────────────────────
clean: ## Remove Python cache and build artefacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} +

healthcheck: ## Run infrastructure health check
	bash scripts/healthcheck.sh
