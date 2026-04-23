# Part 5 Spec: Observability and Reliability Design (Written + Build Guidance)

## Goal
Define the observability and resilience requirements for the campaign pipeline so an AI agent can implement:
1. Datadog metrics + alerts
2. Double-send detection/prevention
3. ESP outage handling (mid-send) with safe recovery

This spec should result in code/config updates (instrumentation + guardrails), not just prose.

## Scope of Existing Components
- Audience build/query: `part_1/sms_reactivation_audience.sql`
- Send orchestration code: `part_2/campaign_send_pipeline.py`
- DAG orchestration: `part_3/campaign_pipeline_dag.py`
- Model-integration design docs: `part_4/*`

The implementation agent may add utility modules/config files as needed.

---

## 1) Datadog Metrics and Alerts

## Objective
Expose end-to-end health of the pipeline with metrics and alerting for latency, audience anomalies, ESP reliability, and send completion.

## Required metric families

### A. DAG / orchestration metrics
Emit for every DAG run:
- `campaign_pipeline.dag.run_started` (count)
- `campaign_pipeline.dag.run_succeeded` (count)
- `campaign_pipeline.dag.run_failed` (count)
- `campaign_pipeline.dag.run_duration_seconds` (gauge/distribution)
- `campaign_pipeline.dag.task_duration_seconds` (distribution, tagged by task)
- `campaign_pipeline.dag.sla_miss` (count)

Required tags:
- `dag_id`
- `env` (dev/staging/prod)
- `run_date`
- `campaign_id`
- `segment_id` (if available)

### B. Audience quality metrics
Emit from Task 1/2:
- `campaign_pipeline.audience.count` (gauge)
- `campaign_pipeline.audience.historical_avg` (gauge)
- `campaign_pipeline.audience.count_to_avg_ratio` (gauge)
- `campaign_pipeline.audience.validation_failed` (count)
- `campaign_pipeline.audience.zero_count` (count)
- `campaign_pipeline.audience.model_threshold_used` (gauge)

Optional high-value metric:
- `campaign_pipeline.audience.scored_coverage_ratio` = scored/eligible_base

### C. ESP send metrics (from Part 2 send loop)
Emit per run and per batch:
- `campaign_pipeline.esp.batch.attempted` (count)
- `campaign_pipeline.esp.batch.succeeded` (count)
- `campaign_pipeline.esp.batch.failed` (count)
- `campaign_pipeline.esp.batch.size` (distribution)
- `campaign_pipeline.esp.request.latency_ms` (distribution)
- `campaign_pipeline.esp.response.status_code` (count, tag by status)
- `campaign_pipeline.esp.retry.429` (count)
- `campaign_pipeline.esp.retry.exhausted` (count)
- `campaign_pipeline.esp.circuit_breaker.opened` (count)

Run summary metrics:
- `campaign_pipeline.send.total_sent` (gauge/count)
- `campaign_pipeline.send.total_failed` (gauge/count)
- `campaign_pipeline.send.total_skipped` (gauge/count)
- `campaign_pipeline.send.completion_rate` (gauge) = sent / (sent + failed)

### D. Idempotency / safety metrics
- `campaign_pipeline.idempotency.duplicate_attempts_blocked` (count)
- `campaign_pipeline.idempotency.send_lock_acquire_failed` (count)
- `campaign_pipeline.idempotency.replayed_run_detected` (count)

## Alert requirements (minimum)

1. **DAG latency/SLA alert**
   - Trigger when run duration approaches/exceeds SLA window (e.g. > 2.5h warning, > 3h critical).

2. **Run failure alert**
   - Trigger on any failed DAG run in prod.

3. **Audience anomaly alert**
   - Trigger if:
     - audience count = 0, OR
     - count_to_avg_ratio > 2.0, OR
     - severe drop (e.g. ratio < 0.5) if configured.

4. **ESP error-rate alert**
   - Trigger when 5xx or non-2xx error ratio over rolling window exceeds threshold (e.g. > 5% warning, > 15% critical).

5. **Send completion-rate alert**
   - Trigger when completion rate falls below target (e.g. < 95% warning, < 90% critical).

6. **Retry pressure / throttling alert**
   - Trigger on sustained spikes in `retry.429` or exhausted retries.

All alerts should include run metadata: campaign_id, segment_id, run_date, DAG run link.

---

## 2) Double-Send Detection and Prevention

## Objective
Guarantee at-most-once send semantics per `(campaign_id, renter_id)` even if Airflow retries or manual re-triggers occur.

## Required safeguards (layered)

### Layer 1: Application-level dedupe (already present baseline)
Keep and extend Part 2 sent-log behavior:
- Dedupe key: `(campaign_id, renter_id)`
- Skip already-sent recipients before batching
- Persist successful sends durably
- Emit `total_skipped` and idempotency metrics

### Layer 2: Run-level idempotency key + lock
Introduce a run-level idempotency key:
- `idempotency_key = campaign_id + ":" + as_of_date + ":" + segment_id`

Before Task 3 send:
1. Attempt to acquire lock/lease for this idempotency key (e.g., BigQuery table row with atomic insert, Redis lock, or DB unique constraint).
2. If lock exists and status is `running` or `completed`, do not send again:
   - mark run as replay/duplicate
   - short-circuit send task safely
3. Release/update lock status at end (`completed` / `failed` with reason).

### Layer 3: Outbound request idempotency (if ESP supports)
Pass an idempotency token per batch/request:
- `batch_idempotency_key = idempotency_key + ":batch_" + batch_index`

If ESP supports idempotency headers/keys, require their use.

### Layer 4: Reporting reconciliation
Write per-run + per-batch records with uniqueness constraints on:
- `(campaign_id, as_of_date, segment_id, batch_index)`

If duplicates appear, alert and block downstream completion marking.

## Required behavior on Airflow retries / reruns
- **Task retry (same run):** should resume safely; already sent recipients are skipped.
- **Manual rerun (same day):** should detect replay via run-level idempotency key and avoid re-sending.
- **Backfill for different date:** allowed (different `as_of_date` => different key).

---

## 3) ESP Down Mid-Send (Circuit Breaker + Recovery)

## Objective
Fail safely during ESP outages, avoid hammering provider, preserve recoverability.

## Circuit-breaker policy requirements

Implement a simple circuit breaker in Part 2 send loop:

### States
- `CLOSED`: normal operation
- `OPEN`: stop sending new batches temporarily
- `HALF_OPEN`: probe with limited trial batch after cooldown

### Open conditions (configurable)
Open breaker when any condition met over rolling window:
- consecutive batch failures >= `N` (e.g. 5)
- 5xx error rate above threshold
- repeated connection/timeouts above threshold

### Behavior by state
- **CLOSED:** send normally with existing 429 backoff behavior.
- **OPEN:** stop further batch sends immediately for this run; mark remaining recipients as deferred/failed-not-sent.
- **HALF_OPEN:** after cooldown, attempt one probe batch:
  - success => close breaker, continue
  - failure => reopen breaker and stop run

### Recovery + replay
When breaker opens:
1. Persist checkpoint state (last successful batch index + unsent recipient IDs).
2. End task with partial-failure status (do not pretend success).
3. Emit Datadog + Slack alert with recovery context.
4. Allow next scheduled run (or controlled replay job) to resume using dedupe and unsent list.

### Important guardrails
- Never re-send successful batches during recovery.
- Never mark unsent batches as sent.
- Keep manual retry path simple: operator can replay only unsent recipients using checkpoint artifact.

---

## Implementation Requirements for Agent

1. Add observability instrumentation hooks in:
   - `part_2/campaign_send_pipeline.py` (batch and run metrics)
   - `part_3/campaign_pipeline_dag.py` (task/run metrics + validation metrics)

2. Add idempotency lock/checkpoint mechanism:
   - new storage artifact/module acceptable (e.g. `part_5/idempotency_store.py` or BigQuery-based lock table helper)
   - integrate with Task 3 before invoking send

3. Add circuit-breaker logic in send pipeline:
   - configurable thresholds
   - state transitions
   - checkpoint persistence for unsent recipients

4. Add reporting/notification fields:
   - idempotency key
   - breaker state transitions
   - replay/duplicate status
   - partial-failure reason

---

## Configuration Surface (minimum)

Required config keys (env vars or Airflow Variables):
- `DD_ENABLED` / Datadog endpoint settings
- `IDEMPOTENCY_STORE_BACKEND` (file/db/bq)
- `IDEMPOTENCY_LOCK_TTL_SECONDS`
- `CIRCUIT_BREAKER_CONSECUTIVE_FAILURES`
- `CIRCUIT_BREAKER_COOLDOWN_SECONDS`
- `ESP_FAILURE_RATE_WINDOW_SIZE`
- `ESP_FAILURE_RATE_OPEN_THRESHOLD`

Defaults should be conservative and documented.

---

## Acceptance Checklist
- Datadog metrics exist for DAG health, audience anomalies, ESP reliability, and send completion.
- Alerts are defined for SLA/latency, run failures, audience anomalies, ESP error rate, and completion rate.
- Duplicate-send prevention works for retries and manual re-triggers via layered idempotency controls.
- Mid-send ESP outage triggers circuit breaker and preserves unsent checkpoint state.
- Recovery path avoids double-sending and supports controlled replay.
- Logging is structured, actionable, and correlated with run/campaign identifiers.
