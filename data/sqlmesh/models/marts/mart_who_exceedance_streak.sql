-- Consecutive station days with at least one WHO daily guideline exceeded.
MODEL (
  name marts.mart_who_exceedance_streak,
  kind FULL,
  grain (observation_date, station_id, source),
  audits (
    not_null(columns := (
      observation_date, station_id, country_code, source,
      current_exceedance_streak_days, mart_ts
    )),
    accepted_values(column := source, is_in := ('openmeteo', 'openaq', 'waqi')),
    accepted_values(column := has_who_exceedance, is_in := (0, 1)),
    accepted_range(column := current_exceedance_streak_days, min_v := 0)
  )
);

WITH station_days AS (
  SELECT
    observation_date,
    station_id,
    station_name,
    country_code,
    source,
    MAX(who_pm25_exceed) AS who_pm25_exceed,
    MAX(who_pm10_exceed) AS who_pm10_exceed,
    MAX(who_no2_exceed) AS who_no2_exceed,
    MAX(who_o3_exceed) AS who_o3_exceed
  FROM staging.stg_air_quality
  GROUP BY 1, 2, 3, 4, 5
),

marked AS (
  SELECT
    *,
    CASE
      WHEN who_pm25_exceed = 1
        OR who_pm10_exceed = 1
        OR who_no2_exceed = 1
        OR who_o3_exceed = 1
      THEN 1
      ELSE 0
    END AS has_who_exceedance
  FROM station_days
),

-- Each clean day opens a new group, so the exceedance days counted so far in
-- the group are the current streak, and the clean day itself counts 0.
grouped AS (
  SELECT
    *,
    SUM(CASE WHEN has_who_exceedance = 0 THEN 1 ELSE 0 END) OVER (
      PARTITION BY station_id, source
      ORDER BY observation_date
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS streak_group
  FROM marked
)

SELECT
  observation_date,
  station_id,
  station_name,
  country_code,
  source,
  who_pm25_exceed,
  who_pm10_exceed,
  who_no2_exceed,
  who_o3_exceed,
  has_who_exceedance,
  SUM(has_who_exceedance) OVER (
    PARTITION BY station_id, source, streak_group
    ORDER BY observation_date
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS current_exceedance_streak_days,
  CURRENT_TIMESTAMP AS mart_ts
FROM grouped
