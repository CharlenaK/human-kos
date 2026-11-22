# Human KOS Database II (L1) — Schema Reference

- **Schema Version**: `1.0.0`
- **SQLite Compatibility**: SQLite 3.35+ (built-in `FTS5` + `R*Tree`, zero external extensions)
- **Embedding Contract**: `intfloat/multilingual-e5-small` · `384d` · `int8` · version `e5-small-int8-v1`
- **2D Map Layout Contract**: UMAP (`n_neighbors=15, min_dist=0.1, metric=cosine, random_state=42`) · version `umap-n15-d01-cos-s42-v1` · coordinate bounds `[0, 10000] × [0, 10000]`

This database is a **read-only, standalone, Client-agnostic** relational & hybrid-retrieval Knowledge Organization System (KOS) base. It contains no Client runtime fields, no UI state, and no personal mapping ledger (Database III).

---

## 1. Core 8 Tables (`human_kos_core.sqlite` & standard `human_kos_ext/*.sqlite`)

| # | Table | Purpose | Key Columns / Invariants |
| :--- | :--- | :--- | :--- |
| 1 | `kos_provenance` | Source-level licensing, attribution & distribution tier | `source_id (PK)`, `license_spdx`, `dist_tier ∈ {dist_core, dist_ext_permissive, dist_ext_sa, local_only}`, `attribution_text` |
| 2 | `kos_nodes` | Controlled ontology nodes across 5 orthogonal facets | `node_id (PK)`, `spatial_id (UNIQUE)`, `facet_type ∈ {DOMAIN, ACTIVITY, PROCESS, ARTIFACT, STANCE}`, `canonical_label_en`, `canonical_label_zh (NULL allowed)`, `label_zh_status`, `breadcrumb_path (NOT NULL, non-empty)`, `info_content`, `map_x`, `map_y`, `domain_partition_id`, `min_zoom_tier`, `met_value`, `geo_lat`, `geo_lon`, `extra_json` |
| 3 | `kos_node_aliases` | Multilingual aliases, synonyms, examples & index terms | `alias_id (PK)`, `node_id (FK)`, `alias`, `alias_norm`, `lang`, `alias_type`, `source_id (FK)`, `weight` |
| 4 | `kos_edges` | Hierarchical, semantic, co-occurrence & negative exclusion edges | `(source_id, target_id, relation_type, provenance_source_id) (PK)`, `weight`, `polarity ∈ {-1, 1}`, `evidence_json` |
| 5 | `kos_facet_pool` | Physically isolated per-facet candidate recall pool | `(facet_type, node_id) (UNIQUE)`, `pool_rank_score`, `search_text_zh`, `search_text_en`, `is_active` |
| 6 | `kos_embeddings` | Frozen offline quantized semantic vectors | `node_id (PK, FK)`, `model_id`, `embedding_version`, `dim`, `quant_type`, `scale`, `vector_blob` |
| 7 | `kos_map_partitions` | Frozen 2D Voronoi partition polygons & LoD bounds | `partition_id (PK)`, `facet_type`, `layout_version`, `centroid_x/y`, `min/max_x/y`, `polygon_geojson`, `node_count` |
| 8 | `kos_meta` | Version, build profile & coordinate/embedding metadata | `key (PK)`, `value`, `updated_at` |

### Auxiliary Calibration Table (in `human_kos_core.sqlite`)

- `kos_aei_calibration`: Per-O*NET task (`3,812` rows) and per-request cluster (`1,192` rows) GLOBAL distribution across Anthropic Economic Index's 5 collaboration buckets (`directive_pct`, `feedback_loop_pct`, `task_iteration_pct`, `learning_pct`, `validation_pct`) plus `none_pct` (`total_pct ≈ 99.7%–100.0%`).

### Built-in Virtual Indexes & Views (Zero External Extensions)

- `kos_fts` (`USING fts5(..., tokenize='porter unicode61 remove_diacritics 2', detail='column')`): Facet-filterable BM25 full-text index with pre-tokenized CJK bigrams/terms in `cjk_tokens` for sub-20 ms Chinese & English recall.
- `kos_spatial` (`USING rtree(id, min_x, max_x, min_y, max_y)`): 2D viewport spatial index joined via `kos_spatial.id = kos_nodes.spatial_id`.
- `v_kos_facet_candidates`: Convenience view joining active `kos_facet_pool` entries with `kos_nodes` and `kos_provenance`.

---

## 2. Isolated ConceptNet Extension Schema (`human_kos_ext/cn_rel.sqlite`, CC BY-SA 4.0)

- `kos_provenance` & `kos_meta`: License & version metadata (`CC-BY-SA-4.0`).
- `cn_relations`: Four selected action/procedural relation types (`HasPrerequisite`, `HasSubevent`, `UsedFor`, `CapableOf`).
- `cn_alias`: Chinese-to-English `Synonym` bridge (`zh_term`, `zh_norm`, `en_term`, `en_norm`, `weight`, `matched_kos`).
- `cn_alias_fts`: FTS5 index over `cn_alias` for fast Chinese-to-English lexical bridging.

---

## 3. Orthogonal Facet Taxonomy

| `facet_type` | Meaning | Core Sources (≤ 500 MB Permissive Tier A, DEC-26) |
| :--- | :--- | :--- |
| `DOMAIN` | Disciplines & world knowledge | OpenAlex Discipline Tree (`4,798`), OpenAlex Top Concepts (`10,000`), Glottolog 5.4 (`12,712`) |
| `ACTIVITY` | Human activities, occupations & skills | ICATUS 2016 + ATUS examples (`230`), O*NET GWA/IWA/DWA (`2,460`), ESCO Skills (`14,600`), Compendium PA 2024 (`1,133` with MET), 2024/25 Emerging Occupations (`36`) |
| `PROCESS` | Computing & research processes | ACM CCS 2012 (`2,113`), Contemporary AI/LLM Supplement (`155`) |
| `ARTIFACT` | Objects, materials, styles & historical periods | Getty AAT full bilingual Chinese-labeled slice (`27,892`), PeriodO Historical Periods (`9,537`) |
| `STANCE` | Human-AI interaction modes & cognitive stances | AEI 5 Collaboration Buckets + 5 Cognitive Stances (`10` active nodes) |
