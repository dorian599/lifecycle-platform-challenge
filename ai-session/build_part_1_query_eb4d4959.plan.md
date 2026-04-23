---
name: Build Part 1 Query
overview: Create the Part 1 SQL artifact in a new `part_1` folder using the approved spec, with deterministic daily behavior and explicit eligibility/suppression filtering.
todos:
  - id: create-part1-dir
    content: Create `part_1` directory in repository root
    status: completed
  - id: add-sql-file
    content: Add `part_1/sms_reactivation_audience.sql` with spec-compliant BigQuery query
    status: completed
  - id: validate-spec-alignment
    content: Verify all filters, joins, idempotency, and output columns match `ai-session/part-1_spec.md`
    status: completed
isProject: false
---

# Build SMS Reactivation Query in part_1

## What I found
- The spec is ready at [ai-session/part-1_spec.md](/Users/dorian599/Work/ME/lifecycle-platform-challenge/ai-session/part-1_spec.md).
- There is currently no `part_1` directory in the workspace.
- There are currently no existing `.sql` files to mirror for naming/style conventions.

## Implementation plan
- Create a new directory: [part_1](/Users/dorian599/Work/ME/lifecycle-platform-challenge/part_1).
- Add a single SQL file (proposed: `sms_reactivation_audience.sql`) containing the BigQuery Standard SQL from the spec.
- Keep the query idempotent by anchoring all windows/calculations to one `as_of_date` and derived `as_of_ts`.
- Implement eligibility filters exactly as specified:
  - churned subscribers only
  - `last_login` older than 30 days
  - at least 3 searches in prior 90 days
  - valid non-empty phone and `sms_consent = TRUE`
  - `dnd_until` null or past
  - excluded from `suppression_list`
- Return only the required output schema in the final SELECT: `renter_id`, `email`, `phone`, `last_login`, `search_count`, `days_since_login`.

## Validation steps
- Run a static check pass over SQL structure for:
  - correct CTE flow (`params` -> search aggregation -> eligible profiles -> suppression exclusion)
  - explicit null/blank handling
  - deterministic same-day results
- Verify final column list and aliases match spec exactly.

## Deliverable
- One runnable BigQuery SQL file in `part_1` with clear comments and deterministic time anchoring, ready for execution in BigQuery.