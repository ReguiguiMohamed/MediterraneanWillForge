# MediterraneanWillForge

[![Data CI](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-data.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-data.yml)
[![Infrastructure CI](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-infra.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-infra.yml)
[![Scheduled pipeline](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/pipeline-run.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/pipeline-run.yml)
[![Latest release](https://img.shields.io/github/v/release/ReguiguiMohamed/MediterraneanWillForge)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/releases)

A small air-quality lakehouse for the Mediterranean and North Africa.

It ingests real Open-Meteo, OpenAQ, and WAQI data. Python jobs write Bronze,
Silver, and Gold Delta tables. Quality checks, dbt/DuckDB models, an Isolation
Forest report, metrics, and a static portfolio report sit around that core.
There is no synthetic fallback data.

The scheduled GitHub workflow runs against Backblaze B2. Report artifacts are
refreshed after successful runs and published with GitHub Pages. Grafana Cloud
metrics are optional and never block data processing. dbt runs in MinIO-backed
CI, not in the scheduled B2 workflow.

**[Open the published report](https://reguiguimohamed.github.io/MediterraneanWillForge/)**

## Data

| Source | Input | Bronze path |
|---|---|---|
| Open-Meteo | CAMS-backed daily PM2.5, PM10, NO2, and O3 for 12 city grid points | `s3://bronze/openmeteo/air_quality` |
| OpenAQ v3 | Daily station aggregates across nine target countries | `s3://bronze/openaq/air_quality` |
| WAQI | Current station readings for 15 city searches | `s3://bronze/waqi/air_quality` |

Silver is written to `s3://silver/air_quality` with the frozen source labels
`openmeteo`, `openaq`, and `waqi`.

```text
station_id, station_name, city, country_code, latitude, longitude, date,
pm2_5, pm10, nitrogen_dioxide, ozone, aqi_category,
who_pm25_exceed, who_pm10_exceed, who_no2_exceed, who_o3_exceed,
data_completeness, source, silver_ts, partition_date
```

Gold contains:

- `daily_country_summary`
- `wildfire_risk_index`
- `anomaly_alerts`

WAQI values are IAQI indexes. They remain in Bronze, Silver, coverage, and WHO
reporting, but are excluded from concentration-based anomaly detection.

## Architecture

```text
Open-Meteo ---\
OpenAQ --------+--> Bronze --> Silver --> Gold --> report + Pages
WAQI ---------/
                         |
                         +--> quality checks
                         +--> dbt/DuckDB in MinIO CI
                         +--> best-effort Grafana Cloud metrics

Hosted lake: Backblaze B2        Local/CI lake: MinIO
```

Delta writes create checkpoints after each write. This keeps later table opens
from replaying the full Delta log and limits B2 Class B reads.

More detail: [architecture](docs/architecture.md) and
[ADR-001](docs/adr/001-lakehouse-format.md).

## Results

Every chart below is rebuilt from the live Gold layer after each pipeline run and
published to **[reguiguimohamed.github.io/MediterraneanWillForge](https://reguiguimohamed.github.io/MediterraneanWillForge/)**.
The report source notebook lives at [`docs/pipeline_report.ipynb`](docs/pipeline_report.ipynb);
its rendered output is served from Pages rather than committed, to keep the
repository free of daily binary churn.

**Station coverage by country and date**
![Coverage heatmap](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/coverage_heatmap.png)

**WHO guideline exceedance**
![WHO exceedance](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/who_exceedance.png)

**Isolation Forest anomaly report**
![Anomaly detection](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/anomaly_detection.png)

**Top anomaly of the latest date**

The most anomalous reading of the day, plotted against that day's spread across every
station — so the reason the Isolation Forest flagged it is visible, not just asserted.

![Top anomaly](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/top_anomaly.png)

**Source coverage**
![Source coverage](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/source_coverage.png)

Also see [pollutant concentrations](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/pollutants_by_country.png),
[wildfire risk](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/wildfire_risk.png),
[readiness diagnostics](https://reguiguimohamed.github.io/MediterraneanWillForge/reporting_readiness.csv), and
[the full rendered report](https://reguiguimohamed.github.io/MediterraneanWillForge/).

## AI Daily Brief

The published report carries two generated sections, written by Gemini
(`gemini-3.7-flash`) from each run's Gold layer:

- **Anomaly fact-check** — the day's top anomaly is passed to the model together
  with that day's distribution across all stations. Using Grounding with Google
  Search, the model looks for a real-world explanation (wildfire, Saharan dust,
  heatwave, traffic event) and reports whether the reading is implausible, real
  and explained, or real and unexplained. It is instructed to report an absence of
  evidence as an absence of evidence — an uncorroborated anomaly is the normal
  case, not a prompt to invent a cause — and every external claim carries its
  source link.
- **Country briefings** — one to three sentences per country, grounded in that
  country's own aggregates for the day and compared against WHO 2021 guidelines.

Both are generated text and are labelled as such in the report. Every figure they
cite comes from the pipeline, not from the model.

**This runs entirely on Gemini's free tier.** Flash text tokens are free, and
Search grounding on Gemini 3.x includes 5,000 free requests per month; this
pipeline uses roughly 30 (one per day). Gemini was chosen specifically because it
is the only major provider whose free tier includes real search grounding — Groq,
Cerebras and the free OpenRouter models have no search, and the Anthropic and
OpenAI APIs have no free tier at all.

To enable, get a key from [Google AI Studio](https://aistudio.google.com/apikey)
(no card required) and add it as a `GEMINI_API_KEY` repository secret
(Settings → Secrets and variables → Actions). **Never commit the key** — this is a
public repository, and scanners find committed keys within minutes. Without the
secret the pipeline runs exactly as before and the report simply omits the
section; the brief is best-effort and never fails a run.

Raw output: [`ai_brief.json`](https://reguiguimohamed.github.io/MediterraneanWillForge/ai_brief.json).

## Local Setup

Requirements: Python 3.11, Docker with Compose, and Make for the convenience
targets.

```bash
git clone https://github.com/ReguiguiMohamed/MediterraneanWillForge.git
cd MediterraneanWillForge
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
make up
```

`make up` starts local MinIO, Prometheus, Pushgateway, Alertmanager, and
cAdvisor. MinIO is available at `http://localhost:9001` with the development
credentials from `.env.example`.

`make ingest` calls the real APIs. OpenAQ works without a key at a lower rate
limit. WAQI requires `WAQI_API_KEY`. Do not use the hosted B2 bucket values for
local testing.

## Verification

The main local checks are:

```bash
python -m ruff check data tests
python -m black --check data tests
python -m pytest tests/unit -v --cov=data --cov-report=term-missing
python -m compileall -q data tests
docker compose -f docker/docker-compose.yml config --quiet
docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml config --quiet
```

Run deterministic MinIO integration tests without calling public data APIs:

```bash
make test-integration
```

CI also builds both Docker images, runs the Silver and Gold jobs against MinIO,
verifies output contracts, then runs:

```bash
python -m venv .dbt-venv
.dbt-venv/bin/python -m pip install -r requirements-dbt.txt
.dbt-venv/bin/dbt compile --profiles-dir data/dbt --project-dir data/dbt
.dbt-venv/bin/dbt run --profiles-dir data/dbt --project-dir data/dbt
.dbt-venv/bin/dbt test --profiles-dir data/dbt --project-dir data/dbt
```

Prometheus and Alertmanager configuration is validated with the pinned container
versions in `ci-infra.yml`.

## Layout

```text
.github/workflows/   seven CI, publishing, pipeline, report, and Pages workflows
data/ingestion/      Bronze, Silver, and Gold jobs
data/quality/        Great Expectations checks and Gold output contracts
data/dbt/            DuckDB models and tests
data/reporting/      shared report analytics
docker/              job images and local Compose stack
monitoring/          local Prometheus and Alertmanager configuration
grafana/             Grafana Cloud dashboard export and alert reference
docs/                architecture, ADR, notebook, HTML report, CSV, and charts
tests/               unit and MinIO integration tests
```

## Limits

- OpenAQ coverage is sparse and rate-limited. Zero-row days are possible.
- WAQI has no historical free-tier endpoint and exposes IAQI, not concentration.
- The B2 free tier has a Class B transaction limit. Checkpoints reduce reads but
  do not remove that limit.
- Hosted runs need repository secrets for B2 and WAQI. Grafana and
  `GEMINI_API_KEY` secrets are optional.
- The report is published to GitHub Pages after each run, not committed. Its
  freshness depends on the scheduled pipeline and report workflows succeeding.
- The AI brief is generated text. It is grounded in the pipeline's own numbers and
  cites sources for external claims, but it is not a substitute for the data.
- cAdvisor support depends on the Docker host. It is most reliable on Linux.
- This project has no SLA, Kubernetes deployment, or secrets manager.

Release history is in [CHANGELOG.md](CHANGELOG.md).
