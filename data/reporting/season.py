"""
data/reporting/season.py
────────────────────────
Which weather story the report tells on a given day.

Every country covered sits in the northern hemisphere, so meteorological
seasons apply: winter is December to February, spring March to May, summer
June to August, autumn September to November. Each season leads with the
hazard that defines it around the Mediterranean:

  winter  cold     cold waves and frost nights
  spring  dust     Saharan dust intrusions, which peak in spring over the
                   central and eastern basin
  summer  heat     heatwaves
  autumn  rain     heavy rain on dry ground, the flash-flood season

An active heatwave or cold wave outranks the calendar. An October heatwave
leads the report in October.

To change what a season shows, edit FOCI or SEASON_FOCUS. The notebook and the
AI brief both read them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import pandas as pd

HEATWAVE = ("heatwave", "extreme_heatwave")
COLD_WAVE = ("cold_wave", "severe_cold_wave")

# ETCCDI's R10mm index: a day with 10 mm of rain or more is a heavy-rain day.
HEAVY_RAIN_MM = 10.0

# dust_level bands from Silver. "high" starts at 50 ug/m3, a Saharan intrusion.
DUSTY = ("high", "severe")


@dataclass(frozen=True)
class Focus:
    name: str
    title: str
    # Gold daily_country_weather column charted per country and day.
    column: str
    label: str
    cmap: str
    # True when the lowest value leads, as with cold.
    lowest_first: bool
    # Days averaged when ranking countries for the spotlight note. One means
    # the latest day alone. Rain needs a window because most days are dry.
    rank_days: int
    event_label: str
    is_event: Callable[[pd.DataFrame], pd.Series]

    @property
    def unit(self) -> str:
        return self.label.split(" (")[-1].rstrip(")")

    @property
    def measure(self) -> str:
        """What the leading country leads on, as the caption and the prompt say it."""
        leader = "lowest" if self.lowest_first else "highest"
        unit = self.label.split(" (")[0].lower()
        if self.rank_days == 1:
            return f"{leader} {unit} today"
        return f"{leader} average {unit} over the last {self.rank_days} days"


FOCI = {
    "heat": Focus(
        name="heat",
        title="Heat",
        column="temp_max_c",
        label="Daily high (C)",
        cmap="inferno",
        lowest_first=False,
        rank_days=1,
        event_label="Days inside a heatwave",
        is_event=lambda df: df["heat_alert"].isin(HEATWAVE),
    ),
    "cold": Focus(
        name="cold",
        title="Cold",
        column="temp_min_c",
        label="Daily low (C)",
        cmap="Blues_r",
        lowest_first=True,
        rank_days=1,
        event_label="Days inside a cold wave",
        is_event=lambda df: df["cold_alert"].isin(COLD_WAVE),
    ),
    "rain": Focus(
        name="rain",
        title="Rain",
        column="precipitation_mm",
        label="Daily rain (mm)",
        cmap="Blues",
        lowest_first=False,
        rank_days=10,
        event_label=f"Days with {HEAVY_RAIN_MM:g} mm of rain or more",
        is_event=lambda df: pd.to_numeric(df["precipitation_mm"], errors="coerce")
        >= HEAVY_RAIN_MM,
    ),
    "dust": Focus(
        name="dust",
        title="Saharan Dust",
        column="dust",
        label="Daily dust (ug/m3)",
        cmap="YlOrBr",
        lowest_first=False,
        rank_days=1,
        event_label="Days with high or severe dust",
        is_event=lambda df: df["dust_level"].isin(DUSTY),
    ),
}

SEASON_FOCUS = {"winter": "cold", "spring": "dust", "summer": "heat", "autumn": "rain"}

_SEASONS = ("winter", "spring", "summer", "autumn")


def season_of(day: str | date) -> str:
    month = pd.Timestamp(day).month
    return _SEASONS[month % 12 // 3]


def focus_for(weather: pd.DataFrame | None, day: str | date) -> Focus:
    """The focus for one day: an active heatwave or cold wave, else the season's."""
    if weather is not None and not weather.empty:
        today = weather[weather["partition_date"].astype(str) == str(day)[:10]]
        if today.get("heat_alert", pd.Series(dtype=str)).isin(HEATWAVE).any():
            return FOCI["heat"]
        if today.get("cold_alert", pd.Series(dtype=str)).isin(COLD_WAVE).any():
            return FOCI["cold"]
    return FOCI[SEASON_FOCUS[season_of(day)]]


def rank_countries(weather: pd.DataFrame, focus: Focus, day: str) -> pd.Series:
    """Each country's figure for the focus, leader first, over rank_days to day."""
    dates = sorted(
        d for d in weather["partition_date"].astype(str).unique() if d <= day
    )
    recent = weather[
        weather["partition_date"].astype(str).isin(dates[-focus.rank_days :])
    ]
    values = pd.to_numeric(recent[focus.column], errors="coerce")
    return (
        values.groupby(recent["country_code"])
        .mean()
        .dropna()
        .sort_values(ascending=focus.lowest_first)
    )
