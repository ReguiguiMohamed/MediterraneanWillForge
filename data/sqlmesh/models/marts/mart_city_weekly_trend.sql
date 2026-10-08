-- Daily PM2.5 and ozone per city and source, with a trailing seven-day mean.
MODEL (
  name marts.mart_city_weekly_trend,
  kind FULL,
  grain (observation_date, country_code, city_name, source),
  audits (
    not_null(columns := (
      observation_date, country_code, city_name, source, station_count, mart_ts
    )),
    accepted_values(column := source, is_in := ('openmeteo', 'openaq', 'waqi')),
    accepted_range(column := station_count, min_v := 1),
    accepted_range(column := rolling_7d_pm2_5, min_v := 0, max_v := 1000),
    accepted_range(column := rolling_7d_ozone, min_v := 0, max_v := 500)
  )
);

WITH daily AS (
  SELECT
    observation_date,
    country_code,
    COALESCE(NULLIF(city, ''), station_name, station_id) AS city_name,
    source,
    COUNT(DISTINCT station_id) AS station_count,
    ROUND(AVG(pm2_5), 2) AS daily_mean_pm2_5,
    ROUND(AVG(ozone), 2) AS daily_mean_ozone,
    ROUND(AVG(data_completeness), 4) AS mean_data_completeness
  FROM staging.stg_air_quality
  GROUP BY 1, 2, 3, 4
)

SELECT
  observation_date,
  country_code,
  city_name,
  source,
  station_count,
  daily_mean_pm2_5,
  daily_mean_ozone,
  ROUND(
    AVG(daily_mean_pm2_5) OVER (
      PARTITION BY country_code, city_name, source
      ORDER BY observation_date
      ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ),
    2
  ) AS rolling_7d_pm2_5,
  ROUND(
    AVG(daily_mean_ozone) OVER (
      PARTITION BY country_code, city_name, source
      ORDER BY observation_date
      ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ),
    2
  ) AS rolling_7d_ozone,
  mean_data_completeness,
  CURRENT_TIMESTAMP AS mart_ts
FROM daily
