# Part 4 Spec: Value Model Integration (Design + Implementation Guidance)

## Goal
Integrate predictive conversion scores into the campaign pipeline so only high-value renters are messaged, while preserving operational resilience when model data is delayed.

This spec defines how to modify:
1. Part 1 BigQuery audience query
2. Part 3 Airflow DAG orchestration
3. Delay-handling policy when model scores are not fresh by run time

## New Data Source

```sql
CREATE TABLE ml_predictions.renter_send_scores (
  renter_id STRING,
  predicted_conversion_probability FLOAT64,  -- 0.0 to 1.0
  model_version STRING,
  scored_at TIMESTAMP
);
```

## Design Principles
- **Volume quality first**: avoid sending without model scores when possible.
- **Revenue protection**: avoid indefinite blocking if scores are delayed.
- **Config-driven behavior**: thresholds and model selection must be configurable.
- **Forward-compatible**: support multiple segment-specific models/thresholds without rearchitecture.

---

## 1) BigQuery Query Changes (Part 1 Integration)

## Objective
Filter audience to renters with score >= configurable threshold from the correct model/segment and fresh scoring date.

## Required Query Additions

### A. Add score parameters
Add parameters (or script variables) to control model filtering:
- `as_of_date DATE` (already present conceptually)
- `segment_id STRING` (campaign segment identifier; e.g., `"reactivation_churned"`)
- `min_conversion_threshold FLOAT64` (e.g., `0.30`)
- `required_model_version STRING` (optional; nullable to allow latest-per-segment)

### B. Add model config mapping (multi-model ready)
To support the second model in ~6 weeks without redesign, define a segment-to-model config source:
- Option 1 (preferred long-term): table `ml_predictions.model_send_config`
- Option 2 (short-term): CTE with static rows

Recommended table shape:

```sql
CREATE TABLE ml_predictions.model_send_config (
  segment_id STRING,                 -- e.g. 'reactivation_churned'
  model_version STRING,              -- e.g. 'reactivation_v3'
  min_conversion_threshold FLOAT64,  -- e.g. 0.30
  is_active BOOL,
  effective_start_date DATE,
  effective_end_date DATE
);
```

### C. Join pattern for scores
Join eligible renters to a **latest score per renter for the selected model/segment on as_of_date**.

Implementation pattern:
1. Resolve active config for `segment_id` and `as_of_date`
2. Filter `renter_send_scores` by:
   - `DATE(scored_at) = as_of_date` (freshness)
   - matching `model_version` from config (or explicit parameter)
3. Deduplicate scores per renter using latest `scored_at`:
   - `ROW_NUMBER() OVER (PARTITION BY renter_id ORDER BY scored_at DESC) = 1`
4. Inner join scores to audience and enforce threshold:
   - `predicted_conversion_probability >= min_conversion_threshold`

### D. Return fields
Augment Part 1 output with model metadata for observability:
- `predicted_conversion_probability`
- `model_version`
- `score_scored_at`
- `threshold_used`
- `segment_id`

### E. Pseudocode SQL skeleton

```sql
DECLARE as_of_date DATE DEFAULT CURRENT_DATE('UTC');
DECLARE segment_id STRING DEFAULT 'reactivation_churned';

WITH params AS (
  SELECT as_of_date, segment_id
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
    AND p.as_of_date BETWEEN c.effective_start_date AND COALESCE(c.effective_end_date, DATE '9999-12-31')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY c.segment_id
    ORDER BY c.effective_start_date DESC
  ) = 1
),
-- existing Part 1 CTEs:
search_activity AS (...),
eligible_profiles AS (...),
latest_scores AS (
  SELECT
    s.renter_id,
    s.predicted_conversion_probability,
    s.model_version,
    s.scored_at
  FROM ml_predictions.renter_send_scores s
  CROSS JOIN params p
  JOIN config c
    ON s.model_version = c.model_version
  WHERE DATE(s.scored_at) = p.as_of_date
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.renter_id
    ORDER BY s.scored_at DESC
  ) = 1
)
SELECT
  ep.renter_id,
  ep.email,
  ep.phone,
  ep.last_login,
  sa.search_count,
  ep.days_since_login,
  ls.predicted_conversion_probability,
  ls.model_version,
  ls.scored_at AS score_scored_at,
  c.min_conversion_threshold AS threshold_used,
  c.segment_id
FROM eligible_profiles ep
JOIN search_activity sa ON sa.renter_id = ep.renter_id
LEFT JOIN suppression_list sl ON sl.renter_id = ep.renter_id
JOIN latest_scores ls ON ls.renter_id = ep.renter_id
CROSS JOIN config c
WHERE sl.renter_id IS NULL
  AND sa.search_count >= 3
  AND ls.predicted_conversion_probability >= c.min_conversion_threshold;
```

### F. Idempotency note
Keep daily idempotency by anchoring all date filters (`as_of_date`) and using deterministic active config selection.

---

## 2) Airflow DAG Changes (Part 3 Integration)

## Objective
Add an explicit dependency that model scores are fresh for today before audience build/send proceeds.

## DAG Additions

### A. New pre-check task (before Task 1)
Add `task_0_wait_for_model_scores_fresh` before `task_1_run_audience_query`.

Dependency becomes:
- `task_0_wait_for_model_scores_fresh` -> `task_1_run_audience_query` -> `task_2_validate_audience` -> `task_3_execute_campaign_send` -> `task_4_log_and_notify`

### B. Freshness condition
Fresh means for selected model version / segment:
- At least one row exists in `ml_predictions.renter_send_scores`
- `DATE(scored_at) = as_of_date`
- Optional stronger check: minimum expected volume threshold (`score_row_count >= min_expected_rows`)

### C. Recommended Airflow pattern
Use one of:
1. `SqlSensor`/`BigQueryTableExistenceSensor` + custom query check
2. TaskFlow `@task.sensor` with `poke_interval`, `timeout`, and soft-fail policy

Suggested sensor config:
- `poke_interval`: 5 minutes
- `timeout`: 90 minutes (e.g., wait until ~06:30 UTC)
- `mode`: `"reschedule"` to reduce worker slot usage

### D. Configurability
Expose as DAG params/Variables:
- `segment_id`
- `wait_for_scores_timeout_minutes`
- `score_freshness_grace_minutes`
- `model_delay_policy` (see section 3)

---

## 3) Handling Delayed Model Job (Business + Technical Policy)

## Objective
Handle scoring delays gracefully, balancing wasted send volume vs missed revenue.

### Recommended policy framework
Implement configurable policy modes:

1. `BLOCK_AND_WAIT` (default)
   - Wait for fresh scores up to timeout.
   - If fresh scores arrive, proceed normally.
   - If timeout reached, move to fallback decision.

2. `SKIP_CAMPAIGN_IF_STALE`
   - If scores still stale after timeout, skip send tasks for the day.
   - Record status as `skipped_model_stale` and notify Slack with reason.
   - Best for strict efficiency controls.

3. `FALLBACK_WITH_STRICT_GUARDRAILS`
   - If stale after timeout, optionally proceed with a conservative fallback:
     - use last available scoring date within max staleness window (e.g., <= 1 day old)
     - and/or apply stricter threshold uplift (e.g., +0.05)
     - and/or cap send volume percentile
   - Record status as `fallback_stale_scores` with staleness metadata.
   - Best compromise between lost revenue and waste.

### Recommended default
- Default to `BLOCK_AND_WAIT`, then `SKIP_CAMPAIGN_IF_STALE` if timeout reached.
- Enable `FALLBACK_WITH_STRICT_GUARDRAILS` only after business sign-off.

### DAG behavior by policy
- **Skip path**: downstream send task is short-circuited; reporting still runs with skip status.
- **Fallback path**: Task 1 query receives fallback scoring date/threshold via params.
- **Always notify**: Slack message must clearly indicate normal vs stale-skip vs stale-fallback.

---

## Required Changes to Existing Part 3 Tasks

### Task 1 (Run audience query)
- Pass `segment_id` and threshold/model config inputs.
- Include score freshness metadata in output payload:
  - `score_date_used`
  - `model_version_used`
  - `threshold_used`
  - `is_fallback_mode`

### Task 2 (Validate audience)
- Add anomaly check specific to model filter:
  - compare audience drop relative to pre-score eligible base (optional but recommended)
- Keep existing checks (`count > 0`, `<= 2x historical avg`) and include model context in validation logs.

### Task 4 (Reporting + Slack)
- Persist model fields and policy outcome:
  - `segment_id`
  - `model_version_used`
  - `threshold_used`
  - `score_date_used`
  - `model_delay_policy`
  - `pipeline_status`
- Slack summary must include whether run used fresh, stale-fallback, or skipped due to stale scores.

---

## Acceptance Checklist
- Part 1 query joins score table correctly and filters by configurable threshold.
- Query supports segment/model extensibility (no hardcoded single-model architecture).
- DAG has explicit model freshness dependency before audience query.
- Delay handling is configurable (wait/skip/fallback) and implemented as deterministic control flow.
- Reporting and Slack output include model/freshness/policy metadata.
- Design articulates business tradeoff and defaults to minimizing wasted send volume while preserving controlled revenue capture.

---

## Notes for Implementing Agent
- This part is primarily design + spec integration; prioritize parameterization and orchestration contracts over environment-specific secret wiring.
- Keep BigQuery SQL and DAG code interfaces stable so additional segment models can be added through config rows, not code rewrites.
