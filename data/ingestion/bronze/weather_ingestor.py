"""
data/ingestion/bronze/weather_ingestor.py
─────────────────────────────────────────
Open-Meteo weather ingestor, over the same 12 city grid points the air-quality
ingestor already covers.

Two free endpoints, neither of which needs a key:

  archive-api.open-meteo.com/v1/archive
      ERA5 reanalysis. Daily high, low and mean temperature, apparent
      temperature, precipitation, wind speed and gusts, humidity, and a WMO
      weather code. It serves yesterday as readily as it serves 1940, so one
      endpoint covers both the daily cron and a backfill of the history
      already sitting in Silver.

  air-quality-api.open-meteo.com/v1/air-quality
      The hourly `dust` variable, averaged to a daily mean. Saharan dust is the
      Mediterranean's signature air-quality event and the reason a hot, still
      day is often also a PM10 day.

A date range costs one request per city per endpoint whatever its length, so
run_range() backfills months for the same 24 calls a single day costs.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BronzeIngestor, StorageConfig
from .copernicus_ingestor import MEDITERRANEAN_CITIES

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_REQUEST_TIMEOUT = 30  # seconds

# Open-Meteo daily variable → canonical column, units in the name so nothing
# downstream has to remember that gusts arrive in km/h rather than m/s.
_DAILY_COLUMNS: dict[str, str] = {
    "temperature_2m_max": "temp_max_c",
    "temperature_2m_min": "temp_min_c",
    "temperature_2m_mean": "temp_mean_c",
    "apparent_temperature_max": "apparent_temp_max_c",
    "precipitation_sum": "precipitation_mm",
    "wind_speed_10m_max": "wind_speed_max_kmh",
    "wind_gusts_10m_max": "wind_gust_max_kmh",
    "relative_humidity_2m_mean": "humidity_pct",
    "weather_code": "weather_code",
}

_DAILY_VARS = ",".join(_DAILY_COLUMNS)


class WeatherIngestor(BronzeIngestor):
    """Daily weather and dust for the 12 Mediterranean grid points.

    One row per city per date, 12 rows a day, using the same station_id values
    as the Open-Meteo air-quality source so the two join on (station_id, date)
    without a lookup table.
    """

    @property
    def source_name(self) -> str:
        return "openmeteo_weather"

    @property
    def table_path(self) -> str:
        return f"s3://{self.storage.bronze_bucket}/openmeteo_weather/weather"

    def fetch(self, target_date: date) -> pd.DataFrame:
        return self._fetch_range(target_date, target_date)

    # ── Range ingest ──────────────────────────────────────────────────────────

    def run_range(self, date_from: date, date_to: date) -> None:
        """Fetch every missing date in [date_from, date_to] in one pass.

        The archive endpoint charges the same one request per city whether the
        range is a day or a year, so a five-month backfill is 24 API calls and
        a single Delta write rather than one of each per date.

        Idempotent: dates already in Bronze are left alone.
        """
        existing = self._existing_partition_dates()
        wanted = [
            date_from + timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)
        ]
        missing = [d for d in wanted if d.isoformat() not in existing]

        if not missing:
            logger.info(
                f"[{self.source_name}] All partitions in "
                f"{date_from} → {date_to} already present — skipping."
            )
            return

        logger.info(
            f"[{self.source_name}] Range ingest {date_from} → {date_to}: "
            f"{len(missing)} date(s) to fetch, "
            f"{len(wanted) - len(missing)} already present"
        )

        t0 = time.monotonic()
        df = self._fetch_range(min(missing), max(missing))

        if not df.empty:
            df = df[df["partition_date"].isin({d.isoformat() for d in missing})]

        if df.empty:
            logger.warning(
                f"[{self.source_name}] No new rows for range {date_from} → {date_to}."
            )
            return

        self._write(df)
        elapsed = time.monotonic() - t0
        self._push_metrics(len(df), elapsed)
        logger.success(
            f"[{self.source_name}] Range ingest complete — {len(df)} rows across "
            f"{df['partition_date'].nunique()} date(s) in {elapsed:.1f}s"
        )

    # ── Fetch helpers ─────────────────────────────────────────────────────────

    def _fetch_range(self, start: date, end: date) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []

        for station_id, lat, lon, name, country in MEDITERRANEAN_CITIES:
            try:
                daily = self._fetch_city_weather(lat, lon, start, end)
            except Exception as exc:
                logger.warning(
                    f"[{self.source_name}] {name} weather fetch failed: {exc}"
                )
                continue

            if daily.empty:
                logger.warning(f"[{self.source_name}] {name} returned no daily rows.")
                continue

            daily["dust"] = daily["date"].map(
                self._city_dust(lat, lon, start, end, name)
            )
            daily["station_id"] = station_id
            daily["station_name"] = name
            daily["country_code"] = country
            daily["latitude"] = lat
            daily["longitude"] = lon
            frames.append(daily)

        if not frames:
            logger.error(
                f"[{self.source_name}] Every city fetch failed — returning empty frame."
            )
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)
        df["partition_date"] = df["date"]
        df["ingestion_ts"] = pd.Timestamp.utcnow().isoformat()
        df["source"] = self.source_name
        return df

    def _city_dust(
        self, lat: float, lon: float, start: date, end: date, name: str
    ) -> dict[str, float]:
        """Daily mean dust per date, or an empty mapping when the call fails.

        Dust rides on a second endpoint, so it is the one field that can go
        missing while the rest of the row is fine. An empty mapping leaves the
        column null for that city rather than dropping its temperatures.
        """
        try:
            return self._fetch_city_dust(lat, lon, start, end)
        except Exception as exc:
            logger.warning(f"[{self.source_name}] {name} dust fetch failed: {exc}")
            return {}

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20)
    )
    def _fetch_city_weather(
        self, lat: float, lon: float, start: date, end: date
    ) -> pd.DataFrame:
        resp = requests.get(
            _ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": _DAILY_VARS,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "UTC",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        daily = resp.json().get("daily") or {}
        if not daily.get("time"):
            return pd.DataFrame()

        frame = pd.DataFrame(daily).rename(columns={"time": "date", **_DAILY_COLUMNS})
        # reindex rather than select: a variable the API declines to return
        # should cost that column, not the whole city.
        return frame.reindex(columns=["date", *_DAILY_COLUMNS.values()])

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=20)
    )
    def _fetch_city_dust(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[str, float]:
        resp = requests.get(
            _AIR_QUALITY_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "dust",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "UTC",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        hourly = resp.json().get("hourly") or {}
        if not hourly.get("time") or hourly.get("dust") is None:
            return {}

        frame = pd.DataFrame(
            {
                "date": [str(stamp)[:10] for stamp in hourly["time"]],
                "dust": pd.to_numeric(hourly["dust"], errors="coerce"),
            }
        )
        return frame.groupby("date")["dust"].mean().round(2).to_dict()


# ── Module-level entry points ──────────────────────────────────────────────────


def _ingestor() -> WeatherIngestor:
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    return WeatherIngestor(storage, pushgateway)


def run(target_date: date | None = None) -> None:
    """Single date; used by the daily cron and by tests."""
    _ingestor().run(target_date)


def run_range(date_from: date, date_to: date) -> None:
    """Backfill; fetches the whole range in one pass per city."""
    _ingestor().run_range(date_from, date_to)


if __name__ == "__main__":
    run()
