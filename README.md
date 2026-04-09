Mediterranean Ops Fortress
A production-grade climate resilience data platform focused on the Mediterranean region. This project integrates data engineering workflows with rigorous DevOps practices to ensure reliable ingestion, transformation, and monitoring of environmental metrics.

Overview
This platform is designed to handle real-world climate data—including air quality, temperature extremes, and wildfire risk indicators—specifically for Tunisia and North Africa. By utilizing a Medallion architecture and Infrastructure as Code (IaC), the system prioritizes data integrity and environment reproducibility.

Key Components
Data Engineering
Architecture: Implementation of a Medallion Lakehouse (Bronze, Silver, and Gold layers).

Ingestion: Incremental data fetching from Copernicus Atmosphere Monitoring Service and EEA open APIs.

Validation: Strict schema enforcement and data lineage tracking to ensure quality.

Modeling: Analytics-ready marts focused on regional climate resilience.

Infrastructure and Operations
Reproducibility: Vagrant for consistent local development environments.

Automation: Terraform for infrastructure provisioning and Ansible for configuration management.

Containerization: Dockerized ingestion jobs and microservices.

Observability: Comprehensive monitoring using Prometheus, Grafana, and Alertmanager to track pipeline latency and data freshness.

CI/CD: GitHub Actions pipelines for automated infrastructure testing and deployment.

Technical Stack
Data Processing: Python, Delta Lake / Iceberg, Great Expectations

Infrastructure: Terraform, Ansible, Vagrant

Orchestration: Docker, GitHub Actions

Monitoring: Prometheus, Grafana, Alertmanager

Architecture Flow
Vagrant Box: Hosts the local environment.

Terraform & Ansible: Provisions and configures the service stack.

Data Jobs: Dockerized containers execute ingestion into Delta Lake (MinIO).

Metrics: Prometheus scrapes system and pipeline performance data.

Visualization: Grafana provides real-time dashboards for climate indicators and system health.

Setup Instructions
Run vagrant up to initialize the environment.

Execute Terraform scripts to provision resources.

Apply Ansible playbooks for service orchestration.

Trigger data ingestion jobs via the provided containers.

Access Grafana dashboards to monitor data flow and infrastructure metrics.

Built for Mediterranean climate resilience and stable data operations.
