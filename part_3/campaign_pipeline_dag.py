"""Airflow DAG: SMS reactivation campaign pipeline (audience → validate → send → report).

Skeleton per ai-session/part-3_spec.md. Wire BigQuery + Part 2 send + reporting/Slack.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any

import pendulum

from airflow.decorators import dag, task
from airflow.exceptions import AirflowException

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Config (override via env / Airflow Variables in deployment)
# -----------------------------------------------------------------------------
DAG_ID = "sms_reactivation_campaign_pipeline"
GCP_CONN_ID = os.environ.get("GCP_CONN_ID", "google_cloud_default")
# TODO: set real project/dataset in deployment
GCP_PROJECT = os.environ.get("GCP_PROJECT", "YOUR_GCP_PROJECT")
BQ_DATASET = os.environ.get("BQ_DATASET", "lifecycle_marketing")
STAGING_TABLE_ID = os.environ.get("STAGING_TABLE_ID", "audience_staging")
REPORTING_TABLE_ID = os.environ.get("REPORTING_TABLE_ID", "campaign_run_reporting")
CAMPAIGN_ID = os.environ.get("CAMPAIGN_ID", "sms_reactivation_v1")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")  # TODO: secrets backend

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PART1_SQL = _REPO_ROOT / "part_1" / "sms_reactivation_audience.sql"
_PART2_DIR = _REPO_ROOT / "part_2"
if str(_PART2_DIR) not in sys.path:
    sys.path.insert(0, str(_PART2_DIR))

from campaign_send_pipeline import ESPClient, execute_campaign_send  # noqa: E402


_DEFAULT_PARAMS_CTE = """WITH params AS (
  SELECT
    as_of_date,
    TIMESTAMP(as_of_date, 'UTC') AS as_of_ts
),"""


def _build_audience_ctas_sql(as_of_date_iso: str, staging_table_fqn: str) -> str:
    """Turn Part 1 script into CREATE TABLE AS by fixing `params` (removes DECLARE)."""
    if not _PART1_SQL.is_file():
        raise FileNotFoundError(f"Audience SQL not found: {_PART1_SQL}")

    raw = _PART1_SQL.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("DECLARE ")]
    body = "\n".join(lines)
    if _DEFAULT_PARAMS_CTE not in body:
        raise AirflowException(
            "part_1/sms_reactivation_audience.sql shape changed: expected default params CTE"
        )
    fixed_params = f"""WITH params AS (
  SELECT
    DATE('{as_of_date_iso}') AS as_of_date,
    TIMESTAMP(DATE('{as_of_date_iso}'), 'UTC') AS as_of_ts
),"""
    body = body.replace(_DEFAULT_PARAMS_CTE, fixed_params, 1)
    return f"CREATE OR REPLACE TABLE `{staging_table_fqn}` AS\n{body}"


def _staging_fqn() -> str:
    return f"{GCP_PROJECT}.{BQ_DATASET}.{STAGING_TABLE_ID}"


def _reporting_fqn() -> str:
    return f"{GCP_PROJECT}.{BQ_DATASET}.{REPORTING_TABLE_ID}"


class StubESPClient(ESPClient):
    """Placeholder ESP client for skeleton runs (returns HTTP 200)."""

    def send_batch(self, campaign_id: str, recipients: list[dict]) -> Any:
        class _Resp:
            status_code = 200
            text = ""

            def json(self) -> dict:
                return {"ok": True, "campaign_id": campaign_id, "batch_size": len(recipients)}

        return _Resp()


@dag(
    dag_id=DAG_ID,
    schedule="0 5 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    dagrun_timeout=timedelta(hours=3),
    tags=["lifecycle", "sms", "campaign"],
    default_args={
        "owner": "lifecycle-platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "sla": timedelta(hours=3),
    },
    doc_md=__doc__,
)
def campaign_pipeline_dag() -> None:
    @task(task_id="task_1_run_audience_query")
    def task_1_run_audience_query() -> dict[str, Any]:
        from airflow.operators.python import get_current_context

        from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

        ctx = get_current_context()
        logical = ctx["logical_date"]
        run_id = str(ctx["run_id"])
        as_of = logical.in_timezone("UTC").date().isoformat()

        staging = _staging_fqn()
        sql = _build_audience_ctas_sql(as_of, staging)

        hook = BigQueryHook(gcp_conn_id=GCP_CONN_ID, project_id=GCP_PROJECT, use_legacy_sql=False)
        client = hook.get_client(project_id=GCP_PROJECT)
        job = client.query(sql)
        job.result()

        count_sql = f"SELECT COUNT(*) AS cnt FROM `{staging}`"
        cnt_rows = list(client.query(count_sql).result())
        audience_count = int(cnt_rows[0]["cnt"]) if cnt_rows else 0

        out = {
            "staging_table": staging,
            "audience_count": audience_count,
            "as_of_date": as_of,
            "dag_run_id": run_id,
        }
        logger.info(
            "task_1_complete staging_table=%s audience_count=%s as_of_date=%s",
            staging,
            audience_count,
            as_of,
        )
        return out

    @task(task_id="task_2_validate_audience")
    def task_2_validate_audience(load_info: dict[str, Any]) -> dict[str, Any]:
        from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

        staging = load_info["staging_table"]
        audience_count = int(load_info["audience_count"])

        if audience_count <= 0:
            raise AirflowException(
                f"validation_failed reason=empty_audience staging_table={staging} count={audience_count}"
            )

        hook = BigQueryHook(gcp_conn_id=GCP_CONN_ID, project_id=GCP_PROJECT, use_legacy_sql=False)
        client = hook.get_client(project_id=GCP_PROJECT)

        historical_avg: float | None = None
        avg_sql = f"""
        SELECT AVG(audience_count) AS avg_cnt
        FROM `{_reporting_fqn()}`
        WHERE campaign_id = @campaign_id
          AND validation_passed IS TRUE
          AND run_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
        """
        job_config = None
        try:
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("campaign_id", "STRING", CAMPAIGN_ID),
                ]
            )
        except Exception:  # noqa: BLE001
            job_config = None

        try:
            if job_config is not None:
                rows = list(client.query(avg_sql, job_config=job_config).result())
            else:
                safe = avg_sql.replace("@campaign_id", f"'{CAMPAIGN_ID}'")
                rows = list(client.query(safe).result())
            if rows and rows[0]["avg_cnt"] is not None:
                historical_avg = float(rows[0]["avg_cnt"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "historical_baseline_unavailable reporting_table=%s error=%s action=cold_start",
                _reporting_fqn(),
                exc,
            )
            historical_avg = None

        ratio: float | None = None
        passed = True
        if historical_avg is None:
            logger.warning(
                "validation_cold_start campaign_id=%s audience_count=%s action=skip_2x_check",
                CAMPAIGN_ID,
                audience_count,
            )
        else:
            if historical_avg <= 0:
                logger.warning(
                    "historical_avg_non_positive campaign_id=%s avg=%s action=skip_2x_check",
                    CAMPAIGN_ID,
                    historical_avg,
                )
            else:
                ratio = audience_count / historical_avg
                if audience_count > 2 * historical_avg:
                    passed = False

        if not passed:
            raise AirflowException(
                "validation_failed reason=audience_gt_2x_historical_avg "
                f"campaign_id={CAMPAIGN_ID} audience_count={audience_count} "
                f"historical_avg={historical_avg} ratio={ratio}"
            )

        out = {
            "audience_count": audience_count,
            "historical_avg": historical_avg,
            "count_to_avg_ratio": ratio,
            "passed": True,
            "staging_table": staging,
        }
        logger.info(
            "task_2_complete audience_count=%s historical_avg=%s ratio=%s",
            audience_count,
            historical_avg,
            ratio,
        )
        return out

    @task(task_id="task_3_execute_campaign_send")
    def task_3_execute_campaign_send(
        load_info: dict[str, Any],
        validation_info: dict[str, Any],
    ) -> dict[str, Any]:
        from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

        staging = load_info["staging_table"]
        hook = BigQueryHook(gcp_conn_id=GCP_CONN_ID, project_id=GCP_PROJECT, use_legacy_sql=False)
        client = hook.get_client(project_id=GCP_PROJECT)

        # Exclude staging metadata columns if present (keep recipient fields for ESP)
        query = f"""
        SELECT
          renter_id,
          email,
          phone,
          last_login,
          search_count,
          days_since_login
        FROM `{staging}`
        """
        rows = [dict(row.items()) for row in client.query(query).result()]

        sent_log_path = os.environ.get(
            "SENT_LOG_PATH",
            str(_REPO_ROOT / "sent_renters.json"),
        )
        esp = StubESPClient()
        metrics = execute_campaign_send(
            campaign_id=CAMPAIGN_ID,
            audience=rows,
            esp_client=esp,
            sent_log_path=sent_log_path,
        )
        out = {
            "campaign_id": CAMPAIGN_ID,
            "staging_table": staging,
            "validation": validation_info,
            "send_metrics": metrics,
            "audience_count_sent_path": len(rows),
        }
        logger.info(
            "task_3_complete campaign_id=%s total_sent=%s total_failed=%s total_skipped=%s",
            CAMPAIGN_ID,
            metrics.get("total_sent"),
            metrics.get("total_failed"),
            metrics.get("total_skipped"),
        )
        return out

    @task(task_id="task_4_log_and_notify")
    def task_4_log_and_notify(
        load_info: dict[str, Any],
        validation_info: dict[str, Any],
        send_info: dict[str, Any],
    ) -> dict[str, Any]:
        from airflow.operators.python import get_current_context

        from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

        ctx = get_current_context()
        run_id = str(ctx["run_id"])
        logical = ctx["logical_date"]
        run_ts = logical.in_timezone("UTC").to_iso8601_string()

        m = send_info["send_metrics"]
        audience_count = int(validation_info["audience_count"])
        total_failed = int(m.get("total_failed", 0))
        status = "success" if total_failed == 0 else "partial_failure"

        row = {
            "run_id": run_id,
            "run_ts": run_ts,
            "dag_id": DAG_ID,
            "campaign_id": send_info["campaign_id"],
            "staging_table": load_info["staging_table"],
            "audience_count": audience_count,
            "historical_avg": validation_info.get("historical_avg"),
            "count_to_avg_ratio": validation_info.get("count_to_avg_ratio"),
            "validation_passed": True,
            "total_sent": m.get("total_sent"),
            "total_failed": m.get("total_failed"),
            "total_skipped": m.get("total_skipped"),
            "elapsed_seconds": m.get("elapsed_seconds"),
            "status": status,
        }

        hook = BigQueryHook(gcp_conn_id=GCP_CONN_ID, project_id=GCP_PROJECT, use_legacy_sql=False)
        client = hook.get_client(project_id=GCP_PROJECT)
        reporting = _reporting_fqn()

        # Ensure table exists (minimal schema skeleton)
        ddl = f"""
        CREATE TABLE IF NOT EXISTS `{reporting}` (
          run_id STRING,
          run_ts TIMESTAMP,
          dag_id STRING,
          campaign_id STRING,
          staging_table STRING,
          audience_count INT64,
          historical_avg FLOAT64,
          count_to_avg_ratio FLOAT64,
          validation_passed BOOL,
          total_sent INT64,
          total_failed INT64,
          total_skipped INT64,
          elapsed_seconds FLOAT64,
          status STRING
        );
        """
        client.query(ddl).result()

        insert_sql = f"""
        INSERT INTO `{reporting}` (
          run_id, run_ts, dag_id, campaign_id, staging_table, audience_count,
          historical_avg, count_to_avg_ratio, validation_passed,
          total_sent, total_failed, total_skipped, elapsed_seconds, status
        )
        SELECT
          @run_id, TIMESTAMP(@run_ts), @dag_id, @campaign_id, @staging_table, @audience_count,
          @historical_avg, @count_to_avg_ratio, @validation_passed,
          @total_sent, @total_failed, @total_skipped, @elapsed_seconds, @status
        """
        try:
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("run_id", "STRING", row["run_id"]),
                    bigquery.ScalarQueryParameter("run_ts", "STRING", row["run_ts"]),
                    bigquery.ScalarQueryParameter("dag_id", "STRING", row["dag_id"]),
                    bigquery.ScalarQueryParameter("campaign_id", "STRING", row["campaign_id"]),
                    bigquery.ScalarQueryParameter("staging_table", "STRING", row["staging_table"]),
                    bigquery.ScalarQueryParameter("audience_count", "INT64", row["audience_count"]),
                    bigquery.ScalarQueryParameter(
                        "historical_avg",
                        "FLOAT64",
                        row["historical_avg"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "count_to_avg_ratio",
                        "FLOAT64",
                        row["count_to_avg_ratio"],
                    ),
                    bigquery.ScalarQueryParameter(
                        "validation_passed",
                        "BOOL",
                        row["validation_passed"],
                    ),
                    bigquery.ScalarQueryParameter("total_sent", "INT64", row["total_sent"]),
                    bigquery.ScalarQueryParameter("total_failed", "INT64", row["total_failed"]),
                    bigquery.ScalarQueryParameter("total_skipped", "INT64", row["total_skipped"]),
                    bigquery.ScalarQueryParameter(
                        "elapsed_seconds",
                        "FLOAT64",
                        row["elapsed_seconds"],
                    ),
                    bigquery.ScalarQueryParameter("status", "STRING", row["status"]),
                ]
            )
            client.query(insert_sql, job_config=job_config).result()
        except Exception as exc:  # noqa: BLE001
            logger.error("reporting_insert_failed table=%s error=%s", reporting, exc)
            raise

        slack_text = (
            f"dag_id={DAG_ID} run_id={run_id} campaign_id={row['campaign_id']} "
            f"audience_count={audience_count} "
            f"sent={m.get('total_sent')} failed={m.get('total_failed')} "
            f"skipped={m.get('total_skipped')} elapsed_s={m.get('elapsed_seconds')} "
            f"status={status}"
        )
        if SLACK_WEBHOOK_URL:
            try:
                payload = json.dumps({"text": slack_text}).encode("utf-8")
                req = urllib.request.Request(  # noqa: S310
                    SLACK_WEBHOOK_URL,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    _ = resp.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                logger.error("slack_notify_failed error=%s text=%s", exc, slack_text)
        else:
            logger.warning("slack_webhook_missing action=skip_notify text=%s", slack_text)

        logger.info("task_4_complete %s", slack_text)
        return {"reporting_row": row, "slack_text": slack_text}

    t1 = task_1_run_audience_query()
    t2 = task_2_validate_audience(t1)
    t3 = task_3_execute_campaign_send(t1, t2)
    task_4_log_and_notify(t1, t2, t3)


campaign_pipeline_dag()
