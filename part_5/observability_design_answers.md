# Observability design (written answers)

Aligned with [ai-session/part-5_spec.md](../ai-session/part-5_spec.md). Prose only; no implementation code.

---

## 1. What Datadog metrics and alerts would you set up for this pipeline?

Emit custom metrics with consistent tags (`dag_id`, `env`, `run_date`, `campaign_id`, `segment_id`) so you can slice by campaign and environment. For orchestration, track run lifecycle and latency: counters for run started, succeeded, and failed; a distribution or gauge for total DAG duration; per-task duration tagged by `task_id`; and a counter when SLA is missed. For audience health, emit `campaign_pipeline.audience.count`, optional `historical_avg` and `count_to_avg_ratio`, plus flags for validation failure and zero-audience runs; if model scoring is in play, also track threshold used and scored coverage so you can tell “small audience” from “model or join failure.”

On the ESP side, instrument each batch attempt: counts for attempted, succeeded, and failed batches; distribution of batch size; request latency; counts tagged by HTTP status; separate counters for 429 retries and exhausted retries; and run-level gauges for `total_sent`, `total_failed`, `total_skipped`, plus completion rate (sent divided by attempted sends or sent plus failed, depending on your definition). Pair metrics with alerts that encode the business tradeoff: warn or page on DAG duration approaching the 8am-style SLA window; any prod DAG failure; audience count at zero or ratio above two times historical average (and optionally large drops); ESP non-success or 5xx rate over a rolling window; completion rate below target; and sustained 429 or retry-exhaustion spikes. Every alert payload should include run identifiers and links back to Airflow and logs so on-call can retry or skip with context.

---

## 2. How would you detect and prevent double-sends if the pipeline runs twice due to an Airflow retry or manual re-trigger?

Use layered idempotency so the same logical send cannot commit twice. At the recipient level, keep a durable sent log keyed by `campaign_id` and `renter_id`: before each batch, skip anyone already recorded for that campaign, and only append IDs after the ESP accepts the batch, with atomic writes so a crash mid-batch does not corrupt state. That alone makes task-level retries safe because a retry replays from the same audience but skips already-sent renters.

Above that, add a run-level idempotency key such as `campaign_id`, `as_of_date`, and `segment_id` combined: acquire a lease or insert a unique row in a lock table before Task 3 runs; if the same key is already `running` or `completed`, short-circuit the send path and mark the DAG run as a duplicate replay rather than sending again. Where the ESP supports request idempotency headers or keys, include a stable token per batch so network duplicates do not create duplicate messages. Finally, write reporting rows with uniqueness on campaign, date, segment, and batch index so reconciliation jobs or monitors can detect double accounting and fire an alert before marketing assumes the run was clean.

---

## 3. What happens if the ESP goes down mid-send? Describe your circuit-breaker or recovery strategy.

Treat prolonged ESP failure as a protective stop rather than an infinite retry loop. In the send loop, maintain a simple circuit breaker: while closed, send batches with existing rate-limit backoff on 429. If consecutive batch failures, timeout errors, or 5xx responses exceed a threshold within a window, open the breaker: stop issuing new batches for this run, record partial progress honestly, and emit a high-severity alert with campaign, run id, last successful batch, and counts still unsent. After a cooldown, optionally enter half-open and send a single probe batch; if it succeeds, close the breaker and continue; if not, stay open and end the task as partial failure.

Recovery should never mark unsent recipients as sent. Persist a checkpoint (last successful batch index and remaining audience or batch list) to object storage or a table, and rely on recipient-level dedupe plus the run-level lock so a later replay or the next scheduled run only delivers to people who never received a successful accept from the ESP. Communicate clearly in Slack and reporting whether the run was partial so stakeholders can choose to wait for stability versus triggering a controlled replay, without risking double-send volume.
