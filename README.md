# Lifecycle platform challenge

This repository is a small, part-based exercise that walks through building an **SMS reactivation campaign** end to end: defining the audience in BigQuery, sending in batches through an ESP client with retries and dedupe, orchestrating runs in Airflow, extending the design with ML conversion scores, and documenting observability and reliability.

Authoritative requirements and agent prompts live under `ai-session/` (specs and plans). Implementations and design notes are grouped by part below.

## Folder structure

### `part_1/`

BigQuery **audience definition**: Standard SQL that builds the reactivation segment (activity, consent, suppression, idempotent daily windows) in `sms_reactivation_audience.sql`.

### `part_2/`

Python **campaign send pipeline**: batches recipients (max 100), retries on HTTP 429 with exponential backoff and jitter, file-based dedupe by `campaign_id` + `renter_id`, resilient per-batch error handling, and run metrics. Entry point: `execute_campaign_send` in `campaign_send_pipeline.py`.

### `part_3/`

**Airflow DAG skeleton** for the full pipeline: materialize audience to a staging table, validate size vs. history, call the Part 2 sender, then log to a reporting table and optionally notify Slack. Scheduled daily at 05:00 UTC with retries and SLA-oriented timeouts. See `campaign_pipeline_dag.py`.

### `part_4/`

**Design documentation** for integrating **ML conversion scores**: how to extend the Part 1 query (joins, thresholds, segment config), add a model-freshness step before the DAG audience task, and policies for delayed scoring (wait, skip, or guarded fallback). Includes a reusable freshness-check SQL snippet.

### `part_5/`

**Written observability answers**: Datadog-style metrics and alerts, double-send prevention across Airflow retries, and ESP outage handling with circuit breaking and recovery—aligned to `ai-session/part-5_spec.md` in `observability_design_answers.md`.

## `ai-session/`

Contains **part specs** (`part-*_spec.md`) and **implementation plans** used to drive the work; it is not required at runtime but documents intent and acceptance criteria.
