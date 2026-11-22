# Human KOS Database II (L1) — Standalone Query Cookbook

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
