-- Station counts and pollutant availability by date, country and source.
MODEL (
  name marts.mart_source_coverage,
  kind FULL,
  grain (observation_date, country_code, source),
  audits (
    not_null(columns := (
      observation_date, country_code, source, station_count,
      mean_data_completeness, mart_ts
    )),
    accepted_values(column := source, is_in := ('openmeteo', 'openaq', 'waqi')),
    accepted_range(column := station_count, min_v := 1),
    accepted_range(column := mean_data_completeness, min_v := 0, max_v := 1),
    accepted_range(column := pm2_5_available_pct, min_v := 0, max_v := 100)
  )
);

SELECT
  observation_date,
  country_code,
  source,
  COUNT(DISTINCT station_id) AS station_count,
  COUNT(*) AS reading_count,
  ROUND(AVG(data_completeness), 4) AS mean_data_completeness,
  ROUND(AVG(CASE WHEN pm2_5 IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) AS pm2_5_available_pct,
  ROUND(AVG(CASE WHEN pm10 IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) AS pm10_available_pct,
  ROUND(AVG(CASE WHEN nitrogen_dioxide IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) AS no2_available_pct,
  ROUND(AVG(CASE WHEN ozone IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) AS ozone_available_pct,
  CURRENT_TIMESTAMP AS mart_ts
FROM staging.stg_air_quality
GROUP BY 1, 2, 3
