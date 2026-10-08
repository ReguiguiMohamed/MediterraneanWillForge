import pandas as pd
import pytest

from data.reporting.season import FOCI, SEASON_FOCUS, focus_for, season_of


@pytest.mark.parametrize(
    ("day", "season"),
    [
        ("2026-12-01", "winter"),
        ("2027-02-28", "winter"),
        ("2026-03-01", "spring"),
        ("2026-05-31", "spring"),
        ("2026-06-01", "summer"),
        ("2026-08-31", "summer"),
        ("2026-09-01", "autumn"),
        ("2026-11-30", "autumn"),
    ],
)
def test_meteorological_season_boundaries(day, season):
    assert season_of(day) == season


def test_every_season_has_a_focus():
    assert set(SEASON_FOCUS) == {"winter", "spring", "summer", "autumn"}
    assert set(SEASON_FOCUS.values()) <= set(FOCI)


def test_focus_follows_the_calendar_without_alerts():
    assert focus_for(None, "2026-10-08").name == "rain"
    assert focus_for(pd.DataFrame(), "2026-04-08").name == "dust"


def test_a_cold_wave_outranks_the_season():
    weather = pd.DataFrame(
        {
            "partition_date": ["2026-04-08", "2026-04-07"],
            "heat_alert": ["none", "none"],
            "cold_alert": ["cold_wave", "none"],
        }
    )

    assert focus_for(weather, "2026-04-08").name == "cold"
    # An alert from an earlier day does not carry over.
    assert focus_for(weather, "2026-04-07").name == "dust"


def test_rain_event_uses_the_heavy_rain_threshold():
    days = pd.DataFrame({"precipitation_mm": [9.9, 10.0, None]})

    assert FOCI["rain"].is_event(days).tolist() == [False, True, False]


def test_units_come_from_the_chart_label():
    assert [FOCI[name].unit for name in ("heat", "rain", "dust")] == [
        "C",
        "mm",
        "ug/m3",
    ]
