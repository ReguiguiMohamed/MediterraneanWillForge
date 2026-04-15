-- Staging view over the Silver Delta table.
--
-- Reads Parquet data files directly via DuckDB httpfs. The MinIO connection is
-- configured in profiles.yml using DuckDB's s3_* settings.
--
-- The Delta table is partitioned as:
--   partition_date=YYYY-MM-DD/source=openmeteo|openaq/*.parquet
--
-- DuckDB httpfs does not support recursive S3 globs, so the read path uses an
-- explicit two-level wildcard. hive_partitioning=true is required because the
-- delta-rs rust writer stores partition columns in the directory path rather
-- than inside the Parquet data files.

{{ config(materialized='view') }}

with silver as (
    select *
    from read_parquet(
        's3://{{ env_var("MINIO_BUCKET_SILVER", "silver") }}/air_quality/partition_date=*/source=*/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)

select
    station_id,
    station_name,
    city,
    country_code,
    cast(date as date) as observation_date,
    cast(latitude as double) as latitude,
    cast(longitude as double) as longitude,
    cast(pm2_5 as double) as pm2_5,
    cast(pm10 as double) as pm10,
    cast(nitrogen_dioxide as double) as nitrogen_dioxide,
    cast(ozone as double) as ozone,
    aqi_category,
    cast(who_pm25_exceed as integer) as who_pm25_exceed,
    cast(who_pm10_exceed as integer) as who_pm10_exceed,
    cast(who_no2_exceed as integer) as who_no2_exceed,
    cast(who_o3_exceed as integer) as who_o3_exceed,
    cast(data_completeness as double) as data_completeness,
    source,
    partition_date,
    silver_ts as _loaded_at

from silver
where station_id is not null
