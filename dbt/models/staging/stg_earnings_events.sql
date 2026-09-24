with events as (
    select * from {{ source('raw', 'earnings_events') }}
),
disclosures as (
    select * from {{ source('raw', 'disclosures') }}
)

select
    events.event_id,
    events.disclosure_id,
    trim(events.company) as company,
    events.event_date,
    events.event_type,
    events.description,
    events.source_url,
    events.extracted_at,
    disclosures.published_at,
    disclosures.market,
    disclosures.headline
from events
inner join disclosures using (disclosure_id)
where events.event_date between current_date - interval 30 day and current_date + interval 550 day
  and lower(disclosures.market) like '%helsinki%'

