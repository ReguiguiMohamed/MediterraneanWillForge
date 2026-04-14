# Mediterranean Ops Fortress

Mediterranean Ops Fortress is a hybrid Data Engineering and DevOps portfolio
project for real Mediterranean air quality data. It ingests public API data,
stores it in a Delta Lake medallion architecture on MinIO, validates quality,
builds analytics marts, and exposes pipeline and infrastructure health through
Prometheus, Alertmanager, and Grafana.

This is not a tutorial scaffold. The stack is designed to behave like a small
production data platform: reproducible infrastructure, incremental data writes,
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

```text
Open-Meteo API --------\
                       +--> Bronze Delta --> Silver Delta --> Gold Delta
OpenAQ v2 API --------/
                              |
                              v
                       MinIO object storage

Pipeline jobs --> Pushgateway --> Prometheus --> Grafana
                                    |
                                    v
                              Alertmanager

Vagrant + Terraform + Ansible provision the local Docker-based stack.
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
| Infrastructure | Terraform, `kreuzwerker/docker` provider |
| Configuration | Ansible roles for common setup, Docker, MinIO, Prometheus, Alertmanager, Grafana |
| Storage | MinIO, Delta Lake, delta-rs |
| Data pipeline | Python, pandas, pyarrow, deltalake, requests |
| Transformation | Bronze, Silver, Gold Python jobs plus dbt models over Silver |
| Quality | Great Expectations style checks, Soda check definitions, custom runner |
| Observability | Prometheus, Pushgateway, Alertmanager, Grafana |
| CI/CD | GitHub Actions for data pipeline, infrastructure validation, and image publishing |

## Repository Layout

```text
mediterranean-ops-fortress/
|-- .github/workflows/
|   |-- ci-data.yml
|   |-- ci-infra.yml
|   `-- cd-deploy.yml
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
|   `-- integration/
|-- vagrant/
|-- .env.example
|-- Makefile
`-- README.md
```

The Open-Meteo module is currently named `copernicus_ingestor.py` because the
source is CAMS-backed model data exposed through Open-Meteo. The persisted
source label and storage path are `openmeteo`.

## Quick Start

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| VirtualBox | 7.x | VM hypervisor |
| Vagrant | 2.4+ | Reproducible local VM |
| Docker | 24+ | Containers for local or compose-based runs |
| Python | 3.11 | Local tests and development |

### Configure

```bash
git clone https://github.com/ReguiguiMohamed/mediterranean-ops-fortress.git
cd mediterranean-ops-fortress
cp .env.example .env
```

No data API keys are required for the current ingestion sources.

### Start The Full VM Stack

```bash
make up
make healthcheck
```

### Run The Pipeline

```bash
make ingest-bronze
make ingest-silver
make ingest-gold
```

Or run the full pipeline:

```bash
make ingest
```

### Run Tests And Linters

```bash
ruff check data/ tests/
black --check data/ tests/
pytest tests/unit/ -v
```

Integration tests require a live MinIO endpoint. OpenAQ v2 may return HTTP 410
for historical `/v2/measurements` requests; the integration tests skip cleanly
when that upstream behavior leaves no OpenAQ table to assert on.

## Docker Compose Alternative

For local work without Vagrant:

```bash
make monitoring-up
make ingest
```

The compose stack runs MinIO, Prometheus, Pushgateway, Alertmanager, Grafana,
and profile-gated pipeline jobs.

## Infrastructure

Terraform is organized into three modules:

| Module | Responsibility |
|---|---|
| `networking` | Docker bridge network |
| `storage` | MinIO container, volume, and bucket initialization |
| `compute` | Prometheus, Pushgateway, Grafana, and Alertmanager |

Ansible configures the VM with six roles:

```text
common -> docker -> minio -> prometheus -> alertmanager -> grafana
```

All observability containers join the shared `med-ops-net` network so scrape
targets resolve by service name.

## Observability

Pipeline jobs push batch metrics to Pushgateway:

| Metric | Purpose |
|---|---|
| `pipeline_ingested_rows{layer, source}` | Row count written per run |
| `pipeline_last_successful_run_timestamp{layer}` | Freshness tracking |
| `pipeline_duration_seconds{stage}` | Runtime per pipeline stage |
| `pipeline_quality_check_failures_total{check_name, layer}` | Failed data quality gates |
| `pipeline_schema_drift_events_total{table}` | Schema evolution events |

Prometheus rules cover stale layers, quality failures, slow pipeline stages,
schema drift, missing metrics, and infrastructure health. Grafana dashboards
track pipeline freshness, row counts, duration, and host/container status.

## CI/CD

| Workflow | Purpose |
|---|---|
| `ci-data.yml` | `ruff`, `black --check`, unit tests, dbt compile, Docker image builds, MinIO-backed integration tests, Silver/Gold/dbt checks |
| `ci-infra.yml` | Terraform formatting and validation, Ansible linting, Prometheus config and rule validation |
| `cd-deploy.yml` | Image publishing and deployment workflow |

Pinned Python formatting tools:

```text
ruff==0.15.10
black==26.3.1
```

## Design Rules

- Use real APIs only. Do not add synthetic data fallbacks.
- Keep Delta writes on `engine="rust"` when using `schema_mode="merge"`.
- Treat Pushgateway pushes as best-effort and wrap them in `try/except`.
- Keep Bronze idempotency and Silver incremental processing partition-based.
- Preserve the canonical Silver schema and source labels: `openmeteo`, `openaq`.
- Keep commits clean: no AI co-author lines.

## Acknowledgements

Data sources:

- Open-Meteo Air Quality API, backed by CAMS model data
- OpenAQ open air quality data platform

