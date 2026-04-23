---
name: Part 5 Written Answers
overview: Create a prose-only deliverable in `part_5` that answers the three observability/reliability questions with 1–2 paragraphs each, aligned to `ai-session/part-5_spec.md` and without writing executable code.
todos:
  - id: create-part5-folder-file
    content: Create `part_5/observability_design_answers.md` scaffold with 3 question headings
    status: pending
  - id: write-three-answers
    content: Write 1–2 paragraphs per question covering metrics/alerts, double-send prevention, and ESP outage recovery
    status: pending
  - id: validate-format-constraints
    content: Verify no code blocks, exactly 3 sections, and 1–2 paragraphs per section
    status: pending
isProject: false
---

# Part 5 prose deliverable in `part_5/`

## Goal
Produce a written answer artifact (no implementation code) under [part_5](/Users/dorian599/Work/ME/lifecycle-platform-challenge/part_5) that directly answers the three requested questions in 1–2 paragraphs each, using the guidance in [ai-session/part-5_spec.md](/Users/dorian599/Work/ME/lifecycle-platform-challenge/ai-session/part-5_spec.md).

## Deliverable
- Create [part_5/observability_design_answers.md](/Users/dorian599/Work/ME/lifecycle-platform-challenge/part_5/observability_design_answers.md) with exactly three sections:
  1. Datadog metrics and alerts
  2. Double-send detection and prevention
  3. ESP outage mid-send strategy

## Content constraints
- Each section must be **1–2 paragraphs** only.
- No code blocks and no executable code.
- Keep recommendations concrete and implementation-oriented (metric names, alert classes, idempotency keys, circuit-breaker behavior), but in plain prose.

## Source alignment
- Reuse the Part 5 spec’s core policies:
  - DAG latency/SLA, audience anomaly, ESP error-rate/completion alerts
  - layered idempotency (`campaign_id + as_of_date + segment_id` run key + recipient dedupe)
  - circuit breaker (`CLOSED`/`OPEN`/`HALF_OPEN`) and checkpoint-based recovery

## Acceptance checks
- File exists in `part_5/`.
- Exactly three question sections.
- Each section contains 1–2 paragraphs.
- Answers clearly cover business tradeoffs and operational behavior, not just generic best practices.
