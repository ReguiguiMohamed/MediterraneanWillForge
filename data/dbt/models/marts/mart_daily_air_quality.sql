-- mart_daily_air_quality.sql
-- Daily air quality summary per country, source, and AQI tier.
-- The analytics endpoint: clean, typed, partitioned, documented.
--
-- Granularity: one row per (observation_date, country_code, source, dominant_aqi_tier).
-- partition_by is omitted — DuckDB tables are not partitioned at the SQL level;
-- partitioning is a Delta / object-store concern handled by the Python layer.

{{ config(materialized='table') }}

select
    observation_date,
    country_code,
    source,
    aqi_category                                              as dominant_aqi_tier,
    count(distinct station_id)                                as station_count,
    round(avg(pm25_ug_m3),  2)                               as daily_mean_pm25,
    round(max(pm25_ug_m3),  2)                               as daily_max_pm25,
    round(avg(pm10_ug_m3),  2)                               as daily_mean_pm10,
    round(avg(no2_ug_m3),   2)                               as daily_mean_no2,
    round(avg(o3_ug_m3),    2)                               as daily_mean_o3,
    round(avg(cast(who_pm25_exceeded as double)) * 100, 1)   as who_pm25_exceed_pct,
    round(avg(cast(who_no2_exceeded  as double)) * 100, 1)   as who_no2_exceed_pct,
    round(avg(data_completeness), 4)                         as mean_data_completeness,
    current_timestamp                                         as mart_ts

from {{ ref('stg_air_quality') }}
group by 1, 2, 3, 4
