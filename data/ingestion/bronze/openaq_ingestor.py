"""
data/ingestion/bronze/openaq_ingestor.py
────────────────────────────────────────
OpenAQ v2 station-observation ingestor.

Pulls hourly PM2.5, PM10, NO2, and O3 readings from real monitoring
stations across North Africa and the Mediterranean, aggregates to daily
means per station, and writes a partitioned Delta Lake table to the
bronze bucket on MinIO.

API:  https://api.openaq.org/v2/measurements  (free, no auth required)
Docs: https://docs.openaq.org/
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

# ── Target countries ───────────────────────────────────────────────────────────
# North African coastal + Eastern Mediterranean nations with measurable station
# coverage in the OpenAQ network.
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

_OPENAQ_BASE = "https://api.openaq.org/v2"
_PARAMETERS = ["pm25", "pm10", "no2", "o3"]
_PAGE_LIMIT = 1000  # max records per OpenAQ page
_REQUEST_TIMEOUT = 45  # seconds


# ── Ingestor ───────────────────────────────────────────────────────────────────


class OpenAQIngestor(BronzeIngestor):
    """
    Collects station-level observations from OpenAQ v2 for 10 Mediterranean /
    North African countries, pivots long→wide, and writes daily-mean rows
    (one per station × pollutant combination retained as columns).
    """

    @property
    def source_name(self) -> str:
        return "openaq"

    @property
    def table_path(self) -> str:
        return f"s3://{self.storage.bronze_bucket}/openaq/air_quality"

    def fetch(self, target_date: date) -> pd.DataFrame:
        date_from = f"{target_date.isoformat()}T00:00:00Z"
        date_to = f"{target_date.isoformat()}T23:59:59Z"

        all_results: list[dict] = []
        for country in TARGET_COUNTRIES:
            for parameter in _PARAMETERS:
                records = self._fetch_measurements(
                    country, parameter, date_from, date_to
                )
                all_results.extend(records)

        if not all_results:
            logger.warning(f"[openaq] No measurements returned for {target_date}.")
            return pd.DataFrame()

        return self._build_dataframe(all_results, target_date)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30)
    )
    def _fetch_measurements(
        self,
        country: str,
        parameter: str,
        date_from: str,
        date_to: str,
    ) -> list[dict]:
        """Fetch one page of measurements for a single country + parameter pair."""
        params = {
            "country": country,
            "parameter": parameter,
            "date_from": date_from,
            "date_to": date_to,
            "limit": _PAGE_LIMIT,
            "page": 1,
        }
        headers = {"Accept": "application/json"}
        try:
            resp = requests.get(
                f"{_OPENAQ_BASE}/measurements",
                params=params,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except requests.HTTPError as exc:
            # 429 = rate limited; tenacity will retry
            if exc.response is not None and exc.response.status_code == 429:
                logger.warning(
                    f"[openaq] Rate-limited on {country}/{parameter} — retrying."
                )
                time.sleep(2)
                raise
            logger.warning(
                f"[openaq] HTTP {exc.response.status_code} for {country}/{parameter}: {exc}"
            )
            return []
        except Exception as exc:
            logger.warning(f"[openaq] Request failed for {country}/{parameter}: {exc}")
            return []

    @staticmethod
    def _build_dataframe(results: list[dict], target_date: date) -> pd.DataFrame:
        """
        Flatten OpenAQ result dicts, aggregate hourly → daily mean per
        (location, parameter), then pivot to wide format.
        """
        rows: list[dict] = []
        for r in results:
            coords = r.get("coordinates") or {}
            rows.append(
                {
                    "station_id": r.get("locationId"),
                    "station_name": r.get("location"),
                    "city": r.get("city"),
                    "country_code": r.get("country"),
                    "latitude": coords.get("latitude"),
                    "longitude": coords.get("longitude"),
                    "parameter": r.get("parameter"),
                    "value": r.get("value"),
                    "unit": r.get("unit"),
                }
            )

        df_long = pd.DataFrame(rows)

        # Drop rows without a valid station or pollutant measurement
        df_long = df_long.dropna(subset=["station_id", "parameter", "value"])
        df_long["value"] = pd.to_numeric(df_long["value"], errors="coerce")
        df_long = df_long[df_long["value"] >= 0]  # physical values are non-negative

        if df_long.empty:
            return pd.DataFrame()

        # Aggregate to daily mean per station × parameter
        agg = (
            df_long.groupby(
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

        # Pivot: one column per pollutant
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

        # Normalise column names (pm25 → pm2_5 for consistency with Open-Meteo)
        pivot.columns.name = None
        pivot = pivot.rename(
            columns={
                "pm25": "pm2_5",
                "no2": "nitrogen_dioxide",
                "o3": "ozone",
            }
        )

        # Guarantee all pollutant columns exist even when no data was returned
        for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
            if col not in pivot.columns:
                pivot[col] = None

        date_str = target_date.isoformat()
        pivot["date"] = date_str
        pivot["partition_date"] = date_str
        pivot["ingestion_ts"] = pd.Timestamp.now(tz=timezone.utc).isoformat()
        pivot["source"] = "openaq"

        # station_id must be a string for consistent Delta Lake schema
        pivot["station_id"] = pivot["station_id"].astype(str)

        return pivot


# ── Module-level entry point ───────────────────────────────────────────────────


def run(target_date: date | None = None) -> None:
    """Convenience wrapper; used by Docker CMD and integration tests."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    OpenAQIngestor(storage, pushgateway).run(target_date)


if __name__ == "__main__":
    run()
