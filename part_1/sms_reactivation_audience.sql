-- BigQuery Standard SQL
-- SMS reactivation audience: idempotent per UTC day (all windows anchored to as_of_date).
-- Source: ai-session/part-1_spec.md

DECLARE as_of_date DATE DEFAULT CURRENT_DATE('UTC');

WITH params AS (
  SELECT
    as_of_date,
    TIMESTAMP(as_of_date, 'UTC') AS as_of_ts
),
search_activity AS (
  SELECT
    ra.renter_id,
    COUNT(1) AS search_count
  FROM renter_activity ra
  CROSS JOIN params p
  WHERE ra.event_type = 'search'
    AND ra.event_timestamp >= TIMESTAMP_SUB(p.as_of_ts, INTERVAL 90 DAY)
    AND ra.event_timestamp < p.as_of_ts
  GROUP BY ra.renter_id
),
eligible_profiles AS (
  SELECT
    rp.renter_id,
    rp.email,
    rp.phone,
    rp.last_login,
    DATE_DIFF(p.as_of_date, DATE(rp.last_login), DAY) AS days_since_login
  FROM renter_profiles rp
  CROSS JOIN params p
  WHERE rp.subscription_status = 'churned'
    AND rp.last_login IS NOT NULL
    AND rp.last_login < TIMESTAMP_SUB(p.as_of_ts, INTERVAL 30 DAY)
    AND rp.sms_consent = TRUE
    AND rp.phone IS NOT NULL
    AND TRIM(rp.phone) != ''
    AND (rp.dnd_until IS NULL OR rp.dnd_until < p.as_of_ts)
)
SELECT
  ep.renter_id,
  ep.email,
  ep.phone,
  ep.last_login,
  sa.search_count,
  ep.days_since_login
FROM eligible_profiles ep
JOIN search_activity sa
  ON sa.renter_id = ep.renter_id
LEFT JOIN suppression_list sl
  ON sl.renter_id = ep.renter_id
WHERE sa.search_count >= 3
  AND sl.renter_id IS NULL
ORDER BY ep.renter_id;
