-- Source coverage and completeness by date, country, and source.

{{ config(materialized='table') }}

select
    observation_date,
    country_code,
    source,
    count(distinct station_id) as station_count,
    count(*) as reading_count,
    round(avg(data_completeness), 4) as mean_data_completeness,
    round(avg(case when pm2_5 is not null then 1.0 else 0.0 end) * 100, 1) as pm2_5_available_pct,
    round(avg(case when pm10 is not null then 1.0 else 0.0 end) * 100, 1) as pm10_available_pct,
    round(avg(case when nitrogen_dioxide is not null then 1.0 else 0.0 end) * 100, 1) as no2_available_pct,
    round(avg(case when ozone is not null then 1.0 else 0.0 end) * 100, 1) as ozone_available_pct,
    current_timestamp as mart_ts
from {{ ref('stg_air_quality') }}
group by 1, 2, 3

