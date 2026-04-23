---
name: Build Part 2 Pipeline
overview: Implement the Part 2 Python orchestration module under `part_2` from the approved spec, covering batching, retries with jitter, dedupe persistence, resilient error handling, and run metrics.
todos:
  - id: create-part2-dir-and-module
    content: Create `part_2` directory and `campaign_send_pipeline.py` scaffold with entrypoint signature
    status: pending
  - id: implement-core-flow
    content: Implement dedupe, batching, retry with jitter, error isolation, and metrics return in `execute_campaign_send`
    status: pending
  - id: implement-log-io-and-logging
    content: Add sent-log file I/O strategy and structured logging for successes/retries/failures
    status: pending
  - id: self-validate-against-spec
    content: Verify implementation behavior and returned schema against `ai-session/part-2_spec.md`
    status: pending
isProject: false
---

# Build Part 2 ESP Pipeline

## Scope
Implement the production code from [part-2 spec](/Users/dorian599/Work/ME/lifecycle-platform-challenge/ai-session/part-2_spec.md) in a new `part_2` artifact.

## Files to create
- [part_2/campaign_send_pipeline.py](/Users/dorian599/Work/ME/lifecycle-platform-challenge/part_2/campaign_send_pipeline.py)

## Implementation steps
- Create `execute_campaign_send(campaign_id, audience, esp_client, sent_log_path="sent_renters.json")` as the only public entrypoint.
- Add helper functions inside the same module for:
  - reading/writing sent-log JSON safely (including missing file handling)
  - deduping recipients by `campaign_id` + `renter_id`
  - batching recipients into max-size 100 chunks
  - 429 retry loop with exponential backoff + jitter (max 5 retries)
- Implement batch send flow so one failed batch never aborts the run:
  - success path increments `total_sent` and persists newly sent renter IDs
  - failure path increments `total_failed` and logs actionable batch context
- Use structured logging (`key=value` style) at INFO/WARNING/ERROR levels per spec.
- Compute and return metrics dict with exact keys:
  - `total_sent`, `total_failed`, `total_skipped`, `elapsed_seconds`

## Validation checklist
- Batch size always `<= 100`.
- Retry only for HTTP 429 and stops after 5 retries.
- Dedupe works across reruns for same campaign via sent-log file.
- Non-429 failures and exceptions are isolated to their batch.
- Returned metrics match required schema and semantics.

## Notes from repo scan
- `part_2` does not exist yet; it will be created.
- No existing Python conventions are present in-repo, so implementation will follow the spec and Python stdlib-first patterns.
- Existing artifact layout suggests one clear file per part (e.g., [part_1 query](/Users/dorian599/Work/ME/lifecycle-platform-challenge/part_1/sms_reactivation_audience.sql)).