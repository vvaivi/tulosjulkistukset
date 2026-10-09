select *
from {{ ref('earnings') }}
where event_date >= current_date
