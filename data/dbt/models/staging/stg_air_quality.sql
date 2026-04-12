-- stg_air_quality.sql
-- Staging view over the Silver Delta table.
-- Casts types and aliases to the consistent naming convention used across marts.

{{ config(materialized='view') }}

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
    cast(who_pm25_exceed   as int)     as who_pm25_exceeded,
    cast(who_pm10_exceed   as int)     as who_pm10_exceeded,
    cast(who_no2_exceed    as int)     as who_no2_exceeded,
    cast(who_o3_exceed     as int)     as who_o3_exceeded,
    cast(data_completeness as double)  as data_completeness,
    source,
    partition_date,
    silver_ts                          as _loaded_at

from {{ source('silver', 'air_quality') }}
where station_id is not null
