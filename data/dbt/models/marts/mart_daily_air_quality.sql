-- mart_daily_air_quality.sql
-- Daily air quality summary per station.
-- The analytics layer: clean, typed, documented, tested.

{{ config(
    materialized = 'table',
    partition_by = { 'field': 'observation_date', 'data_type': 'date' }
) }}

select
    observation_date,
    station_id,
    station_name,
    lat,
    lon,
    round(avg(pm25_ug_m3), 2)  as daily_mean_pm25,
    round(max(pm25_ug_m3), 2)  as daily_max_pm25,
    round(avg(pm10_ug_m3), 2)  as daily_mean_pm10,
    round(avg(no2_ug_m3),  2)  as daily_mean_no2,
    round(avg(o3_ug_m3),   2)  as daily_mean_o3,
    max(who_pm25_exceeded)      as who_pm25_exceeded,
    max(aqi_category)           as worst_aqi_category,
    current_timestamp()         as mart_ts

from {{ ref('stg_air_quality') }}
group by 1, 2, 3, 4, 5
