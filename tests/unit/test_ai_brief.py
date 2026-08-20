import json
from types import SimpleNamespace

import pandas as pd
import pytest

from data.reporting import ai_brief


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


class _Client:
    """Stand-in for anthropic.Anthropic that returns a canned response."""

    def __init__(self, response):
        self.messages = SimpleNamespace(create=lambda **kw: self._record(kw, response))
        self.calls = []

    def _record(self, kwargs, response):
        self.calls.append(kwargs)
        return response


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


def test_fact_check_collects_verdict_and_sources():
    search = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(url="https://example.org/a", title="Athens traffic"),
            SimpleNamespace(url="https://example.org/b", title="Air quality alert"),
        ],
    )
    client = _Client(
        _response([search, _text_block("Plausible. NO2 77.1 is traffic.")])
    )

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, client)

    assert out["verdict"].startswith("Plausible")
    assert out["searched"] is True
    assert [s["url"] for s in out["sources"]] == [
        "https://example.org/a",
        "https://example.org/b",
    ]
    # Web search must actually be enabled on the request.
    tools = client.calls[0]["tools"]
    assert tools[0]["type"] == "web_search_20260209"
    assert client.calls[0]["model"] == "claude-opus-5"


def test_fact_check_survives_a_web_search_error_block():
    """A search error returns an object, not a list — it must not crash."""
    err = SimpleNamespace(
        type="web_search_tool_result",
        content=SimpleNamespace(error_code="max_uses_exceeded"),
    )
    client = _Client(_response([err, _text_block("No corroboration found.")]))

    out = ai_brief.fact_check_anomaly(ANOMALY, STATS, client)

    assert out["verdict"] == "No corroboration found."
    assert out["sources"] == []
    assert out["searched"] is False


def test_fact_check_returns_none_on_refusal():
    client = _Client(_response([_text_block("x")], stop_reason="refusal"))
    assert ai_brief.fact_check_anomaly(ANOMALY, STATS, client) is None


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
    client = _Client(_response([_text_block(json.dumps(payload))]))

    out = ai_brief.country_briefings(_summary(), client)

    assert [b["country_code"] for b in out] == ["GR", "TN"]
    # The schema must be enforced server-side, not hoped for.
    schema = client.calls[0]["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["schema"]["required"] == ["briefings"]
    # Countries are aggregated across sources before being sent.
    sent = json.loads(client.calls[0]["messages"][0]["content"].split("\n\n", 1)[1])
    assert {r["country_code"] for r in sent} == {"GR", "TN"}
    assert next(r for r in sent if r["country_code"] == "GR")["stations"] == 6


def test_country_briefings_returns_empty_on_refusal():
    client = _Client(_response([_text_block("{}")], stop_reason="refusal"))
    assert ai_brief.country_briefings(_summary(), client) == []


def test_run_without_api_key_writes_nothing(tmp_path, monkeypatch):
    """A fork with no key must skip quietly, never break the pipeline."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
