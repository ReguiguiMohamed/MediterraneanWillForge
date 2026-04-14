-- stg_air_quality.sql
-- Staging view over the Silver Delta table.
--
-- Reads Parquet data files directly via DuckDB's httpfs extension.
-- S3 credentials are configured in profiles.yml via the settings: block
-- (SET s3_endpoint, s3_access_key_id, etc.) which httpfs picks up correctly.
--
-- Why not delta_scan()?
--   DuckDB 1.0.x (bundled in dbt-duckdb 1.8.0) delta_scan() uses delta-kernel-rs
--   which does NOT read DuckDB's SET s3_* settings.  It falls through to EC2 IMDS
--   and fails with HTTP 411 on GitHub Actions.  read_parquet() uses httpfs which
--   shares the same credential context as the rest of the pipeline.
--
-- Delta Lake stores partition columns in both the directory path (hive-style) and
-- inside the Parquet data files.  Reading with hive_partitioning=false avoids
-- duplicate column errors while still returning all canonical Silver columns.

{{ config(materialized='view') }}

with silver as (
    select *
    from read_parquet(
        's3://silver/air_quality/**/*.parquet',
        hive_partitioning = false,
        union_by_name     = true
    )
)

select
    station_id,
    station_name,
    city,
    country_code,
    cast(date              as date)    as observation_date,
    cast(latitude          as double)  as lat,
    cast(longitude         as double)  as lon,
    cast(pm2_5             as double)  as pm25_ug_m3,
    cast(pm10              as double)  as pm10_ug_m3,
    cast(nitrogen_dioxide  as double)  as no2_ug_m3,
    cast(ozone             as double)  as o3_ug_m3,
    aqi_category,
    cast(who_pm25_exceed   as integer) as who_pm25_exceeded,
    cast(who_pm10_exceed   as integer) as who_pm10_exceeded,
    cast(who_no2_exceed    as integer) as who_no2_exceeded,
    cast(who_o3_exceed     as integer) as who_o3_exceeded,
    cast(data_completeness as double)  as data_completeness,
    source,
    partition_date,
    silver_ts                          as _loaded_at

from silver
where station_id is not null
