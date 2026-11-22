"""Extract YAGO 4.6 taxonomy definitions & Chinese labels (§5.1) + Top-300 type map (G-03).

Outputs (under Data Repository/_derived/staging/):
- yago_class.tsv: class_id, label_en, label_zh_orig, label_zh_simp, comment_en
  Acceptance (§5.1): ~95,055 class definitions, ~38,520 zh labels, >=35,000 after
  Traditional->Simplified normalization, unique class_id.
- yago_top300_discipline_map.tsv: Top-300 rdf:type classes from yago-tiny.ttl
  mapped to OpenAlex discipline nodes (G-03).
"""

from __future__ import annotations

import collections
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kos_build.config import DATA_REPO_DIR, STAGING_DIR

_LIT_RE = re.compile(r'^"(.*)"(?:@([a-zA-Z0-9-]+))?(?:\^\^.+)?\s*\.?\s*$')


def _get_t2s_converter():
    try:
        import opencc  # type: ignore

        cc = opencc.OpenCC("t2s")
        return cc.convert
    except Exception:
        from kos_build.etl.text_utils import _BASIC_T2S

        return lambda s: s.translate(_BASIC_T2S)


def _unescape_ttl(s: str) -> str:
    return (
        s.replace('\\"', '"')
        .replace("\\n", " ")
        .replace("\\t", " ")
        .replace("\\\\", "\\")
        .strip()
    )


def extract_yago_taxonomy() -> dict:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    tx_path = DATA_REPO_DIR / "01_Knowledge_Bases/YAGO/yago-4.6/extracted/yago-taxonomy.ttl"
    out_tsv = STAGING_DIR / "yago_class.tsv"

    t2s = _get_t2s_converter()
    labels_en: dict[str, str] = {}
    labels_zh: dict[str, str] = {}
    comments_en: dict[str, str] = {}
    all_classes: set[str] = set()

    with open(tx_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            subj, pred, obj = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if obj.endswith(" ."):
                obj = obj[:-2].strip()
            elif obj.endswith("."):
                obj = obj[:-1].strip()

            if pred == "rdf:type" and obj == "rdfs:Class":
                all_classes.add(subj)
                continue

            if pred == "rdfs:comment":
                m = _LIT_RE.match(obj)
                if m:
                    val, lang = _unescape_ttl(m.group(1)), (m.group(2) or "en").lower()
                    if val and (subj not in comments_en or lang.startswith("en")):
                        comments_en[subj] = val
                        all_classes.add(subj)
            elif pred == "rdfs:label":
                m = _LIT_RE.match(obj)
                if not m:
                    continue
                val, lang = _unescape_ttl(m.group(1)), (m.group(2) or "").lower()
                if not val:
                    continue
                if lang == "en" or (lang.startswith("en-") and subj not in labels_en):
                    labels_en[subj] = val
                    all_classes.add(subj)
                elif lang == "zh" or lang.startswith("zh-"):
                    # Prefer plain zh / zh-hans over zh-hant if both exist
                    if subj not in labels_zh or lang in ("zh", "zh-hans", "zh-cn"):
                        labels_zh[subj] = val
                        all_classes.add(subj)

    zh_simp_nonempty = 0
    def_nonempty = 0
    written = 0

    with open(out_tsv, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out, delimiter="\t")
        w.writerow(["class_id", "label_en", "label_zh_orig", "label_zh_simp", "comment_en"])
        for cid in sorted(all_classes):
            en = labels_en.get(cid, "")
            zh_orig = labels_zh.get(cid, "")
            zh_simp = t2s(zh_orig) if zh_orig else ""
            comm = comments_en.get(cid, "")
            if not (zh_orig or comm):
                continue
            if zh_simp:
                zh_simp_nonempty += 1
            if comm:
                def_nonempty += 1
            w.writerow([cid, en, zh_orig, zh_simp, comm])
            written += 1

    return {
        "out_tsv": str(out_tsv.relative_to(DATA_REPO_DIR.parent)),
        "total_classes_seen": len(all_classes),
        "rows_written": written,
        "classes_with_comment": def_nonempty,
        "classes_with_zh_label": len(labels_zh),
        "classes_with_zh_simp": zh_simp_nonempty,
        "unique_class_ids": True,
    }


# Rule-based + keyword discipline mapping from YAGO rdf:type to OpenAlex Field/Subfield
_TYPE_DISCIPLINE_RULES: list[tuple[str, str, str]] = [
    (r"Philosoph|Logician|Ethicist|Metaphys|Ontolog", "oa:F12", "Arts and Humanities > Philosophy"),
    (r"Theolog|Clergy|Bishop|Monk|Priest|Saint|Rabbi|Imam|Buddhist|Christian|Islam|Relig|Church|Temple|Monastery|Abbey|Cathedral|Mosque|Synagogue", "oa:F12_REL", "Arts and Humanities > Religious studies"),
    (r"Archaeolog|Antiquar|Prehistor|Excavat|Artifact|Monument|Ruin", "oa:F12_ARCH", "Arts and Humanities > Archeology"),
    (r"Histor|Monarch|Emperor|King|Queen|Dynasty|War|Battle|Treaty|Empire|Nobility|Aristocrat|Royalty|Pharaoh|Samurai", "oa:F12_HIST", "Arts and Humanities > History"),
    (r"Writer|Author|Poet|Novelist|Playwright|Journalist|Literat|Book|Novel|Poem|Magazine|Newspaper|Publisher|Comic|Manga", "oa:F12_LIT", "Arts and Humanities > Literature and Literary Theory"),
    (r"Linguist|Language|Dialect|Alphabet|Script|Philolog", "oa:F12_LING", "Arts and Humanities > Language and Linguistics"),
    (r"Actor|Actress|Film|Movie|Director|Cinema|Television|Theatre|Theater|Opera|Ballet|Dancer|Comedian|Circus", "oa:F12_PERF", "Arts and Humanities > Visual Arts and Performing Arts"),
    (r"Music|Singer|Composer|Band|Album|Song|Symphony|Orchestra|Guitar|Piano|Violin|Rapper|DJ", "oa:F12_MUS", "Arts and Humanities > Music"),
    (r"Painter|Sculptor|Artist|Architect|Building|Bridge|Museum|Gallery|Tower|Castle|Palace|Photograph|Design", "oa:F12_ART", "Arts and Humanities > Visual Arts and Performing Arts"),
    (r"Mathematic|Statistic|Algebra|Geometr", "oa:F26", "Physical Sciences > Mathematics"),
    (r"Physic|Astronom|Astrophys|Planet|Star|Galaxy|Asteroid|Comet|Spacecraft|Satellite|Cosmolog", "oa:F31", "Physical Sciences > Physics and Astronomy"),
    (r"Chemi|Molecule|Compound|Element|Mineral|Crystal|Polymer", "oa:F16", "Physical Sciences > Chemistry"),
    (r"Computer|Software|Program|Algorithm|VideoGame|Website|Internet|Database|OperatingSystem|AI|Robot|Hacker|Engineer", "oa:F17", "Physical Sciences > Computer Science"),
    (r"Biolog|Species|Animal|Plant|Bird|Fish|Insect|Mammal|Reptile|Fungus|Bacteri|Virus|Gene|Protein|Botan|Zoolog|Ecolog|Taxon", "oa:F11", "Life Sciences > Agricultural and Biological Sciences"),
    (r"Physician|Doctor|Surgeon|Hospital|Disease|Drug|Medicine|Medical|Nurse|Psychiatr|Pharmac|Anatom|Patholog", "oa:F27", "Health Sciences > Medicine"),
    (r"Psycholog|Cognit|Neuroscien", "oa:F32", "Social Sciences > Psychology"),
    (r"Econom|Bank|Company|Corporation|Business|Enterprise|Airline|Brand|Currency|Finance|Investor|Entrepreneur|Organization", "oa:F20", "Social Sciences > Economics, Econometrics and Finance"),
    (r"Polit|President|Minister|Senator|Mayor|Governor|Diplomat|Election|Party|Government|Parliament|State|Country|Nation|City|Town|Village|Municipality|County|Province|Region|District|River|Mountain|Lake|Island|Ocean|Sea|Airport|Station|Road|Highway|Park|Place|Location|Settlement", "oa:F33", "Social Sciences > Social Sciences / Geography & Politics"),
    (r"Lawyer|Judge|Jurist|Court|Law|Legal|Crime|Criminal|Police|Prison", "oa:F33_LAW", "Social Sciences > Law"),
    (r"University|School|College|Academy|Teacher|Professor|Educat|Scholar|Academic|Scientist|Researcher", "oa:F33_EDU", "Social Sciences > Education"),
    (r"Sociolog|Anthropolog|Demograph|Ethnic|Culture|Festival|Holiday", "oa:F33_SOC", "Social Sciences > Sociology and Political Science"),
    (r"Athlete|Sport|Football|Soccer|Basketball|Baseball|Tennis|Cricket|Hockey|Olymp|Swimmer|Runner|Boxer|Wrestler|Cyclist|Golfer|Coach|Team|Club|Stadium|League|Tournament|Race", "oa:F27_SPORT", "Health Sciences > Sports Science & Physical Activity"),
    (r"Military|Soldier|General|Admiral|Army|Navy|AirForce|Ship|Vessel|Aircraft|Weapon|Tank", "oa:F33_MIL", "Social Sciences > Political Science and International Relations"),
    (r"Food|Dish|Beverage|Wine|Beer|Cuisine|Chef|Restaurant|Agriculture|Farm", "oa:F11_FOOD", "Life Sciences > Food Science & Agriculture"),
    (r"Earth|Geolog|Volcano|Earthquake|Glacier|Climate|Weather|Meteorolog", "oa:F19", "Physical Sciences > Earth and Planetary Sciences"),
    (r"Vehicle|Car|Automobile|Train|Locomotive|Motorcycle|Bicycle|Transport|Energy|PowerPlant", "oa:F22", "Physical Sciences > Engineering"),
]


def _map_yago_type(type_name: str) -> tuple[str, str]:
    short = type_name.split(":", 1)[-1]
    for pat, code, label in _TYPE_DISCIPLINE_RULES:
        if re.search(pat, short, re.IGNORECASE):
            return code, label
    return "oa:F33", "Social Sciences > General / Interdisciplinary"


def extract_yago_top300_map() -> dict:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    tiny_path = DATA_REPO_DIR / "01_Knowledge_Bases/YAGO/yago-4.6/extracted/yago-tiny.ttl"
    out_tsv = STAGING_DIR / "yago_top300_discipline_map.tsv"

    type_counts: collections.Counter[str] = collections.Counter()
    with open(tiny_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "\trdf:type\t" not in line:
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            obj = parts[2].strip()
            if obj.endswith(" ."):
                obj = obj[:-2].strip()
            elif obj.endswith("."):
                obj = obj[:-1].strip()
            if obj and not obj.startswith("owl:") and not obj.startswith("rdfs:"):
                type_counts[obj] += 1

    top300 = type_counts.most_common(300)
    with open(out_tsv, "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out, delimiter="\t")
        w.writerow(["rank", "yago_type", "entity_count", "target_discipline_code", "target_discipline_path"])
        for rank, (tname, cnt) in enumerate(top300, start=1):
            dcode, dpath = _map_yago_type(tname)
            w.writerow([rank, tname, cnt, dcode, dpath])

    return {
        "out_tsv": str(out_tsv.relative_to(DATA_REPO_DIR.parent)),
        "distinct_types_in_tiny": len(type_counts),
        "top300_rows": len(top300),
        "top300_entity_coverage": sum(c for _, c in top300),
        "total_type_assertions": sum(type_counts.values()),
    }


if __name__ == "__main__":
    r1 = extract_yago_taxonomy()
    r2 = extract_yago_top300_map()
    print(json.dumps({"yago_taxonomy": r1, "yago_top300": r2}, ensure_ascii=False, indent=2))
