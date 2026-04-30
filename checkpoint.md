# Mediterranean Ops Fortress — Session Checkpoint

Last updated: 2026-04-29 (Iter 17 complete — session closing)

## Project Brief

Mediterranean Ops Fortress is a professional Data Engineering + DevOps
portfolio project owned by ReguiguiMohamed. The repo lives locally at:

```text
C:\Users\ahmed\Downloads\mediterranean-ops-fortress
```

GitHub repository:

```text
https://github.com/ReguiguiMohamed/MediterraneanWillForge
```

The project is a real-data Mediterranean air quality platform. It ingests
public air quality data, writes Delta Lake medallion tables on Backblaze B2
(S3-compatible, `eu-central-003`), runs quality checks, builds Gold marts, and
exposes observability through Prometheus, Pushgateway, Alertmanager, and
Grafana. Infrastructure targets OCI Always-Free (ARM A1 VM) via Terraform and
Ansible. Local dev uses MinIO via docker-compose override.

## Non-Negotiable Rules

- Use real data only. Do not introduce synthetic fallback records, dummy paths,
  fake APIs, placeholder data, or mock production outputs.
- Keep source labels canonical: `openmeteo` and `openaq`.
- Keep canonical pollutant columns: `pm2_5`, `pm10`, `nitrogen_dioxide`,
  `ozone`. Do not rename them to `pm2p5`, `no2`, or `o3`.
- Keep the Silver canonical schema:

```text
station_id, station_name, city, country_code, latitude, longitude, date,
pm2_5, pm10, nitrogen_dioxide, ozone, aqi_category,
who_pm25_exceed, who_pm10_exceed, who_no2_exceed, who_o3_exceed,
data_completeness, source, silver_ts, partition_date
```

- Delta writes that use `schema_mode="merge"` must use `engine="rust"`.
- Pushgateway pushes are best-effort only and must stay wrapped in `try/except`.
- Bronze stage metrics use `stage=f"bronze_{source_name}"`; Prometheus alert
  selectors use `stage=~"bronze_(openmeteo|openaq)"`.
- Run `ruff check data/ tests/` and `black --check data/ tests/` before commits.
  Current pinned versions are `ruff==0.15.10` and `black==26.3.1`.
- Do not add AI co-author lines to commits. The README acknowledges Claude
  separately.
- In dbt-duckdb 1.8.0, use `read_parquet()` with httpfs — not `delta_scan()`.
  Use explicit depth wildcards (no `**` glob). Use `hive_partitioning=true` to
  reconstruct partition columns excluded by `engine="rust"`.
- dbt 1.9+ generic test arguments must be nested under `arguments:` key.
- Integration tests skip OpenAQ gracefully when the ingestor returns 0 rows
  (no Bronze table created). This is expected when a date has no station data.

## Current Data Sources

| Source | Role | Notes |
|---|---|---|
| Open-Meteo Air Quality API | CAMS-backed gridded model data for 12 Mediterranean cities | Free, no API key |
| OpenAQ v3 | Station observations for 10 Mediterranean / North African countries | Free API; `OPENAQ_API_KEY` recommended (anonymous tier has very low rate limits) |

Current Bronze paths (on B2 `med-ops-mohamed-bronze`):

```text
s3://med-ops-mohamed-bronze/openmeteo/air_quality
s3://med-ops-mohamed-bronze/openaq/air_quality
```

Current Silver path (on B2 `med-ops-mohamed-silver`):

```text
s3://med-ops-mohamed-silver/air_quality
```

Current Gold paths (on B2 `med-ops-mohamed-gold`):

```text
s3://med-ops-mohamed-gold/daily_country_summary
s3://med-ops-mohamed-gold/wildfire_risk_index
```

## Completed Iterations

### Iter 0 — Environment Foundation

Root scaffolding, Vagrant + bootstrap, Ansible roles (common, docker, minio,
prometheus, alertmanager, grafana), Terraform modules (networking, storage,
compute), GitHub Actions workflows (ci-data.yml, ci-infra.yml, cd-deploy.yml).

### Iter 1 — Bronze Layer

BronzeIngestor ABC, StorageConfig, CopernicusIngestor (Open-Meteo, 12 cities),
OpenAQIngestor (original v2, long→wide pivot), integration tests with MinIO.

### Iter 2 — Silver And Gold

Silver transformer (WHO 2021 flags, AQI, completeness, incremental partitions),
Gold marts (daily_country_summary, wildfire_risk_index), dbt staging and mart
models.

### Iter 3 — Quality Runner

`data/quality/run_checks.py`, Soda-style checks, Bronze schema validation,
unit tests.

### Iter 4 — Observability Alignment

Prometheus alert rules, Grafana dashboard, stage label alignment.

### Iter 5 — Infrastructure Hardening

Ansible: keyring-based Docker, shared network, node_exporter, Grafana bind-mount.
Terraform: provider declarations, null provider for MinIO buckets, abspath() locals.
CI: pinned `black==26.3.1` and `ruff==0.15.10`.

### Iter 6 — dbt Contract Hardening

Canonical columns preserved in stg/mart models, `accepted_range` macro,
45 dbt tests compiled and green (not-null, accepted values, bounds, WHO flags,
completeness, composite keys).

### Iter 7 — Alertmanager Routing

Four routing groups (infra_critical, pipeline_ops x2, infra_ops), inhibit rules,
`amtool check-config` PASS.

### Iter 8 — CI Integration Hardening

`tests/ci_verify_gold.py` added, Gold verify step in ci-data.yml, self-reference
path triggers, expanded monitoring path, `amtool check-config` step in ci-infra.

### Iter 9 — Docker Compose Profiles And Healthchecks

cadvisor service, healthchecks on all services, `condition: service_healthy`
depends_on, all image tags pinned (no `:latest` remaining).

### Iter 10 — Documentation Finalization

README badges, three quickstart paths, Known Limitations, CI/CD table updates.
architecture.md Mermaid diagram, services table, Alertmanager routing docs.

### Iter 11 — Release Polish (v1.0.0)

Pinned `python:3.11-slim` → `python:3.11.15-slim` in both Dockerfiles.
Tagged and released `v1.0.0`. CI green.

### Iter 12 — Cloud Infrastructure Switch (v1.1.0)

- Storage switched from local MinIO to Backblaze B2
  (`s3.eu-central-003.backblazeb2.com`). Three B2 buckets:
  `med-ops-mohamed-bronze`, `med-ops-mohamed-silver`, `med-ops-mohamed-gold`.
- Terraform target updated to OCI (ARM A1 VM, networking, compute, storage modules).
- Ansible minio role deleted; 5 remaining roles: common, docker, prometheus,
  alertmanager, grafana.
- `docker-compose.override.yml` added for local dev (adds MinIO, overrides
  MINIO_ENDPOINT for ingestion and quality services).
- Base `docker-compose.yml` now cloud-storage-first (no MinIO by default).
- README updated. Tagged and released `v1.1.0`.

### Iter 13 — B2 Connectivity Fix + Pipeline Run Workflow

- Created `data/storage.py` with correct uppercase delta-rs keys:
  `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
  `AWS_ALLOW_HTTP`, `AWS_S3_ALLOW_UNSAFE_RENAME`. All three layers (bronze, silver,
  gold) now import from this shared module.
- Added `AWS_REGION=eu-central-003` to `.env.example`.
- Added `.github/workflows/pipeline-run.yml` — `workflow_dispatch` trigger that
  runs Bronze (copernicus + openaq) → Silver → Gold against real B2 using
  `B2_KEY_ID` / `B2_APP_KEY` GitHub Secrets.
- Fixed `ModuleNotFoundError: No module named 'data'` in `tests/ci_verify_*.py`
  by adding `PYTHONPATH: "."` to the `integration-tests` job env in ci-data.yml.
- Pipeline confirmed end-to-end: all three B2 buckets filled with real data.

### Iter 14 — Data Foundation (cron + backfill + storage fix)

- `pipeline-run.yml`: added `schedule: cron: "0 6 * * *"` — platform now
  self-refreshes daily at 06:00 UTC without manual intervention.
- `pipeline-run.yml`: added `backfill_start` / `backfill_end` inputs. A
  `compute-dates` step resolves them into `PIPELINE_DATES`. Both Bronze steps loop
  over every date; Silver and Gold run once after all Bronze dates are loaded.
- `data/storage.py`: `AWS_ALLOW_HTTP` derived from endpoint scheme —
  `"false"` for `https://` (B2), `"true"` for `http://` (local MinIO).

### Iter 15 — Release & Portfolio Polish (v1.2.0)

- README: added fourth badge (Pipeline Run) after the three existing CI/CD badges.
- README: updated `pipeline-run.yml` row in CI/CD table to reflect daily cron and
  backfill support.
- Tagged and pushed `v1.2.0`. GitHub Release to be created manually via the
  GitHub web UI (gh CLI not installed on dev machine).

### Iter 17 — OpenAQ v3 Migration

- Full rewrite of `data/ingestion/bronze/openaq_ingestor.py`.
- Two-phase fetch: single-page `/v3/locations?country={cc}` per country (sensors
  embedded in response), then call `/v3/sensors/{id}/measurements/daily` per
  target sensor. Daily aggregation done by the API, not in Python.
- `_PARAMETER_MAP` maps v3 numeric parameter IDs to canonical column names
  (id 2 → `pm2_5`, id 1 → `pm10`, id 5 → `nitrogen_dioxide`, id 3 → `ozone`).
- `_get()` decorated with tenacity (3 attempts, exponential backoff). Returns
  `None` for 404/410; raises on 429/5xx so tenacity retries.
- `OPENAQ_API_KEY` optional env var wired through `.env.example` and
  `pipeline-run.yml` (`secrets.OPENAQ_API_KEY`). Key already set in GitHub Secrets.
- `_MAX_LOCS_PER_COUNTRY = 50` — hard cap, single page fetch, no pagination.
  TN/DZ/MA have 1000+ stations in OpenAQ; fetching all caused thousands of
  sensor API calls and permanent rate-limit loops. `parameters_id` filter is
  silently ignored by the API — the cap is the reliable guard.
- 1.2s sleep between sensor calls + 2s between countries — stays within
  60 req/min free-tier rate limit.
- Integration test docstring updated. README Known Limitations updated.
- Silver and Gold schemas unchanged.
- Pipeline verified end-to-end: green, ~34 min runtime.
  Bronze: 21 rows / 500 location×sensor queries (most stations have no data
  for a given date — expected OpenAQ v3 behaviour).

### Post-Iter-17 Hotfixes (same session)

Three commits applied to stabilise the v3 ingestor after failed pipeline runs:

1. `2bda861` — `meta.found` returns `'>1000'` string when count is capped.
   Fix: strip leading `>` before `int()` cast in `_fetch_locations`.
2. `ed983cf` — Added `parameters_id=[1,2,3,5]` filter to locations query
   (attempt to reduce volume; silently ignored by API but kept as belt-and-suspenders).
3. `59e6138` — Replaced paginated location loop with single-page cap at
   `_MAX_LOCS_PER_COUNTRY = 50`. Increased inter-sensor sleep to 1.2s.
   This is the fix that made the pipeline green.

## Current State At Session Close

Working tree: clean. Branch: `main`. Up to date with `origin/main`.

HEAD: `59e6138` — `fix(bronze): hard-cap OpenAQ locations at 50/country to prevent rate-limit flooding`

Full recent commit trail:

```text
59e6138 fix(bronze): hard-cap OpenAQ locations at 50/country to prevent rate-limit flooding
ed983cf fix(bronze): filter OpenAQ locations by target parameters to prevent rate-limit flooding
2bda861 fix(bronze): handle OpenAQ v3 meta.found '>1000' string format
02f0dab fix(bronze): cast meta.found to int — OpenAQ v3 returns it as string
5abb1e4 feat(bronze): Iter 17 — migrate OpenAQ ingestor from v2 to v3 API
76cccad docs: Iter 15 — add Pipeline Run badge, update CI/CD table for cron
5eb3b4f feat(pipeline): Iter 14 — daily cron, 30-day backfill, AWS_ALLOW_HTTP fix
```

Tags: `v1.0.0` (6e683b6), `v1.1.0` (c772500), `v1.2.0` (76cccad).
GitHub Release for v1.2.0 to be created manually.

GitHub Secrets set: `B2_KEY_ID`, `B2_APP_KEY`, `OPENAQ_API_KEY`.

30-day backfill triggered at session close (2026-04-30):
- `backfill_start: 2026-03-31`, `backfill_end: 2026-04-28`
- Expected runtime: ~16 hours (29 dates × ~34 min each)
- Check Actions tab on resume — if green, Gold marts have 30 days of history.

## Implementation Path Forward

All work continues under the same non-negotiable rules: real data only, no
synthetic fallbacks, ruff + black before every commit, no co-author lines.

---

### Immediate — 30-Day Backfill

Now that the daily cron and v3 ingestor are stable, run a backfill to give
the Gold marts meaningful historical depth. Trigger **Pipeline — Full Run**
from the Actions tab with:

```
backfill_start: 2026-03-30
backfill_end:   2026-04-27
```

(Use yesterday's date minus 30 days. Adjust to whatever the current date is.)
Bronze is idempotent so re-running existing dates is safe. Silver and Gold
run once after all Bronze dates are loaded.

---

### Iter 16 — Infrastructure Live (OCI VM)

**Goal:** Make the observability stack real, not theoretical.

**Blocked by:** OCI account credit card requirement (when available).

**Items:**

1. Run `terraform apply` against OCI to provision the ARM A1 VM, VCN, and
   storage bucket.
2. Run `ansible-playbook site.yml` to configure Docker, Prometheus, Alertmanager,
   and Grafana on the VM.
3. Configure Slack webhook: add real webhook URL to `ansible/vars/common.yml`.
   Alertmanager routing is already wired — this is one variable.
4. Point `docker/docker-compose.yml` PROMETHEUS_PUSHGATEWAY_URL at the VM's
   public IP. Pipeline runs (from GH Actions) will push metrics to it.
5. Verify Grafana dashboards show live pipeline metrics after a scheduled run.

---

### Future Phases (not ordered, not committed)

- **Streaming ingestion path:** Kafka + Flink or Delta Lake streaming writes
  direct from API webhooks. Complements the existing batch Bronze layer.
- **ML anomaly detection:** Isolation Forest or LSTM on Silver PM2.5 / O3 time
  series. Natural fit for the wildfire_risk_index Gold mart.
- **Extended Grafana dashboards:** Per-city drill-down, seasonal trend overlays,
  WHO exceedance heatmaps by country.
- **Cost tracking:** B2 egress and storage costs via Backblaze billing API or
  manual monitoring.
- **Additional sources:** WAQI, Copernicus ADS historical reanalysis,
  Sentinel-5P satellite data.
- **Spark / Databricks Gold layer:** PySpark implementation of Gold marts to
  demonstrate multi-engine Delta Lake compatibility.

---

## Watchouts For Future Agents

- Do not rename source labels or partition columns casually. Many downstream
  tests, dbt models, dashboards, and alerts depend on them.
- Do not read partition dates from `to_pandas()["partition_date"]` in incremental
  logic. Use `dt.files()` path parsing instead — `engine="rust"` excludes
  partition columns from Parquet data files.
- Do not swallow pipeline write failures. Best-effort applies to metrics pushes,
  not data writes.
- Do not add synthetic fallback behavior to make tests pass. Tests must adapt to
  real upstream behavior.
- `copernicus_ingestor.py` ingests Open-Meteo Air Quality, not Copernicus ADS
  directly. The name is historical. Renaming requires coordinated changes to
  Dockerfile CMD, Makefile, tests, and workflow steps.
- `promtool check config prometheus.yml` fails locally on Windows — expected
  (container-internal absolute paths). Use `promtool check rules` per file and
  `amtool check-config` for Alertmanager. Both pass.
- CI integration tests use local MinIO (`http://localhost:9000`, `minioadmin`).
  They never touch real B2. `pipeline-run.yml` is the only workflow that writes
  to B2 and requires `B2_KEY_ID` / `B2_APP_KEY` / `OPENAQ_API_KEY` secrets.
- OpenAQ v3 `meta.found` can be `'>1000'` (a capped string) when count exceeds
  the API cap. The `lstrip('>')` + `int()` cast in `_fetch_locations` handles
  this. Do not simplify to `int()` alone.
- OpenAQ v3 `_get()` returns `None` for HTTP 404/410 (no data for that sensor/date).
  `_fetch_daily_value` handles `None` gracefully — do not change this to raise.
- `_MAX_LOCS_PER_COUNTRY = 50` is a hard cap, not a soft hint. TN/DZ/MA have
  1000+ stations in OpenAQ. Without this cap the pipeline never finishes.
  Do not raise it without benchmarking runtime first.
- `parameters_id` filter on `/v3/locations` is silently ignored by the OpenAQ
  API — it returns all stations regardless. The cap is the real guard.
- Low Bronze row count from OpenAQ (e.g. 21 rows / 500 queries) is expected.
  Most OpenAQ v3 stations have no pre-aggregated daily data for recent dates.
  This is upstream data sparsity, not a bug.
- dbt models use `read_parquet()` + `hive_partitioning=true` (not `delta_scan()`).
  Do not switch to delta_scan — it falls through to EC2 IMDS on GitHub Actions
  and hangs regardless of env vars.
- `AWS_ALLOW_HTTP` in `data/storage.py` is derived from the endpoint scheme —
  `"false"` for `https://` (B2 cloud), `"true"` for `http://` (local MinIO). Do
  not hardcode it.

---

## Pass-The-Torch Prompt

Copy the block below verbatim as the opening message for the next session.

---

You are continuing **Mediterranean Ops Fortress**, a professional Data
Engineering + DevOps portfolio project owned by **ReguiguiMohamed** (GitHub).

**Repo (local):** `C:\Users\ahmed\Downloads\mediterranean-ops-fortress`
**Repo (GitHub):** `https://github.com/ReguiguiMohamed/MediterraneanWillForge`
**Branch:** `main`
**HEAD commit:** `02f0dab` — `fix(bronze): cast meta.found to int — OpenAQ v3 returns it as string`

Your first action: read `checkpoint.md` in the repo root fully before touching
anything. It contains the complete project state, non-negotiable rules,
canonical schema, known environment notes, and the next iteration scope.

**Project summary:**
A real-data Mediterranean air quality platform. Two public API sources
(Open-Meteo Air Quality + OpenAQ v3), Delta Lake medallion on Backblaze B2
(Bronze → Silver → Gold), dbt models over Silver, data quality runner,
Prometheus + Pushgateway + Alertmanager + Grafana observability, OCI
Terraform + Ansible infrastructure, GitHub Actions CI/CD (4 workflows).

**Non-negotiable rules (abbreviated — full list in checkpoint.md):**
- Real data only. No synthetic fallbacks. No dummy paths.
- Canonical source labels: `openmeteo`, `openaq`.
- Canonical pollutant columns: `pm2_5`, `pm10`, `nitrogen_dioxide`, `ozone`.
- `ruff check data/ tests/` and `black --check data/ tests/` before every commit.
- `ruff==0.15.10`, `black==26.3.1`.
- No AI co-author lines in commits.
- `engine="rust"` on all `write_deltalake` calls with `schema_mode="merge"`.
- `push_to_gateway` always wrapped in `try/except`.

**What was completed:**
- Iters 0–15 and Iter 17 fully done and committed.
- Storage: Backblaze B2 (`eu-central-003`). Three live buckets with real data.
- `pipeline-run.yml` fires daily at 06:00 UTC and supports backfill via
  `backfill_start` / `backfill_end` inputs.
- OpenAQ ingestor migrated to v3 API (two-phase: locations + sensor daily values).
- GitHub Secrets set: `B2_KEY_ID`, `B2_APP_KEY`, `OPENAQ_API_KEY`.
- Tags: `v1.0.0`, `v1.1.0`, `v1.2.0`.

**First thing to check on resume:**
Verify the Pipeline — Full Run triggered at end of last session is green.
If it failed, read the error in the Actions tab before touching any code.

**What comes next:**
1. **30-day backfill** — trigger Pipeline — Full Run with `backfill_start` =
   30 days ago, `backfill_end` = yesterday. Fills Gold marts with history.
2. **Iter 16 (OCI VM)** — blocked on OCI credit card. When available: terraform
   apply, ansible-playbook, Slack webhook, point Pushgateway URL at VM IP.

**Verify repo state before starting:**
```bash
cd C:\Users\ahmed\Downloads\mediterranean-ops-fortress
git log --oneline -5
git status
```
Expected: HEAD is `02f0dab`, working tree clean.
