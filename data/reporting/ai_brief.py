"""
data/reporting/ai_brief.py
──────────────────────────
Narrative layer over the latest Gold day, written by Claude.

Two artefacts, one API call each:

  1. A fact-check of the day's top anomaly. The model is given the flagged
     reading and that day's distribution, and searches the web for corroborating
     real-world conditions (wildfire, dust intrusion, heatwave, traffic event).
     It is told to report an absence of evidence as an absence of evidence — an
     uncorroborated anomaly is a normal outcome, not a prompt to invent a cause.

  2. A one-to-three sentence briefing per country, grounded in that country's
     own Gold aggregates for the day.

Both are best-effort. No API key, an API error, or a malformed response leaves
the report without this section rather than failing the pipeline — this runs
after the data is already safely in Gold, and no narrative is worth losing a run.

Output: docs/ai_brief.json, gitignored and published to Pages with the report.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

from data.storage import read_delta

MODEL = "claude-opus-5"
_MAX_TOKENS = 4096
# ponytail: cap searches per run — this is a daily cron, not an investigation.
_MAX_SEARCHES = 4

_FACT_CHECK_SYSTEM = """You are auditing an automated air-quality anomaly detector.

You are given one reading an Isolation Forest flagged as the most anomalous of the
day, plus that day's distribution across all monitored stations. Decide whether the
reading is plausible and whether anything in the real world explains it.

Search for conditions at that location on that date: wildfires, Saharan dust
intrusion, heatwaves, industrial incidents, traffic or transport disruption,
sandstorms, or public air-quality warnings.

Rules:
- Ground every claim in the numbers you are given or in a source you found.
- If you find no corroborating event, say so plainly. No corroboration is the
  normal case and a perfectly good answer. Never invent a cause to fill the gap.
- Distinguish "this reading is physically implausible / likely a sensor fault"
  from "this reading is real and explained by X" from "real but unexplained".
- 3 sentences maximum. Lead with the verdict. Include the relevant numbers."""

_BRIEFING_SYSTEM = """You write daily air-quality briefings for a public monitoring dashboard.

For each country you are given that day's aggregated measurements. Write one to
three sentences per country.

Rules:
- Cite the actual numbers you are given. Never state a figure you were not given.
- Compare against WHO 2021 24-hour guidelines where relevant: PM2.5 15, PM10 45,
  NO2 25, O3 100 ug/m3.
- Plain, factual, non-alarmist. No advice, no speculation about causes you cannot
  see in the data.
- If a country's station count is low, say the reading is based on few stations.
- Do not begin every entry with the country name."""


def _client():
    """Return an Anthropic client, or None when no key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning("ANTHROPIC_API_KEY not set — skipping AI brief.")
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — skipping AI brief.")
        return None
    return anthropic.Anthropic()


def _text(response) -> str:
    """Concatenate the text blocks of a response, ignoring tool-result blocks."""
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def _round(value, digits: int = 1):
    return None if pd.isna(value) else round(float(value), digits)


def fact_check_anomaly(anomaly: dict, day_stats: dict, client) -> dict | None:
    """Fact-check the day's top anomaly against real-world conditions."""
    prompt = (
        f"Date: {anomaly['partition_date']}\n"
        f"Station: {anomaly['station_name']} ({anomaly['country_code']}), "
        f"source {anomaly['source']}\n"
        f"Isolation Forest score: {anomaly['anomaly_score']:.3f} "
        f"(lower is more anomalous; flagged={bool(anomaly['is_anomaly'])})\n\n"
        "Flagged reading (ug/m3, null means the station has no sensor for it):\n"
        f"  PM2.5 {anomaly['pm2_5']}\n"
        f"  Ozone {anomaly['ozone']}\n"
        f"  NO2   {anomaly['nitrogen_dioxide']}\n\n"
        "That day across all monitored stations:\n"
        f"  PM2.5 median {day_stats['pm2_5']}\n"
        f"  Ozone median {day_stats['ozone']}\n"
        f"  NO2   median {day_stats['nitrogen_dioxide']}\n"
        f"  Stations reporting: {day_stats['stations']}\n\n"
        "Is this reading plausible, and does anything explain it?"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=_MAX_TOKENS,
        system=_FACT_CHECK_SYSTEM,
        thinking={"type": "adaptive"},
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": _MAX_SEARCHES,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "refusal":
        logger.warning("Anomaly fact-check refused — skipping.")
        return None

    sources = []
    for block in response.content:
        if block.type != "web_search_tool_result":
            continue
        # A successful search returns a list; an error returns a single object.
        if not isinstance(block.content, list):
            code = getattr(block.content, "error_code", block.content)
            logger.warning(f"Web search error: {code}")
            continue
        for result in block.content:
            url = getattr(result, "url", None)
            if url:
                title = getattr(result, "title", "") or url
                sources.append({"title": title, "url": url})

    verdict = _text(response)
    if not verdict:
        return None

    return {
        "station": anomaly["station_name"],
        "country_code": anomaly["country_code"],
        "verdict": verdict,
        "sources": sources[:6],
        "searched": bool(sources),
    }


def country_briefings(latest: pd.DataFrame, client) -> list[dict]:
    """One to three sentences per country for the latest day."""
    rows = []
    for country, grp in latest.groupby("country_code"):
        rows.append(
            {
                "country_code": country,
                "stations": int(grp["station_count"].sum()),
                "mean_pm2_5": _round(grp["mean_pm2_5"].mean()),
                "max_pm2_5": _round(grp["max_pm2_5"].max()),
                "mean_pm10": _round(grp["mean_pm10"].mean()),
                "mean_no2": _round(grp["mean_no2"].mean()),
                "mean_o3": _round(grp["mean_o3"].mean()),
                "who_pm25_exceed_pct": _round(grp["who_pm25_exceed_pct"].mean(), 3),
            }
        )

    if not rows:
        return []

    response = client.messages.create(
        model=MODEL,
        max_tokens=_MAX_TOKENS,
        system=_BRIEFING_SYSTEM,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Write a briefing for each country below. Values are ug/m3 "
                    "daily means; who_pm25_exceed_pct is the fraction of "
                    "station-days above the WHO PM2.5 guideline.\n\n"
                    + json.dumps(rows, indent=2)
                ),
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "briefings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "country_code": {"type": "string"},
                                    "briefing": {"type": "string"},
                                },
                                "required": ["country_code", "briefing"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["briefings"],
                    "additionalProperties": False,
                },
            }
        },
    )

    if response.stop_reason == "refusal":
        logger.warning("Country briefings refused — skipping.")
        return []

    return json.loads(_text(response)).get("briefings", [])


def run(output_path: str | Path = "docs/ai_brief.json") -> None:
    client = _client()
    if client is None:
        return

    gold = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    summary = read_delta(f"s3://{gold}/daily_country_summary")
    anomalies = read_delta(f"s3://{gold}/anomaly_alerts")

    latest_date = str(summary["partition_date"].max())
    latest = summary[summary["partition_date"] == latest_date]
    logger.info(
        f"AI brief for {latest_date}: {latest['country_code'].nunique()} countries."
    )

    brief: dict = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "model": MODEL,
        "date": latest_date,
        "fact_check": None,
        "briefings": [],
    }

    day = anomalies[anomalies["partition_date"] == latest_date]
    try:
        if not day.empty:
            top = day.nsmallest(1, "anomaly_score").iloc[0]
            stats = {
                "pm2_5": _round(day["pm2_5"].median()),
                "ozone": _round(day["ozone"].median()),
                "nitrogen_dioxide": _round(day["nitrogen_dioxide"].median()),
                "stations": int(day["station_id"].nunique()),
            }
            brief["fact_check"] = fact_check_anomaly(
                {
                    "partition_date": latest_date,
                    "station_name": top["station_name"],
                    "country_code": top["country_code"],
                    "source": top["source"],
                    "anomaly_score": float(top["anomaly_score"]),
                    "is_anomaly": int(top["is_anomaly"]),
                    "pm2_5": _round(top["pm2_5"]),
                    "ozone": _round(top["ozone"]),
                    "nitrogen_dioxide": _round(top["nitrogen_dioxide"]),
                },
                stats,
                client,
            )
    except Exception as exc:
        logger.warning(f"Anomaly fact-check failed (non-fatal): {exc}")

    try:
        brief["briefings"] = country_briefings(latest, client)
    except Exception as exc:
        logger.warning(f"Country briefings failed (non-fatal): {exc}")

    if brief["fact_check"] is None and not brief["briefings"]:
        logger.warning("AI brief produced nothing — not writing a file.")
        return

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(
        f"AI brief written: {len(brief['briefings'])} briefing(s), "
        f"fact-check={'yes' if brief['fact_check'] else 'no'} -> {out}"
    )


if __name__ == "__main__":
    run()
