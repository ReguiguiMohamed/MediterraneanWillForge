"""
data/ingestion/bronze/waqi_ingestor.py
───────────────────────────────────────
World Air Quality Index (WAQI) Bronze ingestor.

Fetches daily air quality readings from WAQI-monitored stations across
Mediterranean and North African cities, and writes a partitioned Delta
Lake table to the bronze bucket on Backblaze B2.

API:  https://api.waqi.info
Docs: https://aqicn.org/api/
Token: register free at https://aqicn.org/data-platform/token/

The WAQI free tier only provides current (latest) station readings — there
is no historical endpoint. Consequently this ingestor only works for
target_date = yesterday (the daily cron case). Backfill requests for past
dates will produce 0 rows because the station timestamps won't match.

Parameter mapping (WAQI → canonical Silver columns):
  pm25 → pm2_5
  pm10 → pm10
  no2  → nitrogen_dioxide
  o3   → ozone
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BronzeIngestor, StorageConfig

_WAQI_BASE = "https://api.waqi.info"
_REQUEST_TIMEOUT = 20  # seconds
_MAX_PER_QUERY = 3  # max station UIDs to follow up per search keyword

# Canonical parameter mapping: WAQI key → Silver column name
_PARAM_MAP: dict[str, str] = {
    "pm25": "pm2_5",
    "pm10": "pm10",
    "no2": "nitrogen_dioxide",
    "o3": "ozone",
}

# Curated city queries — (country_code, search_keyword).
# Covers LB and MA gaps left by OpenAQ sparsity, plus representative
# cities for all other target countries as a cross-check source.
_TARGET_QUERIES: list[tuple[str, str]] = [
    ("LB", "Beirut"),
    ("MA", "Casablanca"),
    ("MA", "Rabat"),
    ("MA", "Marrakech"),
    ("TN", "Tunis"),
    ("DZ", "Algiers"),
    ("EG", "Cairo"),
    ("EG", "Alexandria"),
    ("TR", "Istanbul"),
    ("TR", "Ankara"),
    ("GR", "Athens"),
    ("ES", "Madrid"),
    ("ES", "Barcelona"),
    ("IT", "Rome"),
    ("IT", "Milan"),
]


class WAQIIngestor(BronzeIngestor):
    """
    Collects station-level air quality readings from WAQI for Mediterranean
    and North African cities. Searches by city keyword, fetches full feed
    for each matched station, and retains only readings whose UTC timestamp
    matches target_date.
    """

    @property
    def source_name(self) -> str:
        return "waqi"

    @property
    def table_path(self) -> str:
        return f"s3://{self.storage.bronze_bucket}/waqi/air_quality"

    def _token(self) -> str:
        return os.environ.get("WAQI_API_KEY", "").strip()

    # ── API helpers ───────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
    )
    def _get(self, url: str, params: dict) -> dict | None:
        """HTTP GET to WAQI API. Returns None when status != 'ok'. Raises on HTTP errors."""
        params = {**params, "token": self._token()}
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            return None
        return data

    def _search_uids(self, keyword: str) -> list[int]:
        """Return up to _MAX_PER_QUERY station UIDs matching the keyword."""
        try:
            data = self._get(f"{_WAQI_BASE}/search/", {"keyword": keyword})
        except Exception as exc:
            logger.debug(f"[waqi] search '{keyword}' failed: {exc}")
            return []
        if not data:
            return []
        results = data.get("data") or []
        return [r["uid"] for r in results[:_MAX_PER_QUERY] if "uid" in r]

    def _fetch_feed(self, uid: int) -> dict | None:
        """Fetch full feed for a station UID, including iaqi pollutant breakdown."""
        try:
            data = self._get(f"{_WAQI_BASE}/feed/@{uid}/", {})
        except Exception as exc:
            logger.debug(f"[waqi] feed @{uid} failed: {exc}")
            return None
        return data.get("data") if data else None

    # ── Data extraction ───────────────────────────────────────────────────────

    def _extract_row(
        self, feed: dict, country_code: str, target_date: date
    ) -> dict | None:
        """
        Extract a canonical Bronze row from a WAQI feed dict.

        WAQI returns a station's latest available reading, which in UTC may be
        a few hours ahead of target_date (e.g. a +03:00 station reporting at
        22:00 local = 19:00 UTC the same day, or just past midnight UTC the
        next day). Accept readings within ±1 day of target_date and attribute
        all of them to target_date so the Bronze partition stays consistent.
        """
        time_info = feed.get("time") or {}
        ts_unix = time_info.get("v")
        if ts_unix is None:
            return None

        utc_date = datetime.utcfromtimestamp(ts_unix).date()
        if abs((utc_date - target_date).days) > 1:
            return None

        city_info = feed.get("city") or {}
        geo = city_info.get("geo") or [None, None]
        iaqi = feed.get("iaqi") or {}

        row: dict = {
            "station_id": str(feed.get("idx")),
            "station_name": city_info.get("name"),
            "city": city_info.get("name"),
            "country_code": country_code,
            "latitude": geo[0] if len(geo) > 0 else None,
            "longitude": geo[1] if len(geo) > 1 else None,
            "date": target_date.isoformat(),
            "partition_date": target_date.isoformat(),
            "ingestion_ts": pd.Timestamp.now(tz=timezone.utc).isoformat(),
            "source": "waqi",
        }
        for waqi_key, col_name in _PARAM_MAP.items():
            val = (iaqi.get(waqi_key) or {}).get("v")
            row[col_name] = float(val) if val is not None else None

        return row

    # ── fetch ─────────────────────────────────────────────────────────────────

    def fetch(self, target_date: date) -> pd.DataFrame:
        if not self._token():
            logger.warning("[waqi] WAQI_API_KEY not set — skipping.")
            return pd.DataFrame()

        rows: list[dict] = []
        seen_uids: set[int] = set()

        for country_code, keyword in _TARGET_QUERIES:
            uids = self._search_uids(keyword)
            time.sleep(0.5)

            for uid in uids:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)

                feed = self._fetch_feed(uid)
                time.sleep(0.5)
                if feed is None:
                    continue

                row = self._extract_row(feed, country_code, target_date)
                if row is not None:
                    rows.append(row)

        if not rows:
            logger.warning(
                f"[waqi] No measurements matched target_date={target_date}. "
                "WAQI only provides current readings — past dates will always yield 0 rows."
            )
            return pd.DataFrame()

        return self._build_dataframe(rows)

    @staticmethod
    def _build_dataframe(rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        for col in ("pm2_5", "pm10", "nitrogen_dioxide", "ozone"):
            if col not in df.columns:
                df[col] = None
        df["station_id"] = df["station_id"].astype(str)
        return df


def run(target_date: date | None = None) -> None:
    """Convenience wrapper used by the pipeline workflow and integration tests."""
    storage = StorageConfig.from_env()
    pushgateway = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
    WAQIIngestor(storage, pushgateway).run(target_date)


if __name__ == "__main__":
    run()
