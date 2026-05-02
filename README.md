# Mediterranean Ops Fortress

[![CI — Data Pipeline](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-data.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-data.yml)
[![CI — Infrastructure](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-infra.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/ci-infra.yml)
[![CD — Publish Images](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/cd-deploy.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/cd-deploy.yml)
[![Pipeline Run](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/pipeline-run.yml/badge.svg)](https://github.com/ReguiguiMohamed/mediterranean-ops-fortress/actions/workflows/pipeline-run.yml)

Mediterranean Ops Fortress is a hybrid Data Engineering and DevOps portfolio
project built around real Mediterranean air quality data. It ingests public API
data, writes a Delta Lake medallion on Backblaze B2, validates quality,
builds analytics Gold marts including ML-based anomaly detection, and pushes
full pipeline observability to a live Grafana Cloud dashboard via Prometheus
remote_write.

This is not a tutorial scaffold. The stack is designed to behave like a small
production data platform: real public APIs, incremental Delta writes, CI
validation, hosted metrics, alert rules, and no synthetic fallback data.

## What It Does

The platform pulls two real public data sources:

| Source | Data | Notes |
|---|---|---|
| Open-Meteo Air Quality API | CAMS-backed daily PM2.5, PM10, NO2, and O3 means for 12 Mediterranean city grid points | Free, no API key |
| OpenAQ v3 | Station observations for TN, DZ, MA, LY, EG, TR, GR, ES, IT, and LB | Free API; API key recommended (register free at openaq.org); daily aggregates via `/v3/sensors/{id}/measurements/daily` |

The pipeline writes:

| Layer | Path | Purpose |
|---|---|---|
| Bronze | `s3://bronze/openmeteo/air_quality` | Open-Meteo gridded source records, partitioned by `partition_date` |
| Bronze | `s3://bronze/openaq/air_quality` | OpenAQ station records, partitioned by `partition_date` |
| Silver | `s3://silver/air_quality` | Canonical cleaned rows with WHO flags, AQI category, completeness, source, and partition date |
| Gold | `s3://gold/daily_country_summary` | Daily country/source pollutant summaries and WHO exceedance percentages |
| Gold | `s3://gold/wildfire_risk_index` | Composite O3 + PM2.5 station risk index (0–100 score) |
| Gold | `s3://gold/anomaly_alerts` | Isolation Forest anomaly flags per station per day (PM2.5, O3, NO2 features) |

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full architecture document
with a Mermaid data flow diagram, medallion schema, infrastructure breakdown, and
observability model.

```text
Open-Meteo ----\
                +--> Bronze --> Silver --> Gold (marts + anomaly detection)
OpenAQ v3 ----/                    |
                                   +--> dbt models
        Backblaze B2 (Delta Lake / S3-compat)

Jobs --> remote_write --> Grafana Cloud (Mimir) --> Dashboards + Alerts
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
| Cloud infrastructure | Oracle Cloud (OCI) Always-Free — ARM Ampere A1 VM (Terraform + Ansible, pending provisioning) |
| Storage | Backblaze B2 (S3-compatible, `eu-central-003`), Delta Lake, delta-rs |
| Local dev storage | MinIO `RELEASE.2025-09-07T16-13-09Z` (via docker-compose override) |
| Data pipeline | Python 3.11, pandas, pyarrow, deltalake |
| Transformation | Bronze → Silver → Gold Python jobs + dbt models (DuckDB over Silver Parquet) |
| ML | scikit-learn `IsolationForest` — anomaly detection on Silver PM2.5 / O3 / NO2 |
| Quality | Soda-style checks, custom runner, Great Expectations schema validation |
| Observability | Grafana Cloud (`mohamedwillforge.grafana.net`) — Prometheus remote_write to Mimir, hosted dashboards and alert rules |
| Local observability | Prometheus `v2.51.0`, Pushgateway, Alertmanager `v0.27.0`, Grafana `10.4.2`, cAdvisor (docker-compose) |
| CI/CD | GitHub Actions (5 workflows), ghcr.io image publishing |

## Repository Layout

```text
mediterranean-ops-fortress/
|-- .github/workflows/
|   |-- ci-data.yml          # lint, unit tests, dbt compile, integration tests
|   |-- ci-infra.yml         # terraform, ansible, prometheus, alertmanager checks
|   |-- cd-deploy.yml        # ghcr.io image publishing
|   |-- pipeline-run.yml     # daily cron + manual run against real B2
|   `-- verify-secrets.yml  # lightweight credential probe (B2 + Grafana)
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
|   |   `-- gold/
|   |       |-- marts.py                 # daily_country_summary + wildfire_risk_index
|   |       `-- anomaly.py              # Isolation Forest anomaly_alerts
|   |-- metrics.py                      # Grafana Cloud remote_write helper
|   |-- quality/
|   |-- schemas/
|   `-- dbt/
|-- docker/
|   |-- docker-compose.yml           # base stack (OCI storage mode)
|   |-- docker-compose.override.yml  # local dev — adds MinIO
|   |-- ingestion/Dockerfile
|   `-- quality/Dockerfile
|-- docs/
|   |-- architecture.md
|   `-- adr/001-lakehouse-format.md
|-- grafana/
|   |-- dashboards/pipeline_observability.json  # import into Grafana Cloud UI
|   `-- alerts/pipeline_alerts.yaml             # alert rule reference (3 rules)
|-- monitoring/
|   |-- prometheus/
|   |-- alertmanager/
|   `-- grafana/
|-- terraform/
|   |-- main.tf
|   |-- versions.tf
|   |-- modules/
|   `-- environments/cloud/terraform.tfvars.example
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
| Terraform | 1.7+ | OCI infrastructure provisioning |
| Ansible | 2.14+ | VM configuration and service deployment |

### Configure

```bash
git clone https://github.com/ReguiguiMohamed/mediterranean-ops-fortress.git
cd mediterranean-ops-fortress
cp .env.example .env
```

Open-Meteo requires no API key. An OpenAQ API key is optional but recommended
(anonymous tier has very low rate limits). Register free at
[explore.openaq.org/register](https://explore.openaq.org/register) and set
`OPENAQ_API_KEY` in your `.env`.

---

### Option A — Docker Compose (local development)

Bring up the observability stack:

```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts Prometheus, Pushgateway, Alertmanager, Grafana, and cAdvisor.
All services expose healthchecks; Prometheus and Grafana wait for their
dependencies before starting.

To run pipeline jobs locally (adds a MinIO container for lakehouse storage):

```bash
# docker-compose.override.yml is merged automatically when present
docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml \
  --profile jobs up -d
```

Run the full pipeline (with local MinIO):

```bash
make ingest       # bronze (both sources) -> silver -> gold
make quality      # data quality checks
make dbt-run      # dbt models over silver
make dbt-test     # dbt schema and freshness tests
```

Access the stack:

| Service | URL |
|---|---|
| MinIO console (local dev only) | http://localhost:9001 (minioadmin / minioadmin) |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / fortress) |
| Alertmanager | http://localhost:9093 |
| Pushgateway | http://localhost:9091 |

---

### Option B — OCI Cloud Deployment

Provision the cloud VM and storage with Terraform, then configure with Ansible.
All OCI resources used fall under the [Always Free tier](https://www.oracle.com/cloud/free/).

```bash
cd terraform
cp environments/cloud/terraform.tfvars.example environments/cloud/terraform.tfvars
# fill in tenancy_ocid, user_ocid, fingerprint, private_key_path, ssh_authorized_keys
terraform init
terraform apply -var-file=environments/cloud/terraform.tfvars
```

Terraform outputs the VM public IP and S3-compatible Object Storage endpoint.

```bash
# Update ansible/inventory/hosts.ini with the VM public IP
# Update ansible/vars/common.yml with OCI storage credentials and Slack webhook URL
cd ../ansible
ansible-playbook site.yml -i inventory/hosts.ini
```

Run the pipeline against Backblaze B2:

```bash
# Fill in B2 credentials in .env (create an Application Key at backblaze.com)
export MINIO_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
export MINIO_ACCESS_KEY=<b2-key-id>
export MINIO_SECRET_KEY=<b2-application-key>
make ingest
make quality
```

Alternatively, trigger the `pipeline-run.yml` workflow from the GitHub Actions tab
(requires `B2_KEY_ID` and `B2_APP_KEY` set as repository secrets).

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

Pipeline jobs push metrics to **Grafana Cloud** (`mohamedwillforge.grafana.net`)
via Prometheus remote_write after every stage. The `data/metrics.py` helper
handles protobuf encoding, raw Snappy compression, and Basic Auth. It is a
silent no-op when `GRAFANA_*` env vars are absent, so local dev is unaffected.

| Metric | Purpose |
|---|---|
| `pipeline_ingested_rows{layer, source}` | Row count written per run |
| `pipeline_last_successful_run_timestamp{layer}` | Freshness tracking |
| `pipeline_duration_seconds{stage}` | Runtime per pipeline stage |
| `pipeline_quality_check_failures_total{check_name, layer}` | Failed data quality gates |
| `pipeline_anomaly_flags_total{model}` | Anomalous readings flagged by Isolation Forest |

**Dashboard:** `grafana/dashboards/pipeline_observability.json` — import via
Grafana Cloud UI (Dashboards → New → Import, select the `grafanacloud-mohamedwillforge-prom`
datasource when prompted).

**Alert rules** (`grafana/alerts/pipeline_alerts.yaml`) — three managed rules:
pipeline stale >25 h (critical), OpenAQ 0 rows (warning), quality failure (warning).
Create via Alerting → Alert rules → New alert rule using the PromQL expressions
in the YAML file.

Local dev retains a self-hosted stack (Prometheus, Pushgateway, Alertmanager,
Grafana, cAdvisor) via `docker/docker-compose.yml`. Alertmanager routes alerts
into three groups (`infra_critical`, `pipeline_ops`, `infra_ops`) with Slack
delivery configured via `ansible/vars/common.yml`.

## CI/CD

| Workflow | Triggers | What It Checks |
|---|---|---|
| `ci-data.yml` | Push/PR on `data/**`, `tests/**`, `docker/**` | ruff, black, unit tests, dbt compile, Docker builds, MinIO integration, Silver/Gold assertions, dbt run+test |
| `ci-infra.yml` | Push/PR on `terraform/**`, `ansible/**`, `monitoring/**` | terraform fmt, terraform validate, tflint, ansible-lint, promtool rules, amtool check-config |
| `cd-deploy.yml` | Push to `main` on `docker/**`, `data/ingestion/**` | Builds and pushes versioned images to ghcr.io |
| `pipeline-run.yml` | Daily cron (06:00 UTC) + manual (`workflow_dispatch`) | Runs Bronze → Silver → Gold → Anomaly against real B2; supports single-date and date-range backfill; requires `B2_KEY_ID`, `B2_APP_KEY`; pushes metrics to Grafana Cloud when `GRAFANA_*` secrets are set |
| `verify-secrets.yml` | Manual (`workflow_dispatch`) | Probes B2 and Grafana Cloud credentials without touching data (~30 s) |

Pinned linting versions:

```text
ruff==0.15.10
black==26.3.1
```

## Known Limitations

- **OpenAQ v3 data sparsity:** The ingestor fetches up to 50 stations per
  country (hard cap to prevent rate-limit flooding on high-density countries
  like TN/DZ/MA which have 1,000+ stations). Most OpenAQ v3 stations have no
  pre-aggregated daily data for recent dates — this is upstream sparsity, not a
  pipeline bug. A typical daily run returns ~20–50 rows from 500 sensor queries.
- **OpenAQ API key:** The `/v3/sensors/{id}/measurements/daily` endpoint works
  without a key but enforces anonymous-tier rate limits. Set `OPENAQ_API_KEY` in
  `.env` (or as a GitHub Actions secret) to use standard free-tier limits.
  Integration tests skip OpenAQ gracefully when the ingestor returns 0 rows.
- **B2 Class B (download) transaction cap:** Backblaze B2's free tier allows
  2,500 Class B transactions per day. Delta Lake checkpoints are created after
  every write (`create_checkpoint()`) so each `DeltaTable()` open costs a fixed
  2 Class B regardless of table history. A steady-state daily run uses ~50–100
  transactions. Backfills over more than ~20 dates should still be split across
  days to avoid hitting the cap. The pipeline fails fast with a 403 when the cap
  is reached — it does not silently retry or skip.
- **B2 storage auth:** The pipeline uses Backblaze B2 Application Keys for
  S3-compatible access. Create an Application Key at the Backblaze console and
  set `B2_KEY_ID` / `B2_APP_KEY` as GitHub Actions secrets before triggering
  `pipeline-run.yml`.
- **OCI VM:** The Terraform and Ansible configuration targets Oracle Cloud
  Always-Free (ARM A1 VM) but has not been provisioned (OCI account requires a
  credit card). Observability is fully covered by Grafana Cloud free tier
  (`mohamedwillforge.grafana.net`) which requires no credit card and no VM.
  The self-hosted docker-compose stack remains for local development.
- **node_exporter:** Shows as DOWN in local compose runs (no VM to scrape).
  UP in cloud deployments where node_exporter runs on the OCI VM.
- **Local dev alerting:** `monitoring/alertmanager/alertmanager.yml` uses
  `localhost:5001` stubs intentionally — local dev does not send real alerts.
  The cloud path (Ansible-generated config) uses Slack.
- **Image digest pinning:** Docker image tags are pinned to specific versions
  (including `python:3.11.15-slim`), but not to SHA-256 digests. Tag pinning
  protects against `:latest` drift; digest pinning would add supply-chain
  protection against tag rewrites and is deferred.
- **No Kubernetes, secrets manager, or production SLAs.**

## Design Rules

- Real APIs only. No synthetic data fallbacks.
- Delta writes use `engine="rust"` when `schema_mode="merge"` is set.
- `create_checkpoint()` is called after every `write_deltalake()` to keep Class B reads at O(1).
- Pushgateway and Grafana remote_write pushes are best-effort and wrapped in `try/except`.
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
