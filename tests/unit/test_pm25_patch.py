import pandas as pd

from data.ingestion.silver.pm25_patch import (
    GROUND_SENSOR,
    MODEL_ESTIMATED,
    MODEL_GRID,
    patch_missing_pm25,
)


def _frame():
    return pd.DataFrame(
        {
            "station_id": ["has_sensor", "no_sensor", "no_sensor_2"],
            "latitude": [36.8, 41.9, 41.9],
            "longitude": [10.17, 12.5, 12.5],
            "pm2_5": [12.0, None, None],
            "ozone": [80.0, 95.0, 60.0],
        }
    )


def test_missing_pm25_is_patched_and_labelled():
    seen = {}

    def fake_fetch(coords, date_str):
        seen["coords"] = coords
        return [33.3]

    out = patch_missing_pm25(_frame(), "openaq", "2026-08-20", fetch=fake_fetch)

    # One lookup per distinct coordinate, not per row — both Rome rows share one.
    assert seen["coords"] == [(41.9, 12.5)]
    assert out.loc[0, "pm2_5"] == 12.0
    assert out.loc[0, "pm2_5_source"] == GROUND_SENSOR
    assert out.loc[1, "pm2_5"] == 33.3
    assert out.loc[2, "pm2_5"] == 33.3
    assert set(out.loc[1:, "pm2_5_source"]) == {MODEL_ESTIMATED}


def test_failed_lookup_keeps_the_row_and_its_other_pollutants():
    def boom(coords, date_str):
        raise RuntimeError("Open-Meteo unreachable")

    out = patch_missing_pm25(_frame(), "openaq", "2026-08-20", fetch=boom)

    assert len(out) == 3
    assert out["pm2_5"].isna().sum() == 2
    assert out["ozone"].notna().all()
    assert out.loc[0, "pm2_5_source"] == GROUND_SENSOR


def test_openmeteo_rows_are_labelled_model_not_ground():
    df = _frame().assign(pm2_5=[12.0, 20.0, 30.0])

    def never_called(coords, date_str):  # pragma: no cover
        raise AssertionError("openmeteo rows are already CAMS — no patch needed")

    out = patch_missing_pm25(df, "openmeteo", "2026-08-20", fetch=never_called)

    assert set(out["pm2_5_source"]) == {MODEL_GRID}


def test_no_lookup_when_nothing_is_missing():
    df = _frame().assign(pm2_5=[12.0, 20.0, 30.0])

    def never_called(coords, date_str):  # pragma: no cover
        raise AssertionError("fetch must not run when PM2.5 is complete")

    out = patch_missing_pm25(df, "openaq", "2026-08-20", fetch=never_called)

    assert set(out["pm2_5_source"]) == {GROUND_SENSOR}
