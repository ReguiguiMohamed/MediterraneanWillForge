select
    station_id,
    observation_date,
    source,
    count(*) as row_count
from {{ ref('stg_air_quality') }}
group by 1, 2, 3
having count(*) > 1
