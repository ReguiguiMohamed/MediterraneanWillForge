-- Typed view over the Silver Delta table on MinIO.
--
-- DuckDB reads the Parquet data files directly. httpfs has no recursive S3
-- glob, so the path names both partition levels, and hive_partitioning puts
-- partition_date and source back, because delta-rs keeps partition columns in
-- the directory path rather than in the files.
MODEL (
  name staging.stg_air_quality,
  kind VIEW,
  grain (station_id, observation_date, source),
  audits (
    not_null(columns := (
      station_id, country_code, observation_date, latitude, longitude,
      aqi_category, data_completeness, source, partition_date, _loaded_at
    )),
    unique_combination_of_columns(columns := (station_id, observation_date, source)),
    accepted_range(column := latitude, min_v := -90, max_v := 90),
    accepted_range(column := longitude, min_v := -180, max_v := 180),
    accepted_range(column := pm2_5, min_v := 0, max_v := 1000),
    accepted_range(column := pm10, min_v := 0, max_v := 2000),
    accepted_range(column := nitrogen_dioxide, min_v := 0, max_v := 500),
    accepted_range(column := ozone, min_v := 0, max_v := 500),
    accepted_range(column := data_completeness, min_v := 0, max_v := 1),
    accepted_values(column := aqi_category, is_in := (
      'good', 'moderate', 'unhealthy_sensitive', 'unhealthy',
      'very_unhealthy', 'hazardous', 'unknown'
    )),
    accepted_values(column := who_pm25_exceed, is_in := (0, 1)),
    accepted_values(column := who_pm10_exceed, is_in := (0, 1)),
    accepted_values(column := who_no2_exceed, is_in := (0, 1)),
    accepted_values(column := who_o3_exceed, is_in := (0, 1)),
    accepted_values(column := source, is_in := ('openmeteo', 'openaq', 'waqi'))
  )
);

SELECT
  station_id,
  station_name,
  city,
  country_code,
  CAST(date AS DATE) AS observation_date,
  CAST(latitude AS DOUBLE) AS latitude,
  CAST(longitude AS DOUBLE) AS longitude,
  CAST(pm2_5 AS DOUBLE) AS pm2_5,
  CAST(pm10 AS DOUBLE) AS pm10,
  CAST(nitrogen_dioxide AS DOUBLE) AS nitrogen_dioxide,
  CAST(ozone AS DOUBLE) AS ozone,
  aqi_category,
  CAST(who_pm25_exceed AS INT) AS who_pm25_exceed,
  CAST(who_pm10_exceed AS INT) AS who_pm10_exceed,
  CAST(who_no2_exceed AS INT) AS who_no2_exceed,
  CAST(who_o3_exceed AS INT) AS who_o3_exceed,
  CAST(data_completeness AS DOUBLE) AS data_completeness,
  source,
  partition_date,
  silver_ts AS _loaded_at
FROM READ_PARQUET(
  's3://silver/air_quality/partition_date=*/source=*/*.parquet',
  hive_partitioning = TRUE,
  union_by_name = TRUE
)
WHERE station_id IS NOT NULL
