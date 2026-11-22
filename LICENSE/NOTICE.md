# NOTICE — Human KOS Database II (L1) Attribution & License Boundaries

This distribution package is strictly divided by upstream license compatibility:

## 1. Permissive Tier A Core (`human_kos_core.sqlite`) & Permissive Extensions

- **OpenAlex Topic Taxonomy (Domain/Field/Subfield/Topic)** (`OA_TREE`, v2026-snapshot) — `CC0-1.0`
  Attribution: OpenAlex (OurResearch), CC0 1.0 Universal.
  Source: https://openalex.org/
- **OpenAlex Concepts (Top works_count flat pool)** (`OA_CONCEPT`, v2026-snapshot) — `CC0-1.0`
  Attribution: OpenAlex (OurResearch), CC0 1.0 Universal.
  Source: https://openalex.org/
- **OpenAlex Works Sample Topic Co-occurrence** (`OA_COOCCUR`, v2026-snapshot) — `CC0-1.0`
  Attribution: Derived from OpenAlex works sample (OurResearch), CC0 1.0 Universal.
  Source: https://openalex.org/
- **UN ICATUS 2016 (International Classification of Activities for Time-Use Statistics)** (`ICATUS`, v2016) — `UN-Public-Statistical`
  Attribution: United Nations Statistics Division, ICATUS 2016.
  Source: https://unstats.un.org/unsd/demographic-social/time-use/icatus-2016/
- **US BLS American Time Use Survey Activity Coding Lexicon** (`ATUS`, v2025) — `US-Public-Domain`
  Attribution: U.S. Bureau of Labor Statistics, ATUS 2025 (Public Domain).
  Source: https://www.bls.gov/tus/
- **O*NET 31.0 Content Model (GWA/IWA/DWA)** (`ONET`, v31.0) — `CC-BY-4.0`
  Attribution: O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), CC BY 4.0.
  Source: https://www.onetcenter.org/
- **ESCO v1.2.1 (European Skills, Competences, Qualifications and Occupations)** (`ESCO`, v1.2.1) — `CC-BY-4.0`
  Attribution: European Commission, ESCO classification v1.2.1, CC BY 4.0.
  Source: https://esco.ec.europa.eu/
- **ACM Computing Classification System 2012** (`ACM_CCS`, v2012) — `ACM-CCS-Attribution`
  Attribution: Association for Computing Machinery (ACM) Computing Classification System (2012).
  Source: https://dl.acm.org/ccs
- **Anthropic Economic Index (Collaboration 5-Bucket Taxonomy & Calibration)** (`AEI`, v2025-2026) — `CC-BY-4.0`
  Attribution: Anthropic Economic Index (Anthropic), CC BY 4.0.
  Source: https://www.anthropic.com/economic-index
- **Getty Art & Architecture Thesaurus (incl. AAT-Taiwan zh labels)** (`AAT`, v2026-01) — `ODC-By-1.0`
  Attribution: Getty Research Institute, Art & Architecture Thesaurus (AAT), ODC-By 1.0.
  Source: https://www.getty.edu/research/tools/vocabularies/aat/
- **YAGO 4.6 Taxonomy Class Definitions & Multilingual Labels** (`YAGO_TAX`, v4.6) — `CC-BY-4.0`
  Attribution: YAGO 4.6 (Max Planck Institute for Informatics & Telecom Paris), CC BY 4.0.
  Source: https://yago-knowledge.org/
- **DevKG / W3C PROV-O / SKOS Metamodel Predicates** (`DEVKG`, v1.0) — `MIT`
  Attribution: Derived from DevKG metamodel and W3C SKOS/PROV-O vocabularies.
  Source: https://www.w3.org/TR/skos-reference/
- **Project Self-Built Extensions (ACM AI ~150, Facet V Stances 5, 2024/25 Jobs 36, YAGO Top300 Map)** (`CUSTOM_SELF`, v1.0.0) — `CC0-1.0`
  Attribution: Original project-authored supplement, CC0 1.0 Universal.
- **2024 Adult Compendium of Physical Activities (MET attributes)** (`COMPENDIUM`, v2024) — `CC-BY-4.0`
  Attribution: 2024 Adult Compendium of Physical Activities (Herrmann et al.).
  Source: https://pacompendium.com/
- **Library of Congress Subject Headings (Deduplicated / Non-subdivided)** (`LCSH`, v2026-snapshot) — `CC0-1.0`
  Attribution: Library of Congress Subject Headings (Public Domain / CC0).
  Source: https://id.loc.gov/authorities/subjects.html
- **Glottolog 5.4 (Families & Languages, Dialects Excluded)** (`GLOTTOLOG`, v5.4) — `CC-BY-4.0`
  Attribution: Glottolog 5.4 (Hammarstrom et al., Max Planck Institute for Evolutionary Anthropology), CC BY 4.0.
  Source: https://glottolog.org/
- **PeriodO Historical Period Gazetteer** (`PERIODO`, v2026-snapshot) — `CC0-1.0`
  Attribution: PeriodO Gazetteer of Historical Periods, CC0 1.0 Public Domain Dedication.
  Source: https://perio.do/

## 2. ShareAlike (CC BY-SA) Isolated Extension Packs

The following packages carry ShareAlike obligations and are distributed ONLY as separate attachable SQLite files (`human_kos_ext/*.sqlite` / `cn_rel.sqlite`), never merged into `human_kos_core.sqlite`:

- **UNESCO Thesaurus** (`UNESCO`, v2026-snapshot) — `CC-BY-SA-3.0-IGO`
  Attribution: UNESCO Thesaurus, CC BY-SA 3.0 IGO.
  Source: https://vocabularies.unesco.org/browser/thesaurus/
- **EDAM Ontology 1.25 (Active Terms Only)** (`EDAM`, v1.25) — `CC-BY-SA-4.0`
  Attribution: EDAM Ontology v1.25, CC BY-SA 4.0.
  Source: https://edamontology.org/
- **UNESCO ISCED-F 2013 Fields of Education and Training** (`ISCEDF`, v2013) — `CC-BY-SA-3.0-IGO`
  Attribution: UNESCO Institute for Statistics, ISCED-F 2013, CC BY-SA 3.0 IGO.
  Source: https://uis.unesco.org/
- **ConceptNet 5.7 (4 Action Relations + zh->en Synonym Bridge)** (`CONCEPTNET`, v5.7.0) — `CC-BY-SA-4.0`
  Attribution: ConceptNet 5.7 (Speer et al.), CC BY-SA 4.0.
  Source: https://conceptnet.io/
- **DILA Buddhist Studies Authority Databases (Person / Place / Time)** (`DILA`, v2026-11) — `CC-BY-SA-3.0`
  Attribution: Dharma Drum Institute of Liberal Arts (DILA) Buddhist Studies Authority Databases, CC BY-SA 3.0 Unported.
  Source: https://authority.dila.edu.tw/

## 3. Excluded Local-Only Sources (Not Present in Distributable Packages)

Under G-01 Policy `A_revised`, the following sources are excluded from `human_kos_full_dist.sqlite`, `human_kos_core.sqlite`, `human_kos_ext/*.sqlite`, and the D7 Release Kit:

- **GB/T 13745-2009 Classification and Code of Disciplines** (`GBT13745`) — `SAC-GBT-Copyright` (local research only)
- **WHO International Classification of Functioning, Disability and Health** (`WHO_ICF`) — `CC-BY-ND-3.0-IGO` (local research only)
- **ILO ISCO-08 International Standard Classification of Occupations** (`ISCO08`) — `ILO-Pre2023-Copyright` (local research only)
- **PhilPapers Category Taxonomy** (`PHILPAPERS`) — `PhilPapers-NoAITrain` (local research only)
- **China Occupational Classification Dictionary 2022 (Public Draft)** (`ZYFL_DADIAN`) — `Official-Public-Draft` (local research only)
