# Airflow DAG changes: freshness before audience build

This document describes how to extend [part_3/campaign_pipeline_dag.py](../part_3/campaign_pipeline_dag.py) so the campaign DAG **depends on model scores being fresh** for the logical run date before Task 1 materializes the audience.

## Target dependency graph

```text
task_0_wait_for_model_scores_fresh
  → task_1_run_audience_query
  → task_2_validate_audience
  → task_3_execute_campaign_send
  → task_4_log_and_notify
```

TaskFlow wiring (conceptual):

```python
t0 = task_0_wait_for_model_scores_fresh()
t1 = task_1_run_audience_query(t0)
t2 = task_2_validate_audience(t1)
t3 = task_3_execute_campaign_send(t1, t2)
task_4_log_and_notify(t1, t2, t3)
```

## Task 0: `task_0_wait_for_model_scores_fresh`

### Responsibility

- Block until scores for **`DATE(scored_at) = as_of_date`** exist for the **configured `model_version`** (resolved from the same segment/config source as BigQuery), **or** until a **timeout** triggers the delay policy ([03_model_delay_policy.md](03_model_delay_policy.md)).

### Freshness SQL contract

Use the same predicate as the audience query’s `latest_scores` filter:

- `DATE(scored_at) = @as_of_date`
- `model_version = @model_version` (from config for `segment_id`)

Optional stronger gate (recommended at scale):

- `COUNT(*) >= @min_expected_rows` for that date + model, to catch partial loads.

A reusable template lives in [snippets/freshness_check.sql](snippets/freshness_check.sql).

### Airflow implementation options

**Option A — `SqlSensor` (Google provider)**

- `conn_id`: BigQuery / GCP connection used elsewhere in the DAG
- `sql`: freshness query returning a single row when ready (e.g. `SELECT 1 WHERE ...`)
- `poke_interval`: 5 minutes
- `timeout`: 90 minutes (example: wait until ~06:30 UTC after 05:00 schedule)
- `mode`: `"reschedule"` (free worker slot between pokes)

**Option B — TaskFlow `@task.sensor`**

- Same SQL and timing; implement `poke()` that runs the hook and returns True when the check passes.

### Task 0 XCom output (recommended)

Emit a small dict consumed by Task 1 and reporting:

```python
{
  "as_of_date": "2026-04-22",
  "segment_id": "reactivation_churned",
  "model_version": "reactivation_v3",
  "freshness_check": "passed",  # or "timeout" if policy short-circuits
  "score_row_count": 12345678,  # optional
}
```

If the delay policy chooses **skip** or **fallback**, Task 0 (or a tiny branching task) should set flags here so Task 1 does not blindly assume `passed`.

## Task 1: `task_1_run_audience_query` changes

### Responsibility

- Build CTAS SQL for staging using **`as_of_date`** and **`segment_id`** aligned with Task 0.
- Pass through model metadata for validation and reporting.

### XCom extensions (add to existing Task 1 payload)

| Field | Meaning |
|-------|---------|
| `score_date_used` | Date used for score freshness (normally `as_of_date`) |
| `model_version_used` | Model version applied in the query |
| `threshold_used` | Numeric threshold after config / overrides |
| `segment_id` | Segment key |
| `is_fallback_mode` | `True` if stale-score fallback rules applied ([03_model_delay_policy.md](03_model_delay_policy.md)) |

### CTAS builder

Today Task 1 reads Part 1 SQL and rewrites the `params` CTE. After integration:

- Inject `segment_id` into `params` (or add a second DECLARE).
- Ensure the SQL file or templated string includes `config` + `latest_scores` + threshold join as in [01_bigquery_integration.md](01_bigquery_integration.md).

## Task 2: `task_2_validate_audience`

- Keep existing gates: `count > 0`, `<= 2 × historical_avg`.
- Log **model context** (`model_version_used`, `threshold_used`, `score_date_used`) on pass/fail.
- Optional extra guardrail: compare audience size to a **pre-score baseline** (e.g. count from a query without the score join) to detect bad joins; omit in v1 if too heavy.

## Task 3: `task_3_execute_campaign_send`

- Staging table now includes score columns; either:
  - **SELECT** only ESP-needed columns into `list[dict]`, or
  - pass through extras if ESP ignores them.

## Task 4: `task_4_log_and_notify`

Extend reporting row + Slack text with:

- `segment_id`, `model_version_used`, `threshold_used`, `score_date_used`
- `model_delay_policy` and `pipeline_status` (e.g. `fresh`, `skipped_model_stale`, `fallback_stale_scores`)

## Configuration surface (env or Airflow Variables)

Suggested:

| Variable | Example |
|----------|---------|
| `SEGMENT_ID` | `reactivation_churned` |
| `SCORE_FRESHNESS_TIMEOUT_MINUTES` | `90` |
| `MODEL_DELAY_POLICY` | `BLOCK_AND_WAIT` / `SKIP_CAMPAIGN_IF_STALE` / `FALLBACK_WITH_STRICT_GUARDRAILS` |
| `MIN_EXPECTED_SCORE_ROWS` | optional integer |

## Pseudocode: Task 0 + policy hook

```python
@task(task_id="task_0_wait_for_model_scores_fresh")
def task_0():
    ctx = get_current_context()
    as_of = ctx["logical_date"].in_timezone("UTC").date().isoformat()
    segment_id = Variable.get("SEGMENT_ID", default_var="reactivation_churned")
    policy = Variable.get("MODEL_DELAY_POLICY", default_var="BLOCK_AND_WAIT")

    model_version, threshold = resolve_config(segment_id, as_of)  # BigQuery or static map

    if policy == "BLOCK_AND_WAIT":
        wait_for_fresh_scores_bq(as_of, model_version, timeout_minutes=90)
    elif policy == "SKIP_CAMPAIGN_IF_STALE":
        if not scores_fresh_bq(as_of, model_version):
            return {"freshness_check": "skip", "as_of_date": as_of, "segment_id": segment_id, ...}
    # ... fallback branch sets is_fallback flags

    return {
        "as_of_date": as_of,
        "segment_id": segment_id,
        "model_version": model_version,
        "threshold_used": threshold,
        "freshness_check": "passed",
    }
```

Exact branching should match [03_model_delay_policy.md](03_model_delay_policy.md).
