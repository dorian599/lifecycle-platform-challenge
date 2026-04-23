# Part 4: Value model integration (design)

This folder translates [ai-session/part-4_spec.md](../ai-session/part-4_spec.md) into concrete design notes and pseudocode for integrating ML conversion scores into the campaign pipeline—without changing [part_1](../part_1/), [part_2](../part_2/), or [part_3](../part_3/) in this deliverable.

## Documents

| Doc | Purpose |
|-----|---------|
| [01_bigquery_integration.md](01_bigquery_integration.md) | How to extend the Part 1 audience query: config table, JOIN/freshness/threshold, output columns |
| [02_airflow_dag_changes.md](02_airflow_dag_changes.md) | New `task_0` freshness dependency, sensor pattern, XCom extensions for Part 3 |
| [03_model_delay_policy.md](03_model_delay_policy.md) | Wait vs skip vs fallback; reporting and Slack semantics |
| [snippets/freshness_check.sql](snippets/freshness_check.sql) | Copy-pastable BigQuery check for a sensor |

## Baseline artifacts

- Audience SQL: [part_1/sms_reactivation_audience.sql](../part_1/sms_reactivation_audience.sql)
- Send pipeline: [part_2/campaign_send_pipeline.py](../part_2/campaign_send_pipeline.py)
- DAG skeleton: [part_3/campaign_pipeline_dag.py](../part_3/campaign_pipeline_dag.py)

## Dependency overview

```text
task_0_wait_for_model_scores_fresh
  → task_1_run_audience_query
  → task_2_validate_audience
  → task_3_execute_campaign_send
  → task_4_log_and_notify
```
