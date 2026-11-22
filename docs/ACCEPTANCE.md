# Quality & Verification Report (v1.0.0)

This report summarizes the automated verification benchmarks executed on the released **Human KOS v1.0.0** artifacts (`human_kos_core.sqlite` and all 10 `human_kos_ext/*.sqlite` packs).

---

## Artifact Overview

| Package | License Tier | Size (MB) | Nodes / Rows | Aliases | Edges | Offline Vectors |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| `human_kos_core.sqlite` | Permissive (`dist_core`) | **237.54** | 84,552 | 266,652 | 113,789 | 66,571 |
| `human_kos_ext/aat.sqlite` | Permissive (`ODC-By-1.0`) | 116.23 | 57,212 | 210,843 | 57,203 | — |
| `human_kos_ext/compendium.sqlite` | Permissive (`CC-BY-4.0`) | 2.29 | 1,134 | 13 | 1,133 | 1,134 |
| `human_kos_ext/glottolog.sqlite` | Permissive (`CC-BY-4.0`) | 15.72 | 12,713 | 933 | 12,712 | — |
| `human_kos_ext/lcsh.sqlite` | Permissive (`CC0-1.0`) | 335.02 | 275,231 | 303,431 | 430,034 | — |
| `human_kos_ext/periodo.sqlite` | Permissive (`CC0-1.0`) | 11.91 | 9,538 | 1,142 | 9,537 | — |
| `human_kos_ext/cn_rel.sqlite` | ShareAlike (`CC-BY-SA-4.0`) | 116.71 | 189,095 rels | 349,777 zh→en | — | — |
| `human_kos_ext/dila.sqlite` | ShareAlike (`CC-BY-SA-3.0`) | 168.45 | 109,096 | 132,078 | 109,094 | — |
| `human_kos_ext/edam.sqlite` | ShareAlike (`CC-BY-SA-4.0`) | 5.34 | 2,360 | 2,720 | 3,439 | 2,360 |
| `human_kos_ext/iscedf.sqlite` | ShareAlike (`CC-BY-SA-3.0-IGO`) | 0.72 | 219 | 30 | 218 | 219 |
| `human_kos_ext/unesco.sqlite` | ShareAlike (`CC-BY-SA-3.0-IGO`) | 8.92 | 4,501 | 4,019 | 10,791 | 4,501 |

---

## Automated Verification Suite (A1–A9)

All 9 automated verification probes pass on the final release artifacts:

| Check | Scope | Target | Measured Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **A1** | Structural integrity & provenance FK coverage | 100% valid `source_id`, non-empty `breadcrumb_path` | **100.0%** (0 violations across all 84,552 core nodes & 10 ext packs) | PASS |
| **A2** | Taxonomic connectivity | 0 orphan nodes | **0 orphans** | PASS |
| **A3** | Bilingual FTS5 BM25 recall speed & Chinese hit rate | P95 `< 20 ms`, strong Chinese Top-20 recall | **P50 = 1.297 ms, P95 = 7.642 ms** · Chinese Top-20 hit **49 / 50 (98.0%)** | PASS |
| **A4** | Multi-hop graph traversal & cross-field bridges | Non-root 1–2 hop paths with IC ≥ 1.5 | **50 / 50 (100.0%)** (`oa:cooccur` 30,211 + `skos:closeMatch` 4,088) | PASS |
| **A5** | Core package footprint budget | 30k–100k nodes, `≤ 500 MB` | **84,552 nodes / 237.54 MB** | PASS |
| **A6** | Facet isolation | 0 cross-facet contamination in `kos_facet_pool` | **0 violations** across all 5 facets | PASS |
| **A7** | License isolation & attribution compliance | Zero `local_only` and zero `CC BY-SA` inside `human_kos_core.sqlite` | **0 restricted/SA records** in core; all 5 SA packs strictly isolated | PASS |
| **A8** | Deterministic reproducibility | Single-command offline rebuild with fixed seed (`random_state=42`) | Verified end-to-end | PASS |
| **A9** | Zero-dependency standalone execution | Runs cleanly via bare `sqlite3 -readonly` and Python stdlib | **4 / 4** standalone queries pass | PASS |

---

## Real-World Cross-Domain Graph Connectivity Test

In an additional offline evaluation against 50 randomly sampled real-world bilingual dialogues (strictly read-only, privacy-filtered, zero personal data retained), the upgraded `human_kos_core.sqlite` achieved:

- **98.0% (49 / 50)** non-root 1–2 hop graph bridge resolution (`IC ≥ 1.5`), including **92.0% (46 / 50)** genuine cross-field bridges across distinct OpenAlex top-level fields.
- **76.0% (38 / 50)** primary anchoring onto the core scholarly discipline tree (`OA_TREE`).
- Confirmed design guidance for downstream AI/RAG integrators: L1 supplies high-recall candidate pools and multi-hop cross-domain topology; downstream systems should retrieve Top-15–20 candidates per facet and apply a lightweight reranker or LLM selection step rather than relying solely on single-shot raw cosine Top-1.
