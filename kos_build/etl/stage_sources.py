"""Unified Staging ETL for L1 KOS Database II (T1.2).

Extracts, cleans, prunes, computes bottom-up Information Content (IC), resolves
full breadcrumb_path (zero orphans; nodes without valid paths are excluded),
enriches Chinese labels/aliases from AAT + YAGO taxonomy + OpenCC t2s, and
writes all staged records into:

    Data Repository/_derived/staging/staging.sqlite

All three build profiles (Product 1 full_local, Product 2 full_dist + cn_rel,
Product 3 core + ext) derive deterministically from this single Staging layer.
"""

from __future__ import annotations

import collections
import csv
import io
import json
import math
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import (
    DATA_REPO_DIR,
    GATE_POLICIES,
    SCHEMA_DIR,
    SCHEMA_VERSION,
    SOURCE_POLICIES,
    STAGING_DIR,
)
from kos_build.etl.custom_tables import (
    ACM_AI_SUPPLEMENT,
    FACET_V_NODES,
    NEW_OCCUPATIONS_2024_2025,
)
from kos_build.etl.text_utils import contains_cjk, format_breadcrumb, normalize_alias

# Raise CSV field limit for large scope notes / descriptions
csv.field_size_limit(min(sys.maxsize, 10_000_000))


def _get_t2s():
    try:
        import opencc  # type: ignore

        cc = opencc.OpenCC("t2s")
        return cc.convert
    except Exception:
        from kos_build.etl.text_utils import _BASIC_T2S

        return lambda s: s.translate(_BASIC_T2S)


def _clean_text(s: Optional[str], max_len: int = 800) -> Optional[str]:
    if not s:
        return None
    s2 = re.sub(r"\s+", " ", s).strip()
    if not s2:
        return None
    return s2[:max_len] if len(s2) > max_len else s2


# Curated Chinese labels for OpenAlex 4 Domains & 26 Fields (exact bilingual backbone)
OA_DOMAIN_FIELD_ZH: Dict[str, str] = {
    "life sciences": "生命科学",
    "social sciences": "社会科学",
    "physical sciences": "物质科学",
    "health sciences": "健康科学",
    "agricultural and biological sciences": "农业与生物科学",
    "arts and humanities": "人文与艺术",
    "biochemistry, genetics and molecular biology": "生物化学、遗传学与分子生物学",
    "business, management and accounting": "商业、管理与会计",
    "chemical engineering": "化学工程",
    "chemistry": "化学",
    "computer science": "计算机科学",
    "decision sciences": "决策科学",
    "dentistry": "口腔医学",
    "earth and planetary sciences": "地球与行星科学",
    "economics, econometrics and finance": "经济学、计量经济学与金融学",
    "energy": "能源科学",
    "engineering": "工程学",
    "environmental science": "环境科学",
    "health professions": "健康卫生专业",
    "immunology and microbiology": "免疫学与微生物学",
    "materials science": "材料科学",
    "mathematics": "数学",
    "medicine": "医学",
    "neuroscience": "神经科学",
    "nursing": "护理学",
    "pharmacology, toxicology and pharmaceutics": "药理学、毒理学与药剂学",
    "physics and astronomy": "物理学与天文学",
    "psychology": "心理学",
    "veterinary": "兽医学",
    "music": "音乐学与乐理",
    "philosophy": "哲学",
    "history": "历史学",
    "literature and literary theory": "文学与文学理论",
    "language and linguistics": "语言学与语言科学",
    "religious studies": "宗教学",
    "archeology": "考古学",
    "classics": "古典学",
    "visual arts and performing arts": "视觉艺术与表演艺术",
    "law": "法学",
    "education": "教育学",
    "sociology and political science": "社会学与政治学",
    "political science and international relations": "政治学与国际关系",
    "communication": "传播学",
    "cultural studies": "文化研究",
    "artificial intelligence": "人工智能",
    "computer networks and communications": "计算机网络与通信",
    "software": "软件工程",
    "computer vision and pattern recognition": "计算机视觉与模式识别",
    "hardware and architecture": "计算机硬件与体系结构",
    "information systems": "信息系统",
    "human-computer interaction": "人机交互",
}


class StagingBuilder:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.t2s = _get_t2s()
        self.nodes: Dict[str, dict] = {}
        self.aliases: Dict[Tuple[str, str, str, str], dict] = {}
        self.edges: Dict[Tuple[str, str, str, str], dict] = {}
        self.pack_members: Dict[str, Set[str]] = collections.defaultdict(set)
        self.inactive_in_pool: Set[str] = set()
        self.stats: Dict[str, dict] = {}
        # Lookup for English label -> Chinese label/definition enrichment
        self.yago_en_to_zh: Dict[str, str] = {}
        self.yago_en_to_def: Dict[str, str] = {}
        self.aat_en_to_zh: Dict[str, str] = {}

    def add_node(
        self,
        *,
        node_id: str,
        canonical_label_en: Optional[str],
        canonical_label_zh: Optional[str],
        label_zh_status: str,
        facet_type: str,
        source_id: str,
        source_code: Optional[str],
        parent_id: Optional[str],
        depth: int,
        is_root: int,
        breadcrumb_path: str,
        definition_summary: Optional[str],
        definition_status: str = "present",
        domain_partition_id: str = "",
        min_zoom_tier: int = 2,
        wikidata_qid: Optional[str] = None,
        met_value: Optional[float] = None,
        geo_lat: Optional[float] = None,
        geo_lon: Optional[float] = None,
        extra: Optional[dict] = None,
        pack_groups: Iterable[str] = (),
        works_count: int = 0,
        active_in_pool: bool = True,
    ) -> None:
        if not breadcrumb_path or not breadcrumb_path.strip():
            return
        if not canonical_label_en and not canonical_label_zh:
            return
        self.nodes[node_id] = {
            "node_id": node_id,
            "canonical_label_en": _clean_text(canonical_label_en, 300),
            "canonical_label_zh": _clean_text(canonical_label_zh, 300),
            "label_zh_status": label_zh_status,
            "facet_type": facet_type,
            "source_id": source_id,
            "source_code": source_code,
            "wikidata_qid": wikidata_qid,
            "parent_id": parent_id,
            "depth": max(0, int(depth)),
            "is_root": 1 if is_root else 0,
            "breadcrumb_path": _clean_text(breadcrumb_path, 600) or "",
            "definition_summary": _clean_text(definition_summary, 700),
            "definition_status": definition_status,
            "info_content": 1.0,
            "domain_partition_id": domain_partition_id or node_id,
            "min_zoom_tier": max(0, min(3, int(min_zoom_tier))),
            "met_value": met_value,
            "geo_lat": geo_lat,
            "geo_lon": geo_lon,
            "extra_json": json.dumps(extra, ensure_ascii=False) if extra else None,
            "_works_count": max(0, int(works_count or 0)),
        }
        for pg in pack_groups:
            self.pack_members[node_id].add(pg)
        if not active_in_pool:
            self.inactive_in_pool.add(node_id)

    def add_alias(
        self,
        node_id: str,
        alias: str,
        lang: str,
        source_id: str,
        alias_type: str = "alt",
        weight: float = 1.0,
    ) -> None:
        a = _clean_text(alias, 240)
        if not a:
            return
        norm = normalize_alias(self.t2s(a) if lang.startswith("zh") else a)
        if not norm:
            return
        key = (node_id, norm, lang, source_id)
        if key not in self.aliases:
            self.aliases[key] = {
                "node_id": node_id,
                "alias": a,
                "alias_norm": norm,
                "lang": lang,
                "alias_type": alias_type,
                "source_id": source_id,
                "weight": weight,
            }

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        provenance_source_id: str,
        weight: float = 1.0,
        polarity: int = 1,
        evidence: Optional[dict] = None,
    ) -> None:
        if source_id == target_id:
            return
        key = (source_id, target_id, relation_type, provenance_source_id)
        if key not in self.edges:
            self.edges[key] = {
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "weight": float(weight),
                "polarity": -1 if polarity < 0 else 1,
                "provenance_source_id": provenance_source_id,
                "evidence_json": json.dumps(evidence, ensure_ascii=False) if evidence else None,
            }

    # ------------------------------------------------------------------ YAGO cache
    def load_yago_enrichment(self) -> None:
        ypath = STAGING_DIR / "yago_class.tsv"
        if not ypath.exists():
            return
        with open(ypath, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                en = (r.get("label_en") or "").strip().lower()
                if not en or len(en) < 3:
                    continue
                zh = (r.get("label_zh_simp") or "").strip()
                comm = (r.get("comment_en") or "").strip()
                if zh and en not in self.yago_en_to_zh:
                    self.yago_en_to_zh[en] = zh
                if comm and en not in self.yago_en_to_def:
                    self.yago_en_to_def[en] = comm
        self.stats["yago_enrichment_index"] = {
            "en_to_zh": len(self.yago_en_to_zh),
            "en_to_def": len(self.yago_en_to_def),
        }

    # ------------------------------------------------------------------ 1. OpenAlex Tree + Concepts + Cooccur
    def stage_openalex(self, top_concepts: int = 5000) -> None:
        base = DATA_REPO_DIR / "01_Knowledge_Bases/OpenAlex/compact_taxonomy"
        tree_csv = base / "openalex_discipline_tree.csv"
        topics_jsonl = base / "taxonomy_jsonl/topics.jsonl"
        concepts_jsonl = base / "taxonomy_jsonl/concepts.jsonl"

        topic_meta: Dict[str, dict] = {}
        with open(topics_jsonl, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                short = (d.get("id") or "").rsplit("/", 1)[-1]
                if short:
                    topic_meta[short] = d

        # Load discipline tree
        rows = list(csv.DictReader(open(tree_csv, encoding="utf-8-sig")))
        by_raw_id: Dict[str, dict] = {}
        for r in rows:
            raw_id = r["id"].strip()
            short = raw_id.rsplit("/", 1)[-1]
            lv = r["level"].strip().lower()
            prefix_map = {"domain": "oa:D", "field": "oa:F", "subfield": "oa:SF", "topic": "oa:"}
            nid = f"{prefix_map.get(lv, 'oa:')}{short}"
            by_raw_id[raw_id] = {
                "nid": nid,
                "short": short,
                "level": lv,
                "name": r["name"].strip(),
                "parent_raw": (r.get("parent_id") or "").strip(),
            }

        depth_map = {"domain": 0, "field": 1, "subfield": 2, "topic": 3}

        def resolve_chain(raw_id: str) -> Tuple[List[str], str]:
            segs = []
            cur = raw_id
            top_nid = by_raw_id[raw_id]["nid"]
            seen = set()
            while cur and cur in by_raw_id and cur not in seen:
                seen.add(cur)
                item = by_raw_id[cur]
                segs.append(item["name"])
                top_nid = item["nid"]
                cur = item["parent_raw"]
            segs.reverse()
            return segs, top_nid

        tree_count = 0
        for raw_id, item in by_raw_id.items():
            segs, top_part = resolve_chain(raw_id)
            p_raw = item["parent_raw"]
            parent_nid = by_raw_id[p_raw]["nid"] if p_raw and p_raw in by_raw_id else None
            lv = item["level"]
            tmeta = topic_meta.get(item["short"], {}) if lv == "topic" else {}
            defn = tmeta.get("description")
            wc = int(tmeta.get("works_count") or 0)
            self.add_node(
                node_id=item["nid"],
                canonical_label_en=item["name"],
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="OA_TREE",
                source_code=item["short"],
                parent_id=parent_nid,
                depth=depth_map.get(lv, 3),
                is_root=1 if parent_nid is None else 0,
                breadcrumb_path=format_breadcrumb(segs),
                definition_summary=defn,
                definition_status="present" if defn else "missing",
                domain_partition_id=top_part,
                min_zoom_tier=depth_map.get(lv, 3),
                pack_groups=("core", "full_dist", "full_local"),
                works_count=wc,
            )
            if parent_nid:
                self.add_edge(item["nid"], parent_nid, "skos:broader", "OA_TREE", 1.0)
            for kw in (tmeta.get("keywords") or [])[:8]:
                self.add_alias(item["nid"], str(kw), "en", "OA_TREE", "alt", 0.9)
            tree_count += 1

        # OpenAlex concepts: flat pool top-N by works_count with non-empty Wikidata Q-id (§5.1)
        pool_root = "oac:ROOT"
        self.add_node(
            node_id=pool_root,
            canonical_label_en="OpenAlex High-Frequency Concepts Pool",
            canonical_label_zh="OpenAlex 高频概念池",
            label_zh_status="native",
            facet_type="DOMAIN",
            source_id="OA_CONCEPT",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="OpenAlex High-Frequency Concepts Pool",
            definition_summary="Flat pool of top OpenAlex concepts ranked by works_count with verified Wikidata Q-ids.",
            domain_partition_id=pool_root,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )
        concept_candidates = []
        with open(concepts_jsonl, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                wd = (d.get("wikidata") or "").strip()
                if not wd:
                    continue
                qid = wd.rsplit("/", 1)[-1]
                if not qid.startswith("Q"):
                    continue
                name = (d.get("display_name") or "").strip()
                if not name:
                    continue
                wc = int(d.get("works_count") or 0)
                cid = (d.get("id") or "").rsplit("/", 1)[-1]
                concept_candidates.append((wc, cid, qid, name, d.get("description"), d.get("level")))
        concept_candidates.sort(key=lambda x: (-x[0], x[1]))
        concept_added = 0
        for wc, cid, qid, name, desc, lv in concept_candidates[:top_concepts]:
            nid = f"oac:{cid}"
            self.add_node(
                node_id=nid,
                canonical_label_en=name,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="OA_CONCEPT",
                source_code=cid,
                wikidata_qid=qid,
                parent_id=pool_root,
                depth=1,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["OpenAlex Concepts", name]),
                definition_summary=desc,
                definition_status="present" if desc else "missing",
                domain_partition_id=pool_root,
                min_zoom_tier=3,
                pack_groups=("core", "full_dist", "full_local"),
                works_count=wc,
            )
            self.add_edge(nid, pool_root, "skos:broader", "OA_CONCEPT", 0.5)
            concept_added += 1

        # OpenAlex works co-occurrence edges
        co_tsv = STAGING_DIR / "oa_cooccur.tsv"
        co_added = 0
        if co_tsv.exists():
            with open(co_tsv, encoding="utf-8") as f:
                for r in csv.DictReader(f, delimiter="\t"):
                    s, t = r["source_id"], r["target_id"]
                    if s in self.nodes and t in self.nodes:
                        self.add_edge(
                            s,
                            t,
                            "oa:cooccur",
                            "OA_COOCCUR",
                            weight=float(r["weight"]),
                            evidence={"freq": int(r["freq"])},
                        )
                        co_added += 1

        self.stats["openalex"] = {
            "tree_nodes": tree_count,
            "concepts_pool_nodes": concept_added,
            "cooccur_edges": co_added,
        }

    # ------------------------------------------------------------------ 2. GB/T 13745-2009 (local_only)
    def stage_gbt13745(self) -> None:
        p = DATA_REPO_DIR / "06_Chinese_KOS/GBT_13745_2009/gbt13745_disciplines.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        by_code = {r["code"].strip(): r for r in rows}

        # Create 5 discipline gate roots (A..E) so hierarchy is clean and unified
        gates = {}
        for r in rows:
            g = r["gate"].strip()
            gn = r["gate_name"].strip()
            if g and g not in gates:
                gates[g] = gn
                gid = f"gbt:GATE_{g}"
                self.add_node(
                    node_id=gid,
                    canonical_label_en=f"GB/T 13745 Gate {g}",
                    canonical_label_zh=gn,
                    label_zh_status="native",
                    facet_type="DOMAIN",
                    source_id="GBT13745",
                    source_code=f"GATE_{g}",
                    parent_id=None,
                    depth=0,
                    is_root=1,
                    breadcrumb_path=gn,
                    definition_summary=f"GB/T 13745-2009 学科门类：{gn}",
                    domain_partition_id=gid,
                    min_zoom_tier=0,
                    pack_groups=("full_local",),
                )

        added = 0
        other_inactive = 0
        for r in rows:
            code = r["code"].strip()
            name = r["name"].strip()
            lv = int(r["level"])
            pcode = (r.get("parent_code") or "").strip()
            gate = r["gate"].strip()
            gate_nid = f"gbt:GATE_{gate}"
            gate_name = gates[gate]

            segs = [name]
            cur = pcode
            while cur and cur in by_code:
                segs.append(by_code[cur]["name"].strip())
                cur = (by_code[cur].get("parent_code") or "").strip()
            segs.append(gate_name)
            segs.reverse()

            nid = f"gbt:{code}"
            parent_nid = f"gbt:{pcode}" if pcode and pcode in by_code else gate_nid
            is_other_catchall = code.endswith("99") or name.endswith("其他学科")
            if is_other_catchall:
                other_inactive += 1

            self.add_node(
                node_id=nid,
                canonical_label_en=None,
                canonical_label_zh=name,
                label_zh_status="native",
                facet_type="DOMAIN",
                source_id="GBT13745",
                source_code=code,
                parent_id=parent_nid,
                depth=lv,
                is_root=0,
                breadcrumb_path=format_breadcrumb(segs),
                definition_summary=f"GB/T 13745-2009 {lv}级学科（代码 {code}）：{name}",
                domain_partition_id=gate_nid,
                min_zoom_tier=min(3, lv),
                pack_groups=("full_local",),
                active_in_pool=not is_other_catchall,
            )
            self.add_edge(nid, parent_nid, "skos:broader", "GBT13745", 1.0)
            added += 1

        self.stats["gbt13745"] = {"nodes": added, "gates": len(gates), "catchall_inactive": other_inactive}

    # ------------------------------------------------------------------ 3. ICATUS 2016 + ATUS 2025 examples (§5.1)
    def stage_icatus_atus(self) -> None:
        ic_path = DATA_REPO_DIR / "04_Activity_KOS/ICATUS_2016/icatus2016_categories.csv"
        at_path = DATA_REPO_DIR / "04_Activity_KOS/ATUS_2025/atus2025_lexicon.csv"
        rows = list(csv.DictReader(open(ic_path, encoding="utf-8-sig")))
        by_id = {r["id"].strip(): r for r in rows}

        root_id = "icatus:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="Human Daily Activities (ICATUS 2016)",
            canonical_label_zh="人类日常活动分类（ICATUS 2016）",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="ICATUS",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="Human Daily Activities (ICATUS 2016)",
            definition_summary="UN International Classification of Activities for Time-Use Statistics (2016).",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )

        code_to_nid: Dict[str, str] = {}
        for r in rows:
            cid = r["code"].strip()
            nid = f"icatus:{cid}"
            code_to_nid[cid] = nid
            segs = [r["title"].strip()]
            pid_raw = (r.get("parent_id") or "").strip()
            cur = pid_raw
            while cur and cur != "0" and cur in by_id:
                segs.append(by_id[cur]["title"].strip())
                cur = (by_id[cur].get("parent_id") or "").strip()
            segs.reverse()

            parent_nid = f"icatus:{by_id[pid_raw]['code'].strip()}" if (pid_raw and pid_raw != "0" and pid_raw in by_id) else root_id
            lv = int(r["level"])
            self.add_node(
                node_id=nid,
                canonical_label_en=r["title"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ICATUS",
                source_code=cid,
                parent_id=parent_nid,
                depth=lv,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ICATUS"] + segs),
                definition_summary=r.get("definition"),
                definition_status="present" if (r.get("definition") or "").strip() else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=min(3, lv),
                extra={"excludes": (r.get("excludes") or "").strip()[:300]} if (r.get("excludes") or "").strip() else None,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, parent_nid, "skos:broader", "ICATUS", 1.0)
            for ex in (r.get("examples") or "").split(";"):
                if ex.strip():
                    self.add_alias(nid, ex.strip(), "en", "ICATUS", "example", 0.85)

        # ATUS 2025 -> ICATUS major-group crosswalk (§5.1: 2,249 examples deduplicated, 168 empty skipped)
        atus_to_icatus_major = {
            "01": "9",  # Personal care -> Self-care
            "02": "3",  # Household activities -> Unpaid domestic
            "03": "4",  # Caring for household members -> Unpaid caregiving
            "04": "4",  # Caring for nonhousehold members -> Unpaid caregiving
            "05": "1",  # Work -> Employment
            "06": "6",  # Education -> Learning
            "07": "3",  # Consumer purchases -> Unpaid domestic (shopping)
            "08": "9",  # Professional & personal care services -> Self-care
            "09": "3",  # Household services -> Unpaid domestic
            "10": "7",  # Government services & civic -> Community participation
            "11": "9",  # Eating & drinking -> Self-care
            "12": "8",  # Socializing, relaxing, leisure -> Culture/leisure
            "13": "8",  # Sports, exercise, recreation -> Sports/leisure
            "14": "7",  # Religious & spiritual -> Community/religious
            "15": "5",  # Volunteer activities -> Unpaid volunteer
            "16": "7",  # Telephone calls -> Socializing & communication
            "18": "8",  # Traveling -> Leisure/general travel fallback
        }
        atus_rows = list(csv.DictReader(open(at_path, encoding="utf-8-sig")))
        skipped_empty = 0
        raw_ex_count = 0
        injected_unique: Set[Tuple[str, str]] = set()
        for r in atus_rows:
            raw_ex = (r.get("examples") or "").strip()
            if not raw_ex:
                skipped_empty += 1
                continue
            major = atus_to_icatus_major.get(r["code"][:2], "8")
            target_nid = code_to_nid.get(major, root_id)
            for item in raw_ex.split(";"):
                it = item.strip()
                if not it:
                    continue
                raw_ex_count += 1
                norm = normalize_alias(it)
                if norm and (target_nid, norm) not in injected_unique:
                    injected_unique.add((target_nid, norm))
                    self.add_alias(target_nid, it, "en", "ATUS", "example", 0.8)

        self.stats["icatus_atus"] = {
            "icatus_nodes": len(rows),
            "atus_codes_skipped_no_examples": skipped_empty,
            "atus_raw_examples_parsed": raw_ex_count,
            "atus_unique_examples_injected": len(injected_unique),
        }

    # ------------------------------------------------------------------ 4. O*NET 31.0 (GWA -> IWA -> DWA)
    def stage_onet(self) -> None:
        base = DATA_REPO_DIR / "04_Activity_KOS/O_NET_31_0/extracted/db_31_0_text"
        cmr = list(csv.DictReader(open(base / "Content Model Reference.txt", encoding="utf-8-sig"), delimiter="\t"))
        gwa_info = {
            r["Element ID"].strip(): (r["Element Name"].strip(), r.get("Description", "").strip())
            for r in cmr
            if r["Element ID"].strip().startswith("4.A.")
        }
        iwa_rows = list(csv.DictReader(open(base / "GWAs to IWAs.txt", encoding="utf-8-sig"), delimiter="\t"))
        dwa_rows = list(csv.DictReader(open(base / "GWAs to IWAs to DWAs.txt", encoding="utf-8-sig"), delimiter="\t"))

        root_id = "onet:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="O*NET Work Activities (GWA/IWA/DWA)",
            canonical_label_zh="O*NET 工作活动体系",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="ONET",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="O*NET Work Activities",
            definition_summary="O*NET 31.0 Generalized, Intermediate, and Detailed Work Activities.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )

        gwas_used = sorted({r["GWA Element ID"].strip() for r in iwa_rows})
        for gid in gwas_used:
            gname, gdesc = gwa_info.get(gid, (gid, ""))
            nid = f"onet:{gid}"
            self.add_node(
                node_id=nid,
                canonical_label_en=gname,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ONET",
                source_code=gid,
                parent_id=root_id,
                depth=1,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["O*NET Work Activities", gname]),
                definition_summary=gdesc or None,
                definition_status="present" if gdesc else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=1,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, root_id, "skos:broader", "ONET", 1.0)

        iwa_map: Dict[str, Tuple[str, str]] = {}
        for r in iwa_rows:
            gid = r["GWA Element ID"].strip()
            iid = r["IWA Element ID"].strip()
            iname = r["IWA Element Name"].strip()
            gname = gwa_info.get(gid, (gid, ""))[0]
            iwa_map[iid] = (gid, iname)
            nid = f"onet:{iid}"
            pnid = f"onet:{gid}"
            self.add_node(
                node_id=nid,
                canonical_label_en=iname,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ONET",
                source_code=iid,
                parent_id=pnid,
                depth=2,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["O*NET Work Activities", gname, iname]),
                definition_summary=iname,
                definition_status="derived",
                domain_partition_id=root_id,
                min_zoom_tier=2,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ONET", 1.0)

        dwa_count = 0
        for r in dwa_rows:
            iid = r["IWA Element ID"].strip()
            did = r["DWA Element ID"].strip()
            dname = r["DWA Element Name"].strip()
            if iid not in iwa_map:
                continue
            gid, iname = iwa_map[iid]
            gname = gwa_info.get(gid, (gid, ""))[0]
            nid = f"onet:{did}"
            pnid = f"onet:{iid}"
            self.add_node(
                node_id=nid,
                canonical_label_en=dname,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ONET",
                source_code=did,
                parent_id=pnid,
                depth=3,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["O*NET Work Activities", gname, iname, dname]),
                definition_summary=dname,
                definition_status="derived",
                domain_partition_id=root_id,
                min_zoom_tier=3,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ONET", 1.0)
            dwa_count += 1

        self.stats["onet"] = {"gwa": len(gwas_used), "iwa": len(iwa_map), "dwa": dwa_count}

    # ------------------------------------------------------------------ 5. ESCO v1.2.1 (SkillGroups + Skills)
    def stage_esco(self) -> None:
        base = DATA_REPO_DIR / "04_Activity_KOS/ESCO_v1_2_1/extracted"
        sg_rows = list(csv.DictReader(open(base / "skillGroups_en.csv", encoding="utf-8-sig")))
        sk_rows = list(csv.DictReader(open(base / "skills_en.csv", encoding="utf-8-sig")))
        br_rows = list(csv.DictReader(open(base / "broaderRelationsSkillPillar_en.csv", encoding="utf-8-sig")))

        root_id = "esco:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="ESCO Skills & Competences Pillar",
            canonical_label_zh="ESCO 技能与能力体系",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="ESCO",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="ESCO Skills",
            definition_summary="European Skills, Competences, Qualifications and Occupations v1.2.1 Skills Pillar.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )

        uri_to_nid: Dict[str, str] = {}
        uri_label: Dict[str, str] = {}
        for r in sg_rows:
            u = r["conceptUri"].strip()
            short = u.rsplit("/", 1)[-1]
            uri_to_nid[u] = f"esco:sg:{short}"
            uri_label[u] = r["preferredLabel"].strip()
        for r in sk_rows:
            u = r["conceptUri"].strip()
            short = u.rsplit("/", 1)[-1]
            uri_to_nid[u] = f"esco:sk:{short}"
            uri_label[u] = r["preferredLabel"].strip()

        broader_map: Dict[str, List[str]] = collections.defaultdict(list)
        for r in br_rows:
            cu = r["conceptUri"].strip()
            bu = r["broaderUri"].strip()
            if cu in uri_to_nid and bu in uri_to_nid and cu != bu:
                broader_map[cu].append(bu)

        def get_primary_parent(u: str) -> Optional[str]:
            pars = broader_map.get(u, [])
            if not pars:
                return None
            # Prefer SkillGroup parents to avoid cycles among skills
            for p in pars:
                if uri_to_nid[p].startswith("esco:sg:"):
                    return p
            return pars[0]

        def resolve_esco_chain(u: str) -> Tuple[List[str], int]:
            segs = [uri_label[u]]
            cur = get_primary_parent(u)
            seen = {u}
            while cur and cur not in seen and len(segs) < 8:
                seen.add(cur)
                segs.append(uri_label[cur])
                cur = get_primary_parent(cur)
            segs.reverse()
            return segs, len(segs)

        for r in sg_rows:
            u = r["conceptUri"].strip()
            nid = uri_to_nid[u]
            p_uri = get_primary_parent(u)
            pnid = uri_to_nid[p_uri] if p_uri else root_id
            segs, depth = resolve_esco_chain(u)
            desc = (r.get("description") or "").strip() or None
            self.add_node(
                node_id=nid,
                canonical_label_en=uri_label[u],
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ESCO",
                source_code=r.get("code") or u.rsplit("/", 1)[-1],
                parent_id=pnid,
                depth=depth,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ESCO Skills"] + segs),
                definition_summary=desc,
                definition_status="present" if desc else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=min(2, depth),
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ESCO", 1.0)

        for r in sk_rows:
            u = r["conceptUri"].strip()
            nid = uri_to_nid[u]
            p_uri = get_primary_parent(u)
            pnid = uri_to_nid[p_uri] if p_uri else root_id
            segs, depth = resolve_esco_chain(u)
            desc = (r.get("description") or "").strip() or (r.get("definition") or "").strip() or None
            self.add_node(
                node_id=nid,
                canonical_label_en=uri_label[u],
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ESCO",
                source_code=u.rsplit("/", 1)[-1],
                parent_id=pnid,
                depth=depth,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ESCO Skills"] + segs),
                definition_summary=desc,
                definition_status="present" if desc else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=3,
                extra={"skillType": r.get("skillType"), "reuseLevel": r.get("reuseLevel")},
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ESCO", 1.0)
            for alt in (r.get("altLabels") or "").splitlines():
                if alt.strip():
                    self.add_alias(nid, alt.strip(), "en", "ESCO", "alt", 0.9)

        # Add all broader edges
        edge_count = 0
        for cu, pars in broader_map.items():
            for bu in pars:
                self.add_edge(uri_to_nid[cu], uri_to_nid[bu], "skos:broader", "ESCO", 1.0)
                edge_count += 1

        self.stats["esco"] = {
            "skill_groups": len(sg_rows),
            "skills": len(sk_rows),
            "broader_edges": edge_count,
        }

    # ------------------------------------------------------------------ 6. ACM CCS 2012 + G-03 ACM AI (~155)
    def stage_acm_ccs_and_ai(self) -> None:
        p = DATA_REPO_DIR / "04_Activity_KOS/ACM_CCS_2012/ccs2012_concepts.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        by_code = {r["code"].strip(): r for r in rows}

        root_id = "ccs:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="ACM Computing Classification System",
            canonical_label_zh="ACM 计算机科学分类体系",
            label_zh_status="native",
            facet_type="PROCESS",
            source_id="ACM_CCS",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="ACM CCS",
            definition_summary="ACM Computing Classification System (2012) supplemented with contemporary AI/LLM concepts.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )

        # Find exact anchor codes for G-03 ACM AI attachment
        label_to_code = {r["label"].strip().lower(): r["code"].strip() for r in rows}

        def resolve_ccs(code: str) -> List[str]:
            segs = []
            cur = code
            seen = set()
            while cur and cur in by_code and cur not in seen:
                seen.add(cur)
                segs.append(by_code[cur]["label"].strip())
                cur = (by_code[cur].get("parent_code") or "").strip()
            segs.reverse()
            return segs

        for r in rows:
            code = r["code"].strip()
            pcode = (r.get("parent_code") or "").strip()
            nid = f"ccs:{code}"
            pnid = f"ccs:{pcode}" if pcode and pcode in by_code else root_id
            segs = resolve_ccs(code)
            depth = int(r["depth"])
            self.add_node(
                node_id=nid,
                canonical_label_en=r["label"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="PROCESS",
                source_id="ACM_CCS",
                source_code=code,
                parent_id=pnid,
                depth=depth,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ACM CCS"] + segs),
                definition_summary=None,
                definition_status="missing",
                domain_partition_id=root_id,
                min_zoom_tier=min(3, depth),
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ACM_CCS", 1.0)

        # Attach G-03 ACM AI supplement (~155 contemporary bilingual nodes)
        anchor_map = {
            "AI": label_to_code.get("artificial intelligence"),
            "ML": label_to_code.get("machine learning"),
            "NLP": label_to_code.get("natural language processing"),
            "CV": label_to_code.get("computer vision"),
            "IR": label_to_code.get("information retrieval"),
            "SEC": label_to_code.get("security and privacy"),
            "SE": label_to_code.get("software and its engineering"),
            "SYS": label_to_code.get("computer systems organization"),
        }
        ai_added = 0
        for slug, grp, en, zh, defn, aliases in ACM_AI_SUPPLEMENT:
            pcode = anchor_map.get(grp) or anchor_map.get("AI")
            pnid = f"ccs:{pcode}" if pcode and f"ccs:{pcode}" in self.nodes else root_id
            parent_bc = self.nodes[pnid]["breadcrumb_path"]
            parent_depth = self.nodes[pnid]["depth"]
            nid = f"custom:ai:{slug}"
            self.add_node(
                node_id=nid,
                canonical_label_en=en,
                canonical_label_zh=zh,
                label_zh_status="native",
                facet_type="PROCESS",
                source_id="CUSTOM_SELF",
                source_code=slug,
                parent_id=pnid,
                depth=parent_depth + 1,
                is_root=0,
                breadcrumb_path=format_breadcrumb([parent_bc, en]),
                definition_summary=defn,
                definition_status="present",
                domain_partition_id=root_id,
                min_zoom_tier=3,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "CUSTOM_SELF", 1.0)
            self.add_alias(nid, zh, "zh", "CUSTOM_SELF", "pref", 1.2)
            for al in aliases:
                lang = "zh" if contains_cjk(al) else "en"
                self.add_alias(nid, al, lang, "CUSTOM_SELF", "alt", 1.0)
            ai_added += 1

        self.stats["acm_ccs_ai"] = {"ccs2012_nodes": len(rows), "custom_ai_nodes": ai_added}

    # ------------------------------------------------------------------ 7. Facet V + 2024/25 New Occupations (G-03)
    def stage_custom_facet_v_and_jobs(self) -> None:
        for (nid, src, pid, is_root, depth, en, zh, bc, defn, al_zh, al_en) in FACET_V_NODES:
            self.add_node(
                node_id=nid,
                canonical_label_en=en,
                canonical_label_zh=zh,
                label_zh_status="native",
                facet_type="STANCE",
                source_id=src,
                source_code=nid.split(":", 1)[-1],
                parent_id=pid,
                depth=depth,
                is_root=is_root,
                breadcrumb_path=bc,
                definition_summary=defn,
                definition_status="present",
                domain_partition_id=pid or nid,
                min_zoom_tier=depth,
                pack_groups=("core", "full_dist", "full_local"),
                active_in_pool=(is_root == 0),
            )
            if pid:
                self.add_edge(nid, pid, "skos:broader", src, 1.0)
            for a in al_zh:
                self.add_alias(nid, a, "zh", src, "alt", 1.1)
            for a in al_en:
                self.add_alias(nid, a, "en", src, "alt", 1.0)

        job_root = "custom:jobs2024:ROOT"
        self.add_node(
            node_id=job_root,
            canonical_label_en="Emerging Occupations (2024-2025 Supplement)",
            canonical_label_zh="2024/2025 新增职业补充目录",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="CUSTOM_SELF",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="Emerging Occupations (2024-2025)",
            definition_summary="36 emerging occupations added in 2024-2025 across AI, green energy, smart manufacturing, and modern services.",
            domain_partition_id=job_root,
            min_zoom_tier=0,
            pack_groups=("core", "full_dist", "full_local"),
            active_in_pool=False,
        )
        for suf, zh, en, defn, aliases in NEW_OCCUPATIONS_2024_2025:
            nid = f"custom:job2425:{suf}"
            self.add_node(
                node_id=nid,
                canonical_label_en=en,
                canonical_label_zh=zh,
                label_zh_status="native",
                facet_type="ACTIVITY",
                source_id="CUSTOM_SELF",
                source_code=suf,
                parent_id=job_root,
                depth=1,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["Emerging Occupations (2024-2025)", zh]),
                definition_summary=defn,
                definition_status="present",
                domain_partition_id=job_root,
                min_zoom_tier=2,
                pack_groups=("core", "full_dist", "full_local"),
            )
            self.add_edge(nid, job_root, "skos:broader", "CUSTOM_SELF", 1.0)
            self.add_alias(nid, zh, "zh", "CUSTOM_SELF", "pref", 1.2)
            for al in aliases:
                self.add_alias(nid, al, "zh" if contains_cjk(al) else "en", "CUSTOM_SELF", "alt", 1.0)

        self.stats["custom_g03"] = {
            "facet_v_nodes": len(FACET_V_NODES),
            "new_occupations_2024_2025": len(NEW_OCCUPATIONS_2024_2025),
        }

    # ------------------------------------------------------------------ 8. Getty AAT (Filter Guide Terms + Core Facet IV slice + zh bridge)
    def stage_aat(self, core_zh_limit: int = 8000) -> None:
        p = DATA_REPO_DIR / "07_Humanities_KOS/Getty_AAT/aat_subjects.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        by_id = {r["subject_id"].strip(): r for r in rows}

        # Filter out Guide Terms (keep Concepts, Facets, Hierarchy Names for tree integrity; mark non-Concept inactive in pool)
        root_id = "aat:300000000"

        def resolve_aat_chain(sid: str) -> Tuple[List[str], Optional[str], str]:
            segs = []
            cur = sid
            seen = set()
            valid_parent: Optional[str] = None
            facet_top = root_id
            while cur and cur in by_id and cur not in seen and len(segs) < 15:
                seen.add(cur)
                rec = by_id[cur]
                rt = rec["record_type"].strip()
                if rt != "Guide Term":
                    lbl = rec["pref_label_en"].strip() or rec["pref_label_zh"].strip() or cur
                    segs.append(lbl)
                    if cur != sid and valid_parent is None:
                        valid_parent = f"aat:{cur}"
                    if rt == "Facet":
                        facet_top = f"aat:{cur}"
                cur = (rec.get("parent_id") or "").strip()
            segs.reverse()
            return segs, valid_parent, facet_top

        # Identify which Chinese-labeled Concepts go into core Facet IV ARTIFACT (high-value bilingual concepts)
        kept = 0
        guide_removed = 0
        core_added = 0
        zh_labeled = 0

        # Sort so Facet/Hierarchy Name come first, then Concepts with Chinese CJK labels + scope_note
        def _extract_aat_zh(r: dict) -> Tuple[Optional[str], List[str]]:
            cjk_terms: List[str] = []
            pz = (r.get("pref_label_zh") or "").strip()
            if pz and contains_cjk(pz):
                cjk_terms.append(pz)
            for al in (r.get("alt_labels_zh") or "").split("|"):
                al = al.strip()
                if al and contains_cjk(al) and al not in cjk_terms:
                    cjk_terms.append(al)
            if not cjk_terms:
                return None, []
            return cjk_terms[0], cjk_terms[1:]

        priority_rows = sorted(
            rows,
            key=lambda r: (
                0 if r["record_type"] in ("Facet", "Hierarchy Name") else 1,
                0 if (_extract_aat_zh(r)[0] and (r.get("scope_note_zh") or r.get("scope_note_en") or "").strip()) else 1,
                0 if _extract_aat_zh(r)[0] else 1,
                r["subject_id"],
            ),
        )

        # First pass: collect core-eligible concept IDs + all their non-Guide-Term ancestors so core has 0 orphans!
        core_sids: Set[str] = set()
        for r in priority_rows:
            if r["record_type"].strip() != "Concept":
                continue
            zh0, _ = _extract_aat_zh(r)
            if not zh0:
                continue
            if len(core_sids) >= core_zh_limit:
                break
            sid = r["subject_id"].strip()
            core_sids.add(sid)
        # Close ancestor chain for core_sids
        core_with_ancestors: Set[str] = set(core_sids)
        for sid in list(core_sids):
            cur = (by_id[sid].get("parent_id") or "").strip()
            seen = {sid}
            while cur and cur in by_id and cur not in seen:
                seen.add(cur)
                if by_id[cur]["record_type"].strip() != "Guide Term":
                    core_with_ancestors.add(cur)
                cur = (by_id[cur].get("parent_id") or "").strip()

        for r in priority_rows:
            rt = r["record_type"].strip()
            if rt == "Guide Term":
                guide_removed += 1
                continue
            sid = r["subject_id"].strip()
            nid = f"aat:{sid}"
            en = (r.get("pref_label_en") or "").strip() or None
            zh_raw, zh_extra = _extract_aat_zh(r)
            zh_simp = self.t2s(zh_raw) if zh_raw else None
            if en and zh_simp and en.lower() not in self.aat_en_to_zh:
                self.aat_en_to_zh[en.lower()] = zh_simp

            segs, pnid, facet_top = resolve_aat_chain(sid)
            if not segs:
                continue
            is_root = 1 if pnid is None else 0
            defn = (r.get("scope_note_zh") or "").strip() or (r.get("scope_note_en") or "").strip() or None
            if defn and contains_cjk(defn):
                defn = self.t2s(defn)

            pgs = ["ext_aat", "full_dist", "full_local"]
            if sid in core_with_ancestors:
                pgs.append("core")
                core_added += 1
            if zh_simp:
                zh_labeled += 1

            self.add_node(
                node_id=nid,
                canonical_label_en=en,
                canonical_label_zh=zh_simp,
                label_zh_status="native" if zh_simp else "missing",
                facet_type="ARTIFACT",
                source_id="AAT",
                source_code=sid,
                parent_id=pnid,
                depth=max(0, len(segs) - 1),
                is_root=is_root,
                breadcrumb_path=format_breadcrumb(segs),
                definition_summary=defn,
                definition_status="present" if defn else "missing",
                domain_partition_id=facet_top,
                min_zoom_tier=0 if is_root else (1 if rt in ("Facet", "Hierarchy Name") else 3),
                pack_groups=pgs,
                active_in_pool=(rt == "Concept"),
            )
            if pnid:
                self.add_edge(nid, pnid, "skos:broader", "AAT", 1.0)
            if zh_raw and zh_raw != zh_simp:
                self.add_alias(nid, zh_raw, "zh-Hant", "AAT", "trad_simp", 1.0)
            for al in zh_extra:
                self.add_alias(nid, self.t2s(al), "zh", "AAT", "alt", 0.95)
            for al in (r.get("alt_labels_en") or "").split("|")[:4]:
                al = al.strip()
                if al and not contains_cjk(al):
                    self.add_alias(nid, al, "en", "AAT", "alt", 0.85)
            kept += 1

        self.stats["aat"] = {
            "kept_nodes": kept,
            "guide_terms_removed": guide_removed,
            "with_zh_label": zh_labeled,
            "core_facet_iv_nodes": core_added,
        }

    # ------------------------------------------------------------------ 9. EDAM 1.25 (§5.1: filter 1,114 Obsolete + OBO signatures)
    def stage_edam(self) -> None:
        base = DATA_REPO_DIR / "04_Activity_KOS/EDAM_1_25"
        tsv_path = base / "EDAM.tsv"
        obo_path = base / "EDAM.obo"

        # Parse EDAM.obo for has_input / has_output signatures (252 ops: 81 in / 223 out)
        # Note: OBO uses 'EDAM_operation:0226' while TSV uses 'operation_0226'
        def _norm_obo_id(raw_id: str) -> str:
            s = raw_id.strip()
            if s.startswith("EDAM_"):
                s = s[5:]
            return s.replace(":", "_")

        obo_inputs: Dict[str, List[str]] = collections.defaultdict(list)
        obo_outputs: Dict[str, List[str]] = collections.defaultdict(list)
        if obo_path.exists():
            txt = obo_path.read_text(encoding="utf-8", errors="ignore")
            for stanza in txt.split("\n[Term]"):
                m_id = re.search(r"^id:\s*(\S+)", stanza, re.M)
                if not m_id:
                    continue
                tid = _norm_obo_id(m_id.group(1))
                for m_in in re.finditer(r"^relationship:\s*has_input\s+(\S+)", stanza, re.M):
                    obo_inputs[tid].append(_norm_obo_id(m_in.group(1)))
                for m_out in re.finditer(r"^relationship:\s*has_output\s+(\S+)", stanza, re.M):
                    obo_outputs[tid].append(_norm_obo_id(m_out.group(1)))

        rows = list(csv.DictReader(open(tsv_path, encoding="utf-8"), delimiter="\t"))
        active_rows = []
        obsolete_skipped = 0
        for r in rows:
            if (r.get("Obsolete") or "").strip().upper() == "TRUE":
                obsolete_skipped += 1
                continue
            active_rows.append(r)

        def short_edam(uri: str) -> str:
            return uri.strip().rsplit("/", 1)[-1]

        by_code = {short_edam(r["Class ID"]): r for r in active_rows}

        root_id = "edam:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="EDAM Bioinformatics Ontology",
            canonical_label_zh="EDAM 生物信息学本体",
            label_zh_status="native",
            facet_type="PROCESS",
            source_id="EDAM",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="EDAM",
            definition_summary="EDAM 1.25 active ontology of bioinformatics operations, topics, data, and formats.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_edam", "full_dist", "full_local"),
            active_in_pool=False,
        )

        def get_parent_codes(r: dict) -> List[str]:
            pars = []
            for p_raw in (r.get("Parents") or "").split("|"):
                p_raw = p_raw.strip()
                if p_raw and "edamontology.org/" in p_raw:
                    sc = short_edam(p_raw)
                    if sc in by_code:
                        pars.append(sc)
            return pars

        def resolve_edam_chain(code: str) -> List[str]:
            segs = []
            cur = code
            seen = set()
            while cur and cur in by_code and cur not in seen and len(segs) < 12:
                seen.add(cur)
                segs.append(by_code[cur]["Preferred Label"].strip())
                pars = get_parent_codes(by_code[cur])
                cur = pars[0] if pars else ""
            segs.reverse()
            return segs

        sig_counts = collections.Counter()
        for code, r in by_code.items():
            nid = f"edam:{code}"
            pars = get_parent_codes(r)
            pnid = f"edam:{pars[0]}" if pars else root_id
            segs = resolve_edam_chain(code)
            defn = (r.get("Definitions") or "").strip() or None
            ins = obo_inputs.get(code, [])
            outs = obo_outputs.get(code, [])
            if ins and outs:
                sig_status = "full"
            elif ins or outs:
                sig_status = "partial"
            else:
                sig_status = "none"
            if code.startswith("operation_"):
                sig_counts[sig_status] += 1

            facet = "PROCESS" if code.startswith("operation_") else ("ARTIFACT" if code.startswith(("data_", "format_")) else "DOMAIN")
            extra = {"signature_status": sig_status}
            if ins:
                extra["has_input"] = ins
            if outs:
                extra["has_output"] = outs
            if (r.get("File extension") or "").strip():
                extra["file_extension"] = r["File extension"].strip()

            self.add_node(
                node_id=nid,
                canonical_label_en=r["Preferred Label"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type=facet,
                source_id="EDAM",
                source_code=code,
                parent_id=pnid,
                depth=len(segs),
                is_root=0,
                breadcrumb_path=format_breadcrumb(["EDAM"] + segs),
                definition_summary=defn,
                definition_status="present" if defn else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=min(3, len(segs)),
                extra=extra,
                pack_groups=("ext_edam", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "EDAM", 1.0)
            for p_extra in pars[1:]:
                self.add_edge(nid, f"edam:{p_extra}", "skos:broader", "EDAM", 0.9)
            for syn in (r.get("Synonyms") or "").split("|"):
                if syn.strip():
                    self.add_alias(nid, syn.strip(), "en", "EDAM", "alt", 0.9)

        self.stats["edam"] = {
            "active_nodes": len(by_code),
            "obsolete_filtered": obsolete_skipped,
            "op_signature_status": dict(sig_counts),
        }

    # ------------------------------------------------------------------ 10. WHO ICF (§5.1: JSON tree, 0 orphans, d4552 chain, local_only)
    def stage_who_icf(self) -> None:
        jpath = DATA_REPO_DIR / "04_Activity_KOS/WHO_ICF_2026-01/icf_2026-01_categories.json"
        doc = json.load(open(jpath, encoding="utf-8"))
        base_prefix = "https://id.who.int/icd/release/11/2026-01/icf/"

        def norm_id(x) -> Optional[str]:
            if not x:
                return None
            s = str(x).strip()
            if s == base_prefix.rstrip("/"):
                return "icf"
            if s.startswith(base_prefix):
                return s[len(base_prefix):]
            return s.rsplit("/", 1)[-1] if "/" in s else s

        by_raw = {norm_id(e["id"]): e for e in doc["entities"] if norm_id(e.get("id"))}
        # Exclude ONLY qualifier value set entities (code starting with 'q', 44 items, per ICF README §6.2)
        excluded_raw: Set[str] = set()
        for rid, e in by_raw.items():
            code = (e.get("code") or "").strip()
            if code.startswith("q"):
                excluded_raw.add(rid)

        # Assign stable node_id: icf:<code> if coded else icf:blk_<raw_id>
        raw_to_nid: Dict[str, str] = {}
        for rid, e in by_raw.items():
            if rid in excluded_raw:
                continue
            code = (e.get("code") or "").strip()
            raw_to_nid[rid] = f"icf:{code}" if code else f"icf:blk_{rid}"

        # Infer structural prefix codes for uncoded blocks (e.g. d -> d4 -> d45) strictly via descendants
        _block_tag_cache: Dict[str, str] = {}

        def infer_block_tag(rid: str) -> str:
            if rid in _block_tag_cache:
                return _block_tag_cache[rid]
            e = by_raw[rid]
            c = (e.get("code") or "").strip()
            if c:
                _block_tag_cache[rid] = c
                return c
            t = (e.get("title") or "").strip()
            top_titles = {
                "Activities and participation": "d",
                "Body functions": "b",
                "Body structures": "s",
                "Environmental factors": "e",
            }
            if t in top_titles:
                _block_tag_cache[rid] = top_titles[t]
                return top_titles[t]
            # Collect coded descendants (BFS up to 3 hops)
            desc_codes: List[str] = []
            q = collections.deque([(rid, 0)])
            seen_d = {rid}
            while q and len(desc_codes) < 30:
                cur, dist = q.popleft()
                if dist >= 3:
                    continue
                for ch in by_raw[cur].get("children") or []:
                    cn = norm_id(ch)
                    if not cn or cn in seen_d or cn not in raw_to_nid:
                        continue
                    seen_d.add(cn)
                    cc = (by_raw[cn].get("code") or "").strip()
                    if cc:
                        desc_codes.append(cc)
                    else:
                        q.append((cn, dist + 1))
            tag = ""
            if desc_codes:
                # Only compute LCP if all descendants share the same branch letter
                letters = {x[0] for x in desc_codes}
                if len(letters) == 1:
                    pref = desc_codes[0]
                    for cc in desc_codes[1:]:
                        while pref and not cc.startswith(pref):
                            pref = pref[:-1]
                    tag = pref
            _block_tag_cache[rid] = tag
            return tag

        # Compute chain from root
        code_to_nid = {
            (by_raw[rid].get("code") or "").strip(): nid
            for rid, nid in raw_to_nid.items()
            if (by_raw[rid].get("code") or "").strip()
        }

        added = 0
        coded_count = 0
        d_branch_count = 0
        missing_def = 0
        idx_term_aliases = 0
        excl_edges = 0

        for rid, nid in raw_to_nid.items():
            e = by_raw[rid]
            code = (e.get("code") or "").strip()
            title = (e.get("title") or "").strip() or code or rid
            prid = norm_id(e.get("parent"))
            pnid = raw_to_nid.get(prid) if prid else None

            # Walk ancestors for breadcrumb
            segs = []
            code_chain = []
            cur = rid
            seen = set()
            is_d_branch = code.startswith("d")
            while cur and cur in raw_to_nid and cur not in seen:
                seen.add(cur)
                ce = by_raw[cur]
                ctitle = (ce.get("title") or "").strip()
                ccode = (ce.get("code") or "").strip() or infer_block_tag(cur)
                if ccode == "d" or ccode.startswith("d"):
                    is_d_branch = True
                if ctitle:
                    segs.append(f"{ccode} {ctitle}".strip() if ccode else ctitle)
                if ccode:
                    code_chain.append(ccode)
                cur = norm_id(ce.get("parent"))
            segs.reverse()
            code_chain.reverse()

            defn = (e.get("definition") or "").strip() or None
            if code and not defn:
                missing_def += 1
            if code:
                coded_count += 1
            if code.startswith("d"):
                d_branch_count += 1

            pgs = ["full_local"]
            if is_d_branch:
                pgs.append("local_ext_icf_d")

            extra = {}
            if code_chain:
                extra["code_chain"] = code_chain
            if e.get("inclusions"):
                extra["inclusions"] = e["inclusions"][:10]

            self.add_node(
                node_id=nid,
                canonical_label_en=title,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="WHO_ICF",
                source_code=code or rid,
                parent_id=pnid,
                depth=max(0, len(segs) - 1),
                is_root=1 if pnid is None else 0,
                breadcrumb_path=format_breadcrumb(segs),
                definition_summary=defn,
                definition_status="present" if defn else "missing",
                domain_partition_id="icf:d" if is_d_branch else "icf:ROOT",
                min_zoom_tier=min(3, max(0, len(segs) - 1)),
                extra=extra or None,
                pack_groups=pgs,
                active_in_pool=bool(code),
            )
            if pnid:
                self.add_edge(nid, pnid, "skos:broader", "WHO_ICF", 1.0)
            for term in e.get("indexTerms") or []:
                t = term.get("label") if isinstance(term, dict) else str(term)
                if t and t.strip():
                    self.add_alias(nid, t.strip(), "en", "WHO_ICF", "index_term", 0.9)
                    idx_term_aliases += 1
            added += 1

        # Add negative icf:excludes edges where target code exists
        for rid, nid in raw_to_nid.items():
            e = by_raw[rid]
            for ex in e.get("exclusions") or []:
                target_codes: List[str] = []
                if isinstance(ex, dict):
                    ec = (ex.get("code") or "").strip()
                    if ec:
                        target_codes.append(ec)
                    txt = ex.get("label") or ""
                else:
                    txt = str(ex)
                for m in re.finditer(r"\b([bsde]\d{3,5})\b", txt or ""):
                    target_codes.append(m.group(1))
                for tc in set(target_codes):
                    tgt = code_to_nid.get(tc)
                    if tgt and tgt != nid:
                        self.add_edge(nid, tgt, "icf:excludes", "WHO_ICF", weight=1.0, polarity=-1)
                        excl_edges += 1

        self.stats["who_icf"] = {
            "total_tree_nodes": added,
            "coded_categories": coded_count,
            "d_branch_coded": d_branch_count,
            "missing_definition_coded": missing_def,
            "index_term_aliases": idx_term_aliases,
            "excludes_negative_edges": excl_edges,
            "d4552_breadcrumb": self.nodes.get("icf:d4552", {}).get("breadcrumb_path"),
        }

    # ------------------------------------------------------------------ 11. Compendium 2024 (§5.1: 22 headings + MET, no 507 intermediate groups)
    def stage_compendium(self) -> None:
        p = DATA_REPO_DIR / "04_Activity_KOS/Compendium_PA_2024/compendium_2024_adult.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))

        root_id = "comp:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="Compendium of Physical Activities 2024",
            canonical_label_zh="成人身体活动能量代谢当量（MET）大全",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="COMPENDIUM",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="Compendium PA 2024",
            definition_summary="2024 Adult Compendium of Physical Activities grouped strictly by 22 headings with MET attributes.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_compendium", "full_dist", "full_local"),
            active_in_pool=False,
        )

        headings: Dict[str, str] = {}
        for r in rows:
            hc = r["heading_code"].strip()
            hn = r["heading"].strip()
            if hc not in headings:
                headings[hc] = hn
                hid = f"comp:H_{hc}"
                self.add_node(
                    node_id=hid,
                    canonical_label_en=hn,
                    canonical_label_zh=None,
                    label_zh_status="missing",
                    facet_type="ACTIVITY",
                    source_id="COMPENDIUM",
                    source_code=hc,
                    parent_id=root_id,
                    depth=1,
                    is_root=0,
                    breadcrumb_path=format_breadcrumb(["Compendium PA 2024", hn]),
                    definition_summary=f"Physical activity heading: {hn}",
                    definition_status="derived",
                    domain_partition_id=root_id,
                    min_zoom_tier=1,
                    pack_groups=("core", "ext_compendium", "full_dist", "full_local"),
                )
                self.add_edge(hid, root_id, "skos:broader", "COMPENDIUM", 1.0)

        act_count = 0
        for r in rows:
            hc = r["heading_code"].strip()
            hn = headings[hc]
            ac = r["activity_code"].strip()
            met = float(r["met"])
            desc = r["description"].strip()
            nid = f"comp:{ac}"
            pnid = f"comp:H_{hc}"
            self.add_node(
                node_id=nid,
                canonical_label_en=desc[:160],
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="COMPENDIUM",
                source_code=ac,
                parent_id=pnid,
                depth=2,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["Compendium PA 2024", hn, desc[:80]]),
                definition_summary=f"{desc} (MET = {met})",
                definition_status="present",
                domain_partition_id=root_id,
                min_zoom_tier=3,
                met_value=met,
                pack_groups=("ext_compendium", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "COMPENDIUM", 1.0)
            act_count += 1

        self.stats["compendium"] = {"headings": len(headings), "activities_with_met": act_count}

    # ------------------------------------------------------------------ 12. ISCED-F 2013 (§5.1: 11/58/149 = 218)
    def stage_iscedf(self) -> None:
        p = DATA_REPO_DIR / "04_Activity_KOS/UNESCO_ISCED-F_2013/iscedf2013_fields.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        by_code = {r["code"].strip(): r for r in rows}

        root_id = "iscedf:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="UNESCO ISCED-F 2013 Fields of Education",
            canonical_label_zh="联合国教科文组织教育与培训学科分类（ISCED-F 2013）",
            label_zh_status="native",
            facet_type="DOMAIN",
            source_id="ISCEDF",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="ISCED-F 2013",
            definition_summary="UNESCO ISCED Fields of Education and Training 2013 (11 broad / 58 narrow / 149 detailed).",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_iscedf", "full_dist", "full_local"),
            active_in_pool=False,
        )

        lv_map = {"broad": 1, "narrow": 2, "detailed": 3}
        lv_counts = collections.Counter()
        for r in rows:
            code = r["code"].strip()
            pcode = (r.get("parent_code") or "").strip()
            lv_name = r["level"].strip().lower()
            lv = lv_map.get(lv_name, 3)
            lv_counts[lv_name] += 1
            segs = [r["title"].strip()]
            cur = pcode
            while cur and cur in by_code:
                segs.append(by_code[cur]["title"].strip())
                cur = (by_code[cur].get("parent_code") or "").strip()
            segs.reverse()

            nid = f"iscedf:{code}"
            pnid = f"iscedf:{pcode}" if pcode and pcode in by_code else root_id
            desc = (r.get("description") or "").strip() or None
            self.add_node(
                node_id=nid,
                canonical_label_en=r["title"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="ISCEDF",
                source_code=code,
                parent_id=pnid,
                depth=lv,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ISCED-F 2013"] + segs),
                definition_summary=desc,
                definition_status="present" if desc else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=lv,
                pack_groups=("ext_iscedf", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "ISCEDF", 1.0)

        self.stats["iscedf"] = {"total": len(rows), "levels": dict(lv_counts)}

    # ------------------------------------------------------------------ 13. UNESCO Thesaurus (concepts + 3,682 skos:related)
    def stage_unesco(self) -> None:
        ttl_path = DATA_REPO_DIR / "07_Humanities_KOS/UNESCO_Thesaurus/unesco-thesaurus.ttl"
        txt = ttl_path.read_text(encoding="utf-8", errors="ignore")
        # Split turtle blocks cleanly on blank lines / subject headers
        blocks = re.split(r"\n(?=:concept\d+\s)", txt)
        concepts: Dict[str, dict] = {}
        for blk in blocks:
            m_id = re.match(r"^:(concept\d+)\s", blk)
            if not m_id:
                continue
            cid = m_id.group(1)
            m_en = re.search(r'skos:prefLabel[^;.]*?"([^"]+)"@en', blk, re.S)
            if not m_en:
                continue
            broader = re.findall(r"skos:broader\s+([^;.]+)", blk)
            b_ids = []
            for bseg in broader:
                b_ids.extend(re.findall(r":(concept\d+)", bseg))
            related = re.findall(r"skos:related\s+([^;.]+)", blk)
            r_ids = []
            for rseg in related:
                r_ids.extend(re.findall(r":(concept\d+)", rseg))
            alt_en = re.findall(r'"([^"]+)"@en', " ".join(re.findall(r"skos:altLabel\s+([^;.]+)", blk, re.S)))
            m_sn = re.search(r'skos:scopeNote[^;.]*?"([^"]+)"@en', blk, re.S)
            concepts[cid] = {
                "en": m_en.group(1).strip(),
                "broader": b_ids,
                "related": r_ids,
                "alt_en": alt_en,
                "scope_note": m_sn.group(1).strip() if m_sn else None,
            }

        root_id = "unesco:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="UNESCO Thesaurus",
            canonical_label_zh="联合国教科文组织叙词表",
            label_zh_status="native",
            facet_type="DOMAIN",
            source_id="UNESCO",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="UNESCO Thesaurus",
            definition_summary="UNESCO controlled vocabulary for education, culture, sciences, and communication.",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_unesco", "full_dist", "full_local"),
            active_in_pool=False,
        )

        def resolve_u_chain(cid: str) -> List[str]:
            segs = []
            cur = cid
            seen = set()
            while cur and cur in concepts and cur not in seen and len(segs) < 10:
                seen.add(cur)
                segs.append(concepts[cur]["en"])
                pars = [b for b in concepts[cur]["broader"] if b in concepts]
                cur = pars[0] if pars else ""
            segs.reverse()
            return segs

        for cid, rec in concepts.items():
            nid = f"unesco:{cid}"
            pars = [b for b in rec["broader"] if b in concepts]
            pnid = f"unesco:{pars[0]}" if pars else root_id
            segs = resolve_u_chain(cid)
            self.add_node(
                node_id=nid,
                canonical_label_en=rec["en"],
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="UNESCO",
                source_code=cid,
                parent_id=pnid,
                depth=len(segs),
                is_root=0,
                breadcrumb_path=format_breadcrumb(["UNESCO"] + segs),
                definition_summary=rec["scope_note"],
                definition_status="present" if rec["scope_note"] else "missing",
                domain_partition_id=root_id,
                min_zoom_tier=min(3, len(segs)),
                pack_groups=("ext_unesco", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "UNESCO", 1.0)
            for al in rec["alt_en"]:
                self.add_alias(nid, al, "en", "UNESCO", "alt", 0.9)

        # Deduplicate undirected skos:related pairs into directed canonical pairs (or keep both)
        rel_pairs = set()
        for cid, rec in concepts.items():
            for rc in rec["related"]:
                if rc in concepts and rc != cid:
                    a, b = sorted((f"unesco:{cid}", f"unesco:{rc}"))
                    if (a, b) not in rel_pairs:
                        rel_pairs.add((a, b))
                        self.add_edge(a, b, "skos:related", "UNESCO", 1.0)

        self.stats["unesco"] = {"concepts": len(concepts), "related_edges": len(rel_pairs)}

    # ------------------------------------------------------------------ 14. Glottolog 5.4 (§5.1: remove 10,531 dialects, keep 12,712)
    def stage_glottolog(self) -> None:
        base = DATA_REPO_DIR / "07_Humanities_KOS/Glottolog_5_4"
        geo_map: Dict[str, Tuple[float, float]] = {}
        with open(base / "languages_and_dialects_geo.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                lat, lon = (r.get("latitude") or "").strip(), (r.get("longitude") or "").strip()
                if lat and lon:
                    try:
                        geo_map[r["glottocode"].strip()] = (float(lat), float(lon))
                    except ValueError:
                        pass

        with zipfile.ZipFile(base / "glottolog_languoid.csv.zip") as z:
            rows = list(csv.DictReader(io.TextIOWrapper(z.open("languoid.csv"), encoding="utf-8-sig")))

        by_pk = {}
        dialects_removed = 0
        kept_rows = []
        for r in rows:
            if r["level"].strip().lower() == "dialect":
                dialects_removed += 1
                continue
            by_pk[r["pk"].strip()] = r
            kept_rows.append(r)

        root_id = "glotto:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="Glottolog World Language Families & Languages",
            canonical_label_zh="Glottolog 世界语系与语言谱系",
            label_zh_status="native",
            facet_type="DOMAIN",
            source_id="GLOTTOLOG",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="Glottolog 5.4",
            definition_summary="Glottolog 5.4 language families and languages (dialects excluded).",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_glottolog", "full_dist", "full_local"),
            active_in_pool=False,
        )

        with_geo = 0
        for r in kept_rows:
            gid = r["id"].strip()
            nid = f"glotto:{gid}"
            fpk = (r.get("father_pk") or "").strip()
            pnid = f"glotto:{by_pk[fpk]['id'].strip()}" if (fpk and fpk in by_pk) else root_id

            segs = [r["name"].strip()]
            cur = fpk
            seen = {r["pk"].strip()}
            while cur and cur in by_pk and cur not in seen and len(segs) < 12:
                seen.add(cur)
                segs.append(by_pk[cur]["name"].strip())
                cur = (by_pk[cur].get("father_pk") or "").strip()
            segs.reverse()

            lat = lon = None
            if (r.get("latitude") or "").strip() and (r.get("longitude") or "").strip():
                try:
                    lat, lon = float(r["latitude"]), float(r["longitude"])
                except ValueError:
                    pass
            elif gid in geo_map:
                lat, lon = geo_map[gid]
            if lat is not None:
                with_geo += 1

            status_val = (r.get("status") or "").strip()
            extra = {"level": r["level"].strip()}
            if status_val:
                extra["endangerment_status"] = status_val
            if (r.get("hid") or "").strip():
                extra["iso639_3"] = r["hid"].strip()

            self.add_node(
                node_id=nid,
                canonical_label_en=r["name"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="GLOTTOLOG",
                source_code=gid,
                parent_id=pnid,
                depth=len(segs),
                is_root=0,
                breadcrumb_path=format_breadcrumb(["Glottolog"] + segs),
                definition_summary=f"Glottolog {r['level']}: {r['name']}" + (f" (status: {status_val})" if status_val else ""),
                definition_status="derived",
                domain_partition_id=root_id,
                min_zoom_tier=min(3, len(segs)),
                geo_lat=lat,
                geo_lon=lon,
                extra=extra,
                pack_groups=("core", "ext_glottolog", "full_dist", "full_local"),
            )
            self.add_edge(nid, pnid, "skos:broader", "GLOTTOLOG", 1.0)

        self.stats["glottolog"] = {
            "kept_nodes": len(kept_rows),
            "dialects_removed": dialects_removed,
            "with_geo": with_geo,
        }

    # ------------------------------------------------------------------ 15. PeriodO (CC0 historical periods)
    def stage_periodo(self) -> None:
        p = DATA_REPO_DIR / "07_Humanities_KOS/PeriodO/periodo-dataset.csv"
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))

        root_id = "periodo:ROOT"
        self.add_node(
            node_id=root_id,
            canonical_label_en="PeriodO Historical Periods",
            canonical_label_zh="PeriodO 历史分期目录",
            label_zh_status="native",
            facet_type="ARTIFACT",
            source_id="PERIODO",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="PeriodO",
            definition_summary="PeriodO gazetteer of scholarly historical period definitions (CC0).",
            domain_partition_id=root_id,
            min_zoom_tier=0,
            pack_groups=("ext_periodo", "full_dist", "full_local"),
            active_in_pool=False,
        )

        added = 0
        for r in rows:
            uri = (r.get("period") or "").strip()
            lbl = (r.get("label") or "").strip()
            if not uri or not lbl:
                continue
            code = uri.rsplit("/", 1)[-1]
            nid = f"periodo:{code}"
            spatial = (r.get("spatial_coverage") or "").strip()
            start = (r.get("start") or "").strip()
            stop = (r.get("stop") or "").strip()
            span = f"{start}..{stop}" if (start or stop) else ""
            segs = ["PeriodO"] + ([spatial[:60]] if spatial else []) + [lbl]
            defn = f"{lbl} ({span})" + (f" — {spatial}" if spatial else "")
            self.add_node(
                node_id=nid,
                canonical_label_en=lbl,
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ARTIFACT",
                source_id="PERIODO",
                source_code=code,
                parent_id=root_id,
                depth=1,
                is_root=0,
                breadcrumb_path=format_breadcrumb(segs),
                definition_summary=defn,
                definition_status="derived",
                domain_partition_id=root_id,
                min_zoom_tier=3,
                extra={"start": start, "stop": stop, "spatial": spatial[:120]} if (span or spatial) else None,
                pack_groups=("core", "ext_periodo", "full_dist", "full_local"),
            )
            self.add_edge(nid, root_id, "skos:broader", "PERIODO", 0.5)
            added += 1

        self.stats["periodo"] = {"nodes": added}

    # ------------------------------------------------------------------ 16. Local-only sources (ISCO-08, ZYFL_DADIAN, PhilPapers)
    def stage_local_only_extras(self) -> None:
        # A. ISCO-08 (619 nodes, local_only under G-01 A_revised)
        isco_rows = list(csv.DictReader(open(DATA_REPO_DIR / "04_Activity_KOS/ISCO-08_ILO/isco08_structure.csv", encoding="utf-8-sig")))
        by_code = {r["code"].strip(): r for r in isco_rows}
        isco_root = "isco:ROOT"
        self.add_node(
            node_id=isco_root,
            canonical_label_en="ISCO-08 Occupations",
            canonical_label_zh="ISCO-08 国际标准职业分类",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="ISCO08",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="ISCO-08",
            definition_summary="ILO International Standard Classification of Occupations (2008) - local use only.",
            domain_partition_id=isco_root,
            min_zoom_tier=0,
            pack_groups=("full_local",),
            active_in_pool=False,
        )
        for r in isco_rows:
            code = r["code"].strip()
            pcode = (r.get("parent_code") or "").strip()
            segs = [r["title"].strip()]
            cur = pcode
            while cur and cur in by_code:
                segs.append(by_code[cur]["title"].strip())
                cur = (by_code[cur].get("parent_code") or "").strip()
            segs.reverse()
            nid = f"isco:{code}"
            pnid = f"isco:{pcode}" if pcode and pcode in by_code else isco_root
            self.add_node(
                node_id=nid,
                canonical_label_en=r["title"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="ACTIVITY",
                source_id="ISCO08",
                source_code=code,
                parent_id=pnid,
                depth=int(r["level"]),
                is_root=0,
                breadcrumb_path=format_breadcrumb(["ISCO-08"] + segs),
                definition_summary=r.get("definition"),
                definition_status="present" if (r.get("definition") or "").strip() else "missing",
                domain_partition_id=isco_root,
                min_zoom_tier=min(3, int(r["level"])),
                extra={"tasks_include": (r.get("tasks_include") or "")[:400]} if (r.get("tasks_include") or "").strip() else None,
                pack_groups=("full_local",),
            )
            self.add_edge(nid, pnid, "skos:broader", "ISCO08", 1.0)

        # B. China Occupational Classification Dictionary 2022 (ZYFL_DADIAN, local_only)
        zbase = DATA_REPO_DIR / "06_Chinese_KOS/ZYFL_2022_DADIAN"
        zh_hier = list(csv.DictReader(open(zbase / "zyfl2022_hierarchy.csv", encoding="utf-8-sig")))
        zh_occ = list(csv.DictReader(open(zbase / "zyfl2022_occupations.csv", encoding="utf-8-sig")))
        occ_map = {r["code"].strip(): r for r in zh_occ}
        hier_map = {r["code"].strip(): r for r in zh_hier}

        zroot = "zyfl:ROOT"
        self.add_node(
            node_id=zroot,
            canonical_label_en="China Occupational Classification Dictionary 2022",
            canonical_label_zh="中华人民共和国职业分类大典（2022）",
            label_zh_status="native",
            facet_type="ACTIVITY",
            source_id="ZYFL_DADIAN",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="职业分类大典 2022",
            definition_summary="中华人民共和国职业分类大典（2022年版公示稿，仅限本地研究使用）。",
            domain_partition_id=zroot,
            min_zoom_tier=0,
            pack_groups=("full_local",),
            active_in_pool=False,
        )
        for r in zh_hier:
            code = r["code"].strip()
            pcode = (r.get("parent_code") or "").strip()
            segs = [r["name"].strip()]
            cur = pcode
            while cur and cur in hier_map:
                segs.append(hier_map[cur]["name"].strip())
                cur = (hier_map[cur].get("parent_code") or "").strip()
            segs.reverse()
            nid = f"zyfl:{code}"
            pnid = f"zyfl:{pcode}" if pcode and pcode in hier_map else zroot
            self.add_node(
                node_id=nid,
                canonical_label_en=None,
                canonical_label_zh=r["name"].strip(),
                label_zh_status="native",
                facet_type="ACTIVITY",
                source_id="ZYFL_DADIAN",
                source_code=code,
                parent_id=pnid,
                depth=int(r["level"]) + 1,
                is_root=0,
                breadcrumb_path=format_breadcrumb(["职业分类大典"] + segs),
                definition_summary=r["name"].strip(),
                definition_status="derived",
                domain_partition_id=zroot,
                min_zoom_tier=min(2, int(r["level"]) + 1),
                pack_groups=("full_local",),
            )
            self.add_edge(nid, pnid, "skos:broader", "ZYFL_DADIAN", 1.0)

        for r in zh_occ:
            code = r["code"].strip()
            # Parent small-class code: e.g. 1-01-00-01 -> 1-01-00 (or prefix match)
            parts = code.split("-")
            pcode = "-".join(parts[:-1]) if len(parts) > 1 else ""
            while pcode and pcode not in hier_map and "-" in pcode:
                pcode = pcode.rsplit("-", 1)[0]
            pnid = f"zyfl:{pcode}" if pcode and pcode in hier_map else zroot
            p_bc = self.nodes[pnid]["breadcrumb_path"]
            nid = f"zyfl:{code}"
            defn = (r.get("definition") or "").strip() or None
            tasks = (r.get("tasks") or "").strip()
            self.add_node(
                node_id=nid,
                canonical_label_en=None,
                canonical_label_zh=r["name"].strip(),
                label_zh_status="native",
                facet_type="ACTIVITY",
                source_id="ZYFL_DADIAN",
                source_code=code,
                parent_id=pnid,
                depth=self.nodes[pnid]["depth"] + 1,
                is_root=0,
                breadcrumb_path=format_breadcrumb([p_bc, r["name"].strip()]),
                definition_summary=defn,
                definition_status="present" if defn else "missing",
                domain_partition_id=zroot,
                min_zoom_tier=3,
                extra={"tasks": tasks[:400]} if tasks else None,
                pack_groups=("full_local",),
            )
            self.add_edge(nid, pnid, "skos:broader", "ZYFL_DADIAN", 1.0)

        # C. PhilPapers Categories (non-cross-area only, local_only)
        pp_rows = list(csv.DictReader(open(DATA_REPO_DIR / "07_Humanities_KOS/PhilPapers_Categories/philpapers_categories.csv", encoding="utf-8-sig")))
        pp_kept = [r for r in pp_rows if (r.get("is_cross_area") or "").strip().lower() not in ("true", "1", "yes")]
        pp_by_id = {r["id"].strip(): r for r in pp_kept}
        pp_root = "phil:ROOT"
        self.add_node(
            node_id=pp_root,
            canonical_label_en="PhilPapers Philosophy Taxonomy",
            canonical_label_zh="PhilPapers 哲学分类体系",
            label_zh_status="native",
            facet_type="DOMAIN",
            source_id="PHILPAPERS",
            source_code="ROOT",
            parent_id=None,
            depth=0,
            is_root=1,
            breadcrumb_path="PhilPapers",
            definition_summary="PhilPapers philosophy taxonomy (local reference only; ai-train=no).",
            domain_partition_id=pp_root,
            min_zoom_tier=0,
            pack_groups=("full_local",),
            active_in_pool=False,
        )
        for r in pp_kept:
            cid = r["id"].strip()
            pid = (r.get("parent_id") or "").strip()
            segs = [r["label"].strip()]
            cur = pid
            seen = {cid}
            while cur and cur in pp_by_id and cur not in seen and len(segs) < 12:
                seen.add(cur)
                segs.append(pp_by_id[cur]["label"].strip())
                cur = (pp_by_id[cur].get("parent_id") or "").strip()
            segs.reverse()
            nid = f"phil:{cid}"
            pnid = f"phil:{pid}" if pid and pid in pp_by_id else pp_root
            wc = int(r.get("works_count") or 0)
            self.add_node(
                node_id=nid,
                canonical_label_en=r["label"].strip(),
                canonical_label_zh=None,
                label_zh_status="missing",
                facet_type="DOMAIN",
                source_id="PHILPAPERS",
                source_code=cid,
                parent_id=pnid,
                depth=len(segs),
                is_root=0,
                breadcrumb_path=format_breadcrumb(["PhilPapers"] + segs),
                definition_summary=None,
                definition_status="missing",
                domain_partition_id=pp_root,
                min_zoom_tier=min(3, len(segs)),
                pack_groups=("full_local",),
                works_count=wc,
            )
            self.add_edge(nid, pnid, "skos:broader", "PHILPAPERS", 1.0)

        self.stats["local_only_extras"] = {
            "isco08": len(isco_rows),
            "zyfl_hier": len(zh_hier),
            "zyfl_occ": len(zh_occ),
            "philpapers_kept": len(pp_kept),
            "philpapers_cross_removed": len(pp_rows) - len(pp_kept),
        }

    # ------------------------------------------------------------------ 17. Bulk Ext Sources: LCSH (~275k) + DILA (~167k)
    def stage_lcsh_and_dila_to_db(self, con: sqlite3.Connection, start_spatial_id: int) -> Tuple[int, dict]:
        """Stream bulk LCSH (non-subdivided ~275k) and DILA (49.6k person + 117.9k place)
        directly into staging.sqlite so memory stays low and staging remains fast."""
        sid_seq = start_spatial_id
        bulk_stats: dict = {}

        # --- LCSH (§5.1: remove 46.4% subdivided headings containing '--', ~275k kept) ---
        lcsh_root = "lcsh:ROOT"
        con.execute(
            "INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                lcsh_root, sid_seq, "Library of Congress Subject Headings", "美国国会图书馆主题标目表", "native",
                "DOMAIN", "LCSH", "ROOT", None, None, 0, 1, "LCSH",
                "Library of Congress Subject Headings (deduplicated non-subdivided headings).", "present",
                0.5, 5000.0, 5000.0, lcsh_root, 0, None, None, None, None,
            ),
        )
        con.execute(
            "INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?), (?,?), (?,?)",
            (lcsh_root, "ext_lcsh", lcsh_root, "full_dist", lcsh_root, "full_local"),
        )
        sid_seq += 1

        # Load skos:note (13,801) and skos:example (10,561) from subjects.skosrdf.nt.gz (§5.1)
        import gzip

        lcsh_notes: Dict[str, str] = {}
        lcsh_examples: Dict[str, List[str]] = collections.defaultdict(list)
        nt_path = DATA_REPO_DIR / "07_Humanities_KOS/LCSH_Authorities/subjects.skosrdf.nt.gz"
        if nt_path.exists():
            note_pred = "<http://www.w3.org/2004/02/skos/core#note>"
            ex_pred = "<http://www.w3.org/2004/02/skos/core#example>"
            lit_re = re.compile(r'^<[^>]+/subjects/(sh\d+)> <[^>]+> "(.*)"(?:@[a-zA-Z-]+)? \.$')
            with gzip.open(nt_path, "rt", encoding="utf-8", errors="replace") as gf:
                for line in gf:
                    if note_pred in line:
                        m = lit_re.match(line.strip())
                        if m and m.group(1) not in lcsh_notes:
                            lcsh_notes[m.group(1)] = m.group(2).replace('\\"', '"')[:500]
                    elif ex_pred in line:
                        m = lit_re.match(line.strip())
                        if m and len(lcsh_examples[m.group(1)]) < 3:
                            lcsh_examples[m.group(1)].append(m.group(2).replace('\\"', '"')[:200])

        lcsh_path = DATA_REPO_DIR / "07_Humanities_KOS/LCSH_Authorities/lcsh_heads.csv"
        lcsh_total = lcsh_subdiv = lcsh_kept = lcsh_with_def = 0
        node_batch = []
        edge_batch = []
        pack_batch = []
        alias_batch = []

        kept_lcsh_ids: Set[str] = set()
        raw_broader_pairs: List[Tuple[str, str]] = []
        with open(lcsh_path, encoding="utf-8-sig", errors="replace") as f:
            for r in csv.DictReader(f):
                lcsh_total += 1
                lbl = (r.get("pref_label") or "").strip()
                lid = (r.get("id") or "").strip()
                if not lbl or not lid:
                    continue
                if "--" in lbl:
                    lcsh_subdiv += 1
                    continue
                nid = f"lcsh:{lid}"
                if nid in kept_lcsh_ids:
                    continue
                kept_lcsh_ids.add(nid)
                note_txt = lcsh_notes.get(lid)
                ex_list = lcsh_examples.get(lid)
                defn = _clean_text(note_txt or ("; ".join(ex_list) if ex_list else r.get("definition")), 500)
                extra_lcsh = json.dumps({"examples": ex_list}, ensure_ascii=False) if ex_list else None
                if defn:
                    lcsh_with_def += 1
                node_batch.append(
                    (
                        nid, sid_seq, lbl[:280], None, "missing", "DOMAIN", "LCSH", lid, None,
                        lcsh_root, 1, 0, f"LCSH > {lbl[:240]}", defn, "present" if defn else "missing",
                        4.0, 5000.0, 5000.0, lcsh_root, 3, None, None, None, extra_lcsh,
                    )
                )
                edge_batch.append((nid, lcsh_root, "skos:broader", 0.5, 1, "LCSH", None))
                pack_batch.extend([(nid, "ext_lcsh"), (nid, "full_dist"), (nid, "full_local")])

                for al in (r.get("alt_labels") or "").split("|")[:4]:
                    al = al.strip()
                    if al:
                        norm = normalize_alias(al)
                        if norm:
                            alias_batch.append((nid, al[:200], norm[:200], "en", "alt", "LCSH", 0.85))
                for br in (r.get("broader") or "").split("|")[:2]:
                    br = br.strip()
                    if br:
                        raw_broader_pairs.append((nid, f"lcsh:{br}"))
                sid_seq += 1
                lcsh_kept += 1

                if len(node_batch) >= 20000:
                    con.executemany("INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_batch)
                    con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_batch)
                    con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_batch)
                    con.executemany(
                        "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
                        alias_batch,
                    )
                    node_batch.clear()
                    edge_batch.clear()
                    pack_batch.clear()
                    alias_batch.clear()

        if node_batch:
            con.executemany("INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_batch)
            con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_batch)
            con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_batch)
            con.executemany(
                "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
                alias_batch,
            )
            node_batch.clear()
            edge_batch.clear()
            pack_batch.clear()
            alias_batch.clear()

        # Insert valid LCSH internal broader edges
        valid_br = [(s, t, "skos:broader", 1.0, 1, "LCSH", None) for s, t in raw_broader_pairs if t in kept_lcsh_ids and s != t]
        for i in range(0, len(valid_br), 20000):
            con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", valid_br[i : i + 20000])
        con.commit()

        bulk_stats["lcsh"] = {
            "total_rows": lcsh_total,
            "subdivided_removed": lcsh_subdiv,
            "kept_nodes": lcsh_kept,
            "with_note_or_def": lcsh_with_def,
            "internal_broader_edges": len(valid_br),
        }

        # --- DILA (§5.1: 49,644 person + 117,930 place; 49,151 bios; 58,502 geo; t2s; CC BY-SA 3.0 ext_dila) ---
        dila_dir = DATA_REPO_DIR / "06_Chinese_KOS/DILA_Buddhist_Authority"
        dila_p_root = "dila:ROOT_PERSON"
        dila_l_root = "dila:ROOT_PLACE"
        for rid, en, zh in [
            (dila_p_root, "DILA Buddhist Person Authority", "法鼓文理学院佛学人名规范库"),
            (dila_l_root, "DILA Buddhist Place Authority", "法鼓文理学院佛学地名规范库"),
        ]:
            con.execute(
                "INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid, sid_seq, en, zh, "native", "ARTIFACT", "DILA", rid.split(":")[-1], None,
                    None, 0, 1, zh, f"{zh}（CC BY-SA 3.0 Unported）", "present",
                    0.5, 5000.0, 5000.0, rid, 0, None, None, None, None,
                ),
            )
            con.execute(
                "INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?), (?,?), (?,?)",
                (rid, "ext_dila", rid, "full_dist", rid, "full_local"),
            )
            sid_seq += 1

        TEI = "{http://www.tei-c.org/ns/1.0}"
        XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

        # Parse Person XML
        person_count = person_with_bio = 0
        with zipfile.ZipFile(dila_dir / "authority_person.2026-11.zip") as z:
            with z.open("Buddhist_Studies_Person_Authority.xml") as fh:
                for _, elem in ET.iterparse(fh, events=("end",)):
                    if elem.tag != f"{TEI}person":
                        continue
                    pid = elem.get(XML_ID)
                    if not pid:
                        elem.clear()
                        continue
                    names = []
                    for pn in elem.findall(f"{TEI}persName"):
                        t = (pn.text or "").strip()
                        if t:
                            names.append(t)
                    if not names:
                        elem.clear()
                        continue
                    pref_hant = names[0]
                    pref_simp = self.t2s(pref_hant)
                    bio = None
                    for nt in elem.findall(f"{TEI}note"):
                        if nt.get("type") == "concise":
                            txt = "".join(nt.itertext()).strip()
                            if txt:
                                bio = self.t2s(_clean_text(txt, 500) or "")
                                break
                    if not bio:
                        nt0 = elem.find(f"{TEI}note")
                        if nt0 is not None:
                            txt = "".join(nt0.itertext()).strip()
                            if txt:
                                bio = self.t2s(_clean_text(txt, 500) or "")
                    if bio:
                        person_with_bio += 1

                    nid = f"dila:P:{pid}"
                    node_batch.append(
                        (
                            nid, sid_seq, None, pref_simp, "native", "ARTIFACT", "DILA", pid, None,
                            dila_p_root, 1, 0, f"佛学人名规范库 > {pref_simp}", bio,
                            "present" if bio else "missing", 4.5, 5000.0, 5000.0, dila_p_root, 3,
                            None, None, None, None,
                        )
                    )
                    edge_batch.append((nid, dila_p_root, "skos:broader", 0.5, 1, "DILA", None))
                    pack_batch.extend([(nid, "ext_dila"), (nid, "full_dist"), (nid, "full_local")])
                    if pref_hant != pref_simp:
                        alias_batch.append((nid, pref_hant, normalize_alias(pref_simp), "zh-Hant", "trad_simp", "DILA", 1.0))
                    for alt in names[1:6]:
                        asimp = self.t2s(alt)
                        anorm = normalize_alias(asimp)
                        if anorm:
                            alias_batch.append((nid, asimp, anorm, "zh", "alt", "DILA", 0.9))
                    sid_seq += 1
                    person_count += 1
                    elem.clear()

                    if len(node_batch) >= 15000:
                        con.executemany("INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_batch)
                        con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_batch)
                        con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_batch)
                        con.executemany(
                            "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
                            alias_batch,
                        )
                        node_batch.clear()
                        edge_batch.clear()
                        pack_batch.clear()
                        alias_batch.clear()

        # Parse Place XML
        place_count = place_with_geo = 0
        with zipfile.ZipFile(dila_dir / "authority_place.2026-11.zip") as z:
            with z.open("Buddhist_Studies_Place_Authority.xml") as fh:
                for _, elem in ET.iterparse(fh, events=("end",)):
                    if elem.tag != f"{TEI}place":
                        continue
                    plid = elem.get(XML_ID)
                    if not plid:
                        elem.clear()
                        continue
                    names = []
                    for pn in elem.findall(f"{TEI}placeName"):
                        t = (pn.text or "").strip()
                        if t:
                            names.append(t)
                    if not names:
                        elem.clear()
                        continue
                    pref_hant = names[0]
                    pref_simp = self.t2s(pref_hant)
                    lat = lon = None
                    geo_el = elem.find(f".//{TEI}geo")
                    if geo_el is not None and geo_el.text:
                        gparts = geo_el.text.strip().split()
                        if len(gparts) >= 2:
                            try:
                                # DILA TEI <geo> is "lon lat" or "lat lon" — check range
                                a, b = float(gparts[0]), float(gparts[1])
                                if abs(a) <= 90 and abs(b) <= 180 and a < 65:
                                    lat, lon = a, b
                                else:
                                    lon, lat = a, b
                                place_with_geo += 1
                            except ValueError:
                                pass
                    nt = elem.find(f"{TEI}note")
                    defn = self.t2s(_clean_text("".join(nt.itertext()), 400) or "") if nt is not None and "".join(nt.itertext()).strip() else None

                    nid = f"dila:L:{plid}"
                    node_batch.append(
                        (
                            nid, sid_seq, None, pref_simp, "native", "ARTIFACT", "DILA", plid, None,
                            dila_l_root, 1, 0, f"佛学地名规范库 > {pref_simp}", defn,
                            "present" if defn else "missing", 4.5, 5000.0, 5000.0, dila_l_root, 3,
                            None, lat, lon, None,
                        )
                    )
                    edge_batch.append((nid, dila_l_root, "skos:broader", 0.5, 1, "DILA", None))
                    pack_batch.extend([(nid, "ext_dila"), (nid, "full_dist"), (nid, "full_local")])
                    if pref_hant != pref_simp:
                        alias_batch.append((nid, pref_hant, normalize_alias(pref_simp), "zh-Hant", "trad_simp", "DILA", 1.0))
                    for alt in names[1:5]:
                        asimp = self.t2s(alt)
                        anorm = normalize_alias(asimp)
                        if anorm:
                            alias_batch.append((nid, asimp, anorm, "zh", "alt", "DILA", 0.9))
                    sid_seq += 1
                    place_count += 1
                    elem.clear()

                    if len(node_batch) >= 15000:
                        con.executemany("INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_batch)
                        con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_batch)
                        con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_batch)
                        con.executemany(
                            "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
                            alias_batch,
                        )
                        node_batch.clear()
                        edge_batch.clear()
                        pack_batch.clear()
                        alias_batch.clear()

        if node_batch:
            con.executemany("INSERT OR IGNORE INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_batch)
            con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_batch)
            con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_batch)
            con.executemany(
                "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
                alias_batch,
            )
        con.commit()

        bulk_stats["dila"] = {
            "persons": person_count,
            "persons_with_bio": person_with_bio,
            "person_bio_coverage": round(person_with_bio / max(person_count, 1), 4),
            "places": place_count,
            "places_with_geo": place_with_geo,
        }
        return sid_seq, bulk_stats

    # ------------------------------------------------------------------ Enrichment + Bottom-up IC
    def enrich_zh_and_compute_ic(self) -> None:
        # 1. Enrich English-only nodes with Chinese labels/definitions from OA glossary + AAT + YAGO + keyword bridges
        enriched_zh_glossary = 0
        enriched_zh_aat = 0
        enriched_zh_yago = 0
        enriched_zh_kw_aliases = 0
        enriched_def_yago = 0

        # Pre-index existing English aliases per node (e.g. OpenAlex topic keywords)
        en_aliases_by_node: Dict[str, List[str]] = collections.defaultdict(list)
        for a in self.aliases.values():
            if a["lang"] == "en" and a["node_id"] in self.nodes:
                en_aliases_by_node[a["node_id"]].append(a["alias"].strip().lower())

        def lookup_zh(term_lower: str) -> Tuple[Optional[str], Optional[str]]:
            if term_lower in OA_DOMAIN_FIELD_ZH:
                return OA_DOMAIN_FIELD_ZH[term_lower], "CUSTOM_SELF"
            if term_lower in self.aat_en_to_zh:
                return self.aat_en_to_zh[term_lower], "AAT"
            if term_lower in self.yago_en_to_zh:
                return self.yago_en_to_zh[term_lower], "YAGO_TAX"
            return None, None

        for nid, n in self.nodes.items():
            en = (n["canonical_label_en"] or "").strip().lower()
            if not en:
                continue
            if not n["canonical_label_zh"]:
                zh, src = lookup_zh(en)
                if zh and src:
                    n["canonical_label_zh"] = zh
                    n["label_zh_status"] = "human"
                    self.add_alias(nid, zh, "zh", src, "zh_bridge" if src != "YAGO_TAX" else "yago_label", 1.1)
                    if src == "CUSTOM_SELF":
                        enriched_zh_glossary += 1
                    elif src == "AAT":
                        enriched_zh_aat += 1
                    else:
                        enriched_zh_yago += 1

            # Keyword & sub-phrase Chinese alias bridging for OA_TREE, ACM_CCS, ICATUS, ONET, ESCO
            if n["source_id"] in ("OA_TREE", "ACM_CCS", "ICATUS", "ONET", "ESCO"):
                candidate_phrases: List[str] = []
                # Split title on connectors ("and", ",", ":", "of", "in")
                for seg in re.split(r"\s+and\s+|,\s*|:\s*|\s+in\s+|\s+of\s+", en):
                    seg = seg.strip()
                    if len(seg) >= 4 and seg != en:
                        candidate_phrases.append(seg)
                # Add topic keywords / English aliases
                for kw in en_aliases_by_node.get(nid, [])[:10]:
                    if len(kw) >= 3:
                        candidate_phrases.append(kw)
                added_for_node = 0
                for phr in candidate_phrases:
                    if added_for_node >= 6:
                        break
                    zh, src = lookup_zh(phr)
                    if zh and src and len(zh) >= 2:
                        self.add_alias(nid, zh, "zh", src, "zh_bridge", 0.95)
                        enriched_zh_kw_aliases += 1
                        added_for_node += 1

            if n["definition_status"] == "missing" and en in self.yago_en_to_def:
                n["definition_summary"] = _clean_text(self.yago_en_to_def[en], 600)
                n["definition_status"] = "derived"
                enriched_def_yago += 1

        # 1b. Cross-source & Cross-domain skos:closeMatch bridging into OA_TREE (30,211-edge co-occurrence hub)
        oa_kw_to_topics: Dict[str, List[str]] = collections.defaultdict(list)
        for nid, n in self.nodes.items():
            if n["source_id"] == "OA_TREE" and n["is_root"] == 0:
                en = (n["canonical_label_en"] or "").strip().lower()
                if en:
                    oa_kw_to_topics[en].append(nid)
                for kw in en_aliases_by_node.get(nid, []):
                    if len(kw) >= 4:
                        oa_kw_to_topics[kw].append(nid)
                # Also index title sub-phrases >= 5 chars
                for seg in re.split(r"\s+and\s+|,\s*|:\s*", en):
                    seg = seg.strip()
                    if len(seg) >= 5:
                        oa_kw_to_topics[seg].append(nid)

        cross_source_bridges = 0
        for nid, n in self.nodes.items():
            if n["source_id"] not in ("OA_CONCEPT", "ACM_CCS", "CUSTOM_SELF", "AAT", "ESCO", "UNESCO", "GLOTTOLOG"):
                continue
            if n["is_root"] == 1:
                continue
            en = (n["canonical_label_en"] or "").strip().lower()
            if not en or len(en) < 4:
                continue
            targets = oa_kw_to_topics.get(en)
            if not targets:
                continue
            # Prefer concrete oa:T* topics (up to 3 per node so graph stays sparse & high-precision)
            oa_topics = [t for t in targets if t.startswith("oa:T")]
            chosen = (oa_topics or targets)[:3]
            for tgt in chosen:
                self.add_edge(nid, tgt, "skos:closeMatch", n["source_id"], weight=1.0)
                cross_source_bridges += 1

        # 2. Bottom-up Information Content (IC) per source_id (Hard Fix #2)
        children: Dict[str, List[str]] = collections.defaultdict(list)
        by_src: Dict[str, List[str]] = collections.defaultdict(list)
        for nid, n in self.nodes.items():
            by_src[n["source_id"]].append(nid)
            if n["parent_id"] and n["parent_id"] in self.nodes:
                children[n["parent_id"]].append(nid)

        subtree_mass: Dict[str, float] = {}

        def calc_mass(nid: str, use_works: bool, visiting: Set[str]) -> float:
            if nid in subtree_mass:
                return subtree_mass[nid]
            if nid in visiting:
                return 1.0
            visiting.add(nid)
            base_m = float(self.nodes[nid]["_works_count"]) if use_works else 1.0
            if use_works and base_m <= 0.0 and not children.get(nid):
                base_m = 1.0
            tot = base_m
            for ch in children.get(nid, []):
                tot += calc_mass(ch, use_works, visiting)
            visiting.remove(nid)
            subtree_mass[nid] = max(tot, 1.0)
            return subtree_mass[nid]

        for src, nids in by_src.items():
            use_works = src in ("OA_TREE", "OA_CONCEPT", "PHILPAPERS")
            for nid in nids:
                calc_mass(nid, use_works, set())
            total_mass = max(
                sum(
                    subtree_mass[nid]
                    for nid in nids
                    if (self.nodes[nid]["is_root"] == 1 or self.nodes[nid]["parent_id"] not in self.nodes)
                ),
                1.0,
            )
            for nid in nids:
                p = min(1.0, max(1e-9, subtree_mass[nid] / total_mass))
                self.nodes[nid]["info_content"] = round(-math.log(p) + 0.1, 4)

        self.stats["enrichment"] = {
            "zh_from_glossary": enriched_zh_glossary,
            "zh_from_aat": enriched_zh_aat,
            "zh_from_yago": enriched_zh_yago,
            "zh_keyword_aliases_added": enriched_zh_kw_aliases,
            "def_from_yago": enriched_def_yago,
            "cross_source_closeMatch_bridges": cross_source_bridges,
        }

    # ------------------------------------------------------------------ Flush to staging.sqlite
    def write_staging_db(self) -> dict:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()

        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA synchronous = NORMAL;")
        con.executescript((SCHEMA_DIR / "kos_schema.sql").read_text(encoding="utf-8"))
        con.executescript((SCHEMA_DIR / "aei_calibration.sql").read_text(encoding="utf-8"))
        con.execute("PRAGMA foreign_keys = OFF;")  # Re-enabled + verified via PRAGMA foreign_key_check after bulk load
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS staging_pack_membership (
                node_id    TEXT NOT NULL,
                pack_group TEXT NOT NULL,
                PRIMARY KEY (node_id, pack_group)
            );
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_staging_pack_group ON staging_pack_membership(pack_group);")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 1. Provenance
        con.executemany(
            "INSERT INTO kos_provenance VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    p.source_id,
                    p.source_name,
                    p.version,
                    p.license_spdx,
                    p.dist_tier,
                    p.attribution_text,
                    p.homepage_url,
                    now[:10],
                    p.notes,
                )
                for p in SOURCE_POLICIES.values()
            ],
        )

        # 2. Validate parent_id references in memory before insert (guarantee 0 FK errors & 0 orphans!)
        sid_seq = 1
        node_rows = []
        pack_rows = []
        for nid in sorted(self.nodes):
            n = self.nodes[nid]
            pid = n["parent_id"]
            if pid is not None and pid not in self.nodes:
                pid = None
                n["is_root"] = 1
            node_rows.append(
                (
                    nid,
                    sid_seq,
                    n["canonical_label_en"],
                    n["canonical_label_zh"],
                    n["label_zh_status"],
                    n["facet_type"],
                    n["source_id"],
                    n["source_code"],
                    n["wikidata_qid"],
                    pid,
                    n["depth"],
                    n["is_root"],
                    n["breadcrumb_path"],
                    n["definition_summary"],
                    n["definition_status"],
                    n["info_content"],
                    5000.0,  # placeholder until compute_embeddings_layout.py freezes UMAP 2D coords
                    5000.0,
                    n["domain_partition_id"],
                    n["min_zoom_tier"],
                    n["met_value"],
                    n["geo_lat"],
                    n["geo_lon"],
                    n["extra_json"],
                )
            )
            for pg in self.pack_members.get(nid, ()):
                pack_rows.append((nid, pg))
            sid_seq += 1

        con.executemany("INSERT INTO kos_nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", node_rows)
        con.executemany("INSERT OR IGNORE INTO staging_pack_membership VALUES (?,?)", pack_rows)

        # 3. Aliases (filter to existing nodes)
        alias_rows = [
            (a["node_id"], a["alias"], a["alias_norm"], a["lang"], a["alias_type"], a["source_id"], a["weight"])
            for a in self.aliases.values()
            if a["node_id"] in self.nodes
        ]
        con.executemany(
            "INSERT OR IGNORE INTO kos_node_aliases (node_id,alias,alias_norm,lang,alias_type,source_id,weight) VALUES (?,?,?,?,?,?,?)",
            alias_rows,
        )

        # 4. Edges (filter to existing nodes)
        edge_rows = [
            (e["source_id"], e["target_id"], e["relation_type"], e["weight"], e["polarity"], e["provenance_source_id"], e["evidence_json"])
            for e in self.edges.values()
            if e["source_id"] in self.nodes and e["target_id"] in self.nodes
        ]
        con.executemany("INSERT OR IGNORE INTO kos_edges VALUES (?,?,?,?,?,?,?)", edge_rows)

        # 5. Load AEI calibration TSV into kos_aei_calibration
        aei_tsv = STAGING_DIR / "aei_calibration.tsv"
        aei_cal_rows = 0
        if aei_tsv.exists():
            with open(aei_tsv, encoding="utf-8") as f:
                batch = []
                for r in csv.DictReader(f, delimiter="\t"):
                    batch.append(
                        (
                            r["axis_type"],
                            r["axis_key"],
                            r["axis_label"],
                            r["geography"],
                            r["release_version"],
                            float(r["directive_pct"]),
                            float(r["feedback_loop_pct"]),
                            float(r["task_iteration_pct"]),
                            float(r["learning_pct"]),
                            float(r["validation_pct"]),
                            float(r["none_pct"]),
                            float(r["total_pct"]),
                            "AEI",
                        )
                    )
                con.executemany(
                    "INSERT OR IGNORE INTO kos_aei_calibration "
                    "(axis_type,axis_key,axis_label,geography,release_version,"
                    "directive_pct,feedback_loop_pct,task_iteration_pct,learning_pct,validation_pct,none_pct,total_pct,source_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                aei_cal_rows = len(batch)

        con.commit()

        # 6. Stream bulk LCSH + DILA
        sid_seq, bulk_stats = self.stage_lcsh_and_dila_to_db(con, sid_seq)
        self.stats.update(bulk_stats)

        # 7. Store inactive pool list in staging helper table
        con.execute("CREATE TABLE IF NOT EXISTS staging_inactive_nodes (node_id TEXT PRIMARY KEY);")
        con.executemany(
            "INSERT OR IGNORE INTO staging_inactive_nodes VALUES (?)",
            [(nid,) for nid in self.inactive_in_pool],
        )

        # 8. Write staging metadata
        meta_items = [
            ("schema_version", SCHEMA_VERSION, now),
            ("build_profile", "staging", now),
            ("built_at", now, now),
            ("gate_policies_json", json.dumps(GATE_POLICIES), now),
        ]
        con.executemany("INSERT OR REPLACE INTO kos_meta VALUES (?,?,?)", meta_items)
        con.commit()

        # Verify zero FK violations across entire staging DB!
        con.execute("PRAGMA foreign_keys = ON;")
        fk_errors = con.execute("PRAGMA foreign_key_check;").fetchall()

        pack_counts = dict(
            con.execute("SELECT pack_group, COUNT(*) FROM staging_pack_membership GROUP BY pack_group ORDER BY pack_group").fetchall()
        )
        total_nodes = con.execute("SELECT COUNT(*) FROM kos_nodes").fetchone()[0]
        total_aliases = con.execute("SELECT COUNT(*) FROM kos_node_aliases").fetchone()[0]
        total_edges = con.execute("SELECT COUNT(*) FROM kos_edges").fetchone()[0]
        empty_bc = con.execute("SELECT COUNT(*) FROM kos_nodes WHERE breadcrumb_path IS NULL OR length(trim(breadcrumb_path)) = 0").fetchone()[0]
        orphans = con.execute(
            "SELECT COUNT(*) FROM kos_nodes n WHERE n.is_root = 0 AND n.parent_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM kos_edges e WHERE e.source_id = n.node_id OR e.target_id = n.node_id)"
        ).fetchone()[0]
        con.close()

        summary = {
            "ok": len(fk_errors) == 0 and empty_bc == 0 and orphans == 0,
            "staging_db": str(self.db_path.relative_to(DATA_REPO_DIR.parent)),
            "staging_size_mb": round(self.db_path.stat().st_size / 1048576, 2),
            "total_nodes": total_nodes,
            "total_aliases": total_aliases,
            "total_edges": total_edges,
            "aei_calibration_rows": aei_cal_rows,
            "fk_violations": len(fk_errors),
            "empty_breadcrumb_count": empty_bc,
            "orphan_count": orphans,
            "pack_counts": pack_counts,
            "source_stats": self.stats,
        }
        (STAGING_DIR / "staging_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    def run_all(self) -> dict:
        self.load_yago_enrichment()
        self.stage_openalex(top_concepts=10000)
        self.stage_gbt13745()
        self.stage_icatus_atus()
        self.stage_onet()
        self.stage_esco()
        self.stage_acm_ccs_and_ai()
        self.stage_custom_facet_v_and_jobs()
        self.stage_aat(core_zh_limit=26000)
        self.stage_edam()
        self.stage_who_icf()
        self.stage_compendium()
        self.stage_iscedf()
        self.stage_unesco()
        self.stage_glottolog()
        self.stage_periodo()
        self.stage_local_only_extras()
        self.enrich_zh_and_compute_ic()
        return self.write_staging_db()


if __name__ == "__main__":
    builder = StagingBuilder(STAGING_DIR / "staging.sqlite")
    res = builder.run_all()
    print(json.dumps(res, ensure_ascii=False, indent=2))
