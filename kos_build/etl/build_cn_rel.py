"""Build ConceptNet 5.7 CC BY-SA 4.0 isolated package `cn_rel.sqlite` (T1.6 / §5.1).

Contains:
1. `cn_relations`: Four selected semantic relation types (HasPrerequisite,
   HasSubevent, UsedFor, CapableOf) (~170k edges, English & Chinese).
2. `cn_alias`: zh -> en Synonym bridge (~312k rows; single-char/stopwords filtered,
   with `matched_kos` flag against staged KOS English terms).
3. `kos_provenance`: Strictly marked `CC-BY-SA-4.0` (`dist_ext_sa`).
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import (
    DATA_REPO_DIR,
    OUTPUT_DIR,
    SCHEMA_DIR,
    SCHEMA_VERSION,
    SOURCE_POLICIES,
    STAGING_DIR,
)
from kos_build.etl.text_utils import build_cjk_fts_tokens, contains_cjk, normalize_alias

TARGET_RELS = {
    "/r/HasPrerequisite": "HasPrerequisite",
    "/r/HasSubevent": "HasSubevent",
    "/r/UsedFor": "UsedFor",
    "/r/CapableOf": "CapableOf",
}

ZH_STOPWORDS = {
    "的", "了", "和", "是", "在", "我", "有", "不", "人", "都", "一", "一个", "上", "也", "很",
    "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这", "那", "吗", "呢",
}


def _get_t2s():
    try:
        import opencc  # type: ignore

        return opencc.OpenCC("t2s").convert
    except Exception:
        from kos_build.etl.text_utils import _BASIC_T2S

        return lambda s: s.translate(_BASIC_T2S)


def _clean_cn_concept(uri: str) -> tuple[str, str]:
    # Format: /c/<lang>/<term>[/pos/...]
    parts = uri.split("/")
    if len(parts) < 4:
        return "", ""
    lang = parts[2]
    term = parts[3].replace("_", " ").strip()
    return lang, term


def _load_kos_en_vocab() -> Set[str]:
    stg = STAGING_DIR / "staging.sqlite"
    vocab: Set[str] = set()
    if not stg.exists():
        return vocab
    con = sqlite3.connect(stg)
    for (lbl,) in con.execute("SELECT lower(canonical_label_en) FROM kos_nodes WHERE canonical_label_en IS NOT NULL"):
        if lbl:
            vocab.add(lbl.strip())
    con.close()
    return vocab


def build_cn_rel(out_db: Path | None = None) -> dict:
    t0 = time.perf_counter()
    if out_db is None:
        out_db = OUTPUT_DIR / "cn_rel.sqlite"
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    t2s = _get_t2s()
    kos_en_vocab = _load_kos_en_vocab()
    src_gz = DATA_REPO_DIR / "04_Activity_KOS/ConceptNet_5_7/conceptnet-assertions-5.7.0.csv.gz"
    assert src_gz.exists(), f"Missing ConceptNet source: {src_gz}"

    con = sqlite3.connect(out_db)
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    con.executescript((SCHEMA_DIR / "cn_rel_schema.sql").read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cp = SOURCE_POLICIES["CONCEPTNET"]
    con.execute(
        "INSERT INTO kos_provenance VALUES (?,?,?,?,?,?,?,?,?)",
        (
            cp.source_id,
            cp.source_name,
            cp.version,
            cp.license_spdx,
            cp.dist_tier,
            cp.attribution_text,
            cp.homepage_url,
            now[:10],
            cp.notes,
        ),
    )
    con.executemany(
        "INSERT OR REPLACE INTO kos_meta VALUES (?,?,?)",
        [
            ("schema_version", SCHEMA_VERSION, now),
            ("build_profile", "cn_rel_sa", now),
            ("license_spdx", "CC-BY-SA-4.0", now),
            ("packed_at", now, now),
        ],
    )

    rel_batch = []
    alias_batch = []
    rel_seen = 0
    alias_seen = 0
    alias_filtered_single = 0
    lines_scanned = 0

    with gzip.open(src_gz, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            lines_scanned += 1
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            rel = parts[1]
            start_uri = parts[2]
            end_uri = parts[3]

            # 1. Four target semantic action relations (en/zh only)
            if rel in TARGET_RELS:
                slang, sterm = _clean_cn_concept(start_uri)
                elang, eterm = _clean_cn_concept(end_uri)
                if slang in ("en", "zh") and elang in ("en", "zh") and sterm and eterm and sterm != eterm:
                    w = 1.0
                    try:
                        meta = json.loads(parts[4])
                        w = float(meta.get("weight", 1.0))
                    except Exception:
                        pass
                    if slang == "zh":
                        sterm = t2s(sterm)
                    if elang == "zh":
                        eterm = t2s(eterm)
                    rel_batch.append((TARGET_RELS[rel], sterm[:160], slang, eterm[:160], elang, w, "CONCEPTNET"))
                    rel_seen += 1
                    if len(rel_batch) >= 20000:
                        con.executemany(
                            "INSERT OR IGNORE INTO cn_relations "
                            "(relation_type,start_term,start_lang,end_term,end_lang,weight,source_id) VALUES (?,?,?,?,?,?,?)",
                            rel_batch,
                        )
                        rel_batch.clear()

            # 2. zh -> en Synonym bridge (also accept /r/Synonym with /c/zh/ -> /c/en/ or /c/en/ -> /c/zh/)
            elif rel == "/r/Synonym":
                is_zh_en = start_uri.startswith("/c/zh/") and end_uri.startswith("/c/en/")
                is_en_zh = start_uri.startswith("/c/en/") and end_uri.startswith("/c/zh/")
                if not (is_zh_en or is_en_zh):
                    continue
                if is_zh_en:
                    _, zterm = _clean_cn_concept(start_uri)
                    _, eterm = _clean_cn_concept(end_uri)
                else:
                    _, eterm = _clean_cn_concept(start_uri)
                    _, zterm = _clean_cn_concept(end_uri)
                if not zterm or not eterm or not contains_cjk(zterm):
                    continue
                zsimp = t2s(zterm).strip()
                # Filter single-char Chinese stopwords (keep 2+ char terms & informative non-stopword terms)
                if zsimp in ZH_STOPWORDS or (len(zsimp) < 2 and len(eterm) < 3):
                    alias_filtered_single += 1
                    continue
                znorm = normalize_alias(zsimp)
                enorm = normalize_alias(eterm)
                if not znorm or not enorm:
                    continue
                w = 1.0
                try:
                    meta = json.loads(parts[4])
                    w = float(meta.get("weight", 1.0))
                except Exception:
                    pass
                matched = 1 if enorm in kos_en_vocab else 0
                alias_batch.append((zterm[:120], znorm[:120], eterm[:160], enorm[:160], w, matched, "CONCEPTNET"))
                alias_seen += 1
                if len(alias_batch) >= 25000:
                    con.executemany(
                        "INSERT OR IGNORE INTO cn_alias "
                        "(zh_term,zh_norm,en_term,en_norm,weight,matched_kos,source_id) VALUES (?,?,?,?,?,?,?)",
                        alias_batch,
                    )
                    alias_batch.clear()

    if rel_batch:
        con.executemany(
            "INSERT OR IGNORE INTO cn_relations "
            "(relation_type,start_term,start_lang,end_term,end_lang,weight,source_id) VALUES (?,?,?,?,?,?,?)",
            rel_batch,
        )
    if alias_batch:
        con.executemany(
            "INSERT OR IGNORE INTO cn_alias "
            "(zh_term,zh_norm,en_term,en_norm,weight,matched_kos,source_id) VALUES (?,?,?,?,?,?,?)",
            alias_batch,
        )
    con.commit()

    # Build cn_alias_fts for fast Chinese->English bridge queries
    fts_batch = []
    for zt, et in con.execute("SELECT zh_term, en_term FROM cn_alias"):
        fts_batch.append((zt, et, build_cjk_fts_tokens([zt])))
        if len(fts_batch) >= 25000:
            con.executemany("INSERT INTO cn_alias_fts VALUES (?,?,?)", fts_batch)
            fts_batch.clear()
    if fts_batch:
        con.executemany("INSERT INTO cn_alias_fts VALUES (?,?,?)", fts_batch)
    con.execute("INSERT INTO cn_alias_fts(cn_alias_fts) VALUES('optimize');")
    con.commit()

    rel_cnt = con.execute("SELECT COUNT(*) FROM cn_relations").fetchone()[0]
    alias_cnt = con.execute("SELECT COUNT(*) FROM cn_alias").fetchone()[0]
    matched_cnt = con.execute("SELECT COUNT(*) FROM cn_alias WHERE matched_kos = 1").fetchone()[0]
    prov_ok = con.execute("SELECT license_spdx FROM kos_provenance WHERE source_id='CONCEPTNET'").fetchone()[0] == "CC-BY-SA-4.0"

    con.execute("ANALYZE;")
    con.execute("PRAGMA journal_mode = DELETE;")
    con.execute("VACUUM;")
    con.close()

    return {
        "ok": alias_cnt >= 300000 and prov_ok,
        "file": str(out_db.relative_to(DATA_REPO_DIR.parent)),
        "size_mb": round(out_db.stat().st_size / 1048576, 2),
        "lines_scanned": lines_scanned,
        "cn_relations_rows": rel_cnt,
        "cn_alias_rows": alias_cnt,
        "cn_alias_matched_kos": matched_cnt,
        "filtered_single_or_stop": alias_filtered_single,
        "provenance_cc_by_sa_4": prov_ok,
        "elapsed_sec": round(time.perf_counter() - t0, 1),
    }


if __name__ == "__main__":
    print(json.dumps(build_cn_rel(), ensure_ascii=False, indent=2))
