# Mediterranean Ops Fortress

[![CI — Data Pipeline](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-data.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-data.yml)
[![CI — Infrastructure](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-infra.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-infra.yml)
[![CD — Publish Images](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/cd-deploy.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/cd-deploy.yml)

Mediterranean Ops Fortress is a hybrid Data Engineering and DevOps portfolio
project built around real Mediterranean air quality data. It ingests public API
data, writes a Delta Lake medallion on MinIO, validates quality, builds analytics
marts, and exposes full pipeline and infrastructure observability through
Prometheus, Alertmanager, and Grafana.

This is not a tutorial scaffold. The stack is designed to behave like a small
production data platform: reproducible infrastructure, incremental Delta writes,
CI validation, metrics, alerts, dashboards, and no synthetic fallback data.

## What It Does

The platform pulls two real public data sources:

| Source | Data | Notes |
|---|---|---|
| Open-Meteo Air Quality API | CAMS-backed daily PM2.5, PM10, NO2, and O3 means for 12 Mediterranean city grid points | Free, no API key |
| OpenAQ v2 | Station observations for TN, DZ, MA, LY, EG, TR, GR, ES, IT, and LB | Free public API; historical v2 measurement queries may return HTTP 410 |

The pipeline writes:

| Layer | Path | Purpose |
|---|---|---|
| Bronze | `s3://bronze/openmeteo/air_quality` | Open-Meteo gridded source records, partitioned by `partition_date` |
| Bronze | `s3://bronze/openaq/air_quality` | OpenAQ station records, partitioned by `partition_date` |
| Silver | `s3://silver/air_quality` | Canonical cleaned rows with WHO flags, AQI category, completeness, source, and partition date |
| Gold | `s3://gold/daily_country_summary` | Daily country/source pollutant summaries and WHO exceedance percentages |
| Gold | `s3://gold/wildfire_risk_index` | Composite O3 plus PM2.5 station risk index |

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full architecture document
with a Mermaid data flow diagram, medallion schema, infrastructure breakdown, and
observability model.

```text
Open-Meteo ----\
                +--> Bronze --> Silver --> Gold
OpenAQ v2 ----/                    |
                                   +--> dbt models
              MinIO (Delta Lake)

Jobs --> Pushgateway --> Prometheus --> Grafana
                             |
                             v
                       Alertmanager
```

## Canonical Silver Schema

All downstream code uses these columns:

```text
station_id, station_name, city, country_code, latitude, longitude, date,
pm2_5, pm10, nitrogen_dioxide, ozone, aqi_category,
who_pm25_exceed, who_pm10_exceed, who_no2_exceed, who_o3_exceed,
data_completeness, source, silver_ts, partition_date
```

Column names are intentionally explicit: use `pm2_5`, `nitrogen_dioxide`, and
`ozone`; do not introduce aliases such as `pm2p5`, `no2`, or `o3`.

## Stack

| Domain | Tools |
|---|---|
| Local environment | Vagrant, VirtualBox, Ubuntu 22.04 |
| Infrastructure | Terraform (`kreuzwerker/docker` provider, 3 modules) |
| Configuration | Ansible (6 roles: common, docker, minio, prometheus, alertmanager, grafana) |
| Storage | MinIO `RELEASE.2025-09-07T16-13-09Z`, Delta Lake, delta-rs |
| Data pipeline | Python 3.11, pandas, pyarrow, deltalake |
| Transformation | Bronze, Silver, Gold Python jobs + dbt models (DuckDB over Silver) |
| Quality | Soda-style checks, custom runner, Great Expectations schema validation |
| Observability | Prometheus `v2.51.0`, Pushgateway, Alertmanager `v0.27.0`, Grafana `10.4.2`, cAdvisor |
| CI/CD | GitHub Actions (3 workflows), ghcr.io image publishing |

## Repository Layout

```text
mediterranean-ops-fortress/
|-- .github/workflows/
|   |-- ci-data.yml          # lint, unit tests, dbt compile, integration tests
|   |-- ci-infra.yml         # terraform, ansible, prometheus, alertmanager checks
|   `-- cd-deploy.yml        # ghcr.io image publishing
|-- ansible/
|   |-- site.yml
|   |-- vars/common.yml
|   `-- roles/
|-- data/
|   |-- ingestion/
|   |   |-- bronze/
|   |   |   |-- base.py
|   |   |   |-- copernicus_ingestor.py   # Open-Meteo Air Quality source
|   |   |   `-- openaq_ingestor.py
|   |   |-- silver/transformer.py
|   |   `-- gold/marts.py
|   |-- quality/
|   |-- schemas/
|   `-- dbt/
|-- docker/
|   |-- docker-compose.yml
|   |-- ingestion/Dockerfile
|   `-- quality/Dockerfile
|-- docs/
|   |-- architecture.md
|   `-- adr/001-lakehouse-format.md
|-- monitoring/
|   |-- prometheus/
|   |-- alertmanager/
|   `-- grafana/
|-- terraform/
|   |-- main.tf
|   |-- versions.tf
|   `-- modules/
|-- tests/
|   |-- unit/
|   |-- integration/
|   `-- ci_verify_silver.py / ci_verify_gold.py
|-- vagrant/
|-- .env.example
|-- Makefile
`-- README.md
```

The Open-Meteo module is named `copernicus_ingestor.py` because the source is
CAMS-backed model data. The persisted source label and storage path are `openmeteo`.

## Quick Start

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Docker | 24+ | Compose stack and pipeline job containers |
| Python | 3.11 | Local tests and development |
| Vagrant | 2.4+ | Full VM stack (optional) |
| VirtualBox | 7.x | VM hypervisor (optional) |

### Configure

```bash
git clone https://github.com/ReguiguiMohamed/mediterranean-ops-fortress.git
cd mediterranean-ops-fortress
cp .env.example .env
```

No API keys are required. Both Open-Meteo and OpenAQ v2 are free public APIs.

---

### Option A — Docker Compose (recommended for local development)

Bring up the full infrastructure stack:

```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts MinIO, Prometheus, Pushgateway, Alertmanager, Grafana, and cAdvisor.
All services expose healthchecks; Prometheus and Grafana wait for their
dependencies before starting.

Create the lakehouse buckets (one-time setup):

```bash
docker run --rm --network host \
  --entrypoint /bin/sh \
  minio/mc:RELEASE.2025-08-13T08-35-41Z -c "
    mc alias set local http://localhost:9000 minioadmin minioadmin &&
    mc mb --ignore-existing local/bronze &&
    mc mb --ignore-existing local/silver &&
    mc mb --ignore-existing local/gold
  "
```

Run the full pipeline:

```bash
make ingest       # bronze (both sources) -> silver -> gold
make quality      # data quality checks
make dbt-run      # dbt models over silver
make dbt-test     # dbt schema and freshness tests
```

Access the stack:

| Service | URL |
|---|---|
| MinIO console | http://localhost:9001 (minioadmin / minioadmin) |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / fortress) |
| Alertmanager | http://localhost:9093 |
| Pushgateway | http://localhost:9091 |

Stop the stack:

```bash
docker compose -f docker/docker-compose.yml down
```

---

### Option B — Vagrant + Full VM Stack

Provision the local VM and infrastructure:

```bash
make up           # vagrant up — boots Ubuntu 22.04 VM at 192.168.56.10
make tf-init      # terraform init
make tf-apply     # provisions Docker network, MinIO, and observability containers
make provision    # ansible configures all services on the VM
```

Run the pipeline from the host via compose (targets the VM's Docker engine):

```bash
make ingest
make quality
```

---

### Option C — CI

Push to `main` or `develop`. Three workflows fire automatically:

- `ci-data.yml` — lint, unit tests, dbt compile, MinIO-backed integration tests
  including Silver and Gold verification
- `ci-infra.yml` — Terraform format and validate, Ansible lint, Prometheus rule
  validation, Alertmanager config check
- `cd-deploy.yml` — builds and publishes images to ghcr.io on `main`

---

### Run Tests Locally

```bash
ruff check data/ tests/
black --check data/ tests/
pytest tests/unit/ -v
pytest tests/integration/ -v   # requires a live MinIO endpoint
```

## Observability

Pipeline jobs push batch metrics to Pushgateway after each run:

| Metric | Purpose |
|---|---|
| `pipeline_ingested_rows{layer, source}` | Row count written per run |
| `pipeline_last_successful_run_timestamp{layer}` | Freshness tracking |
| `pipeline_duration_seconds{stage}` | Runtime per pipeline stage |
| `pipeline_quality_check_failures_total{check_name, layer}` | Failed data quality gates |
| `pipeline_schema_drift_events_total{table}` | Schema evolution events |

Prometheus alert rules cover stale layers, quality failures, slow pipeline
stages, row count drops, and infrastructure health (ContainerDown, MinIODown,
DiskUsageCritical, HighCPU, HighMemory).

Alertmanager routes alerts into three groups: `infra_critical` (fast path,
1 h repeat), `pipeline_ops` (freshness and quality failures), and `infra_ops`
(warnings and slow stages). Webhook receivers are localhost placeholders;
override in `ansible/vars/common.yml` for a real deployment.

## CI/CD

| Workflow | Triggers | What It Checks |
|---|---|---|
| `ci-data.yml` | Push/PR on `data/**`, `tests/**`, `docker/**` | ruff, black, unit tests, dbt compile, Docker builds, MinIO integration, Silver/Gold assertions, dbt run+test |
| `ci-infra.yml` | Push/PR on `terraform/**`, `ansible/**`, `monitoring/**` | terraform fmt, terraform validate, tflint, ansible-lint, promtool rules, amtool check-config |
| `cd-deploy.yml` | Push to `main` on `docker/**`, `data/ingestion/**` | Builds and pushes versioned images to ghcr.io |

Pinned linting versions:

```text
ruff==0.15.10
black==26.3.1
```

## Known Limitations

- **OpenAQ v2 historical data:** The `/v2/measurements` endpoint returns HTTP 410
  for dates older than the platform's retention window. Integration tests and CI
  skip OpenAQ gracefully when this occurs. No synthetic fallback data is used.
- **Local storage only:** MinIO runs on a local Vagrant VM or Docker Compose
  stack. There is no cloud storage backend or remote Delta catalog.
- **No cloud deployment:** Terraform and Ansible target a local VirtualBox VM.
  No cloud provider is provisioned.
- **node_exporter:** Prometheus scrapes `192.168.56.10:9100` (the Vagrant VM).
  This target shows as DOWN in local compose runs where the VM is not active.
  This is expected and does not affect pipeline operation.
- **Alertmanager receivers:** Webhook URLs in `alertmanager.yml` are
  `localhost:5001` placeholders. Real endpoints must be configured before alerts
  are actionable.
- **python:3.11-slim Dockerfiles:** The base image tracks Python 3.11.x patch
  releases. Full digest pinning is deferred to the v1.0.0 release.

## Design Rules

- Real APIs only. No synthetic data fallbacks.
- Delta writes use `engine="rust"` when `schema_mode="merge"` is set.
- Pushgateway pushes are best-effort and wrapped in `try/except`.
- Bronze idempotency and Silver incremental processing are partition-based.
- Canonical Silver schema and source labels (`openmeteo`, `openaq`) are frozen.
- Commits are clean: no AI co-author lines.

## Acknowledgements

Data sources:

- [Open-Meteo](https://open-meteo.com/) Air Quality API, backed by CAMS model data
- [OpenAQ](https://openaq.org/) open air quality data platform

This project was built with assistance from Claude (Anthropic) as an AI pair
programmer. Claude contributed to architecture decisions, implementation, and
code review across multiple sessions.
