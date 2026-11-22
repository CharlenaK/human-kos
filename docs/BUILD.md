# Offline Build & Developer Guide

This document explains the repository's reproducible build pipeline under [`kos_build/`](../kos_build/). End users who simply want to query or integrate the knowledge atlas do not need to rebuild anything — download the prebuilt `.sqlite` files from **GitHub Releases** instead.

---

## Directory Layout

```text
kos_build/
├── config.py                     # Relative paths, license tiers & gate policies
├── run_all.py                    # Single-command end-to-end build entry
├── schema/
│   ├── kos_schema.sql            # Unified 8-table schema + FTS5 + R*Tree + views
│   ├── aei_calibration.sql       # Anthropic Economic Index 5-bucket calibration DDL
│   └── cn_rel_schema.sql         # ConceptNet 5.7 isolated CC BY-SA 4.0 DDL
├── etl/
│   ├── text_utils.py             # Normalization, CJK FTS5 bigram tokenizer & breadcrumbs
│   ├── custom_tables.py          # Curated supplements (AI/LLM, cognitive stances, 2024–25 roles)
│   ├── extract_aei.py            # AEI distribution extractor
│   ├── extract_oa_cooccur.py     # OpenAlex topic co-occurrence graph extractor
│   ├── extract_yago_taxonomy.py  # YAGO 4.6 class definitions & multilingual label extractor
│   ├── stage_sources.py          # Unified staging ETL + cross-source skos:closeMatch bridges
│   ├── compute_embeddings_layout.py  # Offline intfloat/multilingual-e5-small int8 + UMAP + Voronoi
│   └── build_cn_rel.py           # ConceptNet 5.7 4-relation & zh->en synonym bridge builder
├── build/
│   ├── pack_profiles.py          # License-isolated packer (core + 10 extension packs + NOTICE)
│   └── assemble_release_kit.py   # Release kit assembler & SHA-256 manifest generator
└── probes/
    ├── test_schema_smoke.py      # Bare sqlite3 CLI & Python schema smoke tests
    └── probe_a1_a9.py            # Automated A1–A9 quality, latency & compliance test suite
```

---

## Prerequisites for Full Offline Rebuild

1. **Python `>= 3.11`** (with standard SQLite FTS5 and R\*Tree compiled in) and [`uv`](https://docs.astral.sh/uv/) for isolated dependency execution (`pyarrow`, `opencc-python-reimplemented`, `umap-learn`, `onnxruntime`, `transformers`).
2. **Upstream open datasets** placed read-only under `Data Repository/` relative to the build root (see [`LICENSE/NOTICE.md`](../LICENSE/NOTICE.md) for upstream URLs and versions).
3. **Offline embedding weights**: `intfloat/multilingual-e5-small` ONNX model files (`model.onnx`, `tokenizer.json`, `sentencepiece.bpe.model`, `config.json`, `tokenizer_config.json`, `special_tokens_map.json`) cached locally under `Data Repository/_derived/models/multilingual-e5-small/`.

---

## Rebuilding

```bash
# Run the complete deterministic pipeline (UMAP random_state=42)
python kos_build/run_all.py --step all
```

Individual stages can also be run independently:

```bash
python kos_build/run_all.py --step check-env
python kos_build/run_all.py --step schema-smoke
python kos_build/run_all.py --step product3
python kos_build/run_all.py --step release-kit
python kos_build/run_all.py --step probe-a1-a9
```

---

## Runtime & SQLite Compatibility

- All generated `.sqlite` artifacts require **zero external SQLite extensions** (`sqlite-vec`, ICU, etc. are not used).
- Tested directly with bare `sqlite3 -readonly` CLI and Python's standard-library `sqlite3` module (SQLite 3.35+).
