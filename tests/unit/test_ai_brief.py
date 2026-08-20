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
    # Grounding is free-tier only on 2.5, so the search call must not use 3.x.
    assert client.calls[0]["model"] == ai_brief.SEARCH_MODEL == "gemini-2.5-flash"


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

    out = ai_brief.country_briefings(_summary(), client)

    assert [b["country_code"] for b in out] == ["GR", "TN"]
    # JSON shape must be enforced by the API, not hoped for.
    fmt = client.calls[0]["response_format"]
    assert fmt["mime_type"] == "application/json"
    assert fmt["schema"]["required"] == ["briefings"]
    # Countries are aggregated across sources before being sent.
    sent = json.loads(client.calls[0]["input"].split("\n\n", 1)[1])
    assert {r["country_code"] for r in sent} == {"GR", "TN"}
    assert next(r for r in sent if r["country_code"] == "GR")["stations"] == 6


def test_country_briefings_returns_empty_on_empty_output():
    assert ai_brief.country_briefings(_summary(), _Client(_interaction(""))) == []


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
            "country_code": ["GR", "FR", "TN"],
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
    assert {r["country_code"] for r in sent} == {"GR", "TN"}, "FR must be filtered out"
