"""
data/reporting/ai_brief.py
──────────────────────────
Narrative layer over the latest Gold day, written by Gemini.

Two artefacts, one API call each:

  1. A fact-check of the day's top anomaly. The model is given the flagged
     reading and that day's distribution, and searches Google for corroborating
     real-world conditions (wildfire, dust intrusion, heatwave, traffic event).
     It is told to report an absence of evidence as an absence of evidence — an
     uncorroborated anomaly is a normal outcome, not a prompt to invent a cause.

  2. A one-to-three sentence briefing per country, grounded in that country's
     own Gold aggregates for the day.

Why Gemini: it is the only provider whose free tier includes real search
grounding. Flash text tokens are free, and 2.5 Flash allows 500 grounded
requests a day free — this pipeline uses two calls a day. Costs nothing.

Both artefacts are best-effort. No API key, an API error, or a malformed
response leaves the report without this section rather than failing the
pipeline — this runs after the data is already safely in Gold, and no narrative
is worth losing a run.

Output: docs/ai_brief.json, gitignored and published to Pages with the report.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger

from data.reporting.analytics import (
    filter_anomaly_model_sources,
    filter_report_countries,
)
from data.storage import read_delta

# 2.5 Flash for both calls. Two separate free-tier limits pushed us here:
# grounding is unavailable on Gemini 3.x free tier (a grounded 3.x request is
# rejected outright), and 3.7-flash free tier caps generate_content at 20
# requests, which this pipeline tripped. 2.5 Flash allows 500 grounded requests
# a day and has the headroom for the text call too.
MODEL = "gemini-2.5-flash"
SEARCH_MODEL = MODEL

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
- Do not begin every entry with the country name.
- Return one entry for every country given, and no others."""

_BRIEFING_SCHEMA = {
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
            },
        }
    },
    "required": ["briefings"],
}


def _client():
    """Return a Gemini client, or None when no key is configured."""
    if not os.environ.get("GEMINI_API_KEY"):
        logger.warning("GEMINI_API_KEY not set — skipping AI brief.")
        return None
    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai not installed — skipping AI brief.")
        return None
    return genai.Client()


def _round(value, digits: int = 1):
    return None if pd.isna(value) else round(float(value), digits)


def _citations(interaction) -> list[dict]:
    """Pull url_citation annotations out of an interaction's model output.

    Traversed defensively: a shape change in the SDK should cost the citation
    list, not the whole brief.
    """
    seen: dict[str, str] = {}
    for step in getattr(interaction, "steps", None) or []:
        for block in getattr(step, "content", None) or []:
            for note in getattr(block, "annotations", None) or []:
                if getattr(note, "type", None) != "url_citation":
                    continue
                url = getattr(note, "url", None)
                if url and url not in seen:
                    seen[url] = getattr(note, "title", "") or url
    return [{"title": title, "url": url} for url, title in seen.items()]


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

    interaction = client.interactions.create(
        model=SEARCH_MODEL,
        system_instruction=_FACT_CHECK_SYSTEM,
        input=prompt,
        tools=[{"type": "google_search"}],
    )

    verdict = (interaction.output_text or "").strip()
    if not verdict:
        logger.warning("Anomaly fact-check returned no text.")
        return None

    sources = _citations(interaction)
    return {
        "station": anomaly["station_name"],
        "country_code": anomaly["country_code"],
        "model": SEARCH_MODEL,
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

    interaction = client.interactions.create(
        model=MODEL,
        system_instruction=_BRIEFING_SYSTEM,
        input=(
            "Write a briefing for each country below. Values are ug/m3 daily "
            "means; who_pm25_exceed_pct is the fraction of station-days above "
            "the WHO PM2.5 guideline.\n\n" + json.dumps(rows, indent=2)
        ),
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": _BRIEFING_SCHEMA,
        },
    )

    text = (interaction.output_text or "").strip()
    if not text:
        logger.warning("Country briefings returned no text.")
        return []

    return json.loads(text).get("briefings", [])


def run(output_path: str | Path = "docs/ai_brief.json") -> None:
    client = _client()
    if client is None:
        return

    gold = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    # Same filters the charts use — a briefing about a country the report excludes
    # would contradict every figure next to it.
    summary = filter_report_countries(read_delta(f"s3://{gold}/daily_country_summary"))
    anomalies = filter_anomaly_model_sources(
        filter_report_countries(read_delta(f"s3://{gold}/anomaly_alerts"))
    )

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

    try:
        # Inside the try: an empty or unexpectedly-shaped anomaly table must cost
        # the fact-check only, never the briefings that follow.
        day = anomalies[anomalies["partition_date"] == latest_date]
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
