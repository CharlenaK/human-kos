"""Compute offline multilingual-e5-small int8 embeddings, UMAP 2D coordinates,
and per-facet Voronoi partitions for Staging (G-04).

All three derived products (Product 1 full_local, Product 2 full_dist, Product 3
core + ext) inherit the exact same frozen coordinates and embedding_version.

Run via:
    uv run --with umap-learn --with onnxruntime --with transformers --with scipy --with numpy \
        python kos_build/etl/compute_embeddings_layout.py
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import (
    DATA_REPO_DIR,
    DERIVED_DIR,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_QUANT,
    EMBEDDING_VERSION,
    LAYOUT_VERSION,
    STAGING_DIR,
    UMAP_PARAMS,
)

MODEL_DIR = DERIVED_DIR / "models" / "multilingual-e5-small"

# Pack groups receiving full multilingual-e5-small ONNX encoding (~62k nodes)
EMBED_PACK_GROUPS = (
    "core",
    "ext_edam",
    "ext_iscedf",
    "ext_compendium",
    "ext_unesco",
    "local_ext_icf_d",
)
EMBED_LOCAL_SOURCES = ("GBT13745", "WHO_ICF", "ISCO08", "ZYFL_DADIAN", "PHILPAPERS")


def _mean_pool_l2(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    mask = attention_mask[:, :, None].astype(np.float32)
    summed = (last_hidden * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)
    emb = summed / counts
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    return emb / np.clip(norms, 1e-12, None)


def _quantize_int8(vec: np.ndarray) -> Tuple[float, bytes]:
    scale = float(np.max(np.abs(vec))) / 127.0
    if scale <= 1e-12:
        scale = 1.0 / 127.0
    q = np.clip(np.rint(vec / scale), -127, 127).astype(np.int8)
    return scale, q.tobytes()


def _clip_polygon_to_box(poly: List[Tuple[float, float]], xmin=0.0, xmax=10000.0, ymin=0.0, ymax=10000.0) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman polygon clipping to [xmin, xmax] x [ymin, ymax]."""
    def clip_edge(pts, inside_fn, intersect_fn):
        if not pts:
            return []
        out = []
        prev = pts[-1]
        prev_in = inside_fn(prev)
        for curr in pts:
            curr_in = inside_fn(curr)
            if curr_in:
                if not prev_in:
                    out.append(intersect_fn(prev, curr))
                out.append(curr)
            elif prev_in:
                out.append(intersect_fn(prev, curr))
            prev, prev_in = curr, curr_in
        return out

    def inter_x(a, b, x):
        dx = b[0] - a[0]
        t = (x - a[0]) / dx if abs(dx) > 1e-12 else 0.0
        return (x, a[1] + t * (b[1] - a[1]))

    def inter_y(a, b, y):
        dy = b[1] - a[1]
        t = (y - a[1]) / dy if abs(dy) > 1e-12 else 0.0
        return (a[0] + t * (b[0] - a[0]), y)

    p = list(poly)
    p = clip_edge(p, lambda v: v[0] >= xmin, lambda a, b: inter_x(a, b, xmin))
    p = clip_edge(p, lambda v: v[0] <= xmax, lambda a, b: inter_x(a, b, xmax))
    p = clip_edge(p, lambda v: v[1] >= ymin, lambda a, b: inter_y(a, b, ymin))
    p = clip_edge(p, lambda v: v[1] <= ymax, lambda a, b: inter_y(a, b, ymax))
    return p


def _voronoi_polygons_in_box(points: np.ndarray, xmin=0.0, xmax=10000.0, ymin=0.0, ymax=10000.0) -> List[List[List[float]]]:
    """Compute bounded Voronoi polygons for N >= 1 2D centroids inside [0, 10000]^2."""
    from scipy.spatial import Voronoi

    n = len(points)
    box = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]
    if n <= 1:
        return [box] * n

    # Mirror points across all 4 box edges so every original site has a finite Voronoi cell!
    mirrored = [points]
    mirrored.append(np.column_stack([2 * xmin - points[:, 0], points[:, 1]]))
    mirrored.append(np.column_stack([2 * xmax - points[:, 0], points[:, 1]]))
    mirrored.append(np.column_stack([points[:, 0], 2 * ymin - points[:, 1]]))
    mirrored.append(np.column_stack([points[:, 0], 2 * ymax - points[:, 1]]))
    all_pts = np.vstack(mirrored)

    vor = Voronoi(all_pts)
    polys: List[List[List[float]]] = []
    for idx in range(n):
        reg_idx = vor.point_region[idx]
        verts = vor.regions[reg_idx]
        if not verts or -1 in verts:
            polys.append(box)
            continue
        raw_poly = [(float(vor.vertices[v][0]), float(vor.vertices[v][1])) for v in verts]
        clipped = _clip_polygon_to_box(raw_poly, xmin, xmax, ymin, ymax)
        if len(clipped) < 3:
            polys.append(box)
        else:
            ring = [[round(x, 2), round(y, 2)] for x, y in clipped]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            polys.append(ring)
    return polys


def _det_jitter(node_id: str, cx: float, cy: float, radius: float = 350.0) -> Tuple[float, float]:
    h = hashlib.sha256(node_id.encode("utf-8")).digest()
    u = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    v = int.from_bytes(h[4:8], "big") / 0xFFFFFFFF
    angle = u * 2.0 * np.pi
    r = radius * np.sqrt(v)
    x = float(np.clip(cx + r * np.cos(angle), 20.0, 9980.0))
    y = float(np.clip(cy + r * np.sin(angle), 20.0, 9980.0))
    return round(x, 3), round(y, 3)


def run_embeddings_and_layout(batch_size: int = 256, max_length: int = 64) -> dict:
    import onnxruntime as ort
    import umap
    from transformers import AutoTokenizer

    t0 = time.perf_counter()
    db_path = STAGING_DIR / "staging.sqlite"
    assert db_path.exists(), f"Missing staging DB: {db_path}"
    assert (MODEL_DIR / "model.onnx").exists(), f"Missing model.onnx in {MODEL_DIR}"

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")

    # Select semantic nodes to encode with multilingual-e5-small
    # (All semantic core sources + Glottolog families + structured ext + local_only;
    #  PeriodO date strings & leaf Glottolog languages use FTS5 + partition coords.)
    pg_placeholders = ",".join("?" for _ in EMBED_PACK_GROUPS)
    src_placeholders = ",".join("?" for _ in EMBED_LOCAL_SOURCES)
    rows = con.execute(
        f"""
        SELECT DISTINCT n.node_id, n.canonical_label_zh, n.canonical_label_en,
               n.breadcrumb_path, n.definition_summary, n.facet_type, n.domain_partition_id,
               CASE WHEN EXISTS (
                   SELECT 1 FROM staging_pack_membership m2
                   WHERE m2.node_id = n.node_id AND m2.pack_group = 'core'
               ) THEN 1 ELSE 0 END AS is_core
        FROM kos_nodes n
        WHERE (
            n.source_id IN ({src_placeholders})
            OR EXISTS (
                SELECT 1 FROM staging_pack_membership m
                WHERE m.node_id = n.node_id AND m.pack_group IN ({pg_placeholders})
            )
        )
        AND n.source_id != 'PERIODO'
        AND NOT (n.source_id = 'GLOTTOLOG' AND json_extract(n.extra_json, '$.level') = 'language')
        ORDER BY is_core DESC, n.node_id
        """,
        (*EMBED_LOCAL_SOURCES, *EMBED_PACK_GROUPS),
    ).fetchall()

    # Pre-fetch top zh & en aliases per node so OA_TREE keywords & zh bridges enrich e5-small embeddings!
    alias_zh_map: Dict[str, List[str]] = collections.defaultdict(list)
    alias_en_map: Dict[str, List[str]] = collections.defaultdict(list)
    for nid, al, lang in con.execute(
        "SELECT node_id, alias, lang FROM kos_node_aliases ORDER BY weight DESC, alias_id ASC"
    ):
        if lang.startswith("zh"):
            if len(alias_zh_map[nid]) < 4:
                alias_zh_map[nid].append(al)
        else:
            if len(alias_en_map[nid]) < 4:
                alias_en_map[nid].append(al)

    n_total = len(rows)
    core_indices = [i for i, r in enumerate(rows) if r[7] == 1]
    extra_indices = [i for i, r in enumerate(rows) if r[7] == 0]
    print(f"[G-04] Encoding {n_total} nodes (core={len(core_indices)}, extra={len(extra_indices)}) with {EMBEDDING_MODEL}...")

    # Format balanced bilingual e5 passage texts (title + zh/en aliases + breadcrumb + short definition)
    texts = []
    for nid, zh, en, bc, defn, _, _, _ in rows:
        parts = []
        if en:
            parts.append(en)
        if zh:
            parts.append(zh)
        zh_als = [a for a in alias_zh_map.get(nid, []) if a != zh]
        if zh_als:
            parts.append(" ".join(zh_als[:4]))
        en_als = [a for a in alias_en_map.get(nid, []) if a.lower() != (en or "").lower()]
        if en_als:
            parts.append(", ".join(en_als[:4]))
        if bc:
            parts.append(bc)
        if defn:
            parts.append(defn[:140])
        texts.append("passage: " + " | ".join(parts)[:320])

    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = os.cpu_count() or 8
    sess = ort.InferenceSession(str(MODEL_DIR / "model.onnx"), sess_options=opts, providers=["CPUExecutionProvider"])
    in_names = [i.name for i in sess.get_inputs()]

    embs = np.empty((n_total, EMBEDDING_DIM), dtype=np.float32)
    t_enc0 = time.perf_counter()
    for start in range(0, n_total, batch_size):
        end = min(start + batch_size, n_total)
        enc = tok(texts[start:end], padding=True, truncation=True, max_length=max_length, return_tensors="np")
        feed = {k: enc[k].astype(np.int64) for k in in_names if k in enc}
        if "token_type_ids" in in_names and "token_type_ids" not in feed:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"], dtype=np.int64)
        out = sess.run(None, feed)[0]
        embs[start:end] = _mean_pool_l2(out, enc["attention_mask"])
        if (start // batch_size) % 40 == 0:
            elapsed = time.perf_counter() - t_enc0
            rate = end / max(elapsed, 0.01)
            print(f"  encoded {end}/{n_total} ({rate:.0f} nodes/s)")
    t_enc = time.perf_counter() - t_enc0
    print(f"[G-04] Encoding complete in {t_enc:.1f}s ({n_total / max(t_enc, 0.1):.0f} nodes/s)")

    # Fit UMAP on core nodes (exact G-04 params), then transform extra nodes into the SAME 2D space!
    print(f"[G-04] Fitting UMAP on {len(core_indices)} core nodes: {UMAP_PARAMS}")
    t_umap0 = time.perf_counter()
    reducer = umap.UMAP(
        n_neighbors=UMAP_PARAMS["n_neighbors"],
        min_dist=UMAP_PARAMS["min_dist"],
        metric=UMAP_PARAMS["metric"],
        random_state=UMAP_PARAMS["random_state"],
        low_memory=True,
    )
    coords_2d = np.empty((n_total, 2), dtype=np.float32)
    coords_2d[core_indices] = reducer.fit_transform(embs[core_indices])
    if extra_indices:
        print(f"[G-04] Transforming {len(extra_indices)} extra nodes into frozen core UMAP space...")
        coords_2d[extra_indices] = reducer.transform(embs[extra_indices])
    t_umap = time.perf_counter() - t_umap0
    print(f"[G-04] UMAP complete in {t_umap:.1f}s")

    # Normalize 2D coordinates to [100, 9900] x [100, 9900] using robust core bounds
    c_core = coords_2d[core_indices]
    x_min, x_max = float(np.percentile(c_core[:, 0], 0.2)), float(np.percentile(c_core[:, 0], 99.8))
    y_min, y_max = float(np.percentile(c_core[:, 1], 0.2)), float(np.percentile(c_core[:, 1], 99.8))
    x_span = max(x_max - x_min, 1e-6)
    y_span = max(y_max - y_min, 1e-6)
    xs = np.clip(100.0 + (coords_2d[:, 0] - x_min) / x_span * 9800.0, 20.0, 9980.0)
    ys = np.clip(100.0 + (coords_2d[:, 1] - y_min) / y_span * 9800.0, 20.0, 9980.0)

    # Write quantized int8 embeddings & frozen 2D coordinates into staging.sqlite
    con.execute("DELETE FROM kos_embeddings;")
    emb_batch = []
    coord_batch = []
    embedded_coords: Dict[str, Tuple[float, float]] = {}
    for i, row in enumerate(rows):
        nid = row[0]
        mx, my = round(float(xs[i]), 3), round(float(ys[i]), 3)
        embedded_coords[nid] = (mx, my)
        scale, blob = _quantize_int8(embs[i])
        emb_batch.append((nid, EMBEDDING_MODEL, EMBEDDING_VERSION, EMBEDDING_DIM, EMBEDDING_QUANT, scale, blob))
        coord_batch.append((mx, my, nid))
        if len(emb_batch) >= 10000:
            con.executemany("INSERT INTO kos_embeddings VALUES (?,?,?,?,?,?,?)", emb_batch)
            con.executemany("UPDATE kos_nodes SET map_x = ?, map_y = ? WHERE node_id = ?", coord_batch)
            emb_batch.clear()
            coord_batch.clear()
    if emb_batch:
        con.executemany("INSERT INTO kos_embeddings VALUES (?,?,?,?,?,?,?)", emb_batch)
        con.executemany("UPDATE kos_nodes SET map_x = ?, map_y = ? WHERE node_id = ?", coord_batch)
    con.commit()

    # Assign deterministic jittered 2D coordinates for bulk non-embedded nodes inside their partition centroid
    part_anchors: Dict[str, Tuple[float, float]] = {}
    for pid, ax, ay in con.execute(
        "SELECT domain_partition_id, AVG(map_x), AVG(map_y) FROM kos_nodes "
        "WHERE node_id IN (SELECT node_id FROM kos_embeddings) GROUP BY domain_partition_id"
    ):
        part_anchors[pid] = (float(ax), float(ay))

    # Deterministic anchors for bulk-only partitions (LCSH, DILA, Glottolog, PeriodO)
    bulk_default_anchors = {
        "lcsh:ROOT": (2200.0, 7800.0),
        "dila:ROOT_PERSON": (7800.0, 7800.0),
        "dila:ROOT_PLACE": (8200.0, 7400.0),
        "glotto:ROOT": (2600.0, 2400.0),
        "periodo:ROOT": (7400.0, 2400.0),
    }
    part_anchors.update({k: v for k, v in bulk_default_anchors.items() if k not in part_anchors})

    bulk_rows = con.execute(
        "SELECT node_id, domain_partition_id FROM kos_nodes "
        "WHERE node_id NOT IN (SELECT node_id FROM kos_embeddings)"
    ).fetchall()
    bulk_updates = []
    for nid, dpid in bulk_rows:
        cx, cy = part_anchors.get(dpid, (5000.0, 5000.0))
        bx, by = _det_jitter(nid, cx, cy, radius=420.0)
        bulk_updates.append((bx, by, nid))
        if len(bulk_updates) >= 25000:
            con.executemany("UPDATE kos_nodes SET map_x = ?, map_y = ? WHERE node_id = ?", bulk_updates)
            bulk_updates.clear()
    if bulk_updates:
        con.executemany("UPDATE kos_nodes SET map_x = ?, map_y = ? WHERE node_id = ?", bulk_updates)
    con.commit()

    # Build Voronoi partitions per facet_type in kos_map_partitions
    con.execute("DELETE FROM kos_map_partitions;")
    part_stats = con.execute(
        """
        SELECT n.domain_partition_id, n.facet_type,
               COALESCE(p.canonical_label_en, n.domain_partition_id) AS lbl_en,
               p.canonical_label_zh AS lbl_zh,
               AVG(n.map_x) AS cx, AVG(n.map_y) AS cy,
               MIN(n.map_x) AS minx, MAX(n.map_x) AS maxx,
               MIN(n.map_y) AS miny, MAX(n.map_y) AS maxy,
               COUNT(*) AS cnt
        FROM kos_nodes n
        LEFT JOIN kos_nodes p ON p.node_id = n.domain_partition_id
        GROUP BY n.domain_partition_id, n.facet_type
        """
    ).fetchall()

    by_facet: Dict[str, List[tuple]] = collections.defaultdict(list)
    for rec in part_stats:
        by_facet[rec[1]].append(rec)

    part_inserts = []
    seen_pids = set()
    for facet, plist in by_facet.items():
        pts = np.array([[r[4], r[5]] for r in plist], dtype=np.float64)
        # Ensure unique centroids to avoid degenerate Voronoi sites
        for idx in range(len(pts)):
            pts[idx, 0] += idx * 1e-3
            pts[idx, 1] += idx * 1e-3
        polys = _voronoi_polygons_in_box(pts)
        for rec, poly in zip(plist, polys):
            dpid, ft, len_, lzh, cx, cy, minx, maxx, miny, maxy, cnt = rec
            pk = dpid if dpid not in seen_pids else f"{dpid}::{ft}"
            seen_pids.add(pk)
            part_inserts.append(
                (
                    pk, ft, len_, lzh, LAYOUT_VERSION,
                    round(cx, 3), round(cy, 3),
                    round(minx, 3), round(maxx, 3), round(miny, 3), round(maxy, 3),
                    json.dumps(poly), int(cnt),
                )
            )
    con.executemany("INSERT OR REPLACE INTO kos_map_partitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", part_inserts)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta_updates = [
        ("embedding_model", EMBEDDING_MODEL, now),
        ("embedding_version", EMBEDDING_VERSION, now),
        ("embedding_dim", str(EMBEDDING_DIM), now),
        ("embedding_quant", EMBEDDING_QUANT, now),
        ("layout_version", LAYOUT_VERSION, now),
        ("umap_params_json", json.dumps(UMAP_PARAMS), now),
    ]
    con.executemany("INSERT OR REPLACE INTO kos_meta VALUES (?,?,?)", meta_updates)
    con.commit()
    con.close()

    total_sec = round(time.perf_counter() - t0, 1)
    summary = {
        "ok": True,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_version": EMBEDDING_VERSION,
        "layout_version": LAYOUT_VERSION,
        "umap_params": UMAP_PARAMS,
        "embedded_nodes": n_total,
        "core_embedded_nodes": len(core_indices),
        "bulk_jittered_nodes": len(bulk_rows),
        "voronoi_partitions": len(part_inserts),
        "encode_sec": round(t_enc, 1),
        "umap_sec": round(t_umap, 1),
        "total_sec": total_sec,
    }
    (STAGING_DIR / "layout_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run_embeddings_and_layout(), ensure_ascii=False, indent=2))
