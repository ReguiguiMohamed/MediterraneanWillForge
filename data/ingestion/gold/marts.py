"""
data/ingestion/gold/marts.py
─────────────────────────────
Silver → Gold aggregation layer.

Three analytics-ready marts are produced on each run:

  1. daily_country_summary
     Daily mean / max pollutant concentrations, station count, and WHO
     exceedance rate, grouped by country and date.

  2. wildfire_risk_index
     Composite risk score (0–100) per station per day.
     Formula: 0.6 × normalised O3  +  0.4 × normalised PM2.5.
     This is an air-quality indicator, not a fire-danger forecast.

  3. daily_country_weather
     Daily temperature, wind, rain and dust per country, plus heat and cold
     alerts. Whether a day counts as unusually hot is decided against the
     station's own previous month, which is why this lives in Gold: Silver
     only ever sees the partitions it has not processed yet, and a heatwave is
     a statement about the days around a day.

Each run reads a rolling window of Silver and rewrites only the most recent
slice of each Gold table, splicing it in front of the history already there.
Rebuilding all of Gold from all of Silver every night cost one object fetch per
Silver partition per source and grew by four a day forever. Set
GOLD_WINDOW_DAYS=all to rebuild in full, which a backfill needs. See
data/ingestion/gold/window.py.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from data.ingestion.gold.window import merge_refreshed, read_silver_window
from data.metrics import push_to_grafana
from data.storage import delta_storage_options

# ── Config ─────────────────────────────────────────────────────────────────────


def _storage_options() -> dict[str, str]:
    return delta_storage_options()


# ── Heat and cold event thresholds ─────────────────────────────────────────────
# A day counts as unusually hot only if it clears both an absolute floor and the
# station's own recent normal. The relative half stops every August day in Tunis
# reading as a heatwave; the absolute half stops a mild April day reading as one
# merely because the fortnight before it was milder still.
_HEAT_FLOOR_C = 30.0
_COLD_CEILING_C = 5.0
_EXTREME_HEAT_C = 40.0
_SEVERE_COLD_C = 0.0

# Trailing window for the per-station normal, and the days of history needed
# before it is worth quoting. A station's first week gets no verdict at all,
# which is the honest answer rather than a verdict against no baseline.
_BASELINE_DAYS = 30
_BASELINE_MIN_DAYS = 10

# Consecutive days that turn an advisory into a wave. Three is the threshold
# most national met services use, and the one the WMO guidance describes.
_EVENT_DAYS = 3

# Ordered least to most severe, so the country row can take the worst reading
# among its cities rather than an arbitrary one.
_CONDITION_ORDER = (
    "unknown",
    "clear",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "snow",
    "thunderstorm",
)
_WIND_ORDER = ("unknown", "calm", "breezy", "windy", "gale", "storm")
_DUST_ORDER = ("unknown", "none", "moderate", "high", "severe")


# ── Mart builders ──────────────────────────────────────────────────────────────


def build_daily_country_summary(silver_df: pd.DataFrame) -> pd.DataFrame:
    """
    Daily mean / max / WHO exceedance per country.

    Groups by (partition_date, country_code, source) so downstream consumers
    can compare station-based observations (openaq) against gridded model
    output (openmeteo) side by side.
    """
    group_cols = ["partition_date", "country_code", "source"]

    # openmeteo rows may not have a country_code — derive from station_id suffix
    silver_df = silver_df.copy()
    if "country_code" in silver_df.columns:
        # Fill blanks from station_id where possible (e.g. "tunis_tn" → "TN")
        mask = silver_df["country_code"].isna() & silver_df["station_id"].notna()
        silver_df.loc[mask, "country_code"] = (
            silver_df.loc[mask, "station_id"].str.split("_").str[-1].str.upper()
        )
    else:
        silver_df["country_code"] = None

    summary = (
        silver_df.groupby(group_cols, dropna=False)
        .agg(
            mean_pm2_5=("pm2_5", "mean"),
            max_pm2_5=("pm2_5", "max"),
            mean_pm10=("pm10", "mean"),
            mean_no2=("nitrogen_dioxide", "mean"),
            mean_o3=("ozone", "mean"),
            station_count=("station_id", "nunique"),
            who_pm25_exceed_pct=("who_pm25_exceed", "mean"),
            who_pm10_exceed_pct=("who_pm10_exceed", "mean"),
            who_no2_exceed_pct=("who_no2_exceed", "mean"),
            who_o3_exceed_pct=("who_o3_exceed", "mean"),
        )
        .reset_index()
    )

    # Convert mean exceedance fractions → percentages
    for col in (
        "who_pm25_exceed_pct",
        "who_pm10_exceed_pct",
        "who_no2_exceed_pct",
        "who_o3_exceed_pct",
    ):
        summary[col] = (summary[col] * 100).round(1)

    for col in ("mean_pm2_5", "max_pm2_5", "mean_pm10", "mean_no2", "mean_o3"):
        summary[col] = summary[col].round(2)

    summary["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return summary


def build_wildfire_risk_index(silver_df: pd.DataFrame) -> pd.DataFrame:
    """
    Composite wildfire risk score per station × day.

    Risk = 60% normalised O3 contribution  +  40% normalised PM2.5 contribution,
    scaled to 0–100.  Uses 180 µg/m³ as O3 upper reference and 150 µg/m³ for PM2.5.
    """
    df = silver_df.copy()

    o3_ref = 180.0
    pm25_ref = 150.0

    df["o3_norm"] = (df["ozone"].fillna(0) / o3_ref).clip(0, 1)
    df["pm25_norm"] = (df["pm2_5"].fillna(0) / pm25_ref).clip(0, 1)
    df["risk_index"] = ((df["o3_norm"] * 0.6 + df["pm25_norm"] * 0.4) * 100).round(1)

    df["risk_level"] = pd.cut(
        df["risk_index"],
        bins=[-np.inf, 25, 50, 75, np.inf],
        labels=["low", "moderate", "high", "extreme"],
    ).astype(str)

    keep = [
        "partition_date",
        "source",
        "station_id",
        "station_name",
        "country_code",
        "latitude",
        "longitude",
        "pm2_5",
        "ozone",
        "risk_index",
        "risk_level",
    ]
    # Only keep columns that actually exist
    keep = [c for c in keep if c in df.columns]
    risk = df[keep].copy()
    risk["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return risk


def flag_heat_events(weather_df: pd.DataFrame) -> pd.DataFrame:
    """Mark each station-day as unusually hot or cold, and count the run.

    The comparison is against the station's own previous 30 days, not a fixed
    threshold and not the whole history. A fixed threshold calls every
    Mediterranean summer a heatwave. A whole-history percentile does the same
    thing more slowly, as the seasons drift through it. The trailing month is
    what a person means by "hotter than it has been".

    Adds heat_baseline_c, cold_baseline_c, is_hot_day, is_cold_day,
    heat_streak_days and cold_streak_days.
    """
    df = weather_df.sort_values(["station_id", "partition_date"]).reset_index(drop=True)
    stations = df["station_id"]

    df["heat_baseline_c"] = _trailing_quantile(df, stations, "temp_max_c", 0.90)
    df["cold_baseline_c"] = _trailing_quantile(df, stations, "temp_min_c", 0.10)

    # Strictly past the baseline, not merely level with it. A steady spell sets
    # a baseline equal to itself, and matching it is the definition of ordinary.
    # A missing baseline compares as False, so the opening days of a station's
    # history raise no alert rather than being judged against nothing.
    df["is_hot_day"] = df["temp_max_c"] > df["heat_baseline_c"].clip(
        lower=_HEAT_FLOOR_C
    )
    df["is_cold_day"] = df["temp_min_c"] < df["cold_baseline_c"].clip(
        upper=_COLD_CEILING_C
    )
    df["heat_streak_days"] = _streak(df["is_hot_day"], stations)
    df["cold_streak_days"] = _streak(df["is_cold_day"], stations)
    return df


def _trailing_quantile(
    df: pd.DataFrame, groups: pd.Series, column: str, quantile: float
) -> pd.Series:
    """Quantile of a station's previous _BASELINE_DAYS days, today excluded.

    Today is shifted out first: a day that sets a record would otherwise raise
    the bar it is being measured against.
    """
    prior = df.groupby(groups)[column].shift(1)
    return prior.groupby(groups).transform(
        lambda s: s.rolling(_BASELINE_DAYS, min_periods=_BASELINE_MIN_DAYS).quantile(
            quantile
        )
    )


def _streak(flag: pd.Series, groups: pd.Series) -> pd.Series:
    """Consecutive True days ending on each row, counted within each station."""
    flag = flag.fillna(False).astype(bool)
    breaks = (~flag).groupby(groups).cumsum()
    return flag.astype(int).groupby([groups, breaks]).cumsum().astype("int64")


def _worst(values: pd.Series, order: tuple[str, ...]) -> str:
    """The most severe label present in a country's cities on a given day."""
    rank = {label: index for index, label in enumerate(order)}
    highest = max((rank.get(value, -1) for value in values.dropna()), default=-1)
    return order[highest] if highest >= 0 else "unknown"


def build_daily_country_weather(weather_df: pd.DataFrame) -> pd.DataFrame:
    """Daily temperature, conditions and heat alerts per country and date.

    Countries with several cities take the hottest high, the coldest low and
    the worst conditions among them, because an alert is about the worst place
    to be that day, not the average one.
    """
    flagged = flag_heat_events(weather_df)

    summary = (
        flagged.groupby(["partition_date", "country_code"], dropna=False)
        .agg(
            stations=("station_id", "nunique"),
            temp_max_c=("temp_max_c", "max"),
            temp_mean_c=("temp_mean_c", "mean"),
            temp_min_c=("temp_min_c", "min"),
            apparent_temp_max_c=("apparent_temp_max_c", "max"),
            precipitation_mm=("precipitation_mm", "max"),
            wind_gust_max_kmh=("wind_gust_max_kmh", "max"),
            humidity_pct=("humidity_pct", "mean"),
            dust=("dust", "max"),
            heat_streak_days=("heat_streak_days", "max"),
            cold_streak_days=("cold_streak_days", "max"),
            stations_hot=("is_hot_day", "sum"),
            stations_cold=("is_cold_day", "sum"),
            condition=("condition", lambda s: _worst(s, _CONDITION_ORDER)),
            wind_level=("wind_level", lambda s: _worst(s, _WIND_ORDER)),
            dust_level=("dust_level", lambda s: _worst(s, _DUST_ORDER)),
        )
        .reset_index()
    )

    summary["heat_alert"] = np.select(
        [
            (summary["heat_streak_days"] >= _EVENT_DAYS)
            & (summary["temp_max_c"] >= _EXTREME_HEAT_C),
            summary["heat_streak_days"] >= _EVENT_DAYS,
            summary["heat_streak_days"] > 0,
        ],
        ["extreme_heatwave", "heatwave", "heat_advisory"],
        default="none",
    )
    summary["cold_alert"] = np.select(
        [
            (summary["cold_streak_days"] >= _EVENT_DAYS)
            & (summary["temp_min_c"] <= _SEVERE_COLD_C),
            summary["cold_streak_days"] >= _EVENT_DAYS,
            summary["cold_streak_days"] > 0,
        ],
        ["severe_cold_wave", "cold_wave", "cold_advisory"],
        default="none",
    )

    for column in (
        "temp_max_c",
        "temp_mean_c",
        "temp_min_c",
        "apparent_temp_max_c",
        "precipitation_mm",
        "wind_gust_max_kmh",
        "humidity_pct",
        "dust",
    ):
        summary[column] = summary[column].round(1)

    for column in ("stations", "stations_hot", "stations_cold"):
        summary[column] = summary[column].astype("int64")

    summary["gold_ts"] = pd.Timestamp.utcnow().isoformat()
    return summary


def _read_silver_weather(
    silver_bucket: str, storage_opts: dict[str, str]
) -> pd.DataFrame:
    """Read Silver weather, or return an empty frame when it is not there yet.

    A lake predating the weather source has no such table, and the pollutant
    marts are the ones the rest of the pipeline depends on. Losing the weather
    mart is a warning; losing the run over it is not worth it. The Gold output
    contracts still fail the pipeline if the table stays missing.
    """
    try:
        return read_silver_window(f"s3://{silver_bucket}/weather", storage_opts)
    except Exception as exc:
        logger.warning(f"Silver weather not readable — weather mart skipped: {exc}")
        return pd.DataFrame()


# ── Entry point ────────────────────────────────────────────────────────────────


def run(
    silver_df: pd.DataFrame | None = None,
    weather_df: pd.DataFrame | None = None,
) -> None:
    storage_opts = _storage_options()
    silver_bucket = os.environ.get("MINIO_BUCKET_SILVER", "silver")
    gold_bucket = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")

    reg = CollectorRegistry()
    rows_gauge = Gauge(
        "med_ops_gold_mart_rows", "Total rows written to Gold mart", [], registry=reg
    )
    fresh_gauge = Gauge(
        "med_ops_gold_mart_last_run_ts",
        "Unix timestamp of last successful Gold mart run",
        [],
        registry=reg,
    )
    dur_gauge = Gauge(
        "med_ops_gold_mart_duration_seconds",
        "Wall-clock seconds for Gold mart",
        [],
        registry=reg,
    )

    logger.info("Gold mart build starting.")
    t_start = time.monotonic()

    if silver_df is None:
        silver_path = f"s3://{silver_bucket}/air_quality"
        try:
            silver_df = read_silver_window(silver_path, storage_opts)
        except Exception as exc:
            raise RuntimeError(f"Cannot read Silver layer: {exc}") from exc

    if silver_df.empty:
        raise RuntimeError("Silver layer is empty — Gold marts cannot be produced.")

    logger.info(
        f"Silver snapshot: {len(silver_df)} rows across {silver_df['partition_date'].nunique()} dates."
    )

    # ── daily_country_summary ─────────────────────────────────────────────────
    _summary_path = f"s3://{gold_bucket}/daily_country_summary"
    summary = merge_refreshed(
        _summary_path, build_daily_country_summary(silver_df), storage_opts
    )
    write_deltalake(
        _summary_path,
        summary,
        mode="overwrite",
        engine="rust",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    try:
        DeltaTable(_summary_path, storage_options=storage_opts).create_checkpoint()
    except Exception as exc:
        logger.warning(
            f"Gold daily_country_summary checkpoint failed (non-fatal): {exc}"
        )
    logger.info(f"Gold daily_country_summary: {len(summary)} rows written.")

    # ── wildfire_risk_index ───────────────────────────────────────────────────
    _risk_path = f"s3://{gold_bucket}/wildfire_risk_index"
    risk = merge_refreshed(
        _risk_path, build_wildfire_risk_index(silver_df), storage_opts
    )
    write_deltalake(
        _risk_path,
        risk,
        mode="overwrite",
        engine="rust",
        storage_options=storage_opts,
        schema_mode="overwrite",
    )
    try:
        DeltaTable(_risk_path, storage_options=storage_opts).create_checkpoint()
    except Exception as exc:
        logger.warning(f"Gold wildfire_risk_index checkpoint failed (non-fatal): {exc}")
    logger.info(f"Gold wildfire_risk_index: {len(risk)} rows written.")

    # ── daily_country_weather ─────────────────────────────────────────────────
    if weather_df is None:
        weather_df = _read_silver_weather(silver_bucket, storage_opts)

    weather_rows = 0
    if weather_df.empty:
        logger.warning("Silver weather is empty — daily_country_weather not rebuilt.")
    else:
        _weather_path = f"s3://{gold_bucket}/daily_country_weather"
        country_weather = merge_refreshed(
            _weather_path, build_daily_country_weather(weather_df), storage_opts
        )
        write_deltalake(
            _weather_path,
            country_weather,
            mode="overwrite",
            engine="rust",
            storage_options=storage_opts,
            schema_mode="overwrite",
        )
        try:
            DeltaTable(_weather_path, storage_options=storage_opts).create_checkpoint()
        except Exception as exc:
            logger.warning(
                f"Gold daily_country_weather checkpoint failed (non-fatal): {exc}"
            )
        weather_rows = len(country_weather)
        alerts = int((country_weather["heat_alert"] != "none").sum())
        logger.info(
            f"Gold daily_country_weather: {weather_rows} rows written, "
            f"{alerts} carrying a heat alert."
        )

    total_rows = len(summary) + len(risk) + weather_rows
    elapsed = time.monotonic() - t_start

    rows_gauge.set(total_rows)
    fresh_gauge.set(time.time())
    dur_gauge.set(elapsed)

    try:
        push_to_gateway(pushgateway, job="med_ops_gold_mart", registry=reg)
    except Exception as exc:
        logger.warning(f"Pushgateway push failed (best-effort): {exc}")
    push_to_grafana(reg, job="med_ops_gold_mart")
    logger.success(f"Gold marts complete — {total_rows} total rows, {elapsed:.1f}s")


if __name__ == "__main__":
    run()
