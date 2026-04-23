# Model delay policy: wait, skip, or fallback

Scoring jobs can finish after the campaign DAG starts. The business tradeoff:

- **Sending without scores** wastes volume on low-likelihood renters.
- **Not sending at all** forfeits same-day revenue.

This document defines **configurable** behavior when scores are not fresh by run time.

## Policy modes

| Mode | Behavior | Best when |
|------|----------|-----------|
| `BLOCK_AND_WAIT` | Sensor waits up to timeout for fresh scores; then applies timeout outcome | Default: protect volume quality |
| `SKIP_CAMPAIGN_IF_STALE` | After timeout, **do not send**; record skip + notify | Strict efficiency / compliance |
| `FALLBACK_WITH_STRICT_GUARDRAILS` | After timeout, optionally use **stale** scores with extra constraints | Business accepts controlled risk for revenue |

**Recommendation:** default `BLOCK_AND_WAIT`, then **skip** at timeout unless product approves fallback.

## Definitions

- **Fresh:** for `as_of_date` and resolved `model_version`, there exists score data with `DATE(scored_at) = as_of_date` meeting optional minimum row count.
- **Stale:** fresh condition not met within the sensor timeout.

## Decision flow (conceptual)

```text
DAG starts (e.g. 05:00 UTC)
  → Task 0: poll freshness
       if fresh → continue to Task 1 (normal)
       if not fresh and BLOCK_AND_WAIT → keep polling until timeout
       if timeout:
            SKIP_CAMPAIGN_IF_STALE → short-circuit send path
            FALLBACK_WITH_STRICT_GUARDRAILS → Task 1 uses fallback score_date / stricter threshold / cap
```

## Timeout outcome matrix

| Policy at timeout | Task 1 | Task 3 send | Task 4 reporting | Slack |
|-------------------|--------|-------------|------------------|-------|
| `SKIP_CAMPAIGN_IF_STALE` | Skipped or no-op CTAS | Skipped (`total_sent=0` or task skipped) | `pipeline_status=skipped_model_stale` | Alert: stale scores, no send |
| `FALLBACK_WITH_STRICT_GUARDRAILS` | Runs with `is_fallback_mode=True`, e.g. `score_date_used = as_of_date - 1` and/or higher threshold | May run with smaller audience | `pipeline_status=fallback_stale_scores` | Warning: fallback rules used |
| Forced continue (not recommended) | Same as normal | High waste risk | Must be explicit | Must warn loudly |

Avoid “silent send without scores”: if fallback is disabled, **do not** INNER JOIN an empty score set and still message everyone.

## Fallback guardrails (if enabled)

Examples (combine with product approval):

1. **Max staleness:** only allow `score_date_used >= as_of_date - INTERVAL 1 DAY`.
2. **Threshold uplift:** `threshold_used = base_threshold + 0.05` in fallback.
3. **Volume cap:** top N renters by `predicted_conversion_probability` only.

Always persist: `score_date_used`, `threshold_used`, `is_fallback_mode`, `fallback_reason`.

## Reporting and observability

Every run should write one row (or equivalent) including:

- `model_delay_policy`
- `pipeline_status` (`fresh` | `skipped_model_stale` | `fallback_stale_scores` | …)
- `score_date_used`, `model_version_used`, `threshold_used`
- Audience and send metrics (even when skipped)

Slack must state clearly:

- Normal run vs skip vs fallback
- Reason (timeout, row count too low, etc.)

## Airflow mechanics

- **Sensor timeout** should align with business SLA (e.g. wait up to 90 minutes after 05:00).
- On **skip**, use one of:
  - `ShortCircuitOperator` / conditional task mapping, or
  - downstream tasks that no-op based on XCom flag (reporting still runs).

## Tradeoff summary (for stakeholders)

| Choice | Volume efficiency | Revenue capture | Ops complexity |
|--------|-------------------|-----------------|----------------|
| Wait then skip | High | Lower on delay days | Low |
| Wait then fallback | Medium–high | Higher | Medium (guardrails + monitoring) |
| Send without scores | Low | Highest short-term | Lowest (not recommended) |
