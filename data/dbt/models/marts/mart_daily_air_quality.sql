-- Daily air quality summary per country, source, and AQI tier.
--
-- Granularity: one row per observation_date, country_code, source, and
-- dominant_aqi_tier. DuckDB materializes this as a table; object-store
-- partitioning is handled by the Python Delta writers.

{{ config(materialized='table') }}

select
    observation_date,
    country_code,
    source,
    aqi_category as dominant_aqi_tier,
    count(distinct station_id) as station_count,
    round(avg(pm2_5), 2) as daily_mean_pm2_5,
    round(max(pm2_5), 2) as daily_max_pm2_5,
    round(avg(pm10), 2) as daily_mean_pm10,
    round(avg(nitrogen_dioxide), 2) as daily_mean_nitrogen_dioxide,
    round(avg(ozone), 2) as daily_mean_ozone,
    round(avg(cast(who_pm25_exceed as double)) * 100, 1) as who_pm25_exceed_pct,
    round(avg(cast(who_pm10_exceed as double)) * 100, 1) as who_pm10_exceed_pct,
    round(avg(cast(who_no2_exceed as double)) * 100, 1) as who_no2_exceed_pct,
    round(avg(cast(who_o3_exceed as double)) * 100, 1) as who_o3_exceed_pct,
    round(avg(data_completeness), 4) as mean_data_completeness,
    current_timestamp as mart_ts

from {{ ref('stg_air_quality') }}
group by 1, 2, 3, 4
