"""Extract Anthropic Economic Index (AEI) Facet V 5-bucket + none calibration table (§5.1).

Rules (§5.1):
- Extract the 5 collaboration buckets + `none` from `metric_id` (`collaboration_*_pct`),
  NOT from `category_name`.
- Persist per-ONET task and per-request cluster GLOBAL distributions to
  `Data Repository/_derived/staging/aei_calibration.tsv`.
- Acceptance: all 5 buckets + `none` present; per-node percentage sum in [94, 100]%.
"""

from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import DATA_REPO_DIR, STAGING_DIR

COLLAB_METRICS = {
    "collaboration_directive_pct": "directive_pct",
    "collaboration_feedback_loop_pct": "feedback_loop_pct",
    "collaboration_task_iteration_pct": "task_iteration_pct",
    "collaboration_learning_pct": "learning_pct",
    "collaboration_validation_pct": "validation_pct",
    "collaboration_none_pct": "none_pct",
}


def extract_aei_calibration() -> dict:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    src_csv = (
        DATA_REPO_DIR
        / "04_Activity_KOS/Anthropic_Economic_Index/release_2026_06_26/aei_claude_ai_2026-06-26.csv"
    )
    out_tsv = STAGING_DIR / "aei_calibration.tsv"

    # Key: (axis_type, axis_key, release_version) -> dict of metric values + label
    buckets: dict[tuple[str, str, str], dict] = {}
    metrics_seen: set[str] = set()

    with open(src_csv, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r.get("geo_level") != "global" or r.get("geo_id") != "GLOBAL":
                continue
            mid = r.get("metric_id", "")
            if mid not in COLLAB_METRICS:
                continue
            cat = r.get("category_name", "")
            if cat == "onet":
                axis_type = "onet_task"
            elif cat == "request":
                axis_type = "request_cluster"
            else:
                continue

            metrics_seen.add(mid)
            ext_id = (r.get("node_external_id") or "").strip()
            node_name = (r.get("node_name") or "").strip()
            axis_key = ext_id or node_name
            if not axis_key:
                continue
            rel_ver = f"{r.get('date_start', '')}..{r.get('date_end', '')}"
            key = (axis_type, axis_key, rel_ver)
            rec = buckets.setdefault(
                key,
                {
                    "axis_type": axis_type,
                    "axis_key": axis_key,
                    "axis_label": node_name,
                    "geography": "GLOBAL",
                    "release_version": rel_ver,
                    "directive_pct": 0.0,
                    "feedback_loop_pct": 0.0,
                    "task_iteration_pct": 0.0,
                    "learning_pct": 0.0,
                    "validation_pct": 0.0,
                    "none_pct": 0.0,
                },
            )
            try:
                val = float(r.get("value") or 0.0)
            except ValueError:
                val = 0.0
            rec[COLLAB_METRICS[mid]] = val

    # Keep the latest release_version per (axis_type, axis_key)
    latest: dict[tuple[str, str], dict] = {}
    for (atype, akey, rver), rec in buckets.items():
        k2 = (atype, akey)
        if k2 not in latest or rver > latest[k2]["release_version"]:
            latest[k2] = rec

    in_range = 0
    totals: list[float] = []
    by_axis = collections.Counter()

    with open(out_tsv, "w", encoding="utf-8", newline="") as out:
        cols = [
            "axis_type",
            "axis_key",
            "axis_label",
            "geography",
            "release_version",
            "directive_pct",
            "feedback_loop_pct",
            "task_iteration_pct",
            "learning_pct",
            "validation_pct",
            "none_pct",
            "total_pct",
        ]
        w = csv.DictWriter(out, fieldnames=cols, delimiter="\t")
        w.writeheader()
        for k2 in sorted(latest):
            rec = latest[k2]
            tot = (
                rec["directive_pct"]
                + rec["feedback_loop_pct"]
                + rec["task_iteration_pct"]
                + rec["learning_pct"]
                + rec["validation_pct"]
                + rec["none_pct"]
            )
            rec["total_pct"] = round(tot, 4)
            totals.append(tot)
            if 94.0 <= tot <= 101.0:
                in_range += 1
            by_axis[rec["axis_type"]] += 1
            w.writerow(rec)

    return {
        "out_tsv": str(out_tsv.relative_to(DATA_REPO_DIR.parent)),
        "metrics_found": sorted(metrics_seen),
        "all_6_buckets_present": len(metrics_seen) == 6,
        "rows_written": len(latest),
        "by_axis": dict(by_axis),
        "rows_sum_94_to_101_pct": in_range,
        "pct_sum_min": round(min(totals), 2) if totals else 0.0,
        "pct_sum_median": round(sorted(totals)[len(totals) // 2], 2) if totals else 0.0,
        "pct_sum_max": round(max(totals), 2) if totals else 0.0,
    }


if __name__ == "__main__":
    print(json.dumps(extract_aei_calibration(), ensure_ascii=False, indent=2))
