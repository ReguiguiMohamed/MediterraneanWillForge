import json
from types import SimpleNamespace

import pandas as pd
import pytest

from data.reporting import ai_brief


def _citation(url, title):
    return SimpleNamespace(type="url_citation", url=url, title=title)


def _interaction(text, annotations=None):
    """Mimic a google-genai Interaction: output_text plus annotated steps."""
    block = SimpleNamespace(type="text", text=text, annotations=annotations or [])
    step = SimpleNamespace(type="model_output", content=[block])
    return SimpleNamespace(output_text=text, steps=[step])


class _Client:
    """Stand-in for genai.Client that returns a canned interaction."""

    def __init__(self, interaction):
        self.calls = []
        self.interactions = SimpleNamespace(
            create=lambda **kw: (self.calls.append(kw), interaction)[1]
        )


ANOMALY = {
    "partition_date": "2026-08-19",
    "station_name": "PATISION",
    "country_code": "GR",
    "source": "openaq",
    "anomaly_score": -0.6765,
    "is_anomaly": 1,
    "pm2_5": None,
    "ozone": 5.1,
    "nitrogen_dioxide": 77.1,
}
STATS = {"pm2_5": 12.9, "ozone": 72.1, "nitrogen_dioxide": 13.4, "stations": 77}


def test_fact_check_collects_verdict_and_citations():
    interaction = _interaction(
        "Plausible. NO2 77.1 is traffic.",
        annotations=[
            _citation("https://example.org/a", "Athens traffic"),
            _citation("https://example.org/b", "Air quality alert"),
        ],
    )
    client = _Client(interaction)

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, client)

    assert out["verdict"].startswith("Plausible")
    assert out["searched"] is True
    assert [s["url"] for s in out["sources"]] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    # Google Search grounding must actually be enabled on the request.
    assert client.calls[0]["tools"] == [{"type": "google_search"}]
    # Grounding is free-tier only on 2.5, so no rung of the search ladder is 3.x.
    assert client.calls[0]["model"] == ai_brief.SEARCH_MODELS[0] == "gemini-2.5-flash"
    assert not any(m.startswith("gemini-3") for m in ai_brief.SEARCH_MODELS)


def test_fact_check_deduplicates_repeated_citations():
    """The same source cited on several spans is one source, not many."""
    interaction = _interaction(
        "Real but unexplained.",
        annotations=[
            _citation("https://example.org/a", "Athens"),
            _citation("https://example.org/a", "Athens"),
        ],
    )

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, _Client(interaction))

    assert len(out["sources"]) == 1


def test_fact_check_without_citations_reports_no_search():
    out = ai_brief.fact_check_anomaly(
        ANOMALY, STATS, _Client(_interaction("No corroboration found."))
    )

    assert out["verdict"] == "No corroboration found."
    assert out["sources"] == []
    assert out["searched"] is False


def test_fact_check_survives_a_response_without_steps():
    """A shape change should cost the citations, not the whole brief."""
    bare = SimpleNamespace(output_text="Verdict text.", steps=None)

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, _Client(bare))

    assert out["verdict"] == "Verdict text."
    assert out["sources"] == []


def test_fact_check_returns_none_on_empty_output():
    assert (
        ai_brief.fact_check_anomaly(ANOMALY, STATS, _Client(_interaction(""))) is None
    )


def _summary():
    return pd.DataFrame(
        {
            "country_code": ["GR", "GR", "TN"],
            "station_count": [4, 2, 3],
            "mean_pm2_5": [12.0, 14.0, 30.0],
            "max_pm2_5": [20.0, 25.0, 44.0],
            "mean_pm10": [30.0, 33.0, 60.0],
            "mean_no2": [20.0, 22.0, 11.0],
            "mean_o3": [70.0, 75.0, 90.0],
            "who_pm25_exceed_pct": [0.25, 0.5, 1.0],
        }
    )


def test_country_briefings_parses_structured_output():
    payload = {
        "briefings": [
            {"country_code": "GR", "briefing": "PM2.5 averaged 13.0 ug/m3."},
            {"country_code": "TN", "briefing": "PM2.5 averaged 30.0 ug/m3."},
        ]
    }
    client = _Client(_interaction(json.dumps(payload)))

    model, out = ai_brief.country_briefings(_summary(), client)

    assert model == ai_brief.BRIEFING_MODELS[0] == "gemini-3.7-flash"
    assert [b["country_code"] for b in out] == ["GR", "TN"]
    # JSON shape must be enforced by the API, not hoped for.
    fmt = client.calls[0]["response_format"]
    assert fmt["mime_type"] == "application/json"
    assert fmt["schema"]["required"] == ["briefings"]
    # Countries are aggregated across sources before being sent.
    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    assert {r["country_code"] for r in sent} == {"GR", "TN"}
    assert next(r for r in sent if r["country_code"] == "GR")["stations"] == 6
    # response_format is sent even though only 3.x honours it; the lower rungs
    # fall back on the prompt asking for bare JSON.
    assert "JSON" in ai_brief._BRIEFING_SYSTEM


def _weather():
    return pd.DataFrame(
        {
            "partition_date": ["2026-08-19", "2026-08-19"],
            "country_code": ["GR", "TN"],
            "temp_max_c": [38.1, 41.9],
            "temp_min_c": [27.2, 27.2],
            "temp_mean_c": [32.0, 34.1],
            "condition": ["clear", "cloudy"],
            "wind_level": ["breezy", "gale"],
            "dust": [4.0, None],
            "dust_level": ["none", "unknown"],
            "heat_alert": ["heat_advisory", "extreme_heatwave"],
            "heat_streak_days": [1, 4],
            "cold_alert": ["none", "none"],
            "cold_streak_days": [0, 0],
        }
    )


def test_country_briefings_send_the_weather_with_the_pollutants():
    payload = {"briefings": [{"country_code": "GR", "briefing": "Hot and clear."}]}
    client = _Client(_interaction(json.dumps(payload)))

    ai_brief.country_briefings(_summary(), client, _weather())

    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    greece = next(r for r in sent if r["country_code"] == "GR")
    assert greece["temp_max_c"] == 38.1
    assert greece["heat_alert"] == "heat_advisory"
    assert greece["mean_pm2_5"] == 13.0
    # A null field is left out rather than sent as a null to be described.
    tunisia = next(r for r in sent if r["country_code"] == "TN")
    assert "dust" not in tunisia
    assert tunisia["heat_alert"] == "extreme_heatwave"


def test_briefings_still_run_when_there_is_no_weather():
    payload = {"briefings": [{"country_code": "GR", "briefing": "PM2.5 was 13.0."}]}
    client = _Client(_interaction(json.dumps(payload)))

    _, out = ai_brief.country_briefings(_summary(), client, None)

    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    assert all("temp_max_c" not in row for row in sent)
    assert [b["country_code"] for b in out] == ["GR"]


def test_fact_check_is_given_the_weather_over_the_station():
    client = _Client(_interaction("Real and explained by the heat."))

    ai_brief.fact_check_anomaly(
        ANOMALY, STATS, client, ai_brief.weather_by_country(_weather())["GR"]
    )

    assert "temp_max_c 38.1" in client.calls[0]["input"]
    assert "heat_alert heat_advisory" in client.calls[0]["input"]


def test_country_briefings_returns_empty_when_no_model_answers():
    assert ai_brief.country_briefings(_summary(), _Client(_interaction(""))) == (
        None,
        [],
    )


def test_run_without_api_key_writes_nothing(tmp_path, monkeypatch):
    """A fork with no key must skip quietly, never break the pipeline."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = tmp_path / "ai_brief.json"

    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("must not read Gold without a key")

    monkeypatch.setattr(ai_brief, "read_delta", explode)

    ai_brief.run(out)

    assert not out.exists()


def test_round_handles_missing_values():
    assert ai_brief._round(pd.NA) is None
    assert ai_brief._round(float("nan")) is None
    assert ai_brief._round(12.3456) == 12.3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_run_excludes_ghost_countries_the_charts_exclude(tmp_path, monkeypatch):
    """The narrative must cover the same countries as the charts beside it."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    summary = pd.DataFrame(
        {
            "partition_date": ["2026-08-19"] * 3,
            "country_code": ["GR", "NL", "TN"],
            "station_count": [4, 1, 3],
            "mean_pm2_5": [12.0, 7.3, 30.0],
            "max_pm2_5": [20.0, 9.0, 44.0],
            "mean_pm10": [30.0, 13.2, 60.0],
            "mean_no2": [20.0, 8.2, 11.0],
            "mean_o3": [70.0, 78.8, 90.0],
            "who_pm25_exceed_pct": [0.25, 0.0, 1.0],
        }
    )
    payload = {"briefings": [{"country_code": "GR", "briefing": "ok"}]}
    client = _Client(_interaction(json.dumps(payload)))

    monkeypatch.setattr(ai_brief, "_client", lambda: client)
    monkeypatch.setattr(
        ai_brief,
        "read_delta",
        lambda path: summary if "daily_country_summary" in path else pd.DataFrame(),
    )

    ai_brief.run(tmp_path / "ai_brief.json")

    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    assert {r["country_code"] for r in sent} == {"GR", "TN"}, "NL ghost must go"


def test_briefings_parse_tolerates_markdown_fences():
    payload = '```json\n{"briefings": [{"country_code": "GR", "briefing": "ok"}]}\n```'
    client = _Client(_interaction(payload))

    model, out = ai_brief.country_briefings(_summary(), client)

    assert model == ai_brief.BRIEFING_MODELS[0]
    assert out == [{"country_code": "GR", "briefing": "ok"}]


def test_briefings_parse_returns_empty_on_prose():
    """Non-JSON must cost the section, not raise out of the brief."""
    client = _Client(_interaction("Here are the briefings you asked for:"))

    assert ai_brief.country_briefings(_summary(), client) == (None, [])
    # Prose from one model is not an answer: every rung got a turn.
    assert [c["model"] for c in client.calls] == list(ai_brief.BRIEFING_MODELS)


def test_rate_limit_is_retried_but_other_errors_are_not():
    """429 is transient; a bad request is not worth three round trips."""
    assert ai_brief._is_rate_limited(RuntimeError("Error code: 429 - quota")) is True
    assert ai_brief._is_rate_limited(RuntimeError("too_many_requests")) is True
    assert ai_brief._is_rate_limited(RuntimeError("400 invalid argument")) is False


def test_the_two_ladders_lead_with_different_models():
    """Consolidating breaks either grounding or structured output."""
    assert ai_brief.BRIEFING_MODELS[0] != ai_brief.SEARCH_MODELS[0]
    assert ai_brief.SEARCH_MODELS[0].startswith("gemini-2.5")
    # Every ladder needs somewhere to fall.
    assert len(ai_brief.BRIEFING_MODELS) > 1
    assert len(ai_brief.SEARCH_MODELS) > 1


def test_briefings_fall_back_to_the_next_model_when_a_quota_is_spent(monkeypatch):
    """A 429 is counted per model, so the ladder steps down, one retry apart."""
    monkeypatch.setattr(ai_brief._create.retry, "sleep", lambda _seconds: None)
    payload = {"briefings": [{"country_code": "GR", "briefing": "ok"}]}
    spent, fallback = ai_brief.BRIEFING_MODELS[:2]
    calls = []

    def create(**kw):
        calls.append(kw["model"])
        if kw["model"] == spent:
            raise RuntimeError("Error code: 429 - quota exceeded, limit: 20")
        return _interaction(json.dumps(payload))

    client = SimpleNamespace(interactions=SimpleNamespace(create=create))

    model, out = ai_brief.country_briefings(_summary(), client)

    assert (model, out) == (fallback, payload["briefings"])
    assert calls == [spent, spent, fallback], "one retry, then down the ladder"


def test_fact_check_falls_back_and_reports_the_model_that_answered():
    fallback = ai_brief.SEARCH_MODELS[1]

    def create(**kw):
        if kw["model"] != fallback:
            raise RuntimeError("400 model not found")
        return _interaction("Plausible.")

    client = SimpleNamespace(interactions=SimpleNamespace(create=create))

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, client)

    assert out["model"] == fallback
    assert out["verdict"] == "Plausible."


def test_a_fully_spent_ladder_costs_the_section_not_the_run(monkeypatch):
    monkeypatch.setattr(ai_brief._create.retry, "sleep", lambda _seconds: None)

    def create(**kw):
        raise RuntimeError("Error code: 429 - quota exceeded")

    client = SimpleNamespace(interactions=SimpleNamespace(create=create))

    assert ai_brief.fact_check_anomaly(ANOMALY, STATS, client) is None


def test_unreadable_gold_skips_the_brief_instead_of_raising(tmp_path, monkeypatch):
    """The step that runs this is not best-effort, so the module must be."""
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(ai_brief, "_client", lambda: object())

    def capped(*a, **k):
        raise OSError("403 Forbidden: transaction (Class B) cap exceeded")

    monkeypatch.setattr(ai_brief, "read_delta", capped)
    out = tmp_path / "ai_brief.json"

    ai_brief.run(out)

    assert not out.exists()


# ── Heat-risk note ─────────────────────────────────────────────────────────────


def _weather_history():
    """Ten days for two countries: TN climbs into a heatwave, GR stays flat."""
    dates = [f"2026-08-{d:02d}" for d in range(10, 20)]
    tn_highs = [28.0, 29.0, 30.0, 31.0, 33.0, 34.0, 41.0, 42.0, 43.0, 44.0]
    rows = []
    for i, d in enumerate(dates):
        streak = max(0, i - 5)
        rows.append(
            {
                "partition_date": d,
                "country_code": "TN",
                "temp_max_c": tn_highs[i],
                "temp_min_c": tn_highs[i] - 12,
                "condition": "clear",
                "heat_alert": "extreme_heatwave" if streak >= 3 else "none",
                "heat_streak_days": streak,
                "cold_alert": "none",
            }
        )
        rows.append(
            {
                "partition_date": d,
                "country_code": "GR",
                "temp_max_c": 30.0,
                "temp_min_c": 20.0,
                "condition": "cloudy",
                "heat_alert": "none",
                "heat_streak_days": 0,
                "cold_alert": "none",
            }
        )
    return pd.DataFrame(rows)


def test_profile_measures_the_swing_rather_than_leaving_it_to_the_model():
    profile = ai_brief.temperature_profile(_weather_history(), "TN", "2026-08-19")

    assert profile["high_c"] == 44.0
    assert profile["days_of_history"] == 10
    assert profile["window_high_spread_c"] == 16.0  # 44.0 - 28.0
    assert profile["biggest_day_to_day_change_c"] == 7.0  # 34.0 -> 41.0
    assert profile["biggest_change_on"] == "2026-08-16"
    assert profile["day_night_range_c"] == 12.0
    assert profile["heat_alert"] == "extreme_heatwave"
    assert len(profile["days"]) == 10


def test_profile_flags_a_flip_between_heat_and_cold():
    history = _weather_history()
    history.loc[
        (history["country_code"] == "TN") & (history["partition_date"] == "2026-08-10"),
        "cold_alert",
    ] = "cold_wave"

    flipped = ai_brief.temperature_profile(history, "TN", "2026-08-19")
    steady = ai_brief.temperature_profile(_weather_history(), "TN", "2026-08-19")

    assert flipped["swung_between_heat_and_cold"] is True
    assert steady["swung_between_heat_and_cold"] is False


def test_profile_ignores_days_after_the_reporting_date():
    profile = ai_brief.temperature_profile(_weather_history(), "TN", "2026-08-14")

    assert profile["date"] == "2026-08-14"
    assert profile["high_c"] == 33.0


def test_spotlight_picks_the_hottest_country_and_starts_on_the_cheapest_model():
    client = _Client(_interaction("Tunisia reached 44.0 C today."))

    out = ai_brief.heat_spotlight(_weather_history(), client, "2026-08-19")

    assert out["country_code"] == "TN"
    assert out["paragraph"] == "Tunisia reached 44.0 C today."
    # Cheapest rung first, and never the model the briefings ration.
    assert client.calls[0]["model"] == ai_brief.SPOTLIGHT_MODELS[0]
    assert ai_brief.SPOTLIGHT_MODELS[0] == "gemini-2.5-flash-lite"
    assert "gemini-3.7-flash" not in ai_brief.SPOTLIGHT_MODELS
    # The figures behind the prose travel with it, so the caption can cite them.
    assert out["figures"]["biggest_day_to_day_change_c"] == 7.0


def test_spotlight_gives_the_model_only_computed_figures():
    client = _Client(_interaction("A paragraph."))

    ai_brief.heat_spotlight(_weather_history(), client, "2026-08-19")

    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    assert sent["country_code"] == "TN"
    assert sent["window_high_spread_c"] == 16.0
    assert "days" in sent


def test_spotlight_skips_a_day_with_no_temperatures():
    history = _weather_history()
    history["temp_max_c"] = None

    assert (
        ai_brief.heat_spotlight(history, _Client(_interaction("x")), "2026-08-19")
        is None
    )


def test_spotlight_skips_when_there_is_no_weather_at_all():
    assert (
        ai_brief.heat_spotlight(None, _Client(_interaction("x")), "2026-08-19") is None
    )
    assert (
        ai_brief.heat_spotlight(
            pd.DataFrame(), _Client(_interaction("x")), "2026-08-19"
        )
        is None
    )
