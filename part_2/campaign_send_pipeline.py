"""ESP campaign send pipeline: batching, 429 backoff, file-based dedupe, metrics.

Implements ai-session/part-2_spec.md.
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import time
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 100
BASE_DELAY = 1.0
JITTER_MAX = 0.5
MAX_DELAY = 30.0
MAX_429_RETRIES = 5
RENTER_ID_PREVIEW_LIMIT = 50


class ESPClient:
    def send_batch(self, campaign_id: str, recipients: list[dict]) -> Any:
        """Sends a batch of recipients to the ESP.
        Returns a Response with .status_code and .json()"""
        pass


def execute_campaign_send(
    campaign_id: str,
    audience: list[dict],
    esp_client: ESPClient,
    sent_log_path: str = "sent_renters.json",
) -> dict:
    """Returns {'total_sent': int, 'total_failed': int, 'total_skipped': int, 'elapsed_seconds': float}"""
    start = time.perf_counter()
    total_sent = 0
    total_failed = 0
    total_skipped = 0

    logger.info(
        "campaign_send_start campaign_id=%s audience_size=%s sent_log_path=%s",
        campaign_id,
        len(audience),
        sent_log_path,
    )

    sent_log = _load_sent_log(sent_log_path)
    already_sent = set(sent_log.get(campaign_id, []))

    to_send: list[dict] = []
    seen_in_run: set[str] = set()

    for recipient in audience:
        renter_id = _extract_renter_id(recipient)
        if renter_id is None:
            total_failed += 1
            logger.warning(
                "invalid_renter_id campaign_id=%s recipient_keys=%s",
                campaign_id,
                sorted(recipient.keys()) if isinstance(recipient, dict) else type(recipient).__name__,
            )
            continue
        if renter_id in already_sent:
            total_skipped += 1
            continue
        if renter_id in seen_in_run:
            logger.warning(
                "duplicate_renter_in_audience campaign_id=%s renter_id=%s action=skip_extra",
                campaign_id,
                renter_id,
            )
            continue
        seen_in_run.add(renter_id)
        to_send.append(recipient)

    batches = list(_chunked(to_send, MAX_BATCH_SIZE))

    for batch_index, batch in enumerate(batches):
        success, retries_used, detail = _send_batch_with_retries(
            esp_client, campaign_id, batch, batch_index
        )
        if success:
            new_ids = [_extract_renter_id(r) for r in batch]
            # Defensive: all should be non-None after filtering
            new_ids = [rid for rid in new_ids if rid is not None]
            sent_log.setdefault(campaign_id, []).extend(new_ids)
            try:
                _save_sent_log_atomic(sent_log_path, sent_log)
            except OSError as exc:
                for _ in new_ids:
                    sent_log[campaign_id].pop()
                total_failed += len(batch)
                logger.error(
                    "sent_log_write_failed campaign_id=%s batch_index=%s batch_size=%s "
                    "renter_ids=%s error=%s action=reverted_batch_not_marked_sent",
                    campaign_id,
                    batch_index,
                    len(batch),
                    _format_renter_id_list(new_ids),
                    exc,
                )
            else:
                total_sent += len(batch)
                already_sent.update(new_ids)
                logger.info(
                    "batch_sent campaign_id=%s batch_index=%s batch_size=%s retries_used=%s",
                    campaign_id,
                    batch_index,
                    len(batch),
                    retries_used,
                )
        else:
            total_failed += len(batch)
            renter_ids = [_extract_renter_id(r) for r in batch]
            renter_ids = [rid for rid in renter_ids if rid is not None]
            logger.error(
                "batch_failed campaign_id=%s batch_index=%s batch_size=%s "
                "renter_ids=%s retries_used=%s detail=%s",
                campaign_id,
                batch_index,
                len(batch),
                _format_renter_id_list(renter_ids),
                retries_used,
                detail,
            )

    elapsed = time.perf_counter() - start
    result = {
        "total_sent": total_sent,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
        "elapsed_seconds": float(f"{elapsed:.6f}"),
    }
    logger.info(
        "campaign_send_complete campaign_id=%s total_sent=%s total_failed=%s "
        "total_skipped=%s elapsed_seconds=%s",
        campaign_id,
        total_sent,
        total_failed,
        total_skipped,
        result["elapsed_seconds"],
    )
    return result


def _chunked(items: list[dict], size: int) -> Iterator[list[dict]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _extract_renter_id(recipient: dict) -> str | None:
    if not isinstance(recipient, dict):
        return None
    rid = recipient.get("renter_id")
    if rid is None:
        return None
    s = str(rid).strip()
    if not s:
        return None
    return s


def _format_renter_id_list(renter_ids: list[str]) -> str:
    if len(renter_ids) <= RENTER_ID_PREVIEW_LIMIT:
        return str(renter_ids)
    head = renter_ids[:RENTER_ID_PREVIEW_LIMIT]
    return f"{head!r} ... (+{len(renter_ids) - RENTER_ID_PREVIEW_LIMIT} more)"


def _load_sent_log(path: str) -> dict[str, list[str]]:
    """Load sent log JSON. Malformed JSON: backup file and start fresh (documented strategy)."""
    if not os.path.exists(path):
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        backup = f"{path}.corrupt.{int(time.time())}"
        logger.error(
            "sent_log_corrupt path=%s error=%s backup_path=%s action=reset_empty",
            path,
            exc,
            backup,
        )
        try:
            os.replace(path, backup)
        except OSError as move_exc:
            logger.error(
                "sent_log_backup_failed path=%s backup_path=%s error=%s action=using_empty_log",
                path,
                backup,
                move_exc,
            )
        return {}
    except OSError as exc:
        logger.error(
            "sent_log_read_failed path=%s error=%s action=using_empty_log",
            path,
            exc,
        )
        return {}

    if not isinstance(raw, dict):
        logger.error(
            "sent_log_invalid_root path=%s type=%s action=using_empty_log",
            path,
            type(raw).__name__,
        )
        return {}

    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, list):
            out[key] = [str(x) for x in value]
        else:
            logger.warning(
                "sent_log_skip_campaign path=%s campaign_id=%s reason=not_a_list",
                path,
                key,
            )
    return out


def _save_sent_log_atomic(path: str, data: dict[str, list[str]]) -> None:
    """Write JSON atomically via temp file + replace."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=parent,
        prefix=".sent_renters_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_response_body(response: Any) -> Any:
    try:
        return response.json()
    except Exception as exc:  # noqa: BLE001 — spec: handle json parse errors
        return {"_parse_error": str(exc), "_fallback_text": getattr(response, "text", "")}


def _backoff_delay_seconds(retry_index: int) -> float:
    """retry_index is 0 on first backoff after first 429."""
    return min(BASE_DELAY * (2**retry_index), MAX_DELAY) + random.uniform(0, JITTER_MAX)


def _send_batch_with_retries(
    esp_client: ESPClient,
    campaign_id: str,
    batch: list[dict],
    batch_index: int,
) -> tuple[bool, int, Any]:
    """Returns (success, retries_used, detail). Retries only on HTTP 429, max MAX_429_RETRIES."""
    retries_used = 0
    last_detail: Any = None

    while True:
        try:
            response = esp_client.send_batch(campaign_id, batch)
        except Exception as exc:  # noqa: BLE001 — spec: isolate batch failures
            last_detail = f"exception={type(exc).__name__}:{exc!s}"
            logger.warning(
                "batch_send_exception campaign_id=%s batch_index=%s batch_size=%s "
                "retries_used=%s %s",
                campaign_id,
                batch_index,
                len(batch),
                retries_used,
                last_detail,
            )
            return False, retries_used, last_detail

        status = getattr(response, "status_code", None)
        last_detail = status

        if status is not None and 200 <= int(status) < 300:
            return True, retries_used, status

        if status == 429 and retries_used < MAX_429_RETRIES:
            delay = _backoff_delay_seconds(retries_used)
            logger.warning(
                "esp_rate_limited campaign_id=%s batch_index=%s batch_size=%s "
                "retry=%s/%s sleep_seconds=%.3f",
                campaign_id,
                batch_index,
                len(batch),
                retries_used + 1,
                MAX_429_RETRIES,
                delay,
            )
            time.sleep(delay)
            retries_used += 1
            continue

        body = _safe_response_body(response)
        last_detail = {"status_code": status, "body": body}
        return False, retries_used, last_detail
