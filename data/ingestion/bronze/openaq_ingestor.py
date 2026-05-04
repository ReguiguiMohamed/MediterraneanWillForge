"""
data/ingestion/bronze/openaq_ingestor.py
────────────────────────────────────────
OpenAQ v3 station-observation ingestor.

Pulls pre-aggregated daily PM2.5, PM10, NO2, and O3 values from real
monitoring stations across North Africa and the Mediterranean via the
OpenAQ v3 REST API, and writes a partitioned Delta Lake table to the
bronze bucket on Backblaze B2.

API:  https://api.openaq.org/v3
Docs: https://docs.openaq.org/

Optional env var OPENAQ_API_KEY — register free at https://explore.openaq.org/register
Without it the API enforces anonymous-tier rate limits; with a key, standard free-tier
limits apply.

Two ingestion modes:
  run(target_date)          — single date; used by daily cron.
  run_range(date_from, date_to) — full range in one API pass; used by backfill.
    Each sensor is queried once with limit=N covering all N dates, reducing
    API calls from (500 sensors × N dates) to (500 sensors × 1 call).
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta, timezone

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BronzeIngestor, StorageConfig

TARGET_COUNTRIES: list[str] = [
    "TN",
    "DZ",
    "MA",
    "LY",
    "EG",
    "TR",
    "GR",
    "ES",
    "IT",
    "LB",
]

_OPENAQ_BASE = "https://api.openaq.org/v3"
# Hard cap per country — prevents rate-limit flooding on high-density countries (TN/DZ/MA)
_MAX_LOCS_PER_COUNTRY = 50
_REQUEST_TIMEOUT = 30  # seconds

# OpenAQ v3 numeric parameter IDs → canonical Silver column names
_PARAMETER_MAP: dict[int, str] = {
    2: "pm2_5",
    1: "pm10",
    5: "nitrogen_dioxide",
    3: "ozone",
}


class OpenAQIngestor(BronzeIngestor):
    """
    Collects station-level observations from OpenAQ v3 for 10 Mediterranean /
    North African countries. Fetches up to _MAX_LOCS_PER_COUNTRY locations per
    country, retrieves pre-aggregated daily values per sensor, pivots to wide
    format, and writes partitioned Delta Lake rows to B2.
    """

    @property
    def source_name(self) -> str:
        return "openaq"

    @property
    def table_path(self) -> str:
        return f"s3://{self.storage.bronze_bucket}/openaq/air_quality"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        api_key = os.environ.get("OPENAQ_API_KEY", "").strip()
        if api_key:
            headers["X-API-Key"] = api_key
        return headers

    # ── Single-date path (daily cron) ─────────────────────────────────────────

    def fetch(self, target_date: date) -> pd.DataFrame:
        date_from = f"{target_date.isoformat()}T00:00:00Z"
        date_to = f"{target_date.isoformat()}T23:59:59Z"

        rows: list[dict] = []
        for country in TARGET_COUNTRIES:
            locations = self._fetch_locations(country)
            for loc in locations:
                rows.extend(self._collect_measurements(loc, date_from, date_to))
            time.sleep(2.0)  # let rate-limit window partially reset between countries

        if not rows:
            logger.warning(f"[openaq] No measurements returned for {target_date}.")
            return pd.DataFrame()

        return self._build_dataframe(rows, target_date)

    def _collect_measurements(
        self, loc: dict, date_from: str, date_to: str
    ) -> list[dict]:
        """Fetch the daily aggregated value for each target sensor on a location."""
        base = self._location_base(loc)
        rows: list[dict] = []
        for sensor in loc.get("sensors") or []:
            param = sensor.get("parameter") or {}
            col_name = _PARAMETER_MAP.get(param.get("id"))
            if col_name is None:
                continue
            value = self._fetch_daily_value(sensor["id"], date_from, date_to)
            time.sleep(1.2)  # ~50 req/min — stays within 60 req/min free-tier limit
            if value is None:
                continue
            rows.append({**base, "parameter": col_name, "value": value})
        return rows

    def _fetch_daily_value(
        self, sensor_id: int, date_from: str, date_to: str
    ) -> float | None:
        """Retrieve the pre-aggregated daily mean from the sensor measurements endpoint."""
        try:
            data = self._get(
                f"{_OPENAQ_BASE}/sensors/{sensor_id}/measurements/daily",
                {"datetime_from": date_from, "datetime_to": date_to, "limit": 1},
            )
        except Exception as exc:
            logger.debug(f"[openaq] Sensor {sensor_id}: fetch failed — {exc}")
            return None
        if not data:
            return None
        results = data.get("results", [])
        if not results:
            return None
        val = results[0].get("value")
        if val is None:
            return None
        fval = float(val)
        return fval if fval >= 0 else None

    # ── Range path (backfill) ─────────────────────────────────────────────────

    def run_range(self, date_from: date, date_to: date) -> None:
        """
        Fetch and write all dates in [date_from, date_to] in a single API pass.
        Each sensor is queried once with limit=N returning all N daily values,
        instead of N separate single-day queries.

        Idempotent: dates already present in Bronze are skipped.
        """
        all_dates = [
            date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)
        ]
        missing = [d for d in all_dates if not self._partition_exists(d)]

        if not missing:
            logger.info("[openaq] All partitions in range already present — skipping.")
            return

        skipped = len(all_dates) - len(missing)
        logger.info(
            f"[openaq] Range ingest {date_from} → {date_to}: "
            f"{len(missing)} date(s) to fetch, {skipped} already present"
        )

        fetch_from = f"{min(missing).isoformat()}T00:00:00Z"
        fetch_to = f"{max(missing).isoformat()}T23:59:59Z"
        num_days = (max(missing) - min(missing)).days + 1

        t0 = time.monotonic()
        df = self._fetch_range(fetch_from, fetch_to, num_days)

        if df.empty:
            logger.warning(f"[openaq] No measurements for range {date_from}–{date_to}.")
            return

        # Drop any dates the API returned that are already in Bronze
        missing_strs = {d.isoformat() for d in missing}
        df = df[df["partition_date"].isin(missing_strs)]

        if df.empty:
            logger.warning("[openaq] No new data after filtering existing partitions.")
            return

        self._write(df)
        elapsed = time.monotonic() - t0
        self._push_metrics(len(df), elapsed)
        logger.success(
            f"[openaq] Range ingest complete — {len(df)} rows across "
            f"{df['partition_date'].nunique()} date(s) in {elapsed:.1f}s"
        )

    def _fetch_range(self, date_from: str, date_to: str, num_days: int) -> pd.DataFrame:
        """One API pass over all countries; each sensor call covers the full range."""
        rows: list[dict] = []
        for country in TARGET_COUNTRIES:
            locations = self._fetch_locations(country)
            for loc in locations:
                rows.extend(
                    self._collect_measurements_range(loc, date_from, date_to, num_days)
                )
            time.sleep(2.0)

        if not rows:
            return pd.DataFrame()
        return self._build_dataframe_range(rows)

    def _collect_measurements_range(
        self, loc: dict, date_from: str, date_to: str, num_days: int
    ) -> list[dict]:
        """Fetch daily values for each target sensor across the full date range."""
        base = self._location_base(loc)
        rows: list[dict] = []
        for sensor in loc.get("sensors") or []:
            param = sensor.get("parameter") or {}
            col_name = _PARAMETER_MAP.get(param.get("id"))
            if col_name is None:
                continue
            day_values = self._fetch_daily_range(
                sensor["id"], date_from, date_to, num_days
            )
            time.sleep(1.2)
            for date_str, value in day_values:
                rows.append(
                    {**base, "parameter": col_name, "value": value, "date": date_str}
                )
        return rows

    def _fetch_daily_range(
        self, sensor_id: int, date_from: str, date_to: str, num_days: int
    ) -> list[tuple[str, float]]:
        """
        Retrieve daily aggregates for a sensor over a date range in one call.
        Returns a list of (YYYY-MM-DD, value) tuples for days that have data.
        """
        try:
            data = self._get(
                f"{_OPENAQ_BASE}/sensors/{sensor_id}/measurements/daily",
                {"datetime_from": date_from, "datetime_to": date_to, "limit": num_days},
            )
        except Exception as exc:
            logger.debug(f"[openaq] Sensor {sensor_id}: range fetch failed — {exc}")
            return []
        if not data:
            return []
        out: list[tuple[str, float]] = []
        for r in data.get("results") or []:
            val = r.get("value")
            if val is None:
                continue
            fval = float(val)
            if fval < 0:
                continue
            # Extract date from period.datetimeFrom.utc; fall back to datetime.utc
            period = r.get("period") or {}
            dt_utc = (period.get("datetimeFrom") or {}).get("utc", "")
            if not dt_utc:
                dt_utc = (r.get("datetime") or {}).get("utc", "")
            if not dt_utc:
                continue
            out.append((dt_utc[:10], fval))
        return out

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _location_base(loc: dict) -> dict:
        coords = loc.get("coordinates") or {}
        country_obj = loc.get("country") or {}
        return {
            "station_id": str(loc.get("id")),
            "station_name": loc.get("name"),
            "city": loc.get("locality"),
            "country_code": country_obj.get("code"),
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
        }

    def _fetch_locations(self, country: str) -> list[dict]:
        """Fetch up to _MAX_LOCS_PER_COUNTRY locations with target sensors for a country."""
        try:
            data = self._get(
                f"{_OPENAQ_BASE}/locations",
                {
                    "country": country,
                    "limit": _MAX_LOCS_PER_COUNTRY,
                    "parameters_id": list(_PARAMETER_MAP.keys()),
                },
            )
        except Exception as exc:
            logger.warning(f"[openaq] {country}: location fetch failed — {exc}")
            return []
        if data is None:
            return []
        all_locs = data.get("results", [])
        # OpenAQ occasionally returns stations whose country.code disagrees with the
        # country we queried (mis-attributed records in their DB). Drop them here so
        # foreign stations never enter Bronze.
        locations = [
            loc for loc in all_locs if (loc.get("country") or {}).get("code") == country
        ]
        dropped = len(all_locs) - len(locations)
        if dropped:
            logger.warning(
                f"[openaq] {country}: dropped {dropped} location(s) with mismatched country_code"
            )
        logger.debug(f"[openaq] {country}: {len(locations)} locations")
        return locations

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
    )
    def _get(self, url: str, params: dict) -> dict | None:
        """HTTP GET. Returns None for 404/410; raises on 429 and 5xx so tenacity retries."""
        resp = requests.get(
            url, params=params, headers=self._headers(), timeout=_REQUEST_TIMEOUT
        )
        if resp.status_code in (404, 410):
            return None
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 30))
            logger.warning(f"[openaq] Rate-limited — waiting {retry_after}s")
            time.sleep(retry_after)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _build_dataframe(rows: list[dict], target_date: date) -> pd.DataFrame:
        """Pivot single-date long rows to wide format (one row per station)."""
        df = pd.DataFrame(rows)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["station_id", "parameter", "value"])
        df = df[df["value"] >= 0]

        if df.empty:
            return pd.DataFrame()

        agg = (
            df.groupby(
                [
                    "station_id",
                    "station_name",
                    "city",
                    "country_code",
                    "latitude",
                    "longitude",
                    "parameter",
                ],
                dropna=False,
            )["value"]
            .mean()
            .round(4)
            .reset_index()
        )

        pivot = agg.pivot_table(
            index=[
                "station_id",
                "station_name",
                "city",
                "country_code",
                "latitude",
                "longitude",
            ],
            columns="parameter",
            values="value",
            aggfunc="first",
        ).reset_index()
        pivot.columns.name = None

        for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
            if col not in pivot.columns:
                pivot[col] = None

        date_str = target_date.isoformat()
        pivot["date"] = date_str
        pivot["partition_date"] = date_str
        pivot["ingestion_ts"] = pd.Timestamp.now(tz=timezone.utc).isoformat()
        pivot["source"] = "openaq"
        pivot["station_id"] = pivot["station_id"].astype(str)

        return pivot

    @staticmethod
    def _build_dataframe_range(rows: list[dict]) -> pd.DataFrame:
        """Pivot multi-date long rows to wide format (one row per station × date)."""
        df = pd.DataFrame(rows)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["station_id", "parameter", "value", "date"])
        df = df[df["value"] >= 0]

        if df.empty:
            return pd.DataFrame()

        agg = (
            df.groupby(
                [
                    "station_id",
                    "station_name",
                    "city",
                    "country_code",
                    "latitude",
                    "longitude",
                    "date",
                    "parameter",
                ],
                dropna=False,
            )["value"]
            .mean()
            .round(4)
            .reset_index()
        )

        pivot = agg.pivot_table(
            index=[
                "station_id",
                "station_name",
                "city",
                "country_code",
                "latitude",
                "longitude",
                "date",
            ],
            columns="parameter",
            values="value",
            aggfunc="first",
        ).reset_index()
        pivot.columns.name = None

        for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
            if col not in pivot.columns:
                pivot[col] = None

        pivot["partition_date"] = pivot["date"]
        pivot["ingestion_ts"] = pd.Timestamp.now(tz=timezone.utc).isoformat()
        pivot["source"] = "openaq"
        pivot["station_id"] = pivot["station_id"].astype(str)

        return pivot


def run(target_date: date | None = None) -> None:
    """Convenience wrapper; used by Docker CMD and integration tests."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    OpenAQIngestor(storage, pushgateway).run(target_date)


def run_range(date_from: date, date_to: date) -> None:
    """Convenience wrapper for backfill; fetches all dates in one API pass."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    OpenAQIngestor(storage, pushgateway).run_range(date_from, date_to)


if __name__ == "__main__":
    run()
