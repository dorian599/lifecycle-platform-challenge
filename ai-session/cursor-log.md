# SMS reactivation campaign audience segmentation
_Exported on 4/23/2026 at 00:06:13 GMT-3 from Cursor (3.1.17)_

---

**User**

# Audience Segmentation Query (SQL)

## Schema

```sql
CREATE TABLE renter_activity (
  renter_id STRING,
  event_type STRING,        -- 'page_view', 'search', 'application_start', 'application_complete', 'lease_signed'
  event_timestamp TIMESTAMP,
  property_id STRING,
  channel STRING,           -- 'web', 'ios', 'android'
  utm_source STRING
);

CREATE TABLE renter_profiles (
  renter_id STRING,
  email STRING,
  phone STRING,
  last_login TIMESTAMP,
  subscription_status STRING,  -- 'active', 'churned', 'never_subscribed'
  sms_consent BOOLEAN,
  email_consent BOOLEAN,
  dnd_until TIMESTAMP,         -- Do Not Disturb until this date (NULL = no restriction)
  created_at TIMESTAMP
);

CREATE TABLE suppression_list (
  renter_id STRING,
  suppression_reason STRING,  -- 'unsubscribed', 'bounced', 'complained', 'dnc_registry'
  suppressed_at TIMESTAMP
);
```

## Task

Write a BigQuery query that builds the audience for an **SMS reactivation campaign** with the following criteria:

1. Last login was more than 30 days ago
2. Subscription status is `'churned'`
3. Renter has performed **at least 3 searches** in the past 90 days (showing latent intent)
4. Renter has a phone number on file
5. Renter has `sms_consent = TRUE`
6. Renter is NOT in the suppression list
7. Renter's `dnd_until` is either NULL or in the past
8. The query must be **idempotent** — running it twice on the same day produces the same result

Return: `renter_id`, `email`, `phone`, `last_login`, `search_count`, `days_since_login`

## Evaluation Criteria

- Correct joins and filtering logic
- Proper NULL handling (`phone`, `dnd_until`)
- Use of `CURRENT_TIMESTAMP()` or parameterized date for idempotency
- Suppression list exclusion (`LEFT JOIN + IS NULL` or `NOT EXISTS`)
- Clean, readable SQL

Given the above requirement, write into the @ai-session/part-1_spec.md file the necessary spec to be used by an AI agent to build the code to achieve this.

---

**Cursor**

I’ll draft a concrete implementation spec in `ai-session/part-1_spec.md` that an AI agent can execute directly, including a BigQuery query, idempotency approach, and acceptance checks against your criteria. I’m reading the current file first, then I’ll replace it with the full spec.

I found `ai-session/part-1_spec.md` is empty, so I’m now writing a full spec with a production-ready BigQuery SQL template and explicit acceptance criteria (joins, NULL handling, suppression exclusion, and same-day idempotency).

Updated `ai-session/part-1_spec.md` with a complete agent-ready spec that includes:

- The campaign objective and required output fields
- All 8 eligibility rules translated into SQL logic
- A BigQuery Standard SQL reference query
- Same-day idempotency via a single `as_of_date` / `as_of_ts` anchor
- Explicit NULL/blank handling for `phone`, `last_login`, and `dnd_until`
- Suppression exclusion using `LEFT JOIN ... IS NULL`
- An acceptance checklist aligned to your evaluation criteria

If you want, I can also add a second version that uses a query parameter (e.g. `@as_of_date`) instead of `DECLARE` for easier orchestration in scheduled pipelines.

---

**User**

Take the spec @ai-session/part-1_spec.md and build the query inside the folder "part_1"

---

**Cursor**

I’m blocked from implementing right now because mode is still set to planning-only and the switch to coding mode was not approved. I’ll ask one quick choice so I can proceed correctly.

**Proceed with implementation**

> **Do you want me to implement the query in `part_1` now, or stay in plan mode and only provide a step-by-step plan?**
> - Implement now in `part_1` (requires switching to coding mode)
> - Stay in plan mode and provide implementation plan only ✔

I’ll quickly inspect the repository for the `part_1` folder and existing SQL conventions, then I’ll return a concise implementation plan tailored to this codebase.

---

**User**

Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself.

To-do's from the plan have already been created. Do not create them again. Mark them as in_progress as you work, starting with the first one. Don't stop until you have completed all the to-dos.

---

**Cursor**

Implementing the plan: creating `part_1` and adding the SQL file, then validating against the spec.



Here’s what was implemented:

### Deliverable
- **[part_1/sms_reactivation_audience.sql](part_1/sms_reactivation_audience.sql)** — BigQuery Standard SQL matching [ai-session/part-1_spec.md](ai-session/part-1_spec.md):
  - **`params`**: single `as_of_date` / `as_of_ts` for same-day idempotency  
  - **`search_activity`**: `event_type = 'search'`, 90-day window ending at `as_of_ts`  
  - **`eligible_profiles`**: churned, `last_login` &gt; 30 days ago, SMS consent, non-null non-blank `phone`, DND null or past  
  - **Suppression**: `LEFT JOIN suppression_list` + `sl.renter_id IS NULL`  
  - **Output**: `renter_id`, `email`, `phone`, `last_login`, `search_count`, `days_since_login`

### Todos
All three plan todos are **completed** (directory created via the new file, SQL added, spec alignment verified). The plan file was **not** edited.

---

**User**

# Pipeline Orchestration (Python)

Write a Python module that processes the audience query result and sends it to an ESP API.

## Requirements

1. **Batching** — The ESP API accepts a maximum of 100 recipients per request
2. **Rate Limiting** — The ESP returns HTTP 429 when rate-limited; implement exponential backoff with jitter (max 5 retries)
3. **Deduplication** — Ensure no renter receives the same campaign twice if the pipeline is re-run; use a simple file-based approach
4. **Error Handling** — Log failed batches with enough context to retry manually; do not let one failed batch stop the entire pipeline
5. **Metrics** — Track and return a summary: total sent, total failed, total skipped (deduped), total time

## Provided Interface (do not modify)

```python
class ESPClient:
    def send_batch(self, campaign_id: str, recipients: list[dict]) -> Response:
        """Sends a batch of recipients to the ESP.
        Returns a Response with .status_code and .json()"""
        pass
```

## Deliverable

```python
def execute_campaign_send(
    campaign_id: str,
    audience: list[dict],
    esp_client: ESPClient,
    sent_log_path: str = "sent_renters.json"
) -> dict:
    """Returns {'total_sent': int, 'total_failed': int, 'total_skipped': int, 'elapsed_seconds': float}"""
```

## Evaluation Criteria

- Clean batching logic
- Correct exponential backoff with jitter
- Idempotency mechanism (checks previously sent `renter_id`s)
- Graceful error handling (no single batch failure aborts the run)
- Logging quality (structured, actionable)
- Code readability and structure

Given the above requirement, write into the @ai-session/part-2_spec.md file the necessary spec and definitions to be used by an AI agent to build the code to achieve this.

---

**Cursor**

I’ll update `ai-session/part-2_spec.md` with an agent-ready implementation spec, including function contract, retry/backoff behavior, dedupe file format, logging expectations, and acceptance tests. I’m reading the current file first to replace or refine it safely.

The file is empty, so I’m writing a full spec from scratch that maps each requirement to concrete implementation details and testable acceptance criteria for the agent.

Updated `ai-session/part-2_spec.md` with a complete, agent-ready implementation spec for the Python orchestration module.

It now includes:

- Exact deliverable function contract and expected return schema
- Concrete batching behavior (max 100)
- Explicit 429 retry strategy (exponential backoff + jitter, max 5 retries)
- File-based dedupe design keyed by `campaign_id` + `renter_id`
- Failure handling rules so one failed batch doesn’t stop the run
- Structured logging requirements with actionable retry context
- Metrics definitions (`total_sent`, `total_failed`, `total_skipped`, `elapsed_seconds`)
- Recommended module structure and helper function breakdown
- Acceptance checklist and optional validation/test scenarios

If you want, I can now implement Part 2 code in a new `part_2` module from this spec (same style as Part 1).

---

**User**

Take the spec @ai-session/part-2_spec.md and build the code inside the folder "part_2"

---

**Cursor**

I’ll quickly inspect the repo structure and existing Python patterns so the plan for building `part_2` is concrete and aligned with this codebase.

---

**User**

Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself.

To-do's from the plan have already been created. Do not create them again. Mark them as in_progress as you work, starting with the first one. Don't stop until you have completed all the to-dos.

---

**Cursor**

Implementing `part_2/campaign_send_pipeline.py` per the spec: marking the first todo in progress and reading the spec for exact details.



Implementing the full module: scaffold, core flow, sent-log I/O, and validation in one pass.



Polishing `_chunked` typing for clarity.



### Done: Part 2 ESP pipeline

**Added** [part_2/campaign_send_pipeline.py](part_2/campaign_send_pipeline.py) with:

| Requirement | Behavior |
|-------------|----------|
| **Batching** | Chunks of ≤ `100` via `_chunked`, order preserved after filtering |
| **429 + backoff** | Up to **5** retries; `delay = min(1.0 * 2**retry, 30.0) + U(0, 0.5)` |
| **Dedupe** | JSON map `campaign_id → [renter_id, ...]` at `sent_log_path`; skips already sent → `total_skipped` |
| **Errors** | Failed batch logged with `campaign_id`, `batch_index`, size, renter id preview, retries, status/body; run continues |
| **Metrics** | Returns `total_sent`, `total_failed`, `total_skipped`, `elapsed_seconds` |

**Also:**

- **Malformed sent log**: backup to `*.corrupt.<epoch>`, start empty (logged).
- **Atomic writes**: temp file + `os.replace`.
- **Invalid / blank `renter_id`**: skipped, counted in `total_failed`, `WARNING` log.
- **Duplicate `renter_id` in same audience**: only first queued; extras skipped with `WARNING` (not double-sent).
- **`ESPClient`** included unchanged per spec.

All plan todos are **completed**; the plan file was **not** edited.

---

**User**

# Airflow DAG Skeleton (Python)

Write an Airflow DAG definition that orchestrates the full campaign pipeline:

1. **Task 1** — Run the BigQuery audience query and export results to a staging table
2. **Task 2** — Validate the audience (count > 0, no obvious anomalies like audience > 2× historical average)
3. **Task 3** — Execute the campaign send (using the function from Part 2)
4. **Task 4** — Log results to a reporting table and send a Slack notification with the summary

## Requirements

- Schedule: Daily at 5:00 AM UTC
- Retries: 2 per task, 5-minute delay between retries
- Task dependencies: linear (1 → 2 → 3 → 4)
- SLA: Alert if the DAG hasn't completed by 8:00 AM UTC
- Use `@task` decorator or `PythonOperator` — either is fine

## Evaluation Criteria

- Correct DAG structure and task dependencies
- Proper retry and SLA configuration
- Validation step (not blindly sending)
- Clean separation of concerns between tasks
- Understanding of Airflow patterns (XComs, task flow, etc.)

Given the above requirement, write into the @ai-session/part-3_spec.md file the necessary spec to be used by an AI agent to build the code to achieve this.

---

**Cursor**

I’ll draft a full Part 3 implementation spec in `ai-session/part-3_spec.md` with an Airflow DAG blueprint, task contracts, retry/SLA settings, XCom flow, and acceptance criteria. I’m reading the current file first, then replacing it.

I found `part-3_spec.md` empty, so I’m writing the complete agent-ready DAG spec now.

Updated `ai-session/part-3_spec.md` with a complete, agent-ready spec for building the Airflow DAG skeleton.

It now includes:

- Full DAG objective and 4-task orchestration flow
- Exact scheduling/retry/SLA requirements (`0 5 * * *`, 2 retries, 5m delay, 3h SLA window)
- Linear dependency requirement (`1 -> 2 -> 3 -> 4`)
- Detailed task contracts for:
  - BigQuery audience staging
  - validation gates (`count > 0`, `count <= 2x historical avg`)
  - Part 2 campaign send invocation
  - reporting table insert + Slack summary notification
- Recommended XCom payload contracts between tasks
- Error-handling expectations and implementation notes
- Final acceptance checklist aligned to your evaluation criteria

If you want, I can now implement the actual DAG code in a `part_3` module from this spec.

---

**User**

Take the spec @ai-session/part-3_spec.md and build the code inside the folder "part_3"

---

**Cursor**

I’ll quickly scan the repo for `part_3` and any existing Airflow/DAG conventions so I can produce a precise implementation plan for building from `ai-session/part-3_spec.md`.

---

**User**

Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself.

To-do's from the plan have already been created. Do not create them again. Mark them as in_progress as you work, starting with the first one. Don't stop until you have completed all the to-dos.

---

**Cursor**

Implementing the Part 3 Airflow DAG: marking the first todo in progress and reviewing Part 2's public API for correct imports.



Checking the Part 1 SQL structure (DECLARE/script vs single SELECT) to implement Task 1 correctly.



Implementing the full DAG file: building Task 1 SQL by replacing the DECLARE-based `params` CTE with an Airflow-supplied run date, then wiring Tasks 2–4.



Adding `dagrun_timeout=timedelta(hours=3)` so the DAG run must finish within three hours of start (5am → 8am UTC). Removing the redundant explicit task chain (TaskFlow already encodes dependencies).



### Part 3 Airflow DAG added

**New file:** [part_3/campaign_pipeline_dag.py](part_3/campaign_pipeline_dag.py)

| Spec item | Implementation |
|-----------|----------------|
| **Schedule** | `0 5 * * *`, `start_date` in UTC, `catchup=False` |
| **Retries** | `default_args`: `retries=2`, `retry_delay=5 minutes` |
| **SLA / 8am UTC** | `dagrun_timeout=timedelta(hours=3)` (5am → 8am) + per-task `sla=timedelta(hours=3)` in `default_args` |
| **Linear flow** | TaskFlow: `t2(t1)`, `t3(t1,t2)`, `task_4(t1,t2,t3)` → **1 → 2 → 3 → 4** |
| **Task 1** | Reads [part_1/sms_reactivation_audience.sql](part_1/sms_reactivation_audience.sql), strips `DECLARE`, injects `as_of_date` from `logical_date`, **`CREATE OR REPLACE TABLE`** staging, counts rows |
| **Task 2** | `count > 0`; `count <= 2 ×` historical avg from reporting table (90d); **cold start** if baseline missing |
| **Task 3** | Loads audience from staging, **`execute_campaign_send`** from Part 2 + `StubESPClient` |
| **Task 4** | `CREATE TABLE IF NOT EXISTS` reporting table, parameterized `INSERT`, Slack webhook POST if `SLACK_WEBHOOK_URL` set |

**Config (env):** `GCP_CONN_ID`, `GCP_PROJECT`, `BQ_DATASET`, `STAGING_TABLE_ID`, `REPORTING_TABLE_ID`, `CAMPAIGN_ID`, `SLACK_WEBHOOK_URL`, `SENT_LOG_PATH` (defaults documented in file).

All related todos are **completed**; the plan file was **not** edited.

**Deploy note:** Requires Airflow with **`apache-airflow-providers-google`** (for `BigQueryHook`). Point the DAGs folder (or sync) so this file is on the scheduler’s `PYTHONPATH`; adjust `YOUR_GCP_PROJECT` / dataset env vars for real runs.

---

**User**

# Value Model Integration (Design)

The data science team has built a predictive model that scores each renter on their likelihood to convert. The model outputs a daily BigQuery table:

```sql
CREATE TABLE ml_predictions.renter_send_scores (
  renter_id STRING,
  predicted_conversion_probability FLOAT64,  -- 0.0 to 1.0
  model_version STRING,
  scored_at TIMESTAMP
);
```

**Requirement:** Only send the reactivation SMS to renters whose predicted conversion probability exceeds a configurable threshold (e.g., 0.3).

**Additional context:** A second predictive model for a different user segment is expected in approximately 6 weeks. Your design should support multiple models with different thresholds per segment without requiring rearchitecture.

## Task

Describe (in writing or pseudocode) how you would modify:

1. The **BigQuery query** from Part 1 to incorporate model scores
2. The **Airflow DAG** from Part 3 to add a dependency on the model scoring table being fresh (scored today)
3. How you would handle the case where the **model scoring job hasn't completed** by the time the campaign DAG runs

## Evaluation Criteria

- Clean integration of model scores into the audience query (JOIN pattern, threshold parameterization)
- DAG dependency management (sensor or external task dependency for model freshness)
- Graceful handling of model pipeline delays (wait vs. skip vs. fallback)
- Shows understanding of the business tradeoff: sending without model scores wastes volume, not sending at all loses revenue

Given the above requirement, write into the @ai-session/part-4_spec.md file the necessary spec to be used by an AI agent to build the code to achieve this.

---

**Cursor**

I’ll create a complete Part 4 implementation spec in `ai-session/part-4_spec.md` covering SQL model-score integration, DAG freshness dependency, and delay-handling policy with configurable fallback behavior. I’m reading the file first and then writing it.

The file is empty, so I’m writing the full agent-ready design spec now, including pseudocode and explicit decision rules for freshness and fallback.

Updated `ai-session/part-4_spec.md` with a full agent-ready spec covering all requested design changes:

- **Part 1 SQL integration**:
  - Score-table join pattern
  - Configurable threshold/model/segment parameters
  - Latest-score-per-renter logic
  - Freshness filter (`DATE(scored_at) = as_of_date`)
  - Multi-model readiness via `model_send_config` mapping table/CTE
- **Part 3 DAG integration**:
  - New upstream model-freshness dependency task (`task_0_wait_for_model_scores_fresh`)
  - Sensor pattern recommendations (`poke_interval`, `timeout`, `reschedule`)
  - Configurable runtime controls for model freshness behavior
- **Delayed model handling policy**:
  - `BLOCK_AND_WAIT`, `SKIP_CAMPAIGN_IF_STALE`, and guarded fallback mode
  - Recommended default and business tradeoff rationale
  - Required reporting and Slack metadata for transparency
- **Acceptance checklist** aligned to your evaluation criteria

If you want, I can now implement these Part 4 changes directly in `part_1`, `part_3`, and reporting payloads as a single end-to-end update.

---

**User**

Take the spec @ai-session/part-4_spec.md and write/describe (in writing or pseudocode) inside the folder "part_4" to achive the changes indicated in the spec file

---

**Cursor**

Reviewing the Part 4 spec and repo layout to plan deliverables for `part_4/`.



`part_4/` doesn't exist yet. Planning documentation artifacts that translate `ai-session/part-4_spec.md` into actionable design and pseudocode (no Part 1/3 code edits unless you ask later).



---

**User**

Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself.

To-do's from the plan have already been created. Do not create them again. Mark them as in_progress as you work, starting with the first one. Don't stop until you have completed all the to-dos.

---

**Cursor**

Implementing Part 4 design docs: marking the first todo in progress and creating `part_4` files.



### Part 4 design deliverables

Created **`part_4/`** with the planned docs and snippet (plan file was **not** edited):

| File | Contents |
|------|----------|
| [part_4/README.md](part_4/README.md) | Index, links to docs, dependency overview |
| [part_4/01_bigquery_integration.md](part_4/01_bigquery_integration.md) | `model_send_config`, `latest_scores` CTE, INNER JOIN + threshold, extra output columns, full pseudocode aligned to Part 1 |
| [part_4/02_airflow_dag_changes.md](part_4/02_airflow_dag_changes.md) | `task_0_wait_for_model_scores_fresh` → Task 1–4, sensor options, Task 1 XCom extensions, config surface |
| [part_4/03_model_delay_policy.md](part_4/03_model_delay_policy.md) | `BLOCK_AND_WAIT` / `SKIP_CAMPAIGN_IF_STALE` / `FALLBACK_WITH_STRICT_GUARDRAILS`, timeout matrix, reporting + Slack |
| [part_4/snippets/freshness_check.sql](part_4/snippets/freshness_check.sql) | Copy-pastable BigQuery freshness query (+ commented row-count variant) |

All related todos are **completed**. Part 1–3 code was **not** changed (per plan scope).

---

**User**

# Observability Design (Written)

In 1–2 paragraphs per question, describe:

1. **What Datadog metrics and alerts would you set up** for this pipeline? Think about: DAG latency, audience size anomalies, ESP error rates, send completion rates.
2. **How would you detect and prevent double-sends** if the pipeline runs twice due to an Airflow retry or manual re-trigger?
3. **What happens if the ESP goes down mid-send?** Describe your circuit-breaker or recovery strategy.

Given the above requirement, write into the @ai-session/part-5_spec.md file the necessary spec to be used by an AI agent to build the code to achieve this.

---

**Cursor**

I’ll write a full Part 5 implementation spec in `ai-session/part-5_spec.md` that translates these observability/reliability questions into concrete, buildable requirements for metrics, alerts, idempotency, and outage recovery. I’m reading the file first, then updating it.

The file is empty, so I’m adding a complete agent-ready spec now with required Datadog instrumentation, alert thresholds, double-send protections, and ESP circuit-breaker/recovery behavior.

Updated `ai-session/part-5_spec.md` with a complete, agent-ready spec for observability and reliability implementation.

It now includes:

- **Datadog metrics design** across DAG health, audience quality, ESP behavior, send outcomes, and idempotency safeguards
- **Concrete alert requirements** for SLA latency, run failures, audience anomalies, ESP error rates, completion rate, and retry pressure
- **Double-send prevention strategy** with layered controls:
  - `(campaign_id, renter_id)` dedupe
  - run-level idempotency key + lock
  - batch-level idempotency tokens (if ESP supports)
  - reporting reconciliation
- **ESP outage handling** with a clear **circuit-breaker** model (`CLOSED` / `OPEN` / `HALF_OPEN`), open conditions, checkpointing, and replay-safe recovery
- **Implementation guidance** mapping changes to `part_2` and `part_3`, plus optional new helper modules
- **Config surface** and final acceptance checklist for execution readiness

If you want, I can next turn this into concrete implementation docs/code scaffolding under a `part_5/` folder the same way we did for Part 4.

---

**User**

Take the spec @ai-session/part-5_spec.md  and build the code inside the folder "part_5". Only 1–2 paragraphs per question. No code need to be developed just answer the questions.

---

**Cursor**

I can do this as written prose only, but I need one quick preference because you asked for output “inside `part_5`” while also saying no code should be developed.

**Output format for Part 5**

> **Where do you want the 1–2 paragraph answers per question?**
> - Reply in chat only (no files changed)
> - Write a markdown document in `part_5/` (still prose only, no code) ✔

Great—I'll prepare a concise implementation plan to create a prose-only markdown deliverable in `part_5/` with 1–2 paragraphs per question, aligned to `ai-session/part-5_spec.md`.

---

**User**

Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself.

To-do's from the plan have already been created. Do not create them again. Mark them as in_progress as you work, starting with the first one. Don't stop until you have completed all the to-dos.

---

**Cursor**

Creating `part_5/observability_design_answers.md` with three 1–2 paragraph sections (prose only, no code blocks). Marking todos as we go.



### Part 5 prose deliverable

**Added:** [part_5/observability_design_answers.md](part_5/observability_design_answers.md)

- **Three sections** matching the questions (Datadog metrics/alerts, double-send prevention, ESP outage / circuit breaker).
- **Two paragraphs per section** (within the 1–2 paragraph limit).
- **Prose only** — no fenced code blocks; aligns with [ai-session/part-5_spec.md](ai-session/part-5_spec.md).

All plan todos are **completed**. The plan file was **not** edited.

---

**User**

Create a README.md file in the root of the project describing it and a short description of what of the folder structure contains for the folders: part_1, part_2, part_3, part_4 and part_5 

---

**Cursor**

Gathering project layout to write an accurate root README.



Created [README.md](README.md) at the repo root with a short project overview and descriptions of **`part_1`** through **`part_5`**, plus a brief note on **`ai-session/`** for specs and plans.

