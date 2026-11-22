"""Extract OpenAlex topic co-occurrence edges (freq >= 5) from works_sample parquet.

Run with pyarrow available (e.g. `uv run --with pyarrow python kos_build/etl/extract_oa_cooccur.py`).
Output: Data Repository/_derived/staging/oa_cooccur.tsv
"""

from __future__ import annotations

import collections
import csv
import itertools
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import DATA_REPO_DIR, STAGING_DIR


def extract_oa_cooccur(min_freq: int = 5) -> dict:
    import pyarrow.parquet as pq  # type: ignore

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = DATA_REPO_DIR / "01_Knowledge_Bases/OpenAlex/works_sample/part_0000.parquet"
    out_tsv = STAGING_DIR / "oa_cooccur.tsv"

    pf = pq.ParquetFile(parquet_path)
    pair_counts: collections.Counter[tuple[str, str]] = collections.Counter()
    works_processed = 0
    works_with_multi_topics = 0

    for rg in range(pf.num_row_groups):
        tbl = pf.read_row_group(rg, columns=["topics"])
        col = tbl.column("topics").to_pylist()
        for topics in col:
            works_processed += 1
            if not topics or len(topics) < 2:
                continue
            tids = []
            for item in topics[:3]:
                if isinstance(item, dict):
                    tid = item.get("id") or ""
                    if tid:
                        short = tid.rsplit("/", 1)[-1]
                        if short.startswith("T"):
                            tids.append(f"oa:{short}")
            uniq = sorted(set(tids))
            if len(uniq) >= 2:
                works_with_multi_topics += 1
                for a, b in itertools.combinations(uniq, 2):
                    pair_counts[(a, b)] += 1

    kept = [(a, b, c) for (a, b), c in pair_counts.items() if c >= min_freq]
    kept.sort(key=lambda x: (-x[2], x[0], x[1]))

    with open(out_tsv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["source_id", "target_id", "freq", "weight"])
        for a, b, freq in kept:
            w.writerow([a, b, freq, f"{math.log(1.0 + freq):.6f}"])

    return {
        "out_tsv": str(out_tsv.relative_to(DATA_REPO_DIR.parent)),
        "works_processed": works_processed,
        "works_with_multi_topics": works_with_multi_topics,
        "total_distinct_pairs": len(pair_counts),
        "pairs_kept_ge_5": len(kept),
        "max_freq": kept[0][2] if kept else 0,
    }


if __name__ == "__main__":
    print(json.dumps(extract_oa_cooccur(), ensure_ascii=False, indent=2))
