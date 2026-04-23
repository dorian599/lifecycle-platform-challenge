# Part 2 Spec: Pipeline Orchestration for ESP Send (Python)

## Goal
Implement a Python module that takes an audience payload, sends recipients to an ESP API in controlled batches, and returns execution metrics. The implementation must be safe to rerun (idempotent) and resilient to API throttling/failures.

## Provided Interface (Do Not Modify)

```python
class ESPClient:
    def send_batch(self, campaign_id: str, recipients: list[dict]) -> Response:
        """Sends a batch of recipients to the ESP.
        Returns a Response with .status_code and .json()"""
        pass
```

## Required Deliverable

```python
def execute_campaign_send(
    campaign_id: str,
    audience: list[dict],
    esp_client: ESPClient,
    sent_log_path: str = "sent_renters.json"
) -> dict:
    """Returns {'total_sent': int, 'total_failed': int, 'total_skipped': int, 'elapsed_seconds': float}"""
```

## File/Module Expectations
- Create a Python module in the project area for Part 2 (agent can choose exact file path under `part_2/`).
- Keep `execute_campaign_send(...)` as the primary public entrypoint.
- Helper functions are encouraged for readability (`batching`, `retry`, `sent log I/O`, `logging helpers`).

## Functional Requirements

### 1) Batching
- ESP max batch size is 100 recipients.
- Split `audience` into chunks of at most 100.
- Preserve deterministic order (use input order after dedupe filtering).

### 2) Rate Limiting + Retry
- Retry only when `status_code == 429`.
- Use exponential backoff with jitter, up to 5 retries max.
- Recommended delay formula:
  - `delay_seconds = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, jitter_max)`
  - Suggested constants: `base_delay = 1.0`, `jitter_max = 0.5`, `max_delay = 30.0`.
- Attempt numbering can be zero-based; ensure total retry attempts do not exceed 5.
- If retries exhausted, mark the batch as failed and continue with next batch.

### 3) Deduplication / Idempotency
- Prevent duplicate send per `campaign_id` + `renter_id` across reruns.
- Use file-based sent log at `sent_log_path`.
- Sent log format should be JSON and easy to inspect manually.
- Recommended structure:

```json
{
  "campaign_id_1": ["renter_a", "renter_b"],
  "campaign_id_2": ["renter_x"]
}
```

- On pipeline start:
  - Load existing file if present.
  - If file missing, start from empty structure.
  - If malformed JSON, log warning/error and fail fast OR safely back up and reset (pick one strategy, document in code).
- Before batching:
  - Skip recipients already present for the same campaign.
  - Count those as `total_skipped`.
- After successful batch send:
  - Persist newly sent renter IDs to file (append semantics, no duplicates).
  - Write atomically when possible (temp file + replace) to reduce corruption risk.

### 4) Error Handling
- One batch failure must not abort remaining batches.
- Catch and handle:
  - HTTP non-2xx responses (especially 429 handled via retry)
  - exceptions from `esp_client.send_batch(...)`
  - JSON parsing errors from `response.json()` (if used in logs)
- For failed batches, emit structured logs with:
  - `campaign_id`
  - `batch_index`
  - recipient count
  - renter IDs in batch (or first N + count if large)
  - HTTP status / exception message
  - retry attempts used

### 5) Metrics
- Return exact keys:
  - `total_sent`
  - `total_failed`
  - `total_skipped`
  - `elapsed_seconds`
- Definitions:
  - `total_sent`: recipients successfully accepted by ESP (count batch size on successful response)
  - `total_failed`: recipients in failed batches after retries exhausted / unrecoverable errors
  - `total_skipped`: recipients removed due to dedupe (already sent for campaign)
  - `elapsed_seconds`: wall-clock runtime in seconds as float from function start to end

## Data Contract for Audience Input
- `audience` is `list[dict]`.
- Each recipient dict must include `renter_id` (string-like).
- Missing/blank `renter_id` entries:
  - recommended: skip and count as failed with warning log (or explicit validation error strategy).
  - choose one behavior and implement consistently.

## Response Handling Guidance
- Treat any 2xx status as success unless business logic requires stricter interpretation.
- For non-2xx (except handled 429 retry path), mark batch failed and continue.

## Suggested Implementation Outline
1. Start timer.
2. Load sent log file into memory (campaign -> set of renter IDs).
3. Partition audience into:
   - deduped recipients to send
   - skipped recipients
4. Batch deduped recipients (size 100).
5. For each batch:
   - attempt send
   - on 429: exponential backoff + jitter retry (max 5)
   - on success: increment sent, update in-memory sent set, persist file
   - on failure: increment failed, log actionable error context, continue
6. Stop timer and return metrics dict.

## Logging Requirements
- Use Python `logging` module (avoid print statements).
- Log levels:
  - `INFO`: run start/end, batch successes, summary
  - `WARNING`: retries, recoverable anomalies
  - `ERROR`: failed batches, I/O failures, unrecoverable issues
- Keep log messages structured and grep-friendly (key=value style preferred).

## Non-Goals / Constraints
- Do not modify the `ESPClient` interface.
- No external queueing system required.
- No database dependency required for dedupe (file-based only).

## Acceptance Checklist
- Batch size never exceeds 100.
- 429 path uses exponential backoff with jitter and max 5 retries.
- Deduplication prevents resend for same campaign on rerun.
- Failed batches do not stop the rest of pipeline.
- Return dict has exact required keys and sensible values.
- Logs include enough metadata to manually retry failed batches.
- Code is modular and readable (helpers for core concerns).

## Optional Test Scenarios (for agent validation)
- Audience length 0 returns all zeros quickly.
- Mixed already-sent and new recipients produce correct `total_skipped`.
- Simulated 429 then success validates retry behavior.
- Simulated repeated 429 validates retry exhaustion and failure counting.
- One batch fails while next succeeds validates fault isolation.
- Corrupted sent log file behavior matches chosen strategy.
