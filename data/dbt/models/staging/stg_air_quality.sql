-- stg_air_quality.sql
-- Staging view over the Silver Delta table.
-- Casts types, renames columns to snake_case conventions used across all marts.

{{ config(materialized='view') }}

select
    station_id,
    station_name,
    cast(date        as date)    as observation_date,
    cast(latitude    as float64) as lat,
    cast(longitude   as float64) as lon,
    cast(pm2p5       as float64) as pm25_ug_m3,
    cast(pm10        as float64) as pm10_ug_m3,
    cast(no2         as float64) as no2_ug_m3,
    cast(o3          as float64) as o3_ug_m3,
    aqi_category,
    cast(who_exceedance as int)  as who_pm25_exceeded,
    partition_date,
    silver_ts                    as _loaded_at

from {{ source('silver', 'air_quality') }}
where station_id is not null
  and observation_date is not null
