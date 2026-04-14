# Architecture - Mediterranean Ops Fortress

## System Overview

Mediterranean Ops Fortress combines reproducible local infrastructure, a Delta
Lake medallion data pipeline, and full pipeline observability. The current data
domain is real Mediterranean air quality, sourced from public APIs only.

```text
Developer workstation
|
`-- Vagrant VM (Ubuntu 22.04, 192.168.56.10)
    |
    |-- Terraform
    |   |-- Docker network
    |   |-- MinIO buckets and storage
    |   `-- Observability containers
    |
    |-- Ansible
    |   |-- common host setup
    |   |-- Docker Engine
    |   |-- MinIO
    |   |-- Prometheus + Pushgateway
    |   |-- Alertmanager
    |   `-- Grafana
    |
    `-- Docker services
        |-- minio
        |-- prometheus
        |-- pushgateway
        |-- alertmanager
        `-- grafana
```

## Data Flow

```text
Open-Meteo Air Quality API
  CAMS-backed gridded PM2.5, PM10, NO2, O3 for 12 cities
        |
        v
Bronze Delta tables on MinIO
        |
        v
Silver canonical air_quality table
  cleaning, typing, WHO 2021 exceedance flags, AQI category,
  data completeness, canonical column names
        |
        v
Gold Delta marts
  daily_country_summary and wildfire_risk_index

OpenAQ v2 API
  station observations for TN, DZ, MA, LY, EG, TR, GR, ES, IT, LB
        |
        `-- same Bronze -> Silver -> Gold path
```

Every pipeline stage pushes metrics to Pushgateway. Prometheus scrapes
Pushgateway, infrastructure targets, MinIO, and alert rules. Grafana visualizes
freshness, row counts, quality failures, stage duration, and infrastructure
health.

## Medallion Architecture

| Layer | Location | Content | Format |
|---|---|---|---|
| Bronze | `s3://bronze/openmeteo/air_quality` | Open-Meteo CAMS-backed gridded records, append-only and partitioned by date | Delta Lake |
| Bronze | `s3://bronze/openaq/air_quality` | OpenAQ station observations, append-only and partitioned by date | Delta Lake |
| Silver | `s3://silver/air_quality` | Canonical cleaned air quality rows with WHO flags, AQI category, completeness, source, and partition date | Delta Lake |
| Gold | `s3://gold/daily_country_summary` | Daily country/source pollutant aggregates and WHO exceedance percentages | Delta Lake |
| Gold | `s3://gold/wildfire_risk_index` | Composite O3 plus PM2.5 risk index per station and day | Delta Lake |

## Canonical Silver Schema

All downstream code expects these columns:

```text
station_id, station_name, city, country_code, latitude, longitude, date,
pm2_5, pm10, nitrogen_dioxide, ozone, aqi_category,
who_pm25_exceed, who_pm10_exceed, who_no2_exceed, who_o3_exceed,
data_completeness, source, silver_ts, partition_date
```

Column names intentionally use `pm2_5`, `nitrogen_dioxide`, and `ozone`.
Downstream code should not introduce aliases such as `pm2p5`, `no2`, or `o3`.

## Infrastructure Layers

### Vagrant

A single Ubuntu 22.04 VM hosts the local stack. Ports are forwarded to the host
for MinIO, Prometheus, Pushgateway, Grafana, and Alertmanager.

### Terraform

Terraform is split into three modules:

| Module | Responsibility |
|---|---|
| `networking` | Docker bridge network |
| `storage` | MinIO container, volume, and bucket creation |
| `compute` | Prometheus, Pushgateway, Grafana, and Alertmanager containers |

The project uses the `kreuzwerker/docker` provider, with provider declarations
inside each module to avoid fallback to the nonexistent `hashicorp/docker`
provider.

### Ansible

Six roles configure the VM:

```text
common -> docker -> minio -> prometheus -> alertmanager -> grafana
```

Containers join the shared `med-ops-net` Docker network so Prometheus can reach
targets such as `alertmanager:9093`, `pushgateway:9091`, and `minio:9000`.

### Docker Compose

Compose provides a local alternative for running MinIO and the observability
stack without Vagrant. Pipeline jobs are profile-gated and run against the same
MinIO and Pushgateway services.

## Observability Model

```text
Pipeline jobs -> Pushgateway -> Prometheus -> Grafana
Infrastructure targets ------^          |
                                        v
                                  Alertmanager
```

Custom pipeline metrics include:

| Metric | Purpose |
|---|---|
| `pipeline_ingested_rows{layer, source}` | Row counts per pipeline run |
| `pipeline_last_successful_run_timestamp{layer}` | Freshness tracking |
| `pipeline_duration_seconds{stage}` | Stage latency |
| `pipeline_quality_check_failures_total{check_name, layer}` | Quality gate failures |
| `pipeline_schema_drift_events_total{table}` | Schema evolution events |

Alert rules cover stale Bronze/Silver/Gold layers, stale quality checks,
quality failures, slow pipeline stages, schema drift, and infrastructure health.

## CI/CD

| Workflow | Responsibility |
|---|---|
| `ci-data.yml` | Python linting, formatting check, unit tests, Docker image builds, MinIO-backed integration tests, Silver/Gold/dbt checks |
| `ci-infra.yml` | Terraform formatting and validation, Ansible linting, Prometheus config and rule checks |
| `cd-deploy.yml` | Image publishing and deployment workflow on `main` |

The data pipeline CI uses real APIs. OpenAQ v2 may return HTTP 410 for historical
measurement queries; tests skip cleanly when that upstream behavior leaves no
OpenAQ Bronze table to inspect.
