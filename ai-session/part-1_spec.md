# Part 1 Spec: SMS Reactivation Audience (BigQuery)

## Goal
Build a BigQuery query that returns the audience for an SMS reactivation campaign with strict eligibility and suppression safeguards.

The query output must include:
- `renter_id`
- `email`
- `phone`
- `last_login`
- `search_count`
- `days_since_login`

## Source Tables
- `renter_activity`
- `renter_profiles`
- `suppression_list`

## Eligibility Rules
Include renters only when all conditions are true:
1. `last_login` is more than 30 days ago
2. `subscription_status = 'churned'`
3. At least 3 `search` events in the past 90 days
4. Phone number exists (`phone IS NOT NULL` and not blank)
5. `sms_consent = TRUE`
6. Renter is not in `suppression_list`
7. `dnd_until IS NULL` or `dnd_until` is in the past

## Idempotency Requirement
The query must be idempotent for a given run date (same-day reruns return the same result).

Implementation approach:
- Anchor all time logic to a single `as_of_date` value.
- Default `as_of_date` to `CURRENT_DATE('UTC')` for daily runs.
- Convert once to `as_of_ts` and reuse in all filters/calculations.
- Do not call `CURRENT_TIMESTAMP()` repeatedly inside filter predicates.

## BigQuery SQL (Reference Implementation)

```sql
-- BigQuery Standard SQL
-- Idempotent per UTC day because all time windows are anchored to as_of_date.
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
```

## Implementation Notes for Agent
- Use `LEFT JOIN suppression_list ... WHERE sl.renter_id IS NULL` (or equivalent `NOT EXISTS`) to exclude suppressed renters.
- Treat blank phone values as invalid (`TRIM(phone) != ''`).
- Keep `last_login IS NOT NULL` explicit before date arithmetic.
- Use a fixed `as_of_date` parameter for reproducibility in tests and backfills.
- Preserve readable CTE structure (`params`, `search_activity`, `eligible_profiles`).

## Acceptance Checklist
- Correct join/filter logic across all 8 business rules
- Proper handling of `NULL` for `phone`, `last_login`, and `dnd_until`
- Idempotent behavior for same `as_of_date`
- Exact output columns and names match required return schema
- Query executes in BigQuery Standard SQL
