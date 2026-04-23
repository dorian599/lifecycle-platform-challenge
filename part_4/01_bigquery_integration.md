# BigQuery integration: model scores in the Part 1 audience

This document describes how to extend [part_1/sms_reactivation_audience.sql](../part_1/sms_reactivation_audience.sql) so SMS reactivation only targets renters whose **predicted conversion probability** meets a **configurable threshold**, using **fresh** scores for the run date.

## Data sources

### Scores (existing contract)

```sql
CREATE TABLE ml_predictions.renter_send_scores (
  renter_id STRING,
  predicted_conversion_probability FLOAT64,  -- 0.0 to 1.0
  model_version STRING,
  scored_at TIMESTAMP
);
```

### Segment / model config (recommended)

Multi-segment support without rearchitecture: drive `model_version` and `min_conversion_threshold` from a table (or a temporary CTE during rollout).

```sql
CREATE TABLE ml_predictions.model_send_config (
  segment_id STRING,
  model_version STRING,
  min_conversion_threshold FLOAT64,
  is_active BOOL,
  effective_start_date DATE,
  effective_end_date DATE
);
```

**Active row resolution:** for a given `segment_id` and `as_of_date`, pick the latest `effective_start_date` where `is_active`, and `as_of_date` falls in `[effective_start_date, COALESCE(effective_end_date, DATE '9999-12-31')]`.

## Parameters (idempotency)

Anchor everything on a single **`as_of_date`** (UTC calendar date for the DAG run), plus **`segment_id`**:

| Name | Role |
|------|------|
| `as_of_date` | Same idempotency anchor as Part 1 (`DATE`); all windows and score freshness use this |
| `segment_id` | Selects which model + threshold apply (e.g. `reactivation_churned`) |
| Optional override | `required_model_version` or `min_conversion_threshold` can be passed from Airflow for experiments; otherwise use `model_send_config` |

**Idempotency:** reruns for the same `as_of_date` + `segment_id` + stable config rows yield the same audience, assuming upstream activity and score tables are unchanged.

## Integration pattern

1. **`params` CTE** — expose `as_of_date`, `as_of_ts`, and `segment_id` (replace the Part 1 pattern that only had `as_of_date` in `params`).
2. **`config` CTE** — resolve active `model_version` and `min_conversion_threshold` for `segment_id` at `as_of_date`.
3. **Existing Part 1 CTEs** — keep `search_activity`, `eligible_profiles`, suppression join, and search-count rules as today.
4. **`latest_scores` CTE** — from `renter_send_scores`:
   - Filter `DATE(scored_at) = as_of_date` (fresh for this run)
   - Filter `model_version = config.model_version`
   - Deduplicate: `QUALIFY ROW_NUMBER() OVER (PARTITION BY renter_id ORDER BY scored_at DESC) = 1`
5. **Final join** — **INNER JOIN** `latest_scores` to the eligible audience on `renter_id`.
6. **Threshold** — `predicted_conversion_probability >= config.min_conversion_threshold` (or override).

**Why INNER JOIN:** renters without a fresh score for the chosen model are excluded; this protects send volume quality.

## Augmented output columns

Add to the final `SELECT` for observability and downstream validation:

- `predicted_conversion_probability`
- `model_version` (from scores or config; should match config)
- `score_scored_at` (`scored_at` from the winning row)
- `threshold_used`
- `segment_id`

ESP payload in Part 2 can ignore extra fields if it only needs `renter_id`; staging table can store all columns.

## Pseudocode SQL (full shape)

This is **pseudocode** merging Part 1 structure with Part 4. Adjust table names and qualify column names to match your dataset.

```sql
-- Parameters: replace DECLARE defaults with Airflow-injected date/segment as needed.
DECLARE as_of_date DATE DEFAULT CURRENT_DATE('UTC');
DECLARE segment_id STRING DEFAULT 'reactivation_churned';

WITH params AS (
  SELECT
    as_of_date,
    segment_id,
    TIMESTAMP(as_of_date, 'UTC') AS as_of_ts
),

config AS (
  SELECT
    c.segment_id,
    c.model_version,
    c.min_conversion_threshold
  FROM ml_predictions.model_send_config c
  CROSS JOIN params p
  WHERE c.segment_id = p.segment_id
    AND c.is_active = TRUE
    AND p.as_of_date BETWEEN c.effective_start_date
      AND COALESCE(c.effective_end_date, DATE '9999-12-31')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY c.segment_id
    ORDER BY c.effective_start_date DESC
  ) = 1
),

-- --- Begin existing Part 1 logic (search_activity, eligible_profiles) ---
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
),

base_audience AS (
  SELECT
    ep.renter_id,
    ep.email,
    ep.phone,
    ep.last_login,
    sa.search_count,
    ep.days_since_login
  FROM eligible_profiles ep
  JOIN search_activity sa ON sa.renter_id = ep.renter_id
  LEFT JOIN suppression_list sl ON sl.renter_id = ep.renter_id
  CROSS JOIN config c
  WHERE sa.search_count >= 3
    AND sl.renter_id IS NULL
),

latest_scores AS (
  SELECT
    s.renter_id,
    s.predicted_conversion_probability,
    s.model_version,
    s.scored_at
  FROM ml_predictions.renter_send_scores s
  CROSS JOIN params p
  JOIN config c ON s.model_version = c.model_version
  WHERE DATE(s.scored_at) = p.as_of_date
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.renter_id
    ORDER BY s.scored_at DESC
  ) = 1
)

SELECT
  ba.renter_id,
  ba.email,
  ba.phone,
  ba.last_login,
  ba.search_count,
  ba.days_since_login,
  ls.predicted_conversion_probability,
  ls.model_version,
  ls.scored_at AS score_scored_at,
  c.min_conversion_threshold AS threshold_used,
  c.segment_id
FROM base_audience ba
JOIN latest_scores ls ON ls.renter_id = ba.renter_id
CROSS JOIN config c
WHERE ls.predicted_conversion_probability >= c.min_conversion_threshold
ORDER BY ba.renter_id;
```

## Short-term fallback (no config table yet)

Until `model_send_config` exists, replace the `config` CTE with a static inline mapping:

```sql
config AS (
  SELECT * FROM UNNEST([
    STRUCT(
      'reactivation_churned' AS segment_id,
      'reactivation_v3' AS model_version,
      0.30 AS min_conversion_threshold
    )
  ])
  CROSS JOIN params p
  WHERE segment_id = p.segment_id
)
```

## Operational notes

- **Stale scores:** if the scoring job lands late, `latest_scores` is empty for `as_of_date` → audience shrinks or vanishes; orchestration policy (sensor + skip/fallback) lives in [03_model_delay_policy.md](03_model_delay_policy.md).
- **Second model in six weeks:** add a new row to `model_send_config` (new `segment_id` or new effective dates / version); no query rearchitecture required.
