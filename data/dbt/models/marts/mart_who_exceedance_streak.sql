-- Consecutive station-day streaks where at least one WHO daily guideline
-- threshold is exceeded.

{{ config(materialized='table') }}

with station_days as (
    select
        observation_date,
        station_id,
        station_name,
        country_code,
        source,
        max(who_pm25_exceed) as who_pm25_exceed,
        max(who_pm10_exceed) as who_pm10_exceed,
        max(who_no2_exceed) as who_no2_exceed,
        max(who_o3_exceed) as who_o3_exceed
    from {{ ref('stg_air_quality') }}
    group by 1, 2, 3, 4, 5
),

marked as (
    select
        *,
        case
            when who_pm25_exceed = 1
              or who_pm10_exceed = 1
              or who_no2_exceed = 1
              or who_o3_exceed = 1
            then 1
            else 0
        end as has_who_exceedance
    from station_days
),

grouped as (
    select
        *,
        sum(case when has_who_exceedance = 0 then 1 else 0 end) over (
            partition by station_id, source
            order by observation_date
            rows between unbounded preceding and current row
        ) as streak_group
    from marked
),

streaked as (
    select
        *,
        case
            when has_who_exceedance = 1 then
                row_number() over (
                    partition by station_id, source, streak_group
                    order by observation_date
                )
            else 0
        end as current_exceedance_streak_days
    from grouped
)

select
    observation_date,
    station_id,
    station_name,
    country_code,
    source,
    who_pm25_exceed,
    who_pm10_exceed,
    who_no2_exceed,
    who_o3_exceed,
    has_who_exceedance,
    current_exceedance_streak_days,
    current_timestamp as mart_ts
from streaked

