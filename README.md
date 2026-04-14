# MediterraneanWillForge

This platform is  built to ingest, transform, and monitor real Mediterranean climate resilience data. Air quality readings, temperature extremes, and wildfire risk indicators pulled daily from Copernicus CAMS and EEA Discomap APIs — structured into a proper lakehouse, observed end-to-end, and deployed with zero manual steps.

This is not a tutorial project. Every tool earns its place. Every metric is intentional.

---

## What This Platform Does

Real data flows through three disciplines simultaneously:

**Data Engineering:** Raw API responses land in Bronze. Cleaning, typing, and WHO threshold flagging happen in Silver. Daily summaries and wildfire risk indices are built in Gold. The Medallion architecture is not decoration — it enforces separation of concerns and makes every transformation auditable.

**Infrastructure as Code:** The entire environment — from the Vagrant VM to Docker networks to MinIO buckets — is declared in Terraform and configured by Ansible. `vagrant up` is the only manual step. Everything else is automated.

**Full Observability:** Every pipeline run pushes metrics to Prometheus via Pushgateway. Freshness, row counts, quality gate failures, schema drift, stage duration — all visible in Grafana dashboards before the next day's run begins. Alertmanager fires when something breaks.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Vagrant VM (Ubuntu 22.04)                │
│                                                          │
│   Terraform ──► Docker Network ──► MinIO (S3-compat.)    │
│   Ansible   ──► Prometheus  ──► Grafana                  │
│                 Pushgateway ──► Alertmanager              │
│                                                          │
│   ┌─────────────────────────────────────────────────┐   │
│   │              Medallion Lakehouse (Delta Lake)    │   │
│   │  Bronze (raw) ──► Silver (clean) ──► Gold (mart)│   │
│   └─────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### Data Sources

| Source | What We Pull | Cadence |
|--------|-------------|---------|
| [Copernicus CAMS](https://ads.atmosphere.copernicus.eu) | PM2.5, PM10, NO2, O3 reanalysis — Mediterranean bounding box | Daily |
| [EEA Discomap](https://discomap.eea.europa.eu) | Near-real-time AirBase observations — TN, DZ, MA, LY, EG | Daily |

### Medallion Layers

| Layer  | Storage Path                          | Description |
|--------|---------------------------------------|-------------|
| Bronze | `s3://bronze/copernicus/air_quality`  | Raw API records, append-only, incremental by partition date |
| Bronze | `s3://bronze/eea/air_quality`         | Raw EEA observations per country |
| Silver | `s3://silver/air_quality`             | Cleaned, typed, AQI-categorised, WHO exceedance flagged |
| Gold   | `s3://gold/daily_country_summary`     | Daily mean/max per station + WHO exceedance % |
| Gold   | `s3://gold/wildfire_risk_index`       | Composite O3+PM2.5 risk index per station/day |

---

## Technical Stack

| Domain | Tool | Role |
|--------|------|------|
| **Local environment** | Vagrant + VirtualBox | Reproducible Ubuntu 22.04 VM |
| **Infrastructure** | Terraform | Provision Docker networks, volumes, containers |
| **Configuration** | Ansible | Install Docker, deploy services, template configs |
| **Containerisation** | Docker + Docker Compose | Isolated ingestion and quality job images |
| **Lakehouse storage** | MinIO + Delta Lake | S3-compatible object store + ACID table format |
| **Ingestion** | Python, `deltalake`, `cdsapi` | Copernicus and EEA API clients |
| **Data quality** | Great Expectations + Soda | Completeness, freshness, range, schema checks |
| **dbt** | dbt-core | Staging views and analytics marts over Silver |
| **Metrics** | Prometheus + Pushgateway | Pipeline metrics + infrastructure metrics |
| **Dashboards** | Grafana | Pipeline health + infrastructure overview boards |
| **Alerting** | Alertmanager | Routed alerts for staleness, quality failures, infra down |
| **CI/CD** | GitHub Actions | Lint, test, build, validate, and deploy on every push |

---

## Project Structure

```
mediterranean-ops-fortress/
│
├── .github/workflows/
│   ├── ci-data.yml          # Lint, unit tests, Docker image builds, integration tests
│   ├── ci-infra.yml         # Terraform validate, ansible-lint, promtool config check
│   └── cd-deploy.yml        # Apply Terraform + run full pipeline on main merge
│
├── ansible/
│   ├── inventory/hosts.ini  # Target host definitions
│   ├── site.yml             # Master playbook — applies all roles in order
│   └── roles/
│       ├── common/          # OS hardening, system packages, medops user
│       ├── docker/          # Docker Engine + Compose plugin
│       ├── minio/           # MinIO container + health wait
│       ├── prometheus/      # Prometheus + Pushgateway + alert rules
│       ├── alertmanager/    # Alertmanager with routing config
│       └── grafana/         # Grafana with provisioned datasources + dashboards
│
├── data/
│   ├── ingestion/
│   │   ├── bronze/
│   │   │   ├── copernicus_ingestor.py   # CAMS daily pull → Delta Bronze
│   │   │   └── eea_ingestor.py          # EEA daily pull → Delta Bronze
│   │   ├── silver/
│   │   │   └── transformer.py           # Clean, type, flag → Delta Silver
│   │   └── gold/
│   │       └── marts.py                 # Aggregate → Delta Gold marts
│   ├── quality/
│   │   └── checks/soda_checks.yml       # Soda quality checks on Bronze
│   ├── dbt/
│   │   ├── models/staging/              # stg_air_quality view over Silver
│   │   └── models/marts/               # mart_daily_air_quality table
│   └── schemas/
│       └── bronze_air_quality.json      # JSON Schema for Bronze records
│
├── docker/
│   ├── ingestion/Dockerfile             # Lean Python 3.11 ingestion image
│   ├── ingestion/requirements.txt       # Pinned Python dependencies
│   ├── quality/Dockerfile               # Great Expectations quality runner image
│   └── docker-compose.yml              # Full stack: MinIO + observability + jobs
│
├── monitoring/
│   ├── prometheus/
│   │   ├── prometheus.yml               # Scrape config (Prom, Pushgateway, MinIO, cAdvisor)
│   │   └── rules/
│   │       ├── infra_alerts.yml         # Container down, CPU/memory/disk alerts
│   │       └── pipeline_alerts.yml      # Staleness, row drops, quality failures, schema drift
│   ├── grafana/
│   │   ├── dashboards/
│   │   │   ├── pipeline_health.json     # Freshness stats, row counts, duration, drift
│   │   │   └── infra_overview.json      # Service uptime, CPU, memory, disk I/O
│   │   └── provisioning/               # Auto-loaded datasources + dashboard config
│   └── alertmanager/
│       └── alertmanager.yml             # Route: default / critical / pipeline_team
│
├── terraform/
│   ├── main.tf                          # Orchestrates all modules
│   ├── variables.tf / outputs.tf        # Root variable + output declarations
│   ├── versions.tf                      # Provider version pins
│   └── modules/
│       ├── networking/                  # Docker bridge network
│       ├── storage/                     # MinIO container + volume + bucket init
│       └── compute/                     # Prometheus, Grafana, Alertmanager, Pushgateway
│
├── vagrant/
│   ├── Vagrantfile                      # Ubuntu 22.04, 4 CPU / 6 GB, port forwards
│   └── scripts/bootstrap.sh            # Installs Ansible on the guest VM
│
├── tests/
│   ├── unit/test_transformers.py        # Silver cleaning logic, AQI categorisation
│   └── integration/test_pipeline_e2e.py # Full Bronze write + idempotency vs live MinIO
│
├── scripts/
│   ├── setup.sh                         # Bootstrap dev workstation (Terraform, Ansible, pip)
│   └── healthcheck.sh                   # Curl all service endpoints; exits 1 if any down
│
├── docs/
│   ├── architecture.md                  # System diagram + component explanations
│   └── adr/001-lakehouse-format.md     # Delta Lake vs Iceberg decision record
│
├── .env.example                         # All environment variables with safe defaults
├── .gitignore                           # Python, Terraform, Vagrant, secrets, .claude/
├── Makefile                             # Single-command interface for every operation
└── README.md                            # You are here
```

---

## Quick Start

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| VirtualBox | 7.x | Vagrant VM hypervisor |
| Vagrant | 2.4+ | Reproducible VM provisioning |
| Git | any | Clone the repository |
| (Optional) Docker | 24+ | Run compose stack directly without Vagrant |

### 1. Clone and configure

```bash
git clone https://github.com/ReguiguiMohamed/mediterranean-ops-fortress.git
cd mediterranean-ops-fortress
cp .env.example .env
# Edit .env — add CAMS_API_KEY if you have one (synthetic data used otherwise)
```

### 2. Start the fortress

```bash
make up
# Provisions the Vagrant VM, installs Docker, deploys MinIO + observability stack
# via Ansible. Takes ~5 minutes on first run.
```

### 3. Verify all services are up

```bash
make healthcheck
```

Expected output:
```
  [OK]  MinIO API       →  http://localhost:9000/minio/health/live  (HTTP 200)
  [OK]  MinIO Console   →  http://localhost:9001                    (HTTP 200)
  [OK]  Prometheus      →  http://localhost:9090/-/healthy          (HTTP 200)
  [OK]  Pushgateway     →  http://localhost:9091/-/healthy          (HTTP 200)
  [OK]  Grafana         →  http://localhost:3000/api/health         (HTTP 200)
  [OK]  Alertmanager    →  http://localhost:9093/-/healthy          (HTTP 200)
  Results: 6 passed, 0 failed
```

### 4. Run the data pipeline

```bash
make ingest          # Bronze → Silver → Gold (full pipeline)
# Or run stages individually:
make ingest-bronze   # Copernicus + EEA → Bronze Delta tables
make ingest-silver   # Bronze → cleaned Silver
make ingest-gold     # Silver → Gold marts
```

### 5. Open the dashboards

| Dashboard | URL |
|-----------|-----|
| Grafana (Pipeline Health) | http://localhost:3000 |
| MinIO Console | http://localhost:9001 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |

Default Grafana credentials: `admin` / `fortress`

---

## Alternative: Docker Compose (no Vagrant)

If you have Docker running locally and want to skip the VM:

```bash
# Start the full observability + storage stack
make monitoring-up

# Run pipeline jobs
make ingest
```

---

## Infrastructure Management

```bash
make tf-init     # Initialise Terraform providers
make tf-plan     # Preview infrastructure changes
make tf-apply    # Apply infrastructure (provisions containers/volumes)
make provision   # Re-run Ansible site playbook
```

---

## Data Quality

```bash
make quality     # Run Soda + Great Expectations checks against Bronze layer
make dbt-run     # Build dbt staging views and Gold marts
make dbt-test    # Run dbt schema + data tests
```

---

## Testing

```bash
make test         # Full test suite (unit + integration)
make test-unit    # Unit tests only (no external dependencies)
make lint         # ruff + black check
```

CI runs all of these on every push. Infrastructure CI validates Terraform syntax, Ansible lint, and Prometheus config on every infra-touching PR.

---

## Observability

### Pipeline Metrics (via Pushgateway)

Every pipeline stage pushes to Prometheus Pushgateway immediately after completion:

| Metric | Description |
|--------|-------------|
| `pipeline_ingested_rows{layer, source}` | Row count written per run |
| `pipeline_last_successful_run_timestamp{layer}` | Unix timestamp — used for freshness alerts |
| `pipeline_duration_seconds{stage}` | Wall-clock seconds per stage |
| `pipeline_quality_check_failures_total{check_name, layer}` | Failed quality gates |
| `pipeline_schema_drift_events_total{table}` | Schema evolution events |

### Alert Rules

**Infrastructure alerts:** container down, CPU >80%, memory >85%, disk >90%, MinIO unreachable.

**Pipeline alerts:** Bronze/Silver layer stale (>25h), row count drop >50% vs yesterday, quality check failures, pipeline stage >30 min, schema drift detected.

### Grafana Dashboards

Two provisioned dashboards loaded automatically on container start:

- **Pipeline Health** — freshness indicators, row counts by layer, stage duration trends, quality failures
- **Infrastructure Overview** — service uptime grid, host CPU/memory/disk, container resource usage by service

---

## Design Principles

**No half-measures.** Every component is functional and observed — not included for visual completeness.

**Incremental by default.** Bronze ingestion checks for existing partitions and skips. Silver processes only Bronze partitions not yet reflected in Silver. Nothing is reprocessed without intent.

**Schema evolution without breakage.** Delta Lake's `schema_mode="merge"` absorbs upstream API changes. Drift is logged as a Prometheus metric and surfaced as an alert.

**Idempotent infrastructure.** Terraform resources are declared, not scripted. Ansible roles are idempotent. `vagrant provision` is safe to run twice.

**Local first, cloud-ready.** The entire platform runs on a single laptop. No cloud account required. The ingestors fall back to reproducible synthetic data when API credentials are absent, so CI and local development work without secrets.

---

## Environment Variables

All configuration lives in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMS_API_KEY` | — | Copernicus ADS API key. Leave blank to use synthetic data. |
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO S3 API URL |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO root user |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO root password |
| `PROMETHEUS_PUSHGATEWAY_URL` | `http://localhost:9091` | Where pipeline metrics are pushed |
| `PIPELINE_ENV` | `local` | Environment label on all metrics |

---

## CI/CD Workflows

| Workflow | Triggers | What It Does |
|----------|----------|--------------|
| `ci-data.yml` | Push/PR touching `data/` or `tests/` | Lint → unit tests → Docker image builds → integration tests with live MinIO service |
| `ci-infra.yml` | Push/PR touching `terraform/` or `ansible/` | `terraform fmt` → `terraform validate` → `tflint` → `ansible-lint` → `promtool check config` |
| `cd-deploy.yml` | Push to `main` or manual trigger | `terraform apply` → bronze ingest → silver transform → quality checks |

---

## Acknowledgements

Data provided by:
- [Copernicus Atmosphere Monitoring Service (CAMS)](https://atmosphere.copernicus.eu) — European Centre for Medium-Range Weather Forecasts (ECMWF)
- [European Environment Agency (EEA)](https://www.eea.europa.eu) — AirBase / E1a air quality data

---

*Built for Mediterranean climate resilience — with discipline and clarity, and no fragile infrastructure.*
