# MediterraneanWillForge

[![Data CI](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-data.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-data.yml)
[![Infrastructure CI](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-infra.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/ci-infra.yml)
[![Scheduled pipeline](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/pipeline-run.yml/badge.svg)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/actions/workflows/pipeline-run.yml)
[![Latest release](https://img.shields.io/github/v/release/ReguiguiMohamed/MediterraneanWillForge)](https://github.com/ReguiguiMohamed/MediterraneanWillForge/releases)

An air-quality lakehouse for the Mediterranean and North Africa, running every
day on real data from Open-Meteo, OpenAQ, and WAQI. Nothing here is synthetic.

A GitHub Actions cron builds Bronze, Silver, and Gold Delta tables on Backblaze
B2, runs quality checks and anomaly detection, then publishes a fresh report to
GitHub Pages.

**[Open the published report](https://reguiguimohamed.github.io/MediterraneanWillForge/)**

## Data

| Source | Input | Bronze path |
|---|---|---|
| Open-Meteo | CAMS daily PM2.5, PM10, NO2, O3 for 12 city grid points | `s3://bronze/openmeteo/air_quality` |
| OpenAQ v3 | Daily station aggregates across nine countries | `s3://bronze/openaq/air_quality` |
| WAQI | Current station readings for 15 city searches | `s3://bronze/waqi/air_quality` |

Silver lands in `s3://silver/air_quality`:

```text
station_id, station_name, city, country_code, latitude, longitude, date,
pm2_5, pm10, nitrogen_dioxide, ozone, pm2_5_source, aqi_category,
who_pm25_exceed, who_pm10_exceed, who_no2_exceed, who_o3_exceed,
data_completeness, source, silver_ts, partition_date
```

Roughly half of all rows carry a pollutant reading but no PM2.5, almost always
because the station has no PM2.5 sensor at all. Those are filled from the CAMS
model at the station's own coordinates, and `pm2_5_source` records which is
which: `ground_sensor`, `model_estimated`, or `model_grid`.

Gold holds `daily_country_summary`, `wildfire_risk_index`, and `anomaly_alerts`.

WAQI reports IAQI index values rather than concentrations, so it feeds coverage
and WHO reporting but is kept out of anomaly detection.

## Architecture

```text
Open-Meteo ---\
OpenAQ --------+--> Bronze --> Silver --> Gold --> report + Pages
WAQI ---------/
                         |
                         +--> quality checks
                         +--> dbt/DuckDB in MinIO CI
                         +--> Grafana Cloud metrics (best effort)

Hosted lake: Backblaze B2        Local and CI lake: MinIO
```

Every Delta write is followed by a checkpoint, so later reads skip replaying the
log. That matters: the B2 free tier caps Class B transactions, and the Gold
stage reads all of Silver on every run.

More detail in [architecture](docs/architecture.md) and
[ADR-001](docs/adr/001-lakehouse-format.md).

## Results

Charts rebuild from the live Gold layer after every run. They are served from
Pages rather than committed, which keeps five months of regenerated PNGs out of
the git history. The notebook that produces them is
[`docs/pipeline_report.ipynb`](docs/pipeline_report.ipynb).

**Station coverage by country and date**
![Coverage heatmap](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/coverage_heatmap.png)

**WHO guideline exceedance**
![WHO exceedance](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/who_exceedance.png)

**Top anomaly of the day**, plotted against that day's spread across every
station, so you can see why the model flagged it.
![Top anomaly](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/top_anomaly.png)

Also: [anomaly detection](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/anomaly_detection.png),
[source coverage](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/source_coverage.png),
[pollutants by country](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/pollutants_by_country.png),
[wildfire risk](https://reguiguimohamed.github.io/MediterraneanWillForge/assets/wildfire_risk.png).

## AI daily brief

Two short generated sections in the report, both from that run's Gold layer:

- **Anomaly fact-check.** The day's top anomaly goes to the model with that
  day's distribution across all stations. Using Google Search grounding, it
  looks for a real explanation (wildfire, Saharan dust, heatwave, traffic) and
  says whether the reading is implausible, real and explained, or real and
  unexplained. It is told to report finding nothing as finding nothing, since an
  uncorroborated anomaly is the normal case. External claims carry source links.
- **Country briefings.** One to three sentences per country, using only that
  country's own numbers for the day, against WHO 2021 guidelines.

Both are labelled as generated text in the report. Every figure comes from the
pipeline, not the model.

This runs on Gemini's free tier at no cost. The two sections start on different
models because neither does both jobs: briefings need a `response_format` JSON
schema, which `gemini-3.7-flash` honours and `gemini-2.5-flash` ignores; the
fact-check needs Search grounding, free on 2.5 and unavailable on 3.x.

Each section then walks a ladder of models and keeps the first usable answer.
Free-tier quota is counted per model, and `gemini-3.7-flash` allows only 20
requests a day, so a spent quota drops the section to `gemini-2.5-flash` and
then `gemini-2.5-flash-lite` instead of dropping it from the report. The caption
names whichever model actually wrote it.

To turn it on, get a key from [Google AI Studio](https://aistudio.google.com/apikey)
(no card needed) and add it as a `GEMINI_API_KEY` repository secret under
Settings, Secrets and variables, Actions. **Never commit the key.** This repo is
public and scanners find committed keys within minutes. Without the secret the
pipeline behaves exactly as before and the report just omits the section.

Raw output: [`ai_brief.json`](https://reguiguimohamed.github.io/MediterraneanWillForge/ai_brief.json).

## Local setup

Python 3.11, Docker with Compose, and Make.

```bash
git clone https://github.com/ReguiguiMohamed/MediterraneanWillForge.git
cd MediterraneanWillForge
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
make up
```

`make up` starts MinIO, Prometheus, Pushgateway, Alertmanager, and cAdvisor.
MinIO is at `http://localhost:9001` with the credentials from `.env.example`.

`make ingest` hits the real APIs. OpenAQ works without a key at a lower rate
limit, WAQI needs `WAQI_API_KEY`. Do not point local runs at the hosted B2
buckets.

## Verification

```bash
python -m ruff check data tests
python -m black --check data tests
python -m pytest tests/unit -v --cov=data
make test-integration          # MinIO, no public API calls
```

CI also builds both images, runs Silver and Gold against MinIO, checks the Gold
output contracts, runs dbt, and validates the Prometheus and Alertmanager config.

## Layout

```text
.github/workflows/   CI, publishing, pipeline, and report workflows
data/ingestion/      Bronze, Silver, and Gold jobs
data/quality/        Great Expectations checks and Gold output contracts
data/dbt/            DuckDB models and tests
data/reporting/      report analytics and the AI brief
docker/              job images and the local Compose stack
monitoring/          local Prometheus and Alertmanager config
grafana/             Grafana Cloud dashboard export
docs/                architecture, ADR, and the report notebook
tests/               unit and MinIO integration tests
```

## Limits

- OpenAQ coverage is sparse and rate-limited, so zero-row days happen.
- WAQI has no free historical endpoint and reports IAQI, not concentrations.
- Gold rebuilds from all of Silver daily, so read cost grows with history. The
  B2 free tier will eventually need incremental Gold or coarser partitioning.
- dbt runs against MinIO in CI only, never in the scheduled B2 pipeline.
- The AI brief is generated text. It is grounded in the pipeline's numbers and
  cites sources, but it is not a substitute for reading the data.
- Hosted runs need B2 and WAQI secrets. Grafana and `GEMINI_API_KEY` are optional.
- No SLA, no Kubernetes, no secrets manager. cAdvisor wants a Linux host.

Release history is in [CHANGELOG.md](CHANGELOG.md).
