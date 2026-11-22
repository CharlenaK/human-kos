# Licensing & Attribution Guide

Human KOS combines original schema/build code with authoritative open-access knowledge organization systems from around the world. To keep downstream reuse simple and legally unambiguous, **every artifact in this release is strictly separated by license tier**.

---

## At a Glance

| Layer | Files | License | Can I use commercially? | ShareAlike required? |
| :--- | :--- | :--- | :---: | :---: |
| **Code, Schema & Docs** | `kos_build/`, `scripts/`, `docs/`, `assets/` | [MIT](./MIT.txt) | Yes | No |
| **Core Knowledge Atlas** | `human_kos_core.sqlite` | Permissive Open Data ([CC0](./CC0-1.0.txt) · [CC BY 4.0](./CC-BY-4.0.txt) · [ODC-By 1.0](./ODC-By-1.0.txt) · [Public Domain / ACM CCS](./UPSTREAM-NOTICES.md)) | Yes (with attribution) | **No** |
| **Permissive Extension Packs** | `aat.sqlite`, `compendium.sqlite`, `glottolog.sqlite`, `lcsh.sqlite`, `periodo.sqlite` | [CC0 1.0](./CC0-1.0.txt) / [CC BY 4.0](./CC-BY-4.0.txt) / [ODC-By 1.0](./ODC-By-1.0.txt) | Yes (with attribution) | **No** |
| **ShareAlike Extension Packs** | `cn_rel.sqlite`, `dila.sqlite`, `edam.sqlite`, `iscedf.sqlite`, `unesco.sqlite` | [CC BY-SA 4.0](./CC-BY-SA-4.0.txt) / [CC BY-SA 3.0](./CC-BY-SA-3.0.txt) / [CC BY-SA 3.0 IGO](./CC-BY-SA-3.0-IGO.txt) | Yes (with attribution & ShareAlike) | **Yes** (isolated pack only) |

---

## Core Design Rule: Hard License Isolation

1. **Zero ShareAlike contamination in the Core**: `human_kos_core.sqlite` contains **only** permissive sources (`dist_core`). No `CC BY-SA` records are merged into the core database.
2. **Plug-in ShareAlike packs**: All `CC BY-SA` vocabularies (`ConceptNet`, `DILA`, `EDAM`, `UNESCO Thesaurus`, `ISCED-F`) are shipped strictly as separate, optional `.sqlite` files under `human_kos_ext/`. You attach them at query time only if their license fits your project.
3. **Zero restricted/local-only sources**: Sources with `NoDerivatives` (`CC BY-ND`), `ai-train=no`, draft status, or restrictive copyright (GB/T 13745-2009, WHO ICF, ILO ISCO-08, PhilPapers, ZYFL 2022 draft) are **completely excluded** from all distributed files in this repository and its Releases.
4. **Row-level provenance**: Every concept, alias, and relation inside every `.sqlite` database links directly to the `kos_provenance` table with its exact upstream `source_id`, `version`, `license_spdx`, and `attribution_text`.

---

## Full License Texts in This Directory

- [`MIT.txt`](./MIT.txt) — Repository code, scripts, schema DDL, and documentation
- [`CC0-1.0.txt`](./CC0-1.0.txt) — OpenAlex, PeriodO, LCSH, and original project supplements
- [`CC-BY-4.0.txt`](./CC-BY-4.0.txt) — O*NET 31.0, ESCO v1.2.1, YAGO 4.6, Anthropic Economic Index, Adult Compendium 2024, Glottolog 5.4
- [`ODC-By-1.0.txt`](./ODC-By-1.0.txt) — Getty Art & Architecture Thesaurus (AAT)
- [`CC-BY-SA-4.0.txt`](./CC-BY-SA-4.0.txt) — ConceptNet 5.7 (`cn_rel.sqlite`), EDAM 1.25 (`edam.sqlite`)
- [`CC-BY-SA-3.0.txt`](./CC-BY-SA-3.0.txt) — DILA Buddhist Studies Authority Databases (`dila.sqlite`)
- [`CC-BY-SA-3.0-IGO.txt`](./CC-BY-SA-3.0-IGO.txt) — UNESCO Thesaurus (`unesco.sqlite`), UNESCO ISCED-F 2013 (`iscedf.sqlite`)
- [`UPSTREAM-NOTICES.md`](./UPSTREAM-NOTICES.md) — Special public-domain & attribution terms (UN ICATUS 2016, US BLS ATUS 2025, ACM CCS 2012)
- [`NOTICE.md`](./NOTICE.md) — Complete source-by-source attribution manifest
