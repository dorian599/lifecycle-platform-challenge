# Part 3 Spec: Airflow DAG Skeleton for Full Campaign Pipeline

## Goal
Create an Airflow DAG that orchestrates the full SMS campaign pipeline end-to-end with validation, delivery, and reporting/notification.

Pipeline stages:
1. Build audience from BigQuery query into a staging table
2. Validate audience quality and size
3. Execute campaign send using Part 2 function
4. Persist run results and notify Slack

## Required DAG Behavior

### Schedule and Runtime Controls
- Schedule: daily at **5:00 AM UTC**
- Retries: **2** retries per task
- Retry delay: **5 minutes**
- SLA: raise alert if DAG run has not completed by **8:00 AM UTC**
  - Equivalent SLA duration from schedule time: **3 hours**

### Dependency Graph
- Linear dependency chain only:
  - `task_1_run_audience_query` -> `task_2_validate_audience` -> `task_3_execute_campaign_send` -> `task_4_log_and_notify`

## File and Module Expectations
- Create a new DAG file under a DAGs folder (agent may choose exact path, e.g. `part_3/campaign_pipeline_dag.py` or `dags/campaign_pipeline_dag.py`).
- Keep task logic separated by responsibility (query, validation, send, reporting/notification).
- Use either:
  - Airflow TaskFlow API (`@dag`, `@task`) **or**
  - `PythonOperator`
- Prefer TaskFlow API for readability and cleaner XCom passing.

## Inputs / Upstream References
- Audience SQL source from Part 1:
  - `part_1/sms_reactivation_audience.sql`
- Campaign send executor from Part 2:
  - `part_2/campaign_send_pipeline.py`
  - function: `execute_campaign_send(...)`

## Task Definitions

### Task 1: Run Audience Query to Staging Table
Purpose:
- Execute the Part 1 SQL against BigQuery.
- Write results into a deterministic staging table for this run.

Implementation guidance:
- Use BigQuery hook/operator (`BigQueryInsertJobOperator`, `BigQueryHook`, etc.).
- Read SQL from the Part 1 file (or inline string if needed), then materialize:
  - `project.dataset.audience_staging` (or partitioned/date-suffixed equivalent).
- Include run metadata columns if helpful (`run_date`, `dag_run_id`, `loaded_at`).

Return/XCom payload suggestion:
- staging table reference (`project.dataset.table`)
- audience row count (if available immediately)

### Task 2: Validate Audience
Purpose:
- Prevent blind sends; enforce sanity checks.

Required checks:
1. Audience count is greater than 0.
2. Audience count is not greater than 2x historical average.

Implementation guidance:
- Query staging table count for current run.
- Query historical average from a reporting/history table OR recent successful runs table.
- If historical baseline unavailable (cold start), log warning and apply a safe default policy (documented in code), e.g. allow with warning.
- Fail task (`AirflowException`) on failed validation to stop downstream send.

Return/XCom payload suggestion:
- validated audience count
- validation flags / ratios (e.g., `count_to_avg_ratio`)

### Task 3: Execute Campaign Send
Purpose:
- Pull validated audience rows and send via Part 2 function.

Implementation guidance:
- Load audience records from staging table into `list[dict]` shape expected by `execute_campaign_send`.
- Build or derive `campaign_id` (config, Variable, env, or deterministic convention).
- Instantiate ESP client dependency (placeholder/stub acceptable in skeleton).
- Call:
  - `execute_campaign_send(campaign_id, audience, esp_client, sent_log_path=...)`
- Capture returned metrics dict.

Return/XCom payload suggestion:
- full send summary:
  - `total_sent`
  - `total_failed`
  - `total_skipped`
  - `elapsed_seconds`
  - plus `campaign_id`

### Task 4: Log Reporting + Slack Notification
Purpose:
- Persist run result and notify stakeholders.

Implementation guidance:
- Insert one reporting row into BigQuery reporting table, including:
  - run timestamp / run id
  - campaign_id
  - audience_count
  - send metrics
  - validation metrics (optional but recommended)
  - status (`success`, `partial_failure`, `validation_failed`, etc.)
- Send Slack message summary (via webhook, Slack operator, or helper client):
  - DAG/run identifier
  - campaign_id
  - audience count
  - sent/failed/skipped
  - elapsed seconds
  - link/reference to logs if possible

Slack behavior:
- In skeleton, external secret wiring can be TODO, but function/operator call pattern should be present.

## Airflow Configuration Requirements
- DAG-level defaults should include:
  - `retries=2`
  - `retry_delay=timedelta(minutes=5)`
  - `sla=timedelta(hours=3)` (or task-level equivalent)
- DAG schedule should be equivalent to daily 05:00 UTC:
  - cron: `"0 5 * * *"`
- Set `catchup=False` for skeleton unless backfill is explicitly desired.
- Add clear `start_date` and descriptive DAG id.

## XCom / Data Passing Contract
- Use explicit structured dictionaries between tasks, not raw positional tuples.
- Suggested flow:
1. Task 1 emits: staging table + audience_count(optional)
2. Task 2 consumes Task 1, emits validated counts/ratios
3. Task 3 consumes Task 1 + Task 2 outputs, emits send metrics
4. Task 4 consumes all prior outputs for final reporting and notification

## Error Handling Expectations
- Validation failure should stop send task.
- Send task should complete with metrics even if some recipient batches fail (handled by Part 2 logic).
- Reporting/Slack task should be robust and log actionable errors.

## Suggested DAG Skeleton Outline
1. DAG declaration with schedule, default args, SLA
2. Task 1 function/operator: execute audience SQL to staging
3. Task 2 function/operator: run validation checks and gate
4. Task 3 function/operator: call `execute_campaign_send`
5. Task 4 function/operator: write reporting row + Slack notify
6. Set linear dependencies explicitly

## Acceptance Checklist
- DAG scheduled at 05:00 UTC daily.
- Retries and retry delay configured correctly on tasks.
- SLA protection present for completion by 08:00 UTC.
- Dependency chain is strictly linear: 1 -> 2 -> 3 -> 4.
- Validation checks enforce:
  - audience count > 0
  - audience <= 2x historical average
- Part 2 function is invoked from Task 3 with correct input contract.
- Reporting + Slack summary implemented in Task 4.
- Code demonstrates clear separation of concerns and Airflow-native orchestration patterns.

## Notes for Agent Implementing Code
- This part asks for a **DAG skeleton**; prioritize structure, interfaces, and wiring over production secret management.
- Use clear TODO comments where infrastructure-specific details are unknown (project id, datasets, Slack webhook source, ESP client concrete class).
- Keep imports and task boundaries clean so the file is easy to operationalize later.
