# Mediterranean Ops Fortress

A production-grade climate resilience data platform focused on the Mediterranean region. This project integrates data engineering workflows with rigorous DevOps practices to ensure reliable ingestion, transformation, and monitoring of environmental metrics.

---

## Overview
This platform is designed to handle real-world climate data—including air quality, temperature extremes, and wildfire risk indicators—specifically for Tunisia and North Africa. By utilizing a Medallion architecture and Infrastructure as Code (IaC), the system prioritizes data integrity and environment reproducibility.

## Key Components

### Data Engineering
* Architecture: Implementation of a Medallion Lakehouse (Bronze, Silver, and Gold layers).
* Ingestion: Incremental data fetching from Copernicus Atmosphere Monitoring Service and EEA open APIs.
* Validation: Strict schema enforcement and data lineage tracking to ensure quality.
* Modeling: Analytics-ready marts focused on regional climate resilience.

### Infrastructure and Operations
* Reproducibility: Vagrant for consistent local development environments.
* Automation: Terraform for infrastructure provisioning and Ansible for configuration management.
* Containerization: Dockerized ingestion jobs and microservices.
* Observability: Comprehensive monitoring using Prometheus, Grafana, and Alertmanager to track pipeline latency and data freshness.
* CI/CD: GitHub Actions pipelines for automated infrastructure testing and deployment.

---

## Technical Stack

* Data Processing: Python, Delta Lake / Iceberg, Great Expectations
* Infrastructure: Terraform, Ansible, Vagrant
* Orchestration: Docker, GitHub Actions
* Monitoring: Prometheus, Grafana, Alertmanager

---

## Architecture Flow

1. Vagrant Box: Hosts the local environment.
2. Terraform & Ansible: Provisions and configures the service stack.
3. Data Jobs: Dockerized containers execute ingestion into Delta Lake (MinIO).
4. Metrics: Prometheus scrapes system and pipeline performance data.
5. Visualization: Grafana provides real-time dashboards for climate indicators and system health.

---

## Setup Instructions

1. Run `vagrant up` to initialize the environment.
2. Execute Terraform scripts to provision resources.
3. Apply Ansible playbooks for service orchestration.
4. Trigger data ingestion jobs via the provided containers.
5. Access Grafana dashboards to monitor data flow and infrastructure metrics.

---
Built for Mediterranean climate resilience and stable data operations.
