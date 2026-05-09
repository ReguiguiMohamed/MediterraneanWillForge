-- Seven-day rolling pollutant trend per city, country, and source.
--
-- This model is designed for static portfolio charts: one row per
-- observation_date x country x city x source with raw daily means and a
-- trailing seven-day average where enough history exists.

{{ config(materialized='table') }}

with daily as (
    select
        observation_date,
        country_code,
        coalesce(nullif(city, ''), station_name, station_id) as city_name,
        source,
        count(distinct station_id) as station_count,
        round(avg(pm2_5), 2) as daily_mean_pm2_5,
        round(avg(ozone), 2) as daily_mean_ozone,
        round(avg(data_completeness), 4) as mean_data_completeness
    from {{ ref('stg_air_quality') }}
    group by 1, 2, 3, 4
)

select
    observation_date,
    country_code,
    city_name,
    source,
    station_count,
    daily_mean_pm2_5,
    daily_mean_ozone,
    round(
        avg(daily_mean_pm2_5) over (
            partition by country_code, city_name, source
            order by observation_date
            rows between 6 preceding and current row
        ),
        2
    ) as rolling_7d_pm2_5,
    round(
        avg(daily_mean_ozone) over (
            partition by country_code, city_name, source
            order by observation_date
            rows between 6 preceding and current row
        ),
        2
    ) as rolling_7d_ozone,
    mean_data_completeness,
    current_timestamp as mart_ts
from daily

