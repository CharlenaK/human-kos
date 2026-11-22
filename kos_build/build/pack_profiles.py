"""Pack derived SQLite profiles (Product 1, Product 2, Product 3 + ext) from Staging.

Single-source derivation (Action Plan §2.3):
  Data Repository/_derived/staging/staging.sqlite
    -> Product 1: kos_build/out/human_kos_full_local.sqlite
    -> Product 2: kos_build/out/human_kos_full_dist.sqlite (+ cn_rel.sqlite + NOTICE)
    -> Product 3: kos_build/out/human_kos_core.sqlite + kos_build/out/human_kos_ext/*.sqlite
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import (
    DATA_REPO_DIR,
    GATE_POLICIES,
    OUTPUT_DIR,
    SCHEMA_DIR,
    SCHEMA_VERSION,
    SOURCE_POLICIES,
    STAGING_DIR,
)
from kos_build.etl.text_utils import build_cjk_fts_tokens


def _pack_subset(
    staging_db: Path,
    out_db: Path,
    profile_name: str,
    node_where_sql: str,
    node_params: Sequence = (),
    include_aei_cal: bool = True,
    include_fts: bool = True,
    include_rtree: bool = True,
    include_embeddings: bool = True,
    exclude_local_only: bool = False,
    exclude_sa: bool = False,
) -> dict:
    """Create one clean, vacuumed, read-ready SQLite artifact from staging.sqlite."""
    t0 = time.perf_counter()
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    con = sqlite3.connect(out_db)
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    con.execute("PRAGMA temp_store = MEMORY;")
    con.execute("PRAGMA cache_size = -64000;")
    con.executescript((SCHEMA_DIR / "kos_schema.sql").read_text(encoding="utf-8"))
    if include_aei_cal:
        con.executescript((SCHEMA_DIR / "aei_calibration.sql").read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = OFF;")

    con.execute("ATTACH DATABASE ? AS stg", (str(staging_db),))

    # Build provenance filter clause
    prov_filters = ["1=1"]
    if exclude_local_only:
        prov_filters.append("p.dist_tier != 'local_only'")
    if exclude_sa:
        prov_filters.append("p.dist_tier != 'dist_ext_sa'")
    prov_where = " AND ".join(prov_filters)

    # 1. Materialize selected node IDs into temp table for fast joins
    con.execute("CREATE TEMP TABLE _sel_nodes (node_id TEXT PRIMARY KEY);")
    con.execute(
        f"""
        INSERT OR IGNORE INTO _sel_nodes(node_id)
        SELECT DISTINCT n.node_id
        FROM stg.kos_nodes n
        JOIN stg.kos_provenance p ON p.source_id = n.source_id
        WHERE ({node_where_sql}) AND ({prov_where})
        """,
        node_params,
    )

    # 2. Copy kos_provenance for sources actually referenced (or AEI if calibration included)
    con.execute(
        f"""
        INSERT INTO kos_provenance
        SELECT DISTINCT p.*
        FROM stg.kos_provenance p
        WHERE ({prov_where})
          AND (
              p.source_id IN (SELECT DISTINCT n.source_id FROM stg.kos_nodes n JOIN _sel_nodes s ON s.node_id = n.node_id)
              OR p.source_id IN (SELECT DISTINCT a.source_id FROM stg.kos_node_aliases a JOIN _sel_nodes s ON s.node_id = a.node_id)
              OR p.source_id IN (SELECT DISTINCT e.provenance_source_id FROM stg.kos_edges e JOIN _sel_nodes s1 ON s1.node_id = e.source_id JOIN _sel_nodes s2 ON s2.node_id = e.target_id)
              {"OR p.source_id = 'AEI'" if include_aei_cal else ""}
          )
        """
    )

    # 3. Copy kos_nodes (cleanly nullifying parent_id only if parent is outside subset, marking root if needed)
    con.execute(
        """
        INSERT INTO kos_nodes
        SELECT
            n.node_id, n.spatial_id, n.canonical_label_en, n.canonical_label_zh, n.label_zh_status,
            n.facet_type, n.source_id, n.source_code, n.wikidata_qid,
            CASE WHEN p.node_id IS NOT NULL THEN n.parent_id ELSE NULL END AS parent_id,
            n.depth,
            CASE WHEN p.node_id IS NOT NULL THEN n.is_root ELSE 1 END AS is_root,
            n.breadcrumb_path, n.definition_summary, n.definition_status,
            n.info_content, n.map_x, n.map_y, n.domain_partition_id, n.min_zoom_tier,
            n.met_value, n.geo_lat, n.geo_lon, n.extra_json
        FROM stg.kos_nodes n
        JOIN _sel_nodes s ON s.node_id = n.node_id
        LEFT JOIN _sel_nodes p ON p.node_id = n.parent_id
        """
    )

    # 4. Copy kos_node_aliases (full aliases preserved under <= 500 MB DEC-26 budget)
    con.execute(
        """
        INSERT OR IGNORE INTO kos_node_aliases (node_id, alias, alias_norm, lang, alias_type, source_id, weight)
        SELECT a.node_id, a.alias, a.alias_norm, a.lang, a.alias_type, a.source_id, a.weight
        FROM stg.kos_node_aliases a
        JOIN _sel_nodes s ON s.node_id = a.node_id
        JOIN kos_provenance pr ON pr.source_id = a.source_id
        """
    )

    # 5. Copy kos_edges (both endpoints in subset + provenance allowed)
    con.execute(
        """
        INSERT OR IGNORE INTO kos_edges
        SELECT e.source_id, e.target_id, e.relation_type, e.weight, e.polarity, e.provenance_source_id, e.evidence_json
        FROM stg.kos_edges e
        JOIN _sel_nodes s1 ON s1.node_id = e.source_id
        JOIN _sel_nodes s2 ON s2.node_id = e.target_id
        JOIN kos_provenance pr ON pr.source_id = e.provenance_source_id
        """
    )

    # 6. Copy kos_embeddings
    if include_embeddings:
        con.execute(
            """
            INSERT OR IGNORE INTO kos_embeddings
            SELECT em.*
            FROM stg.kos_embeddings em
            JOIN _sel_nodes s ON s.node_id = em.node_id
            """
        )

    # 7. Copy kos_map_partitions referenced by selected nodes
    con.execute(
        """
        INSERT OR IGNORE INTO kos_map_partitions
        SELECT mp.*
        FROM stg.kos_map_partitions mp
        WHERE mp.partition_id IN (SELECT DISTINCT domain_partition_id FROM kos_nodes)
           OR substr(mp.partition_id, 1, instr(mp.partition_id || '::', '::') - 1)
              IN (SELECT DISTINCT domain_partition_id FROM kos_nodes)
        """
    )

    # 8. Copy kos_aei_calibration
    if include_aei_cal:
        con.execute(
            """
            INSERT OR IGNORE INTO kos_aei_calibration
            SELECT c.*
            FROM stg.kos_aei_calibration c
            JOIN kos_provenance pr ON pr.source_id = c.source_id
            """
        )

    # 9. Populate kos_facet_pool (strictly per-facet, marking inactive_nodes as is_active=0)
    con.execute(
        """
        INSERT INTO kos_facet_pool (facet_type, node_id, pool_rank_score, search_text_zh, search_text_en, is_active)
        SELECT
            n.facet_type,
            n.node_id,
            n.info_content,
            substr(
                COALESCE(n.canonical_label_zh, '') || ' ' || COALESCE(
                    (SELECT group_concat(a.alias, ' ') FROM kos_node_aliases a
                     WHERE a.node_id = n.node_id AND a.lang LIKE 'zh%'), ''
                ), 1, 240
            ),
            substr(
                COALESCE(n.canonical_label_en, '') || ' ' || COALESCE(
                    (SELECT group_concat(a.alias, ' ') FROM kos_node_aliases a
                     WHERE a.node_id = n.node_id AND a.lang NOT LIKE 'zh%'), ''
                ), 1, 240
            ),
            CASE WHEN inv.node_id IS NOT NULL THEN 0 ELSE 1 END
        FROM kos_nodes n
        LEFT JOIN stg.staging_inactive_nodes inv ON inv.node_id = n.node_id
        """
    )

    # 10. Copy staging metadata & stamp build_profile
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("INSERT OR REPLACE INTO kos_meta SELECT * FROM stg.kos_meta")
    con.executemany(
        "INSERT OR REPLACE INTO kos_meta VALUES (?,?,?)",
        [
            ("build_profile", profile_name, now),
            ("packed_at", now, now),
            ("schema_version", SCHEMA_VERSION, now),
            ("gate_policies_json", json.dumps(GATE_POLICIES), now),
        ],
    )
    con.commit()

    # Detach staging before FTS5 / R*Tree / VACUUM
    con.execute("DETACH DATABASE stg;")

    # 11. Populate R*Tree spatial index
    if include_rtree:
        con.execute(
            "INSERT INTO kos_spatial (id, min_x, max_x, min_y, max_y) "
            "SELECT spatial_id, map_x, map_x, map_y, map_y FROM kos_nodes"
        )
        con.commit()

    # 12. Populate FTS5 index (only active facet_pool entries + all non-root nodes so BM25 stays fast and clean)
    if include_fts:
        cur = con.execute(
            """
            SELECT n.node_id, n.facet_type, n.source_id,
                   COALESCE(n.canonical_label_zh, ''),
                   COALESCE(n.canonical_label_en, ''),
                   COALESCE(p.search_text_zh, '') || ' ' || COALESCE(p.search_text_en, ''),
                   n.breadcrumb_path,
                   COALESCE(n.definition_summary, '')
            FROM kos_nodes n
            JOIN kos_facet_pool p ON p.node_id = n.node_id
            WHERE p.is_active = 1
            """
        )
        fts_batch = []
        for nid, ft, sid, lzh, len_, al_txt, bc, defn in cur:
            # Rich bilingual FTS5 indexing under <= 500 MB DEC-26 budget
            cjk_toks = build_cjk_fts_tokens([lzh, al_txt[:260], bc], max_ngram=2)
            fts_batch.append((nid, ft, sid, lzh, len_, al_txt[:360], bc[:240], defn[:200], cjk_toks[:400]))
            if len(fts_batch) >= 10000:
                con.executemany("INSERT INTO kos_fts VALUES (?,?,?,?,?,?,?,?,?)", fts_batch)
                fts_batch.clear()
        if fts_batch:
            con.executemany("INSERT INTO kos_fts VALUES (?,?,?,?,?,?,?,?,?)", fts_batch)
        con.execute("INSERT INTO kos_fts(kos_fts) VALUES('optimize');")
        con.commit()

    # Verify foreign keys & compute A1/A2 metrics
    con.execute("PRAGMA foreign_keys = ON;")
    fk_err = con.execute("PRAGMA foreign_key_check;").fetchall()
    node_cnt = con.execute("SELECT COUNT(*) FROM kos_nodes").fetchone()[0]
    alias_cnt = con.execute("SELECT COUNT(*) FROM kos_node_aliases").fetchone()[0]
    edge_cnt = con.execute("SELECT COUNT(*) FROM kos_edges").fetchone()[0]
    emb_cnt = con.execute("SELECT COUNT(*) FROM kos_embeddings").fetchone()[0]
    fts_cnt = con.execute("SELECT COUNT(*) FROM kos_fts").fetchone()[0]
    empty_bc = con.execute(
        "SELECT COUNT(*) FROM kos_nodes WHERE breadcrumb_path IS NULL OR length(trim(breadcrumb_path)) = 0"
    ).fetchone()[0]
    orphans = con.execute(
        "SELECT COUNT(*) FROM kos_nodes n WHERE n.is_root = 0 AND n.parent_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM kos_edges e WHERE e.source_id = n.node_id OR e.target_id = n.node_id)"
    ).fetchone()[0]
    sources = [r[0] for r in con.execute("SELECT DISTINCT source_id FROM kos_nodes ORDER BY source_id")]
    disallowed_tiers = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT pr.dist_tier FROM kos_nodes n JOIN kos_provenance pr ON pr.source_id = n.source_id"
        )
    ]

    # Switch to DELETE journal + ANALYZE + compact 8KB pages + VACUUM for single-file distribution
    con.execute("ANALYZE;")
    con.execute("PRAGMA journal_mode = DELETE;")
    con.execute("PRAGMA page_size = 8192;")
    con.execute("VACUUM;")
    con.close()

    size_mb = round(out_db.stat().st_size / 1048576, 2)
    return {
        "ok": len(fk_err) == 0 and empty_bc == 0 and orphans == 0,
        "profile": profile_name,
        "file": str(out_db.relative_to(DATA_REPO_DIR.parent)),
        "size_mb": size_mb,
        "nodes": node_cnt,
        "aliases": alias_cnt,
        "edges": edge_cnt,
        "embeddings": emb_cnt,
        "fts_rows": fts_cnt,
        "fk_violations": len(fk_err),
        "empty_breadcrumb": empty_bc,
        "orphans": orphans,
        "sources": sources,
        "dist_tiers_present": sorted(disallowed_tiers),
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }


def build_product_1_full_local() -> dict:
    """Product 1: Full local database including non-distributable local_only sources."""
    return _pack_subset(
        staging_db=STAGING_DIR / "staging.sqlite",
        out_db=OUTPUT_DIR / "human_kos_full_local.sqlite",
        profile_name="full_local",
        node_where_sql="1=1",
        include_aei_cal=True,
        include_fts=True,
        include_rtree=True,
        include_embeddings=True,
        exclude_local_only=False,
        exclude_sa=False,
    )


def build_product_2_full_dist() -> dict:
    """Product 2: Distributable full package (hard-excludes local_only & SA-isolated sources)."""
    res = _pack_subset(
        staging_db=STAGING_DIR / "staging.sqlite",
        out_db=OUTPUT_DIR / "human_kos_full_dist.sqlite",
        profile_name="full_dist",
        node_where_sql="1=1",
        include_aei_cal=True,
        include_fts=True,
        include_rtree=True,
        include_embeddings=True,
        exclude_local_only=True,
        exclude_sa=True,
    )
    write_notice_file(OUTPUT_DIR / "NOTICE")
    return res


def build_product_3_core_and_ext() -> dict:
    """Product 3: Tier A Core (human_kos_core.sqlite) + Tier B Extensions (human_kos_ext/*.sqlite)."""
    ext_dir = OUTPUT_DIR / "human_kos_ext"
    ext_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, dict] = {}

    # Tier A Core (strictly dist_core only!)
    results["core"] = _pack_subset(
        staging_db=STAGING_DIR / "staging.sqlite",
        out_db=OUTPUT_DIR / "human_kos_core.sqlite",
        profile_name="core",
        node_where_sql="n.node_id IN (SELECT node_id FROM stg.staging_pack_membership WHERE pack_group = 'core')",
        include_aei_cal=True,
        include_fts=True,
        include_rtree=True,
        include_embeddings=True,
        exclude_local_only=True,
        exclude_sa=True,
    )

    # Tier B Distributable Extensions (each isolated cleanly by source & license)
    ext_specs = [
        ("edam", "ext_edam", True),
        ("compendium", "ext_compendium", True),
        ("iscedf", "ext_iscedf", True),
        ("unesco", "ext_unesco", True),
        ("glottolog", "ext_glottolog", False),
        ("periodo", "ext_periodo", False),
        ("aat", "ext_aat", False),
        ("lcsh", "ext_lcsh", False),
        ("dila", "ext_dila", False),
    ]
    for name, pg, inc_emb in ext_specs:
        results[f"ext_{name}"] = _pack_subset(
            staging_db=STAGING_DIR / "staging.sqlite",
            out_db=ext_dir / f"{name}.sqlite",
            profile_name=f"ext_{name}",
            node_where_sql="n.node_id IN (SELECT node_id FROM stg.staging_pack_membership WHERE pack_group = ?)",
            node_params=(pg,),
            include_aei_cal=False,
            include_fts=True,
            include_rtree=True,
            include_embeddings=inc_emb,
            exclude_local_only=True,
            exclude_sa=False,  # SA sources allowed in their own isolated ext packs (dila, edam, unesco, iscedf)
        )

    # Local-only ICF d-branch ext (kept strictly outside human_kos_ext / D7!)
    local_ext_dir = OUTPUT_DIR / "local_only_ext"
    local_ext_dir.mkdir(parents=True, exist_ok=True)
    results["local_only_icf_d"] = _pack_subset(
        staging_db=STAGING_DIR / "staging.sqlite",
        out_db=local_ext_dir / "icf_d_local.sqlite",
        profile_name="local_only_icf_d",
        node_where_sql="n.node_id IN (SELECT node_id FROM stg.staging_pack_membership WHERE pack_group = 'local_ext_icf_d')",
        include_aei_cal=False,
        include_fts=True,
        include_rtree=True,
        include_embeddings=True,
        exclude_local_only=False,
        exclude_sa=False,
    )
    write_notice_file(OUTPUT_DIR / "NOTICE")
    return results


def write_notice_file(path: Path) -> None:
    lines = [
        "# NOTICE — Human KOS Database II (L1) Attribution & License Boundaries",
        "",
        "This distribution package is strictly divided by upstream license compatibility:",
        "",
        "## 1. Permissive Tier A Core (`human_kos_core.sqlite`) & Permissive Extensions",
        "",
    ]
    for p in SOURCE_POLICIES.values():
        if p.dist_tier in ("dist_core", "dist_ext_permissive"):
            lines.append(f"- **{p.source_name}** (`{p.source_id}`, v{p.version}) — `{p.license_spdx}`")
            lines.append(f"  Attribution: {p.attribution_text}")
            if p.homepage_url:
                lines.append(f"  Source: {p.homepage_url}")
    lines.extend(
        [
            "",
            "## 2. ShareAlike (CC BY-SA) Isolated Extension Packs",
            "",
            "The following packages carry ShareAlike obligations and are distributed ONLY as separate attachable SQLite files (`human_kos_ext/*.sqlite` / `cn_rel.sqlite`), never merged into `human_kos_core.sqlite`:",
            "",
        ]
    )
    for p in SOURCE_POLICIES.values():
        if p.dist_tier == "dist_ext_sa":
            lines.append(f"- **{p.source_name}** (`{p.source_id}`, v{p.version}) — `{p.license_spdx}`")
            lines.append(f"  Attribution: {p.attribution_text}")
            if p.homepage_url:
                lines.append(f"  Source: {p.homepage_url}")
    lines.extend(
        [
            "",
            "## 3. Excluded Local-Only Sources (Not Present in Distributable Packages)",
            "",
            "Under G-01 Policy `A_revised`, the following sources are excluded from `human_kos_full_dist.sqlite`, `human_kos_core.sqlite`, `human_kos_ext/*.sqlite`, and the D7 Release Kit:",
            "",
        ]
    )
    for p in SOURCE_POLICIES.values():
        if p.dist_tier == "local_only":
            lines.append(f"- **{p.source_name}** (`{p.source_id}`) — `{p.license_spdx}` ({p.notes or 'local research only'})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["p1", "p2", "p3"], required=True)
    args = ap.parse_args()
    if args.profile == "p1":
        print(json.dumps(build_product_1_full_local(), ensure_ascii=False, indent=2))
    elif args.profile == "p2":
        print(json.dumps(build_product_2_full_dist(), ensure_ascii=False, indent=2))
    elif args.profile == "p3":
        print(json.dumps(build_product_3_core_and_ext(), ensure_ascii=False, indent=2))
