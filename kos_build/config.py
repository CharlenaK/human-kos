"""Central configuration for L1 KOS Database II build pipeline.

All filesystem paths are resolved relative to the repository root so no
machine-local absolute paths are embedded in source or release artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

SCHEMA_VERSION = "1.0.0"
EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384
EMBEDDING_QUANT = "int8"
EMBEDDING_VERSION = "e5-small-int8-v1"
LAYOUT_VERSION = "umap-n15-d01-cos-s42-v1"

UMAP_PARAMS = {
    "n_neighbors": 15,
    "min_dist": 0.1,
    "metric": "cosine",
    "random_state": 42,
}

# Gate policy defaults locked by T1.0 (2026-11-22)
GATE_POLICIES = {
    "G-01": "A_revised",  # DILA -> dist_ext_sa (CC BY-SA 3.0); GBT13745 / WHO_ICF / ISCO08 -> local_only
    "G-02": "A",          # Defer 43.67 GB Wikidata truthy until A3 recall evidence requires it
    "G-03": "A",          # Build all 4 custom mini-tables (ACM AI ~150, Facet V 10, 2024/25 jobs 36, YAGO Top300)
    "G-04": "A",          # multilingual-e5-small 384d int8 + UMAP(15, 0.1, cosine, 42) + Voronoi
}

REPO_ROOT = Path(__file__).resolve().parent.parent
KOS_BUILD_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = KOS_BUILD_DIR / "schema"
DATA_REPO_DIR = REPO_ROOT / "Data Repository"
DERIVED_DIR = DATA_REPO_DIR / "_derived"
STAGING_DIR = DERIVED_DIR / "staging"
OUTPUT_DIR = KOS_BUILD_DIR / "out"
RELEASE_DIR = KOS_BUILD_DIR / "release"

VALID_FACETS = ("DOMAIN", "ACTIVITY", "PROCESS", "ARTIFACT", "STANCE")


@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    source_name: str
    version: str
    license_spdx: str
    dist_tier: str  # dist_core | dist_ext_permissive | dist_ext_sa | local_only
    attribution_text: str
    homepage_url: str
    notes: str = ""


# Authoritative provenance & distribution matrix (incorporating G-01 A_revised)
SOURCE_POLICIES: Dict[str, SourcePolicy] = {
    "OA_TREE": SourcePolicy(
        source_id="OA_TREE",
        source_name="OpenAlex Topic Taxonomy (Domain/Field/Subfield/Topic)",
        version="2026-snapshot",
        license_spdx="CC0-1.0",
        dist_tier="dist_core",
        attribution_text="OpenAlex (OurResearch), CC0 1.0 Universal.",
        homepage_url="https://openalex.org/",
    ),
    "OA_CONCEPT": SourcePolicy(
        source_id="OA_CONCEPT",
        source_name="OpenAlex Concepts (Top works_count flat pool)",
        version="2026-snapshot",
        license_spdx="CC0-1.0",
        dist_tier="dist_core",
        attribution_text="OpenAlex (OurResearch), CC0 1.0 Universal.",
        homepage_url="https://openalex.org/",
    ),
    "OA_COOCCUR": SourcePolicy(
        source_id="OA_COOCCUR",
        source_name="OpenAlex Works Sample Topic Co-occurrence",
        version="2026-snapshot",
        license_spdx="CC0-1.0",
        dist_tier="dist_core",
        attribution_text="Derived from OpenAlex works sample (OurResearch), CC0 1.0 Universal.",
        homepage_url="https://openalex.org/",
    ),
    "ICATUS": SourcePolicy(
        source_id="ICATUS",
        source_name="UN ICATUS 2016 (International Classification of Activities for Time-Use Statistics)",
        version="2016",
        license_spdx="UN-Public-Statistical",
        dist_tier="dist_core",
        attribution_text="United Nations Statistics Division, ICATUS 2016.",
        homepage_url="https://unstats.un.org/unsd/demographic-social/time-use/icatus-2016/",
    ),
    "ATUS": SourcePolicy(
        source_id="ATUS",
        source_name="US BLS American Time Use Survey Activity Coding Lexicon",
        version="2025",
        license_spdx="US-Public-Domain",
        dist_tier="dist_core",
        attribution_text="U.S. Bureau of Labor Statistics, ATUS 2025 (Public Domain).",
        homepage_url="https://www.bls.gov/tus/",
    ),
    "ONET": SourcePolicy(
        source_id="ONET",
        source_name="O*NET 31.0 Content Model (GWA/IWA/DWA)",
        version="31.0",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), CC BY 4.0.",
        homepage_url="https://www.onetcenter.org/",
    ),
    "ESCO": SourcePolicy(
        source_id="ESCO",
        source_name="ESCO v1.2.1 (European Skills, Competences, Qualifications and Occupations)",
        version="1.2.1",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="European Commission, ESCO classification v1.2.1, CC BY 4.0.",
        homepage_url="https://esco.ec.europa.eu/",
    ),
    "ACM_CCS": SourcePolicy(
        source_id="ACM_CCS",
        source_name="ACM Computing Classification System 2012",
        version="2012",
        license_spdx="ACM-CCS-Attribution",
        dist_tier="dist_core",
        attribution_text="Association for Computing Machinery (ACM) Computing Classification System (2012).",
        homepage_url="https://dl.acm.org/ccs",
        notes="Verify ACM CCS citation notice in NOTICE.",
    ),
    "AEI": SourcePolicy(
        source_id="AEI",
        source_name="Anthropic Economic Index (Collaboration 5-Bucket Taxonomy & Calibration)",
        version="2025-2026",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="Anthropic Economic Index (Anthropic), CC BY 4.0.",
        homepage_url="https://www.anthropic.com/economic-index",
    ),
    "AAT": SourcePolicy(
        source_id="AAT",
        source_name="Getty Art & Architecture Thesaurus (incl. AAT-Taiwan zh labels)",
        version="2026-01",
        license_spdx="ODC-By-1.0",
        dist_tier="dist_core",
        attribution_text="Getty Research Institute, Art & Architecture Thesaurus (AAT), ODC-By 1.0.",
        homepage_url="https://www.getty.edu/research/tools/vocabularies/aat/",
    ),
    "UNESCO": SourcePolicy(
        source_id="UNESCO",
        source_name="UNESCO Thesaurus",
        version="2026-snapshot",
        license_spdx="CC-BY-SA-3.0-IGO",
        dist_tier="dist_ext_sa",
        attribution_text="UNESCO Thesaurus, CC BY-SA 3.0 IGO.",
        homepage_url="https://vocabularies.unesco.org/browser/thesaurus/",
        notes="Verify whether UNESCO related edges require SA isolation in T1.5 G-01 gate.",
    ),
    "YAGO_TAX": SourcePolicy(
        source_id="YAGO_TAX",
        source_name="YAGO 4.6 Taxonomy Class Definitions & Multilingual Labels",
        version="4.6",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="YAGO 4.6 (Max Planck Institute for Informatics & Telecom Paris), CC BY 4.0.",
        homepage_url="https://yago-knowledge.org/",
    ),
    "DEVKG": SourcePolicy(
        source_id="DEVKG",
        source_name="DevKG / W3C PROV-O / SKOS Metamodel Predicates",
        version="1.0",
        license_spdx="MIT",
        dist_tier="dist_core",
        attribution_text="Derived from DevKG metamodel and W3C SKOS/PROV-O vocabularies.",
        homepage_url="https://www.w3.org/TR/skos-reference/",
    ),
    "CUSTOM_SELF": SourcePolicy(
        source_id="CUSTOM_SELF",
        source_name="Project Self-Built Extensions (ACM AI ~150, Facet V Stances 5, 2024/25 Jobs 36, YAGO Top300 Map)",
        version="1.0.0",
        license_spdx="CC0-1.0",
        dist_tier="dist_core",
        attribution_text="Original project-authored supplement, CC0 1.0 Universal.",
        homepage_url="",
    ),
    "EDAM": SourcePolicy(
        source_id="EDAM",
        source_name="EDAM Ontology 1.25 (Active Terms Only)",
        version="1.25",
        license_spdx="CC-BY-SA-4.0",
        dist_tier="dist_ext_sa",
        attribution_text="EDAM Ontology v1.25, CC BY-SA 4.0.",
        homepage_url="https://edamontology.org/",
        notes="EDAM is CC BY-SA 4.0; distributed as independent ext pack edam.sqlite.",
    ),
    "ISCEDF": SourcePolicy(
        source_id="ISCEDF",
        source_name="UNESCO ISCED-F 2013 Fields of Education and Training",
        version="2013",
        license_spdx="CC-BY-SA-3.0-IGO",
        dist_tier="dist_ext_sa",
        attribution_text="UNESCO Institute for Statistics, ISCED-F 2013, CC BY-SA 3.0 IGO.",
        homepage_url="https://uis.unesco.org/",
    ),
    "COMPENDIUM": SourcePolicy(
        source_id="COMPENDIUM",
        source_name="2024 Adult Compendium of Physical Activities (MET attributes)",
        version="2024",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="2024 Adult Compendium of Physical Activities (Herrmann et al.).",
        homepage_url="https://pacompendium.com/",
    ),
    "LCSH": SourcePolicy(
        source_id="LCSH",
        source_name="Library of Congress Subject Headings (Deduplicated / Non-subdivided)",
        version="2026-snapshot",
        license_spdx="CC0-1.0",
        dist_tier="dist_ext_permissive",
        attribution_text="Library of Congress Subject Headings (Public Domain / CC0).",
        homepage_url="https://id.loc.gov/authorities/subjects.html",
    ),
    "GLOTTOLOG": SourcePolicy(
        source_id="GLOTTOLOG",
        source_name="Glottolog 5.4 (Families & Languages, Dialects Excluded)",
        version="5.4",
        license_spdx="CC-BY-4.0",
        dist_tier="dist_core",
        attribution_text="Glottolog 5.4 (Hammarstrom et al., Max Planck Institute for Evolutionary Anthropology), CC BY 4.0.",
        homepage_url="https://glottolog.org/",
    ),
    "PERIODO": SourcePolicy(
        source_id="PERIODO",
        source_name="PeriodO Historical Period Gazetteer",
        version="2026-snapshot",
        license_spdx="CC0-1.0",
        dist_tier="dist_core",
        attribution_text="PeriodO Gazetteer of Historical Periods, CC0 1.0 Public Domain Dedication.",
        homepage_url="https://perio.do/",
    ),
    "CONCEPTNET": SourcePolicy(
        source_id="CONCEPTNET",
        source_name="ConceptNet 5.7 (4 Action Relations + zh->en Synonym Bridge)",
        version="5.7.0",
        license_spdx="CC-BY-SA-4.0",
        dist_tier="dist_ext_sa",
        attribution_text="ConceptNet 5.7 (Speer et al.), CC BY-SA 4.0.",
        homepage_url="https://conceptnet.io/",
    ),
    "DILA": SourcePolicy(
        source_id="DILA",
        source_name="DILA Buddhist Studies Authority Databases (Person / Place / Time)",
        version="2026-11",
        license_spdx="CC-BY-SA-3.0",
        dist_tier="dist_ext_sa",
        attribution_text="Dharma Drum Institute of Liberal Arts (DILA) Buddhist Studies Authority Databases, CC BY-SA 3.0 Unported.",
        homepage_url="https://authority.dila.edu.tw/",
        notes="Verified CC BY-SA 3.0 Unported in TEI headers & official GitHub (G-01 A_revised).",
    ),
    "GBT13745": SourcePolicy(
        source_id="GBT13745",
        source_name="GB/T 13745-2009 Classification and Code of Disciplines",
        version="2009",
        license_spdx="SAC-GBT-Copyright",
        dist_tier="local_only",
        attribution_text="GB/T 13745-2009 (SAC China). Local research use only; excluded from public distribution.",
        homepage_url="https://std.samr.gov.cn/",
    ),
    "WHO_ICF": SourcePolicy(
        source_id="WHO_ICF",
        source_name="WHO International Classification of Functioning, Disability and Health",
        version="2026-01",
        license_spdx="CC-BY-ND-3.0-IGO",
        dist_tier="local_only",
        attribution_text="World Health Organization, ICF release 2026-01 (CC BY-ND 3.0 IGO). Local use only under G-01 A_revised.",
        homepage_url="https://icd.who.int/browse/latest-release/icf/en",
    ),
    "ISCO08": SourcePolicy(
        source_id="ISCO08",
        source_name="ILO ISCO-08 International Standard Classification of Occupations",
        version="2008-2021",
        license_spdx="ILO-Pre2023-Copyright",
        dist_tier="local_only",
        attribution_text="International Labour Organization, ISCO-08 (pre-2023-05-03 copyright). Local use only under G-01 A_revised.",
        homepage_url="https://www.ilo.org/rights-and-permissions",
    ),
    "PHILPAPERS": SourcePolicy(
        source_id="PHILPAPERS",
        source_name="PhilPapers Category Taxonomy",
        version="2026-snapshot",
        license_spdx="PhilPapers-NoAITrain",
        dist_tier="local_only",
        attribution_text="PhilPapers Categories (ai-train=no). Local reference only; excluded from public distribution.",
        homepage_url="https://philpapers.org/",
    ),
    "ZYFL_DADIAN": SourcePolicy(
        source_id="ZYFL_DADIAN",
        source_name="China Occupational Classification Dictionary 2022 (Public Draft)",
        version="2022-draft",
        license_spdx="Official-Public-Draft",
        dist_tier="local_only",
        attribution_text="Occupational Classification Dictionary of the PRC (2022 draft). Local use only.",
        homepage_url="",
    ),
}
