"""
data/ingestion/bronze/copernicus_ingestor.py
────────────────────────────────────────────
Open-Meteo Air Quality API ingestor (CAMS-backed gridded model data).

Fetches hourly air quality for 12 Mediterranean / North African cities,
aggregates to daily means, and writes a partitioned Delta Lake table to
the bronze bucket on MinIO.

No API key required. Open-Meteo is free and CAMS-licensed.
API docs: https://open-meteo.com/en/docs/air-quality-api
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BronzeIngestor, StorageConfig


# ── City grid ──────────────────────────────────────────────────────────────────
# 12 cities covering the Western / Eastern Mediterranean basin and North Africa.
# (station_id, latitude, longitude, display_name, country_iso2)
MEDITERRANEAN_CITIES: list[tuple[str, float, float, str, str]] = [
    ("tunis_tn", 36.82, 10.17, "Tunis", "TN"),
    ("algiers_dz", 36.74, 3.06, "Algiers", "DZ"),
    ("casablanca_ma", 33.57, -7.59, "Casablanca", "MA"),
    ("tripoli_ly", 32.90, 13.18, "Tripoli", "LY"),
    ("cairo_eg", 30.06, 31.24, "Cairo", "EG"),
    ("alexandria_eg", 31.20, 29.92, "Alexandria", "EG"),
    ("beirut_lb", 33.89, 35.50, "Beirut", "LB"),
    ("athens_gr", 37.98, 23.73, "Athens", "GR"),
    ("istanbul_tr", 41.01, 28.95, "Istanbul", "TR"),
    ("rome_it", 41.90, 12.50, "Rome", "IT"),
    ("marseille_fr", 43.30, 5.37, "Marseille", "FR"),
    ("barcelona_es", 41.39, 2.17, "Barcelona", "ES"),
]

_OPENMETEO_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_HOURLY_VARS = "pm2_5,pm10,nitrogen_dioxide,ozone"
_REQUEST_TIMEOUT = 30  # seconds


# ── Ingestor ───────────────────────────────────────────────────────────────────


class CopernicusIngestor(BronzeIngestor):
    """
    Fetches gridded CAMS model data (via Open-Meteo) for 12 Mediterranean cities.

    Each run produces one row per city with daily-mean pollutant values, yielding
    12 rows per date partition.
    """

    @property
    def source_name(self) -> str:
        return "openmeteo"

    @property
    def table_path(self) -> str:
        return f"s3://{self.storage.bronze_bucket}/openmeteo/air_quality"

    def fetch(self, target_date: date) -> pd.DataFrame:
        rows: list[dict] = []
        date_str = target_date.isoformat()

        for station_id, lat, lon, name, country in MEDITERRANEAN_CITIES:
            try:
                record = self._fetch_city(station_id, lat, lon, name, country, date_str)
                if record is not None:
                    rows.append(record)
            except Exception as exc:
                logger.warning(f"[openmeteo] {name} fetch failed: {exc}")

        if not rows:
            logger.error("[openmeteo] All city fetches failed — returning empty frame.")
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = date_str
        df["partition_date"] = date_str
        df["ingestion_ts"] = pd.Timestamp.utcnow().isoformat()
        df["source"] = "openmeteo"
        return df

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20)
    )
    def _fetch_city(
        self,
        station_id: str,
        lat: float,
        lon: float,
        name: str,
        country: str,
        date_str: str,
    ) -> dict | None:
        """Fetch hourly data for a single city and return a daily-mean record."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": _HOURLY_VARS,
            "start_date": date_str,
            "end_date": date_str,
            "timezone": "UTC",
        }
        resp = requests.get(_OPENMETEO_URL, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()

        hourly = payload.get("hourly", {})
        if not hourly or not hourly.get("time"):
            logger.warning(f"[openmeteo] Empty hourly block for {name}")
            return None

        df_h = pd.DataFrame(hourly)
        means = {
            "pm2_5": _safe_mean(df_h, "pm2_5"),
            "pm10": _safe_mean(df_h, "pm10"),
            "nitrogen_dioxide": _safe_mean(df_h, "nitrogen_dioxide"),
            "ozone": _safe_mean(df_h, "ozone"),
        }

        return {
            "station_id": station_id,
            "station_name": name,
            "country_code": country,
            "latitude": lat,
            "longitude": lon,
            **means,
        }


def _safe_mean(df: pd.DataFrame, col: str) -> float | None:
    if col not in df.columns:
        return None
    series = pd.to_numeric(df[col], errors="coerce")
    return round(float(series.mean()), 4) if series.notna().any() else None


# ── Module-level entry point ───────────────────────────────────────────────────


def run(target_date: date | None = None) -> None:
    """Convenience wrapper; used by Docker CMD and integration tests."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    CopernicusIngestor(storage, pushgateway).run(target_date)


if __name__ == "__main__":
    run()
