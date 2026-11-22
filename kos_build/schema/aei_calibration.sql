-- ============================================================================
-- AEI Facet V Calibration Table DDL (Action Plan §5.1)
-- Stores per-ONET task and per-request cluster collaboration 5-bucket + none
-- distributions extracted from Anthropic Economic Index metric_id columns.
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS kos_aei_calibration (
    calibration_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    axis_type         TEXT NOT NULL CHECK (axis_type IN ('onet_task', 'request_cluster', 'global')),
    axis_key          TEXT NOT NULL,
    axis_label        TEXT,
    geography         TEXT NOT NULL DEFAULT 'GLOBAL',
    release_version   TEXT NOT NULL,
    directive_pct     REAL NOT NULL DEFAULT 0.0,
    feedback_loop_pct REAL NOT NULL DEFAULT 0.0,
    task_iteration_pct REAL NOT NULL DEFAULT 0.0,
    learning_pct      REAL NOT NULL DEFAULT 0.0,
    validation_pct    REAL NOT NULL DEFAULT 0.0,
    none_pct          REAL NOT NULL DEFAULT 0.0,
    total_pct         REAL NOT NULL DEFAULT 0.0,
    source_id         TEXT NOT NULL REFERENCES kos_provenance(source_id),
    UNIQUE (axis_type, axis_key, geography, release_version)
);

CREATE INDEX IF NOT EXISTS idx_aei_cal_axis ON kos_aei_calibration(axis_type, axis_key);
