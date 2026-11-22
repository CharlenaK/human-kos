-- ============================================================================
-- Human KOS Database II (L1) - Unified 8-Table Schema DDL
-- Compatible with bare SQLite 3.35+ (built-in FTS5 + R*Tree, zero extensions)
-- Zero Client / UI / Plugin / DB III coupling.
-- ============================================================================

PRAGMA foreign_keys = ON;
PRAGMA encoding = 'UTF-8';

-- ----------------------------------------------------------------------------
-- 1. kos_provenance: Source-level licensing, attribution & distribution boundary
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_provenance (
    source_id        TEXT PRIMARY KEY,
    source_name      TEXT NOT NULL,
    version          TEXT NOT NULL,
    license_spdx     TEXT NOT NULL,
    dist_tier        TEXT NOT NULL CHECK (dist_tier IN (
                         'dist_core',
                         'dist_ext_permissive',
                         'dist_ext_sa',
                         'local_only'
                     )),
    attribution_text TEXT NOT NULL,
    homepage_url     TEXT,
    fetched_at       TEXT NOT NULL,
    notes            TEXT
);

-- ----------------------------------------------------------------------------
-- 2. kos_nodes: Controlled ontology/KOS nodes across 5 orthogonal facets
--    Hard rules:
--      - breadcrumb_path MUST be non-empty (nodes without path are excluded)
--      - canonical_label_zh MAY be NULL with explicit label_zh_status
--      - info_content computed bottom-up (works_count or subtree size)
--      - 2D map coordinates frozen offline at build time
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_nodes (
    node_id             TEXT PRIMARY KEY,
    spatial_id          INTEGER UNIQUE NOT NULL,
    canonical_label_en  TEXT,
    canonical_label_zh  TEXT,
    label_zh_status     TEXT NOT NULL DEFAULT 'missing' CHECK (label_zh_status IN (
                            'native', 'human', 'mt', 'missing'
                        )),
    facet_type          TEXT NOT NULL CHECK (facet_type IN (
                            'DOMAIN', 'ACTIVITY', 'PROCESS', 'ARTIFACT', 'STANCE'
                        )),
    source_id           TEXT NOT NULL REFERENCES kos_provenance(source_id),
    source_code         TEXT,
    wikidata_qid        TEXT,
    parent_id           TEXT REFERENCES kos_nodes(node_id),
    depth               INTEGER NOT NULL DEFAULT 0 CHECK (depth >= 0),
    is_root             INTEGER NOT NULL DEFAULT 0 CHECK (is_root IN (0, 1)),
    breadcrumb_path     TEXT NOT NULL CHECK (length(trim(breadcrumb_path)) > 0),
    definition_summary  TEXT,
    definition_status   TEXT NOT NULL DEFAULT 'present' CHECK (definition_status IN (
                            'present', 'missing', 'derived'
                        )),
    info_content        REAL NOT NULL DEFAULT 1.0 CHECK (info_content >= 0.0),
    map_x               REAL NOT NULL,
    map_y               REAL NOT NULL,
    domain_partition_id TEXT NOT NULL,
    min_zoom_tier       INTEGER NOT NULL DEFAULT 2 CHECK (min_zoom_tier BETWEEN 0 AND 3),
    met_value           REAL,
    geo_lat             REAL,
    geo_lon             REAL,
    extra_json          TEXT,
    CHECK (canonical_label_en IS NOT NULL OR canonical_label_zh IS NOT NULL),
    CHECK (is_root = 1 OR parent_id IS NOT NULL OR source_id IN ('OA_CONCEPT', 'DILA', 'PERIODO', 'CUSTOM_FLAT'))
);

CREATE INDEX IF NOT EXISTS idx_kos_nodes_facet       ON kos_nodes(facet_type);
CREATE INDEX IF NOT EXISTS idx_kos_nodes_source      ON kos_nodes(source_id);
CREATE INDEX IF NOT EXISTS idx_kos_nodes_parent      ON kos_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_kos_nodes_partition   ON kos_nodes(domain_partition_id, min_zoom_tier);
CREATE INDEX IF NOT EXISTS idx_kos_nodes_qid         ON kos_nodes(wikidata_qid) WHERE wikidata_qid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kos_nodes_source_code ON kos_nodes(source_id, source_code);

-- ----------------------------------------------------------------------------
-- 3. kos_node_aliases: Multilingual aliases, synonyms, examples & index terms
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_node_aliases (
    alias_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL REFERENCES kos_nodes(node_id) ON DELETE CASCADE,
    alias       TEXT NOT NULL CHECK (length(trim(alias)) > 0),
    alias_norm  TEXT NOT NULL CHECK (length(trim(alias_norm)) > 0),
    lang        TEXT NOT NULL,
    alias_type  TEXT NOT NULL DEFAULT 'alt' CHECK (alias_type IN (
                    'pref', 'alt', 'abbr', 'example', 'index_term',
                    'zh_bridge', 'yago_label', 'trad_simp'
                )),
    source_id   TEXT NOT NULL REFERENCES kos_provenance(source_id),
    weight      REAL NOT NULL DEFAULT 1.0 CHECK (weight > 0.0),
    UNIQUE (node_id, alias_norm, lang, source_id)
);

-- Note: UNIQUE (node_id, alias_norm, lang, source_id) already indexes node_id prefix
CREATE INDEX IF NOT EXISTS idx_kos_aliases_norm ON kos_node_aliases(alias_norm, lang);

-- ----------------------------------------------------------------------------
-- 4. kos_edges: Hierarchical, semantic, co-occurrence & negative exclusion edges
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_edges (
    source_id            TEXT NOT NULL REFERENCES kos_nodes(node_id) ON DELETE CASCADE,
    target_id            TEXT NOT NULL REFERENCES kos_nodes(node_id) ON DELETE CASCADE,
    relation_type        TEXT NOT NULL CHECK (relation_type IN (
                             'skos:broader',
                             'skos:narrower',
                             'skos:related',
                             'skos:exactMatch',
                             'skos:closeMatch',
                             'oa:cooccur',
                             'yago:type',
                             'icf:excludes',
                             'onet:task_of',
                             'esco:essentialSkill',
                             'esco:optionalSkill',
                             'cn:HasPrerequisite',
                             'cn:HasSubevent',
                             'cn:UsedFor',
                             'cn:CapableOf',
                             'devkg:predicate'
                         )),
    weight               REAL NOT NULL DEFAULT 1.0,
    polarity             INTEGER NOT NULL DEFAULT 1 CHECK (polarity IN (-1, 1)),
    provenance_source_id TEXT NOT NULL REFERENCES kos_provenance(source_id),
    evidence_json        TEXT,
    PRIMARY KEY (source_id, target_id, relation_type, provenance_source_id)
);

CREATE INDEX IF NOT EXISTS idx_kos_edges_source_rel ON kos_edges(source_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_kos_edges_target_rel ON kos_edges(target_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_kos_edges_rel_weight ON kos_edges(relation_type, weight DESC);

-- ----------------------------------------------------------------------------
-- 5. kos_facet_pool: Physically isolated per-facet recall candidate pool
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_facet_pool (
    pool_entry_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    facet_type      TEXT NOT NULL CHECK (facet_type IN (
                        'DOMAIN', 'ACTIVITY', 'PROCESS', 'ARTIFACT', 'STANCE'
                    )),
    node_id         TEXT NOT NULL REFERENCES kos_nodes(node_id) ON DELETE CASCADE,
    pool_rank_score REAL NOT NULL DEFAULT 1.0,
    search_text_zh  TEXT NOT NULL DEFAULT '',
    search_text_en  TEXT NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    UNIQUE (facet_type, node_id)
);

CREATE INDEX IF NOT EXISTS idx_kos_facet_pool_active ON kos_facet_pool(facet_type, is_active, pool_rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_kos_facet_pool_node   ON kos_facet_pool(node_id);

-- ----------------------------------------------------------------------------
-- 6. kos_embeddings: Offline quantized embeddings (shared version across tiers)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_embeddings (
    node_id           TEXT PRIMARY KEY REFERENCES kos_nodes(node_id) ON DELETE CASCADE,
    model_id          TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    dim               INTEGER NOT NULL CHECK (dim > 0),
    quant_type        TEXT NOT NULL CHECK (quant_type IN ('int8', 'fp16', 'fp32')),
    scale             REAL NOT NULL DEFAULT 1.0,
    vector_blob       BLOB NOT NULL
);

-- ----------------------------------------------------------------------------
-- 7. kos_map_partitions: Offline 2D Voronoi partition polygons & LoD bounds
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_map_partitions (
    partition_id    TEXT PRIMARY KEY,
    facet_type      TEXT NOT NULL CHECK (facet_type IN (
                        'DOMAIN', 'ACTIVITY', 'PROCESS', 'ARTIFACT', 'STANCE'
                    )),
    label_en        TEXT NOT NULL,
    label_zh        TEXT,
    layout_version  TEXT NOT NULL,
    centroid_x      REAL NOT NULL,
    centroid_y      REAL NOT NULL,
    min_x           REAL NOT NULL,
    max_x           REAL NOT NULL,
    min_y           REAL NOT NULL,
    max_y           REAL NOT NULL,
    polygon_geojson TEXT NOT NULL,
    node_count      INTEGER NOT NULL DEFAULT 0 CHECK (node_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_kos_partitions_facet ON kos_map_partitions(facet_type);

-- ----------------------------------------------------------------------------
-- 8. kos_meta: Build metadata, schema version, profile & coordinate contract
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kos_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- Built-in Virtual Index 1: FTS5 (unicode61 + pre-tokenized CJK bigrams/terms)
-- Enables <20ms BM25 in bare sqlite3 for 2-char Chinese & English queries,
-- with strict facet_type column filtering for facet isolation (A6).
-- ----------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS kos_fts USING fts5(
    node_id UNINDEXED,
    facet_type,
    source_id UNINDEXED,
    canonical_label_zh,
    canonical_label_en,
    aliases_text,
    breadcrumb_path,
    definition_summary,
    cjk_tokens,
    tokenize = 'porter unicode61 remove_diacritics 2',
    detail = 'column'
);

-- ----------------------------------------------------------------------------
-- Built-in Virtual Index 2: R*Tree 2D viewport spatial index
-- Joined via kos_spatial.id = kos_nodes.spatial_id
-- ----------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS kos_spatial USING rtree(
    id,
    min_x, max_x,
    min_y, max_y
);

-- ----------------------------------------------------------------------------
-- Read-only convenience view for facet-isolated candidate lookup
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_kos_facet_candidates AS
SELECT
    p.facet_type,
    p.node_id,
    p.pool_rank_score,
    n.canonical_label_zh,
    n.canonical_label_en,
    n.label_zh_status,
    n.breadcrumb_path,
    n.definition_summary,
    n.info_content,
    n.source_id,
    pr.license_spdx,
    pr.dist_tier
FROM kos_facet_pool p
JOIN kos_nodes n       ON n.node_id = p.node_id
JOIN kos_provenance pr ON pr.source_id = n.source_id
WHERE p.is_active = 1;
