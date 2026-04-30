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
"""

from __future__ import annotations

import os
import time
from datetime import date, timezone

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
_PAGE_LIMIT = 1000
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
    North African countries. Fetches all locations (with embedded sensor metadata)
    in one pass per country, retrieves pre-aggregated daily values per sensor,
    pivots to wide format, and writes partitioned Delta Lake rows.
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

    def fetch(self, target_date: date) -> pd.DataFrame:
        date_from = f"{target_date.isoformat()}T00:00:00Z"
        date_to = f"{target_date.isoformat()}T23:59:59Z"

        rows: list[dict] = []
        for country in TARGET_COUNTRIES:
            locations = self._fetch_locations(country)
            for loc in locations:
                rows.extend(self._collect_measurements(loc, date_from, date_to))

        if not rows:
            logger.warning(f"[openaq] No measurements returned for {target_date}.")
            return pd.DataFrame()

        return self._build_dataframe(rows, target_date)

    def _fetch_locations(self, country: str) -> list[dict]:
        """Page through /v3/locations for a country; return all locations."""
        locations: list[dict] = []
        page = 1
        while True:
            try:
                data = self._get(
                    f"{_OPENAQ_BASE}/locations",
                    {"country": country, "limit": _PAGE_LIMIT, "page": page},
                )
            except Exception as exc:
                logger.warning(
                    f"[openaq] {country}: location page {page} failed — {exc}"
                )
                break
            if data is None:
                break
            results = data.get("results", [])
            locations.extend(results)
            found_raw = str(data.get("meta", {}).get("found", 0))
            found = int(found_raw.lstrip(">").strip() or 0)
            if not results or len(locations) >= found:
                break
            page += 1
        logger.debug(f"[openaq] {country}: {len(locations)} locations")
        return locations

    def _collect_measurements(
        self, loc: dict, date_from: str, date_to: str
    ) -> list[dict]:
        """Fetch the daily aggregated value for each target sensor on a location."""
        coords = loc.get("coordinates") or {}
        country_obj = loc.get("country") or {}

        base = {
            "station_id": str(loc.get("id")),
            "station_name": loc.get("name"),
            "city": loc.get("locality"),
            "country_code": country_obj.get("code"),
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
        }

        rows: list[dict] = []
        for sensor in loc.get("sensors") or []:
            param = sensor.get("parameter") or {}
            col_name = _PARAMETER_MAP.get(param.get("id"))
            if col_name is None:
                continue
            value = self._fetch_daily_value(sensor["id"], date_from, date_to)
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
        """
        Pivot long rows (one per station × parameter) to wide format with one
        column per pollutant. Averages any duplicates from pagination overlap.
        """
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


def run(target_date: date | None = None) -> None:
    """Convenience wrapper; used by Docker CMD and integration tests."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    OpenAQIngestor(storage, pushgateway).run(target_date)


if __name__ == "__main__":
    run()
