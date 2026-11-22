-- ============================================================================
-- ConceptNet 5.7 Isolated Extension Pack DDL (cn_rel.sqlite)
-- License boundary: CC BY-SA 4.0 (isolated from non-SA core package)
-- Contains:
--   1) Four selected semantic relation types (~170k edges)
--   2) zh -> en Synonym alias bridge (~312k rows)
-- ============================================================================

PRAGMA foreign_keys = ON;
PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS kos_provenance (
    source_id        TEXT PRIMARY KEY,
    source_name      TEXT NOT NULL,
    version          TEXT NOT NULL,
    license_spdx     TEXT NOT NULL,
    dist_tier        TEXT NOT NULL CHECK (dist_tier IN ('dist_ext_sa', 'local_only')),
    attribution_text TEXT NOT NULL,
    homepage_url     TEXT,
    fetched_at       TEXT NOT NULL,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS kos_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cn_relations (
    rel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
                      'HasPrerequisite', 'HasSubevent', 'UsedFor', 'CapableOf'
                  )),
    start_term    TEXT NOT NULL,
    start_lang    TEXT NOT NULL,
    end_term      TEXT NOT NULL,
    end_lang      TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 1.0,
    source_id     TEXT NOT NULL REFERENCES kos_provenance(source_id),
    UNIQUE (relation_type, start_term, start_lang, end_term, end_lang)
);

CREATE INDEX IF NOT EXISTS idx_cn_rel_start ON cn_relations(start_term, relation_type);
CREATE INDEX IF NOT EXISTS idx_cn_rel_end   ON cn_relations(end_term, relation_type);

CREATE TABLE IF NOT EXISTS cn_alias (
    alias_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    zh_term     TEXT NOT NULL CHECK (length(trim(zh_term)) > 0),
    zh_norm     TEXT NOT NULL CHECK (length(trim(zh_norm)) > 0),
    en_term     TEXT NOT NULL CHECK (length(trim(en_term)) > 0),
    en_norm     TEXT NOT NULL CHECK (length(trim(en_norm)) > 0),
    weight      REAL NOT NULL DEFAULT 1.0,
    matched_kos INTEGER NOT NULL DEFAULT 0 CHECK (matched_kos IN (0, 1)),
    source_id   TEXT NOT NULL REFERENCES kos_provenance(source_id),
    UNIQUE (zh_term, en_norm)
);

CREATE INDEX IF NOT EXISTS idx_cn_alias_zh ON cn_alias(zh_norm);
CREATE INDEX IF NOT EXISTS idx_cn_alias_en ON cn_alias(en_norm);

CREATE VIRTUAL TABLE IF NOT EXISTS cn_alias_fts USING fts5(
    zh_term,
    en_term,
    cjk_tokens,
    tokenize = 'unicode61 remove_diacritics 2'
);
