"""Assemble D7 Release Kit (`kos_build/release/`) for standalone L1 distribution.

Contents (Action Plan §2.2 D7 & §4.5):
- human_kos_core.sqlite          (Tier A core, strictly permissive dist_core)
- human_kos_ext/*.sqlite         (Tier B optional extensions, license-isolated)
- SCHEMA.md                      (Complete 8-table + FTS5 + R*Tree + ext DDL guide)
- QUERIES.md                     (Standalone bare sqlite3 & Python stdlib examples, >=3 incl. edge traversal)
- NOTICE                         (Upstream license & attribution boundaries)
- PROVENANCE_SUMMARY.json        (Machine-readable source/license/count summary)
- MANIFEST.json                  (Version list, file sizes, SHA256 checksums, coordinate/embedding versions)

Hard constraints:
- ZERO Client code, ZERO DB III tables, ZERO local_only sources, ZERO local absolute paths.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.build.pack_profiles import write_notice_file
from kos_build.config import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_QUANT,
    EMBEDDING_VERSION,
    GATE_POLICIES,
    LAYOUT_VERSION,
    OUTPUT_DIR,
    RELEASE_DIR,
    SCHEMA_VERSION,
    SOURCE_POLICIES,
    UMAP_PARAMS,
)

DISTRIBUTABLE_EXT_NAMES = (
    "edam.sqlite",
    "compendium.sqlite",
    "iscedf.sqlite",
    "unesco.sqlite",
    "glottolog.sqlite",
    "periodo.sqlite",
    "aat.sqlite",
    "lcsh.sqlite",
    "dila.sqlite",
    "cn_rel.sqlite",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_schema_md(path: Path) -> None:
    md = f"""# Human KOS Database II (L1) — Schema Reference

- **Schema Version**: `{SCHEMA_VERSION}`
- **SQLite Compatibility**: SQLite 3.35+ (built-in `FTS5` + `R*Tree`, zero external extensions)
- **Embedding Contract**: `{EMBEDDING_MODEL}` · `{EMBEDDING_DIM}d` · `{EMBEDDING_QUANT}` · version `{EMBEDDING_VERSION}`
- **2D Map Layout Contract**: UMAP (`n_neighbors={UMAP_PARAMS['n_neighbors']}, min_dist={UMAP_PARAMS['min_dist']}, metric={UMAP_PARAMS['metric']}, random_state={UMAP_PARAMS['random_state']}`) · version `{LAYOUT_VERSION}` · coordinate bounds `[0, 10000] × [0, 10000]`

This database is a **read-only, standalone, Client-agnostic** relational & hybrid-retrieval Knowledge Organization System (KOS) base. It contains no Client runtime fields, no UI state, and no personal mapping ledger (Database III).

---

## 1. Core 8 Tables (`human_kos_core.sqlite` & standard `human_kos_ext/*.sqlite`)

| # | Table | Purpose | Key Columns / Invariants |
| :--- | :--- | :--- | :--- |
| 1 | `kos_provenance` | Source-level licensing, attribution & distribution tier | `source_id (PK)`, `license_spdx`, `dist_tier ∈ {{dist_core, dist_ext_permissive, dist_ext_sa, local_only}}`, `attribution_text` |
| 2 | `kos_nodes` | Controlled ontology nodes across 5 orthogonal facets | `node_id (PK)`, `spatial_id (UNIQUE)`, `facet_type ∈ {{DOMAIN, ACTIVITY, PROCESS, ARTIFACT, STANCE}}`, `canonical_label_en`, `canonical_label_zh (NULL allowed)`, `label_zh_status`, `breadcrumb_path (NOT NULL, non-empty)`, `info_content`, `map_x`, `map_y`, `domain_partition_id`, `min_zoom_tier`, `met_value`, `geo_lat`, `geo_lon`, `extra_json` |
| 3 | `kos_node_aliases` | Multilingual aliases, synonyms, examples & index terms | `alias_id (PK)`, `node_id (FK)`, `alias`, `alias_norm`, `lang`, `alias_type`, `source_id (FK)`, `weight` |
| 4 | `kos_edges` | Hierarchical, semantic, co-occurrence & negative exclusion edges | `(source_id, target_id, relation_type, provenance_source_id) (PK)`, `weight`, `polarity ∈ {{-1, 1}}`, `evidence_json` |
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
"""
    path.write_text(md, encoding="utf-8")


def _write_queries_md(path: Path) -> None:
    md = """# Human KOS Database II (L1) — Standalone Query Cookbook

All examples below run in a clean directory containing only `human_kos_core.sqlite` (and optionally `human_kos_ext/*.sqlite`) using bare `sqlite3` CLI or Python standard library `sqlite3`, with **zero external dependencies** and **zero Client runtime**.

---

## Query 1: Facet-Isolated Chinese FTS5 BM25 Recall (`PROCESS` facet)

```sql
SELECT n.node_id,
       COALESCE(n.canonical_label_zh, n.canonical_label_en) AS label,
       n.breadcrumb_path,
       round(bm25(kos_fts), 3) AS score
FROM kos_fts f
JOIN kos_nodes n ON n.node_id = f.node_id
WHERE kos_fts MATCH 'facet_type : "PROCESS" AND ("大语言" OR "智能体")'
ORDER BY bm25(kos_fts)
LIMIT 10;
```

## Query 2: Multi-Hop Graph Edge Traversal (Recursive CTE over `skos:broader` & `oa:cooccur`)

Traverses up to 2 hops from a concrete OpenAlex Topic node along both taxonomic parent links (`skos:broader`) and cross-field scholarly co-occurrence bridges (`oa:cooccur`), filtering out negative edges (`polarity = 1`) and enforcing an Information Content (IC) floor:

```sql
WITH RECURSIVE
seed AS (
    SELECT node_id, canonical_label_en
    FROM kos_nodes
    WHERE source_id = 'OA_TREE' AND depth = 3
    ORDER BY info_content DESC
    LIMIT 1
),
walk(nid, hop, rel, weight, path) AS (
    SELECT node_id, 0, 'start', 1.0, canonical_label_en FROM seed
    UNION ALL
    SELECT e.target_id,
           w.hop + 1,
           e.relation_type,
           e.weight,
           w.path || ' --[' || e.relation_type || ']--> ' || n2.canonical_label_en
    FROM walk w
    JOIN kos_edges e ON e.source_id = w.nid
    JOIN kos_nodes n2 ON n2.node_id = e.target_id
    WHERE w.hop < 2
      AND e.polarity = 1
      AND e.relation_type IN ('skos:broader', 'oa:cooccur')
      AND n2.info_content >= 1.5
)
SELECT nid, hop, rel, round(weight, 3) AS weight, path
FROM walk
WHERE hop > 0
ORDER BY hop ASC, weight DESC
LIMIT 15;
```

## Query 3: 2D Knowledge Map Viewport Query (R*Tree + Provenance)

```sql
SELECT n.node_id,
       n.facet_type,
       COALESCE(n.canonical_label_zh, n.canonical_label_en) AS label,
       round(n.map_x, 1) AS x,
       round(n.map_y, 1) AS y,
       p.license_spdx
FROM kos_spatial s
JOIN kos_nodes n ON n.spatial_id = s.id
JOIN kos_provenance p ON p.source_id = n.source_id
WHERE s.min_x >= 1000 AND s.max_x <= 6000
  AND s.min_y >= 1000 AND s.max_y <= 6000
  AND n.min_zoom_tier <= 2
LIMIT 15;
```

## Query 4: Facet V AEI Interaction-Mode Calibration Lookup

```sql
SELECT axis_type, axis_key, axis_label,
       directive_pct, feedback_loop_pct, task_iteration_pct,
       learning_pct, validation_pct, none_pct, total_pct
FROM kos_aei_calibration
WHERE axis_type = 'onet_task'
ORDER BY task_iteration_pct DESC
LIMIT 10;
```

## Query 5: On-Demand `ATTACH` of ShareAlike Extension Pack (`cn_rel.sqlite` / `dila.sqlite`)

```sql
ATTACH DATABASE 'human_kos_ext/cn_rel.sqlite' AS cn_ext;

SELECT a.zh_term, a.en_term, n.node_id, n.facet_type, n.breadcrumb_path
FROM cn_ext.cn_alias a
JOIN kos_nodes n ON lower(n.canonical_label_en) = a.en_norm
WHERE a.zh_norm = '哲学'
LIMIT 10;
```

---

## Bare CLI & Python One-Liners

```bash
# Bare sqlite3 CLI (read-only)
sqlite3 -readonly human_kos_core.sqlite "SELECT facet_type, COUNT(*) FROM kos_nodes GROUP BY facet_type;"
```

```python
import sqlite3

con = sqlite3.connect("file:human_kos_core.sqlite?mode=ro", uri=True)
print(con.execute("SELECT key, value FROM kos_meta ORDER BY key").fetchall())
con.close()
```
"""
    path.write_text(md, encoding="utf-8")


def assemble_release_kit() -> dict:
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    ext_release_dir = RELEASE_DIR / "human_kos_ext"
    ext_release_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy Tier A Core
    core_src = OUTPUT_DIR / "human_kos_core.sqlite"
    assert core_src.exists(), f"Missing Tier A core DB: {core_src}"
    core_dst = RELEASE_DIR / "human_kos_core.sqlite"
    shutil.copy2(core_src, core_dst)

    # 2. Copy distributable Tier B Extensions (strict allowlist; never local_only!)
    copied_exts = []
    for fname in DISTRIBUTABLE_EXT_NAMES:
        src = OUTPUT_DIR / "human_kos_ext" / fname
        if src.exists():
            dst = ext_release_dir / fname
            shutil.copy2(src, dst)
            copied_exts.append(fname)

    # 3. Write SCHEMA.md, QUERIES.md, NOTICE
    _write_schema_md(RELEASE_DIR / "SCHEMA.md")
    _write_queries_md(RELEASE_DIR / "QUERIES.md")
    write_notice_file(RELEASE_DIR / "NOTICE")

    # 4. Build PROVENANCE_SUMMARY.json (from actual release SQLite files, relative paths only)
    prov_summary = {
        "schema_version": SCHEMA_VERSION,
        "gate_policies": GATE_POLICIES,
        "packages": {},
    }
    manifest_files = []

    def inspect_rel_db(rel_path: str, abs_path: Path) -> dict:
        con = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        provs = [
            {
                "source_id": r[0],
                "source_name": r[1],
                "version": r[2],
                "license_spdx": r[3],
                "dist_tier": r[4],
            }
            for r in con.execute("SELECT source_id, source_name, version, license_spdx, dist_tier FROM kos_provenance ORDER BY source_id")
        ]
        counts = {}
        if "kos_nodes" in tables:
            counts["nodes"] = con.execute("SELECT COUNT(*) FROM kos_nodes").fetchone()[0]
            counts["aliases"] = con.execute("SELECT COUNT(*) FROM kos_node_aliases").fetchone()[0]
            counts["edges"] = con.execute("SELECT COUNT(*) FROM kos_edges").fetchone()[0]
            counts["embeddings"] = con.execute("SELECT COUNT(*) FROM kos_embeddings").fetchone()[0]
        if "cn_relations" in tables:
            counts["cn_relations"] = con.execute("SELECT COUNT(*) FROM cn_relations").fetchone()[0]
            counts["cn_alias"] = con.execute("SELECT COUNT(*) FROM cn_alias").fetchone()[0]
        con.close()
        return {"counts": counts, "provenance": provs}

    all_rel_files = [
        ("human_kos_core.sqlite", core_dst),
        *[("human_kos_ext/" + fn, ext_release_dir / fn) for fn in copied_exts],
        ("SCHEMA.md", RELEASE_DIR / "SCHEMA.md"),
        ("QUERIES.md", RELEASE_DIR / "QUERIES.md"),
        ("NOTICE", RELEASE_DIR / "NOTICE"),
    ]

    for rel_p, abs_p in all_rel_files:
        sz = abs_p.stat().st_size
        sha = _sha256_file(abs_p)
        manifest_files.append(
            {
                "path": rel_p,
                "size_bytes": sz,
                "size_mb": round(sz / 1048576, 2),
                "sha256": sha,
            }
        )
        if rel_p.endswith(".sqlite"):
            prov_summary["packages"][rel_p] = inspect_rel_db(rel_p, abs_p)

    (RELEASE_DIR / "PROVENANCE_SUMMARY.json").write_text(
        json.dumps(prov_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "release_name": "human-kos-db2-l1",
        "schema_version": SCHEMA_VERSION,
        "built_at_utc": now,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_version": EMBEDDING_VERSION,
        "embedding_dim": EMBEDDING_DIM,
        "embedding_quant": EMBEDDING_QUANT,
        "layout_version": LAYOUT_VERSION,
        "umap_params": UMAP_PARAMS,
        "gate_policies": GATE_POLICIES,
        "files": manifest_files,
    }
    (RELEASE_DIR / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "ok": True,
        "release_dir": "kos_build/release",
        "core_size_mb": round(core_dst.stat().st_size / 1048576, 2),
        "ext_packs_count": len(copied_exts),
        "ext_packs": copied_exts,
        "total_manifest_files": len(manifest_files) + 2,
    }


if __name__ == "__main__":
    print(json.dumps(assemble_release_kit(), ensure_ascii=False, indent=2))
