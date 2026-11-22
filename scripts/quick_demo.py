#!/usr/bin/env python3
"""
Quick 30-second tour of Human KOS using only Python's built-in sqlite3 module.

Usage:
    python scripts/quick_demo.py data/human_kos_core.sqlite
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/human_kos_core.sqlite")
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Download it first with: python scripts/download_release.py --core-only")
        return 1

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    print("=== 1. Atlas Overview (5 Dimensions) ===")
    for facet, count in con.execute(
        "SELECT facet_type, COUNT(*) FROM kos_nodes GROUP BY facet_type ORDER BY COUNT(*) DESC"
    ):
        print(f"  {facet:<10} : {count:>7,} concepts")

    print("\n=== 2. Bilingual Search Example ('人工智能' / AI) ===")
    rows = con.execute(
        """
        SELECT COALESCE(n.canonical_label_zh, n.canonical_label_en),
               n.facet_type,
               n.breadcrumb_path
        FROM kos_fts f
        JOIN kos_nodes n ON n.node_id = f.node_id
        WHERE kos_fts MATCH '"人工" AND "智能"'
        ORDER BY bm25(kos_fts)
        LIMIT 5
        """
    ).fetchall()
    for label, facet, path in rows:
        print(f"  [{facet}] {label}  |  {path}")

    print("\n=== 3. Cross-Field Connection Example ===")
    links = con.execute(
        """
        SELECT n1.canonical_label_en, e.relation_type, n2.canonical_label_en, round(e.weight, 2)
        FROM kos_edges e
        JOIN kos_nodes n1 ON n1.node_id = e.source_id
        JOIN kos_nodes n2 ON n2.node_id = e.target_id
        WHERE e.relation_type = 'oa:cooccur' AND e.polarity = 1
        ORDER BY e.weight DESC
        LIMIT 5
        """
    ).fetchall()
    for src, rel, dst, w in links:
        print(f"  {src}  --[{rel} ({w})]-->  {dst}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
