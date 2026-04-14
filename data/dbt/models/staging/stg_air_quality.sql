-- stg_air_quality.sql
-- Staging view over the Silver Delta table.
--
-- Reads directly via DuckDB's delta_scan() — MinIO S3 credentials are
-- applied at connection time from profiles.yml (s3_endpoint, s3_access_key_id,
-- s3_secret_access_key, s3_use_ssl, s3_url_style).
--
-- Column aliases follow the mart naming convention: pollutant values get
-- explicit units (_ug_m3), WHO flags get the past-tense suffix (_exceeded),
-- and the date is renamed to observation_date to avoid reserved-word clashes.

{{ config(materialized='view') }}

with silver as (
    select *
    from delta_scan('s3://silver/air_quality')
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
