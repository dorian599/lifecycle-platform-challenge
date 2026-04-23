-- Template: BigQuery freshness check for model scores (use with Airflow SqlSensor or poke query).
-- Bind @as_of_date (DATE), @model_version (STRING). Optionally add @min_row_count (INT64).

SELECT 1 AS ready
FROM ml_predictions.renter_send_scores
WHERE model_version = @model_version
  AND DATE(scored_at) = @as_of_date
LIMIT 1;

-- Stronger variant: require minimum daily volume (guards against partial loads).
-- SELECT 1 AS ready
-- FROM (
--   SELECT COUNT(*) AS c
--   FROM ml_predictions.renter_send_scores
--   WHERE model_version = @model_version
--     AND DATE(scored_at) = @as_of_date
-- )
-- WHERE c >= @min_row_count;
