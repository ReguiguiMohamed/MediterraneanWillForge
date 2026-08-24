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
from deltalake import DeltaTable, write_deltalake
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BronzeIngestor, StorageConfig
from .copernicus_ingestor import MEDITERRANEAN_CITIES

_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# A day's request answers in under a second; a five-month range is a far bigger
# response and a slower query behind it. A flat 30s read timeout was enough for
# eleven cities of a 146-day backfill and not for the twelfth, which lost Beirut
# for the whole range. Give a range the time its size asks for.
_BASE_TIMEOUT = 30  # seconds for a single day
_MAX_TIMEOUT = 240


def _timeout(start: date, end: date) -> int:
    """Read timeout scaled to the number of days requested."""
    return min(_BASE_TIMEOUT + (end - start).days, _MAX_TIMEOUT)


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

    def run_range(self, date_from: date, date_to: date, rebuild: bool = False) -> None:
        """Fetch every missing date in [date_from, date_to] in one pass.

        The archive endpoint charges the same one request per city whether the
        range is a day or a year, so a five-month backfill is 24 API calls and
        a single Delta write rather than one of each per date.

        Idempotent: dates already in Bronze are left alone. `rebuild` refetches
        the range and replaces it instead, which is the only way to correct a
        range once written, since a partition marked present is skipped from
        then on. Two things need it: ERA5 revises its preliminary values to
        final within about three months, and a range that lost a city to a
        timeout has a hole no ordinary re-run will fill.
        """
        wanted = [
            date_from + timedelta(days=offset)
            for offset in range((date_to - date_from).days + 1)
        ]
        missing = (
            wanted
            if rebuild
            else [
                d
                for d in wanted
                if d.isoformat() not in self._existing_partition_dates()
            ]
        )

        if not missing:
            logger.info(
                f"[{self.source_name}] All partitions in "
                f"{date_from} → {date_to} already present — skipping."
            )
            return

        logger.info(
            f"[{self.source_name}] Range {'rebuild' if rebuild else 'ingest'} "
            f"{date_from} → {date_to}: {len(missing)} date(s) to fetch, "
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

        # All twelve cities or none. A partial write still marks every partition
        # in the range present, so the missing city is skipped from then on and
        # the gap becomes permanent. Failing here costs a re-run, which is the
        # cheaper of the two: the range is 24 free API calls.
        fetched = set(df["station_id"])
        expected = {city[0] for city in MEDITERRANEAN_CITIES}
        if fetched != expected:
            raise RuntimeError(
                f"[{self.source_name}] Range ingest incomplete — "
                f"{len(fetched)} of {len(expected)} cities returned data, "
                f"missing {sorted(expected - fetched)}. Nothing written; re-run."
            )

        if rebuild:
            self._replace(df, min(missing), max(missing))
        else:
            self._write(df)

        elapsed = time.monotonic() - t0
        self._push_metrics(len(df), elapsed)
        logger.success(
            f"[{self.source_name}] Range {'rebuild' if rebuild else 'ingest'} "
            f"complete — {len(df)} rows across "
            f"{df['partition_date'].nunique()} date(s) in {elapsed:.1f}s"
        )

    def _replace(self, df: pd.DataFrame, start: date, end: date) -> None:
        """Overwrite exactly the requested date range, leaving the rest alone.

        replaceWhere rather than a whole-table overwrite: a rebuild of one
        month must not take the other months with it. The replaced version
        stays in the Delta log, so a bad rebuild is recoverable by time travel.
        """
        write_deltalake(
            self.table_path,
            df,
            mode="overwrite",
            predicate=(
                f"partition_date >= '{start.isoformat()}' "
                f"AND partition_date <= '{end.isoformat()}'"
            ),
            partition_by=["partition_date"],
            storage_options=self.storage.options,
            schema_mode="merge",
            engine="rust",
        )
        try:
            DeltaTable(
                self.table_path, storage_options=self.storage.options
            ).create_checkpoint()
        except Exception as exc:
            logger.warning(
                f"[{self.source_name}] Delta checkpoint failed (non-fatal): {exc}"
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
            timeout=_timeout(start, end),
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
            timeout=_timeout(start, end),
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


def run_range(date_from: date, date_to: date, rebuild: bool = False) -> None:
    """Backfill; fetches the whole range in one pass per city."""
    _ingestor().run_range(date_from, date_to, rebuild)


if __name__ == "__main__":
    run()
