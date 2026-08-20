"""
data/ingestion/silver/pm25_patch.py
───────────────────────────────────
Fill missing PM2.5 from the Copernicus CAMS model, at each station's own
coordinates.

Why a model patch rather than imputation: 96% of Silver rows missing PM2.5 come
from stations that have *never* reported it — NO2/O3-only instruments, not
sensors with gaps. Forward-fill or a rolling mean has nothing to carry forward
for those, and averaging neighbouring stations would invent a measurement and
attribute it to a station that does not measure PM2.5 at all.

Provenance is recorded per row in pm2_5_source, so no consumer has to guess
whether a value was measured or modelled:

    ground_sensor    measured by the station (OpenAQ / WAQI)
    model_estimated  missing at the station, patched here from CAMS
    model_grid       an openmeteo row — CAMS grid output, modelled by definition
"""

from __future__ import annotations

import pandas as pd
import requests
from loguru import logger

# Same endpoint and daily-mean helper the openmeteo Bronze ingestor uses.
from data.ingestion.bronze.copernicus_ingestor import (
    _HOURLY_VARS,
    _OPENMETEO_URL,
    _REQUEST_TIMEOUT,
    _safe_mean,
)

GROUND_SENSOR = "ground_sensor"
MODEL_ESTIMATED = "model_estimated"
MODEL_GRID = "model_grid"

# ponytail: 50 coordinates per request keeps the URL well under any practical
# length limit. Raise it if the station list ever grows enough to matter.
_BATCH = 50


def _fetch_model_pm25(
    coords: list[tuple[float, float]], date_str: str
) -> list[float | None]:
    """Daily-mean CAMS PM2.5 for each coordinate, in the order given.

    Open-Meteo accepts comma-separated coordinates and returns one result per
    coordinate *in request order* — it echoes back the grid-cell centre, not the
    requested point, so results cannot be matched by latitude/longitude.
    """
    out: list[float | None] = []

    for start in range(0, len(coords), _BATCH):
        chunk = coords[start : start + _BATCH]
        params = {
            "latitude": ",".join(str(lat) for lat, _ in chunk),
            "longitude": ",".join(str(lon) for _, lon in chunk),
            "hourly": _HOURLY_VARS,
            "start_date": date_str,
            "end_date": date_str,
            "timezone": "UTC",
        }
        resp = requests.get(_OPENMETEO_URL, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()

        # A single coordinate comes back as one object, several as a list.
        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(chunk):
            raise ValueError(
                f"Open-Meteo returned {len(results)} result(s) for {len(chunk)} "
                "coordinate(s) — cannot map results to stations by position."
            )

        for entry in results:
            hourly = entry.get("hourly", {})
            if not hourly or not hourly.get("time"):
                out.append(None)
                continue
            out.append(_safe_mean(pd.DataFrame(hourly), "pm2_5"))

    return out


def patch_missing_pm25(
    df: pd.DataFrame,
    source: str,
    date_str: str,
    fetch=_fetch_model_pm25,
) -> pd.DataFrame:
    """Return df with missing PM2.5 filled from CAMS and pm2_5_source recorded.

    A failed lookup is logged and left unfilled — the pollutants the station did
    report are never discarded over a patch that could not be fetched.
    """
    df = df.copy()

    if df.empty or "pm2_5" not in df.columns:
        return df

    if source == "openmeteo":
        df["pm2_5_source"] = df["pm2_5"].where(df["pm2_5"].isna(), MODEL_GRID)
        return df

    df["pm2_5_source"] = df["pm2_5"].where(df["pm2_5"].isna(), GROUND_SENSOR)

    missing = df["pm2_5"].isna()
    if not missing.any():
        return df

    # One lookup per distinct coordinate, not per row.
    coords = (
        df.loc[missing, ["latitude", "longitude"]]
        .round(4)
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    coords = [(float(lat), float(lon)) for lat, lon in coords]

    try:
        values = fetch(coords, date_str)
    except Exception as exc:
        logger.warning(
            f"[{source}] PM2.5 model patch failed for {date_str} "
            f"({len(coords)} coordinate(s)) — rows left unpatched: {exc}"
        )
        return df

    patched = {c: v for c, v in zip(coords, values) if v is not None}
    if not patched:
        logger.warning(f"[{source}] PM2.5 model patch returned no values.")
        return df

    row_coords = list(
        df.loc[missing, ["latitude", "longitude"]]
        .round(4)
        .itertuples(index=False, name=None)
    )
    fills = pd.Series(
        [patched.get((float(lat), float(lon))) for lat, lon in row_coords],
        index=df.index[missing],
        dtype="float64",
    )

    filled = fills.notna()
    df.loc[fills.index[filled], "pm2_5"] = fills[filled]
    df.loc[fills.index[filled], "pm2_5_source"] = MODEL_ESTIMATED

    logger.info(
        f"[{source}] PM2.5 patched from CAMS for {int(filled.sum())} row(s) "
        f"across {len(patched)} coordinate(s) on {date_str}."
    )
    return df
