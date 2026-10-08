"""
data/reporting/ai_brief.py
──────────────────────────
Narrative layer over the latest Gold day, written by Gemini.

Three artefacts, one API call each, though each call walks a ladder of models
and takes the first that answers, so a spent free-tier quota costs a better
model rather than the whole section:

  1. A fact-check of the day's top anomaly. The model is given the flagged
     reading, that day's distribution, and the weather over that country, then
     searches Google for corroborating real-world conditions (wildfire, dust
     intrusion, heatwave, traffic event). It is told to report an absence of
     evidence as an absence of evidence — an uncorroborated anomaly is a normal
     outcome, not a prompt to invent a cause.

  2. A one-to-three sentence briefing per country, grounded in that country's
     own Gold aggregates for the day: pollutants from daily_country_summary,
     temperature, conditions and heat alerts from daily_country_weather.

  3. A paragraph on the country leading today's seasonal story: the hottest in
     summer, the coldest in winter, the wettest in autumn, the dustiest in
     spring, or whichever is inside a heatwave or cold wave right now. The
     season and the ranking come from data/reporting/season.py. Every figure in
     the paragraph, including the size of each swing, is computed here and
     handed over rather than left to the model to read off a list.

Why Gemini: it is the only provider whose free tier includes real search
grounding. Flash text tokens are free, and 2.5 Flash allows 500 grounded
requests a day free. This pipeline uses three calls a day and costs nothing.

All three are best-effort. No API key, an API error, an unreadable Gold layer,
or a malformed response leaves the report without that section rather than
failing the pipeline. This runs after the data is already safely in Gold, and
no narrative is worth losing a run.

Output: docs/ai_brief.json, gitignored and published to Pages with the report.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from data.reporting.analytics import (
    filter_anomaly_model_sources,
    filter_report_countries,
)
from data.reporting.season import focus_for, rank_countries, season_of
from data.storage import read_delta

# A ladder per job, tried in order, first model that answers wins. Free-tier
# quota is counted per model, so a 429 means step down the ladder, not wait.
# What each job needs, established by running them:
#
#   response_format JSON schema  3.7-flash honours it; 2.5-flash ignores it, so
#                                the prompt asks for bare JSON as well and the
#                                parser tolerates a fence. Belt and braces beats
#                                a section that only one model can produce.
#   Google Search grounding      free on 2.5; on 3.x the free tier reports
#                                "Not available" and a grounded request 429s
#                                outright, so 3.x never enters the search ladder.
#
# Ordered best-first: the head of each ladder gives the best answer, the tail
# has the loosest free-tier quota. 3.7-flash allows 20 requests/day free and is
# routinely spent by mid-morning, which is exactly what the ladder is for.
BRIEFING_MODELS = ("gemini-3.7-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite")
SEARCH_MODELS = ("gemini-2.5-flash", "gemini-2.5-flash-lite")

# The spotlight is the one ladder that runs cheapest-first rather than
# best-first. It is a single paragraph over a dozen numbers that are already
# computed, which the smallest model does perfectly well, and starting at the
# bottom keeps it out of the 20-a-day free quota that gemini-3.7-flash rations
# and the briefings actually need.
SPOTLIGHT_MODELS = ("gemini-2.5-flash-lite", "gemini-2.5-flash")

# Days of history behind the spotlight note. Long enough to show a swing
# building, short enough that the model is reading a handful of numbers.
SPOTLIGHT_DAYS = 10

_HEAT_ALERTS = {"heat_advisory", "heatwave", "extreme_heatwave"}
_COLD_ALERTS = {"cold_advisory", "cold_wave", "severe_cold_wave"}

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

_BRIEFING_SYSTEM = """You write daily weather and air-quality briefings for a public monitoring dashboard.

For each country you are given that day's aggregated measurements, and usually
that day's weather. Write one to three sentences per country.

Rules:
- Cite the actual numbers you are given. Never state a figure you were not given.
- Compare against WHO 2021 24-hour guidelines where relevant: PM2.5 15, PM10 45,
  NO2 25, O3 100 ug/m3.
- Say what the weather did. Give the day's high in Celsius, and name any heat or
  cold alert, strong wind, rain or dust the numbers show. Write it the way a
  person would, not as a raw label: "hot and still", "a gale off the sea".
- heat_alert reads: heat_advisory means one or two days above that station's own
  recent normal; heatwave means three or more in a row; extreme_heatwave means
  three or more with a high at or above 40 C. cold_alert mirrors it. Do not call
  a day a heatwave when the alert does not.
- Weather figures may be missing for a country. Then write about air quality
  alone and do not guess at the weather.
- Plain, factual, non-alarmist. No advice, no speculation about causes you cannot
  see in the data.
- If a country's station count is low, say the reading is based on few stations.
- Do not begin every entry with the country name.
- Return one entry for every country given, and no others.

Answer with bare JSON and nothing else, no markdown fence, no preamble:
{"briefings": [{"country_code": "XX", "briefing": "..."}]}"""

_SPOTLIGHT_SYSTEM = """You write a short weather-risk note for a public weather and air-quality dashboard.

You are given the recent daily weather for one country, picked because it leads
today on the measure named in the input, plus its heat and cold alerts and the
size of the swings between those days.

Write one paragraph, three to five sentences.

Rules:
- Name the country and today's figure for that measure. Use only figures you
  were given.
- {focus}
- Cover the swing as well as the level. A large jump between two days, a wide
  spread across the window, or a flip between heat and cold alerts is worth
  naming, because a body adapts to gradual change and not to a sudden one.
- Alert levels mean: heat_advisory, one or two days above that station's own
  recent normal; heatwave, three or more in a row; extreme_heatwave, three or
  more with a high at or above 40 C. The cold alerts mirror them, with
  severe_cold_wave at or below 0 C.
- If there is no alert and nothing notable, say so plainly. The leading country
  on an ordinary day is an ordinary finding, not a story.
- Plain and factual. No alarm, no reassurance, no instructions, nothing
  addressed to an individual reader as advice.

Return the paragraph as plain text. No heading, no markdown, no preamble."""

# What each focus asks the model to explain, in general and well-established
# terms.
_SPOTLIGHT_FOCUS = {
    "heat": (
        "Say what heat at that level does to people: who feels it first, what "
        "sustained heat costs a body, why a night that stays warm matters as "
        "much as the afternoon. No diagnosis, no treatment."
    ),
    "cold": (
        "Say what cold at that level does to people: who feels it first, why "
        "several cold days in a row cost more than one, why a night below "
        "freezing matters for homes that are hard to heat. No diagnosis, no "
        "treatment."
    ),
    "rain": (
        "Say what that much rain means on the ground: heavy rain on dry ground "
        "runs off instead of soaking in, which is how Mediterranean flash floods "
        "start, and one day's total matters more than the window's. If the "
        "window was dry, say that instead."
    ),
    "dust": (
        "Say what Saharan dust at that level does: fine particles reach deep "
        "into the lungs, people with asthma or heart and lung conditions feel "
        "it first, and it often arrives on a warm southerly wind. If dust was "
        "low, say that instead."
    ),
}

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


# The SDK waits forever by default. A request that never answers held a report
# build for twenty minutes before it was killed by hand, and the ladder below
# cannot step to the next model until the current one returns something, even
# an error. Grounded search legitimately takes tens of seconds, so the bar is
# set well above a slow answer and well below a stalled one.
_REQUEST_TIMEOUT_MS = 180_000


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
    return genai.Client(http_options={"timeout": _REQUEST_TIMEOUT_MS})


def _round(value, digits: int = 1):
    return None if pd.isna(value) else round(float(value), digits)


def _days(value) -> int:
    return 0 if value is None or pd.isna(value) else int(value)


def _is_rate_limited(exc: BaseException) -> bool:
    """True for 429s, whether a per-minute bump or a spent daily quota."""
    return "429" in str(exc) or "too_many_requests" in str(exc).lower()


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=4, min=4, max=30),
    retry=retry_if_exception(_is_rate_limited),
    reraise=True,
)
def _create(client, **kwargs):
    """Call the API, retrying a rate-limit bump once.

    One retry, not three: a spent daily quota never clears within the run, and
    the next model down the ladder is a better answer to a 429 than waiting.
    """
    return client.interactions.create(**kwargs)


def _first_working(client, models, extract, **kwargs):
    """Run the same request down a ladder of models; first usable answer wins.

    `extract` turns an interaction into the artefact and returns something
    falsy when the response is unusable: empty text, or prose where JSON was
    asked for. That counts as a failure of that model, so the ladder continues
    rather than accepting an empty section from the first model that replied.

    Returns (model, artefact), or (None, None) when the whole ladder is spent.
    Every failure is non-fatal by design: no narrative is worth a pipeline.
    """
    for model in models:
        try:
            artefact = extract(_create(client, model=model, **kwargs))
        except Exception as exc:
            logger.warning(f"{model} failed: {exc}")
            continue
        if artefact:
            logger.info(f"{model} answered.")
            return model, artefact
        logger.warning(f"{model} returned nothing usable.")
    logger.warning(f"No model answered: {', '.join(models)}")
    return None, None


def _parse_briefings(text: str) -> list[dict]:
    """Parse the briefings payload, tolerating a markdown-fenced response.

    response_format asks for bare JSON, but a fenced ```json block is a common
    way for it to come back anyway; stripping the fence is cheaper than losing
    the section. Anything else is logged with a snippet so the next failure is
    diagnosable from the run log instead of a re-run.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split(chr(10), 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        return json.loads(cleaned).get("briefings", [])
    except (json.JSONDecodeError, AttributeError):
        logger.warning(
            f"Briefings response was not JSON. First 300 chars: {text[:300]!r}"
        )
        return []


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


def fact_check_anomaly(
    anomaly: dict,
    day_stats: dict,
    client,
    weather: dict | None = None,
) -> dict | None:
    """Fact-check the day's top anomaly against real-world conditions."""
    # The pipeline now knows what the weather was doing over that station, so
    # the model can weigh a heatwave or a dust intrusion against the reading
    # instead of searching blind for one.
    weather_block = ""
    if weather:
        weather_block = "That country's weather that day:\n" + "".join(
            f"  {field} {value}\n" for field, value in weather.items()
        )

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
        f"{weather_block}\n"
        "Is this reading plausible, and does anything explain it?"
    )

    def _verdict(interaction):
        text = (interaction.output_text or "").strip()
        return (text, _citations(interaction)) if text else None

    model, answer = _first_working(
        client,
        SEARCH_MODELS,
        _verdict,
        system_instruction=_FACT_CHECK_SYSTEM,
        input=prompt,
        tools=[{"type": "google_search"}],
    )
    if answer is None:
        return None

    verdict, sources = answer
    return {
        "station": anomaly["station_name"],
        "country_code": anomaly["country_code"],
        "model": model,
        "verdict": verdict,
        "sources": sources[:6],
        "searched": bool(sources),
    }


# Weather columns worth a sentence, in the order the model reads them. The
# baselines and station counts behind the alerts stay in Gold: the model needs
# the verdict and the temperature, not the arithmetic that produced them.
_WEATHER_FIELDS = (
    "temp_max_c",
    "temp_min_c",
    "temp_mean_c",
    "apparent_temp_max_c",
    "condition",
    "wind_level",
    "wind_gust_max_kmh",
    "precipitation_mm",
    "humidity_pct",
    "dust",
    "dust_level",
    "heat_alert",
    "heat_streak_days",
    "cold_alert",
    "cold_streak_days",
)


def weather_by_country(weather: pd.DataFrame | None) -> dict[str, dict]:
    """That day's weather per country, keyed for lookup by country code."""
    if weather is None or weather.empty:
        return {}

    lookup: dict[str, dict] = {}
    for record in weather.to_dict("records"):
        entry = {}
        for field in _WEATHER_FIELDS:
            value = record.get(field)
            if value is None or (not isinstance(value, str) and pd.isna(value)):
                continue
            entry[field] = value if isinstance(value, str) else _round(value)
        if entry:
            lookup[record["country_code"]] = entry
    return lookup


def weather_profile(
    history: pd.DataFrame,
    country: str,
    latest_date: str,
    days: int = SPOTLIGHT_DAYS,
) -> dict | None:
    """One country's recent weather, and the swings worth naming.

    The swings are measured here rather than left to the model. Asking it to
    eyeball a jump from a list is asking it to do arithmetic it is not reliably
    good at, and every figure in the note has to be one the pipeline computed.
    """
    rows = (
        history[
            (history["country_code"] == country)
            & (history["partition_date"].astype(str) <= str(latest_date))
        ]
        .sort_values("partition_date")
        .tail(days)
    )
    if rows.empty:
        return None

    highs = pd.to_numeric(rows["temp_max_c"], errors="coerce")
    lows = pd.to_numeric(rows["temp_min_c"], errors="coerce")
    latest = rows.iloc[-1]

    alerts = set(rows.get("heat_alert", pd.Series(dtype=str)).dropna()) | set(
        rows.get("cold_alert", pd.Series(dtype=str)).dropna()
    )

    profile = {
        "country_code": country,
        "date": str(latest["partition_date"]),
        "days_of_history": int(len(rows)),
        "high_c": _round(latest["temp_max_c"]),
        "low_c": _round(latest["temp_min_c"]),
        "day_night_range_c": _round(highs.iloc[-1] - lows.iloc[-1]),
        "heat_alert": latest.get("heat_alert"),
        "heat_streak_days": _days(latest.get("heat_streak_days")),
        "cold_alert": latest.get("cold_alert"),
        "cold_streak_days": _days(latest.get("cold_streak_days")),
        "condition": latest.get("condition"),
        "window_highest_c": _round(highs.max()),
        "window_lowest_c": _round(lows.min()),
        "window_high_spread_c": _round(highs.max() - highs.min()),
        # A window holding both kinds of alert is the hot-to-cold flip the
        # dashboard most wants surfaced.
        "swung_between_heat_and_cold": bool(
            (alerts & _HEAT_ALERTS) and (alerts & _COLD_ALERTS)
        ),
    }

    if "precipitation_mm" in rows:
        rain = pd.to_numeric(rows["precipitation_mm"], errors="coerce")
        profile["rain_today_mm"] = _round(rain.iloc[-1])
        profile["window_rain_total_mm"] = _round(rain.sum())
        profile["window_wettest_day_mm"] = _round(rain.max())
    if "dust" in rows:
        dust = pd.to_numeric(rows["dust"], errors="coerce")
        profile["dust_today"] = _round(dust.iloc[-1])
        profile["window_dust_peak"] = _round(dust.max())

    profile["days"] = [
        {
            "date": str(row["partition_date"]),
            "high_c": _round(row["temp_max_c"]),
            "low_c": _round(row["temp_min_c"]),
            **{
                key: _round(row[column])
                for key, column in (("rain_mm", "precipitation_mm"), ("dust", "dust"))
                if column in row
            },
            "heat_alert": row.get("heat_alert"),
            "cold_alert": row.get("cold_alert"),
        }
        for _, row in rows.iterrows()
    ]

    steps = highs.diff()
    if steps.notna().any():
        sharpest = steps.abs().idxmax()
        profile["biggest_day_to_day_change_c"] = _round(steps.loc[sharpest])
        profile["biggest_change_on"] = str(rows.loc[sharpest, "partition_date"])

    return profile


def weather_spotlight(
    history: pd.DataFrame | None, client, latest_date: str
) -> dict | None:
    """A paragraph on the country leading today's seasonal weather story."""
    if history is None or history.empty:
        return None

    focus = focus_for(history, latest_date)
    ranking = rank_countries(history, focus, str(latest_date))
    if ranking.empty:
        logger.warning(f"No {focus.column} for {latest_date}, skipping the spotlight.")
        return None

    country = ranking.index[0]
    profile = weather_profile(history, country, latest_date)
    if profile is None:
        return None

    season = season_of(latest_date)
    measure = focus.measure
    value = _round(ranking.iloc[0])
    logger.info(f"Spotlight on {country}, {season}, {measure}: {value}.")

    model, paragraph = _first_working(
        client,
        SPOTLIGHT_MODELS,
        lambda interaction: (interaction.output_text or "").strip(),
        system_instruction=_SPOTLIGHT_SYSTEM.format(focus=_SPOTLIGHT_FOCUS[focus.name]),
        input=(
            f"Season: {season}. Temperatures are Celsius, rain mm, dust ug/m3. "
            f"Of every country covered, this one had the {measure}: "
            f"{value} {focus.unit}.\n\n" + json.dumps(profile, indent=2)
        ),
    )
    if not paragraph:
        return None

    return {
        "country_code": country,
        "date": profile["date"],
        "season": season,
        "focus": focus.name,
        "measure": measure,
        "value": value,
        "unit": focus.unit,
        "model": model,
        "paragraph": paragraph,
        "figures": profile,
    }


def country_briefings(
    latest: pd.DataFrame,
    client,
    weather: pd.DataFrame | None = None,
) -> tuple[str | None, list[dict]]:
    """One to three sentences per country, and the model that wrote them."""
    conditions = weather_by_country(weather)
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
                **conditions.get(country, {}),
            }
        )

    if not rows:
        return None, []

    with_weather = sum(1 for row in rows if "temp_max_c" in row)
    logger.info(f"Weather attached to {with_weather} of {len(rows)} countries.")

    model, briefings = _first_working(
        client,
        BRIEFING_MODELS,
        lambda interaction: _parse_briefings(interaction.output_text or ""),
        system_instruction=_BRIEFING_SYSTEM,
        input=(
            "Write a briefing for each country below. Pollutant values are "
            "ug/m3 daily means; who_pm25_exceed_pct is the fraction of "
            "station-days above the WHO PM2.5 guideline. Temperatures are "
            "Celsius, wind gusts km/h, precipitation mm, dust ug/m3.\n\n"
            + json.dumps(rows, indent=2)
        ),
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": _BRIEFING_SCHEMA,
        },
    )
    return model, briefings or []


def run(output_path: str | Path = "docs/ai_brief.json") -> None:
    client = _client()
    if client is None:
        return

    gold = os.environ.get("MINIO_BUCKET_GOLD", "gold")
    # Same filters the charts use — a briefing about a country the report excludes
    # would contradict every figure next to it.
    #
    # Guarded because this section is best-effort and the step that runs it is
    # not. An exhausted B2 daily cap 403s here, and an unguarded raise turned
    # that into a failed report build rather than a report without a brief.
    try:
        summary = filter_report_countries(
            read_delta(f"s3://{gold}/daily_country_summary")
        )
        anomalies = filter_anomaly_model_sources(
            filter_report_countries(read_delta(f"s3://{gold}/anomaly_alerts"))
        )
    except Exception as exc:
        logger.warning(f"Gold unreadable — skipping the AI brief entirely: {exc}")
        return

    latest_date = str(summary["partition_date"].max())
    latest = summary[summary["partition_date"] == latest_date]
    logger.info(
        f"AI brief for {latest_date}: {latest['country_code'].nunique()} countries."
    )

    # Weather is a whole Gold table younger than the rest, and a day where it
    # failed to land should still get an air-quality brief. Read it defensively
    # and let an absence be an absence.
    # The whole table, not just today: the spotlight needs the days behind the
    # latest one to say whether the weather turned gradually or all at once.
    try:
        weather_history = filter_report_countries(
            read_delta(f"s3://{gold}/daily_country_weather")
        )
        weather = weather_history[weather_history["partition_date"] == latest_date]
    except Exception as exc:
        logger.warning(f"Gold weather unavailable for the brief: {exc}")
        weather_history = weather = None

    brief: dict = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "model": None,
        "date": latest_date,
        "fact_check": None,
        "weather_spotlight": None,
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
                weather_by_country(weather).get(top["country_code"]),
            )
    except Exception as exc:
        logger.warning(f"Anomaly fact-check failed (non-fatal): {exc}")

    try:
        brief["weather_spotlight"] = weather_spotlight(
            weather_history, client, latest_date
        )
    except Exception as exc:
        logger.warning(f"Spotlight note failed (non-fatal): {exc}")

    try:
        model, brief["briefings"] = country_briefings(latest, client, weather)
        # The report caption names the model that actually answered, which the
        # ladder makes a runtime fact rather than a constant.
        brief["model"] = (
            model
            or (brief["fact_check"] or {}).get("model")
            or (brief["weather_spotlight"] or {}).get("model")
        )
    except Exception as exc:
        logger.warning(f"Country briefings failed (non-fatal): {exc}")

    if (
        brief["fact_check"] is None
        and brief["weather_spotlight"] is None
        and not brief["briefings"]
    ):
        logger.warning("AI brief produced nothing — not writing a file.")
        return

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.success(
        f"AI brief written: {len(brief['briefings'])} briefing(s) "
        f"via {brief['model'] or 'no model'}, "
        f"fact-check={'yes' if brief['fact_check'] else 'no'}, "
        f"spotlight={'yes' if brief['weather_spotlight'] else 'no'} -> {out}"
    )


if __name__ == "__main__":
    run()
