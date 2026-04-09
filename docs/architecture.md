# Architecture — Mediterranean Ops Fortress

## System Overview

The platform is built on three pillars that reinforce each other: **reproducible infrastructure**, **disciplined data engineering**, and **total observability**. Every component earns its place. Nothing is included for appearance.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Developer Workstation                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Vagrant VM  (192.168.56.10)               │  │
│  │                                                               │  │
│  │   ┌────────────────┐   ┌──────────────────────────────────┐  │  │
│  │   │   Terraform    │   │          Docker Network           │  │  │
│  │   │   (provision)  │   │  ┌──────┐ ┌──────┐ ┌─────────┐  │  │  │
│  │   └───────┬────────┘   │  │MinIO │ │Prom. │ │Grafana  │  │  │  │
│  │           │             │  └──────┘ └──────┘ └─────────┘  │  │  │
│  │   ┌───────▼────────┐   │  ┌──────────────┐ ┌──────────┐  │  │  │
│  │   │    Ansible     │   │  │ Alertmanager │ │Pushgatew.│  │  │  │
│  │   │  (configure)   │   │  └──────────────┘ └──────────┘  │  │  │
│  │   └────────────────┘   └──────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         Data Flow                                   │
│                                                                     │
│  Copernicus CAMS API ──┐                                            │
│                        ├──► Bronze (Delta/MinIO) ──► Silver ──► Gold│
│  EEA Discomap API ─────┘         ▲                    ▲             │
│                              raw + schema          cleaned +        │
│                              partitioned           typed + AQI      │
│                                                                     │
│  Every write pushes metrics → Pushgateway → Prometheus → Grafana    │
└─────────────────────────────────────────────────────────────────────┘
```

## Medallion Architecture

| Layer  | Location                           | Content                                          | Format       |
|--------|------------------------------------|--------------------------------------------------|--------------|
| Bronze | `s3://bronze/copernicus/air_quality` | Raw API responses, append-only, schema-evolved  | Delta Lake   |
| Bronze | `s3://bronze/eea/air_quality`        | Raw EEA observations per country               | Delta Lake   |
| Silver | `s3://silver/air_quality`            | Cleaned, typed, WHO-flagged, AQI-categorised   | Delta Lake   |
| Gold   | `s3://gold/daily_country_summary`    | Daily aggregates per station                   | Delta Lake   |
| Gold   | `s3://gold/wildfire_risk_index`      | Composite O3+PM2.5 risk index per station/day  | Delta Lake   |

## Infrastructure Layers

### Vagrant
Single Ubuntu 22.04 LTS VM. Hosts everything locally. Ports forwarded to host. Provisioned via shell bootstrap → Ansible.

### Terraform
Three modules: `networking` (Docker bridge), `storage` (MinIO containers + volumes + bucket init), `compute` (observability stack). State is local for the `local` environment.

### Ansible
Five roles applied in order: `common` → `docker` → `minio` → `prometheus` → `alertmanager` → `grafana`. Each role is idempotent and tested via ansible-lint in CI.

### Docker Compose
Dev-first alternative to Terraform for spinning up the full stack with a single command. Same images, same mount paths as the Terraform configuration.

## Observability Model

```
Pipeline jobs ──► Prometheus Pushgateway ──► Prometheus ──► Grafana
Infrastructure ──────────────────────────► Prometheus ──► Grafana
                                                    │
                                               Alert Rules
                                                    │
                                           Alertmanager ──► Webhooks
```

Custom metrics exposed per pipeline run:
- `pipeline_ingested_rows{layer, source}` — row counts per layer
- `pipeline_last_successful_run_timestamp{layer}` — freshness tracking
- `pipeline_duration_seconds{stage}` — latency per stage
- `pipeline_quality_check_failures_total{check_name, layer}` — quality gates
- `pipeline_schema_drift_events_total{table}` — schema evolution tracking

## CI/CD

Three GitHub Actions workflows:
1. `ci-data.yml` — lint, unit tests, build images, integration tests (with MinIO service)
2. `ci-infra.yml` — Terraform fmt/validate, ansible-lint, promtool config validation
3. `cd-deploy.yml` — triggered on `main` merge; applies Terraform then runs full pipeline
