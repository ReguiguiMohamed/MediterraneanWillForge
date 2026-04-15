select
    observation_date,
    country_code,
    source,
    dominant_aqi_tier,
    count(*) as row_count
from {{ ref('mart_daily_air_quality') }}
group by 1, 2, 3, 4
having count(*) > 1
