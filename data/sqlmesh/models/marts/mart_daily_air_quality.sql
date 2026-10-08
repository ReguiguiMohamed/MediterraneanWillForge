-- Daily air quality per country, source and AQI tier.
MODEL (
  name marts.mart_daily_air_quality,
  kind FULL,
  grain (observation_date, country_code, source, dominant_aqi_tier),
  audits (
    not_null(columns := (
      observation_date, country_code, source, dominant_aqi_tier,
      station_count, mean_data_completeness, mart_ts
    )),
    unique_combination_of_columns(columns := (
      observation_date, country_code, source, dominant_aqi_tier
    )),
    accepted_values(column := source, is_in := ('openmeteo', 'openaq', 'waqi')),
    accepted_values(column := dominant_aqi_tier, is_in := (
      'good', 'moderate', 'unhealthy_sensitive', 'unhealthy',
      'very_unhealthy', 'hazardous', 'unknown'
    )),
    accepted_range(column := station_count, min_v := 1),
    accepted_range(column := daily_mean_pm2_5, min_v := 0, max_v := 1000),
    accepted_range(column := daily_max_pm2_5, min_v := 0, max_v := 1000),
    accepted_range(column := daily_mean_pm10, min_v := 0, max_v := 2000),
    accepted_range(column := daily_mean_nitrogen_dioxide, min_v := 0, max_v := 500),
    accepted_range(column := daily_mean_ozone, min_v := 0, max_v := 500),
    accepted_range(column := who_pm25_exceed_pct, min_v := 0, max_v := 100),
    accepted_range(column := who_pm10_exceed_pct, min_v := 0, max_v := 100),
    accepted_range(column := who_no2_exceed_pct, min_v := 0, max_v := 100),
    accepted_range(column := who_o3_exceed_pct, min_v := 0, max_v := 100),
    accepted_range(column := mean_data_completeness, min_v := 0, max_v := 1)
  )
);

SELECT
  observation_date,
  country_code,
  source,
  aqi_category AS dominant_aqi_tier,
  COUNT(DISTINCT station_id) AS station_count,
  ROUND(AVG(pm2_5), 2) AS daily_mean_pm2_5,
  ROUND(MAX(pm2_5), 2) AS daily_max_pm2_5,
  ROUND(AVG(pm10), 2) AS daily_mean_pm10,
  ROUND(AVG(nitrogen_dioxide), 2) AS daily_mean_nitrogen_dioxide,
  ROUND(AVG(ozone), 2) AS daily_mean_ozone,
  ROUND(AVG(CAST(who_pm25_exceed AS DOUBLE)) * 100, 1) AS who_pm25_exceed_pct,
  ROUND(AVG(CAST(who_pm10_exceed AS DOUBLE)) * 100, 1) AS who_pm10_exceed_pct,
  ROUND(AVG(CAST(who_no2_exceed AS DOUBLE)) * 100, 1) AS who_no2_exceed_pct,
  ROUND(AVG(CAST(who_o3_exceed AS DOUBLE)) * 100, 1) AS who_o3_exceed_pct,
  ROUND(AVG(data_completeness), 4) AS mean_data_completeness,
  CURRENT_TIMESTAMP AS mart_ts
FROM staging.stg_air_quality
GROUP BY 1, 2, 3, 4
