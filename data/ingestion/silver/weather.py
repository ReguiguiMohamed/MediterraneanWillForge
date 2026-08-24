"""
data/ingestion/silver/weather.py
─────────────────────────────────
Bronze → Silver transformation for the Open-Meteo weather source.

Reads `bronze/openmeteo_weather/weather`, enforces types, clips physically
impossible readings, and labels each row with the conditions a person would
recognise: what the sky did, how hard it blew, how much Saharan dust was in the
air. Writes `silver/weather`, partitioned by date.

Everything here is row-local on purpose. Whether a day is a heatwave depends on
the days around it, and Silver only ever sees the partitions it has not
processed yet, so that judgement belongs in Gold where the full history is in
hand. See build_daily_country_weather in data/ingestion/gold/marts.py.

Silver weather schema
─────────────────────
station_id           str   — city grid point, shared with the air-quality source
station_name         str   — human-readable name
country_code         str   — ISO-3166-1 alpha-2
latitude             float — WGS-84
longitude            float — WGS-84
date                 str   — YYYY-MM-DD observation date
temp_max_c           float — daily high, degrees Celsius
temp_min_c           float — daily low
temp_mean_c          float — daily mean
apparent_temp_max_c  float — daily high of the heat index
precipitation_mm     float — daily total
wind_speed_max_kmh   float — daily maximum sustained wind
wind_gust_max_kmh    float — daily maximum gust
humidity_pct         float — daily mean relative humidity
weather_code         int   — WMO present-weather code
dust                 float — µg/m³ daily mean, CAMS
condition            str   — clear | cloudy | fog | drizzle | rain | snow |
                             thunderstorm | unknown
wind_level           str   — calm | breezy | windy | gale | storm (Beaufort)
dust_level           str   — none | moderate | high | severe
source               str   — openmeteo_weather
silver_ts            str   — UTC ISO-8601 write timestamp
partition_date       str   — YYYY-MM-DD (Delta partition key)
"""

from __future__ import annotations

import time
from datetime import timezone

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from data.ingestion.silver.transformer import SilverConfig, _unprocessed_partitions
from data.metrics import push_to_grafana

SOURCE = "openmeteo_weather"

# WMO present-weather codes, grouped into the words a briefing would use.
# Open-Meteo emits this subset; anything else lands as "unknown" rather than
# being guessed into the nearest group.
_WMO_CONDITIONS: dict[int, str] = {
    0: "clear",
    **dict.fromkeys((1, 2, 3), "cloudy"),
    **dict.fromkeys((45, 48), "fog"),
    **dict.fromkeys((51, 53, 55, 56, 57), "drizzle"),
    **dict.fromkeys((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    **dict.fromkeys((71, 73, 75, 77, 85, 86), "snow"),
    **dict.fromkeys((95, 96, 99), "thunderstorm"),
}

# Gust bands, in km/h, following the Beaufort scale: strong breeze at 39,
# gale at 62, storm at 89.
_WIND_BINS = [-np.inf, 20.0, 39.0, 62.0, 89.0, np.inf]
_WIND_LABELS = ["calm", "breezy", "windy", "gale", "storm"]

# Daily mean dust, µg/m³. Mediterranean background sits under 20; a Saharan
# intrusion runs from about 50 into the hundreds.
_DUST_BINS = [-np.inf, 20.0, 50.0, 200.0, np.inf]
_DUST_LABELS = ["none", "moderate", "high", "severe"]

# Physical bounds. Anything outside these is an upstream fault, not weather.
_CLIP = {
    "temp_max_c": (-60.0, 60.0),
    "temp_min_c": (-60.0, 60.0),
    "temp_mean_c": (-60.0, 60.0),
    "apparent_temp_max_c": (-80.0, 80.0),
    "precipitation_mm": (0.0, 1_000.0),
    "wind_speed_max_kmh": (0.0, 400.0),
    "wind_gust_max_kmh": (0.0, 500.0),
    "humidity_pct": (0.0, 100.0),
    "dust": (0.0, 10_000.0),
}

_WEATHER_COLUMNS = [
    "station_id",
    "station_name",
    "country_code",
    "latitude",
    "longitude",
    "date",
    "temp_max_c",
    "temp_min_c",
    "temp_mean_c",
    "apparent_temp_max_c",
    "precipitation_mm",
    "wind_speed_max_kmh",
    "wind_gust_max_kmh",
    "humidity_pct",
    "weather_code",
    "dust",
    "condition",
    "wind_level",
    "dust_level",
    "source",
    "silver_ts",
    "partition_date",
]

_STRING_COLUMNS = [
    "station_id",
    "station_name",
    "country_code",
    "date",
    "condition",
    "wind_level",
    "dust_level",
    "source",
    "silver_ts",
    "partition_date",
]

_FLOAT_COLUMNS = [
    "latitude",
    "longitude",
    *_CLIP,
]


# ── Pure cleaning and enrichment ──────────────────────────────────────────────


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Type-enforce, clip, and drop rows with no usable identity or reading."""
    df = df.copy()

    for column, (low, high) in _CLIP.items():
        df[column] = pd.to_numeric(df.get(column), errors="coerce").clip(low, high)

    df["latitude"] = pd.to_numeric(df.get("latitude"), errors="coerce")
    df["longitude"] = pd.to_numeric(df.get("longitude"), errors="coerce")
    df["weather_code"] = pd.to_numeric(df.get("weather_code"), errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["source"] = SOURCE

    for column in ("station_name", "country_code"):
        if column not in df.columns:
            df[column] = None

    df = df.dropna(subset=["station_id", "date", "temp_max_c"])
    return df.reset_index(drop=True)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Label the sky, the wind and the dust for each row."""
    df = df.copy()

    df["condition"] = (
        df["weather_code"]
        .map(lambda code: _WMO_CONDITIONS.get(int(code)) if pd.notna(code) else None)
        .fillna("unknown")
    )
    df["wind_level"] = _band(df["wind_gust_max_kmh"], _WIND_BINS, _WIND_LABELS)
    df["dust_level"] = _band(df["dust"], _DUST_BINS, _DUST_LABELS)
    df["silver_ts"] = pd.Timestamp.now(tz=timezone.utc).isoformat()
    return df


def _band(series: pd.Series, bins: list[float], labels: list[str]) -> pd.Series:
    """Cut a numeric series into named bands, nulls becoming 'unknown'."""
    return (
        pd.cut(series, bins=bins, labels=labels, right=False)
        .astype("object")
        .fillna("unknown")
    )


def _canonicalize_for_delta(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical column order and Delta-safe dtypes.

    delta-rs rejects Arrow Null columns outright, so every column is given a
    concrete type even when the whole partition happens to be null.
    """
    df = df.copy()

    for column in _WEATHER_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    for column in _STRING_COLUMNS:
        df[column] = df[column].astype("string")

    for column in _FLOAT_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")

    df["weather_code"] = (
        pd.to_numeric(df["weather_code"], errors="coerce").fillna(-1).astype("int64")
    )

    return df[_WEATHER_COLUMNS]


# ── Entry point ────────────────────────────────────────────────────────────────


def run() -> None:
    cfg = SilverConfig.from_env()
    bronze_path = f"s3://{cfg.bronze_bucket}/openmeteo_weather/weather"
    silver_path = f"s3://{cfg.silver_bucket}/weather"

    reg = CollectorRegistry()
    rows_gauge = Gauge(
        "med_ops_silver_weather_rows",
        "Rows written to Silver weather this run",
        [],
        registry=reg,
    )
    fresh_gauge = Gauge(
        "med_ops_silver_weather_last_run_ts",
        "Unix timestamp of last successful Silver weather run",
        [],
        registry=reg,
    )
    dur_gauge = Gauge(
        "med_ops_silver_weather_duration_seconds",
        "Wall-clock seconds for the Silver weather transform",
        [],
        registry=reg,
    )

    logger.info("Silver weather transformation starting.")
    t_start = time.monotonic()

    partitions = _unprocessed_partitions(
        bronze_path, silver_path, SOURCE, cfg.storage_options
    )

    total_rows = 0
    if not partitions:
        logger.info("[weather] Silver up to date — nothing to process.")
    else:
        logger.info(
            f"[weather] Processing {len(partitions)} new partition(s): {partitions}"
        )
        total_rows = _transform_partitions(
            bronze_path, silver_path, partitions, cfg.storage_options
        )

    elapsed = time.monotonic() - t_start
    rows_gauge.set(total_rows)
    fresh_gauge.set(time.time())
    dur_gauge.set(elapsed)

    try:
        push_to_gateway(cfg.pushgateway_url, job="med_ops_silver_weather", registry=reg)
    except Exception as exc:
        logger.warning(f"Pushgateway push failed (best-effort): {exc}")
    push_to_grafana(reg, job="med_ops_silver_weather")

    logger.success(f"Silver weather complete — {total_rows} rows, {elapsed:.1f}s")


def _transform_partitions(
    bronze_path: str,
    silver_path: str,
    partitions: list[str],
    storage_options: dict[str, str],
) -> int:
    """Transform and write the given Bronze partitions in one Delta write.

    One open of Bronze and one write to Silver however many partitions are
    pending, the same batching the air-quality transformer uses: each
    write_deltalake call reopens the Silver log, and those reads are billed.
    """
    bronze_dt = DeltaTable(bronze_path, storage_options=storage_options)
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for partition in partitions:
        try:
            raw = bronze_dt.to_pandas(filters=[("partition_date", "=", partition)])
            # engine="rust" keeps partition columns out of the Parquet files,
            # so put the one we filtered on back if it did not come along.
            if "partition_date" not in raw.columns:
                raw = raw.copy()
                raw["partition_date"] = partition

            frame = _canonicalize_for_delta(enrich(clean(raw)))
            if frame.empty:
                logger.warning(f"[weather] Partition {partition} — 0 rows after clean.")
                continue

            frames.append(frame)
            logger.info(f"[weather] Partition {partition}: {len(frame)} rows prepared.")
        except Exception as exc:
            failures.append(f"weather/{partition}: {exc}")
            logger.error(f"[weather] Partition {partition} failed: {exc}")

    if failures:
        raise RuntimeError(
            "Silver weather transformation failed for partition(s): "
            + "; ".join(failures)
        )

    if not frames:
        return 0

    batch = pd.concat(frames, ignore_index=True)
    write_deltalake(
        silver_path,
        batch,
        mode="append",
        engine="rust",
        partition_by=["partition_date", "source"],
        storage_options=storage_options,
        schema_mode="merge",
    )
    try:
        DeltaTable(silver_path, storage_options=storage_options).create_checkpoint()
    except Exception as exc:
        logger.warning(f"[weather] Silver checkpoint failed (non-fatal): {exc}")

    logger.info(
        f"[weather] Wrote {len(batch)} rows ({len(frames)} partition(s)) → Silver."
    )
    return len(batch)


if __name__ == "__main__":
    run()
