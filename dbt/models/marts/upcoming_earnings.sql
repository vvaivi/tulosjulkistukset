-- A later disclosure wins if Nasdaq has announced the same company/date/event again.
with ranked as (
    select
        *,
        row_number() over (
            partition by lower(company), event_date, event_type
            order by published_at desc, disclosure_id desc
        ) as version_number
    from {{ ref('stg_earnings_events') }}
)

select
    event_id,
    disclosure_id,
    company,
    event_date,
    event_type,
    description,
    source_url,
    published_at,
    market
from ranked
where version_number = 1
  and event_date >= current_date

