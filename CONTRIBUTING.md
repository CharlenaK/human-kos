# Contributing to Human KOS

Thank you for helping improve **Human KOS**! Whether you are fixing a Chinese/English translation, suggesting an authoritative open dataset, reporting a broken link, or building a companion tool on top of the atlas, your contribution is welcome.

---

## Ways to Contribute

- **Improve bilingual labels & aliases**: Report missing or awkward Chinese/English labels, or open a Pull Request updating the custom seed tables in `kos_build/etl/custom_tables.py`.
- **Report data quality issues**: If you spot an orphan concept, noisy cross-field bridge, or unexpected facet placement, open an Issue with the `node_id` and expected behavior.
- **Suggest new open-access vocabularies**: Open an Issue naming the upstream dataset, its exact SPDX license, and which of the 5 dimensions it strengthens.
- **Build companion apps & client adapters**: Check out our companion reference app [**`human-kos-example`**](https://github.com/CharlenaK/human-kos-example) if you want to build a viewer, search plugin, or AI client integration — keeping this repository focused purely on the standalone read-only Atlas.

---

## Core Boundaries (Please Read First)

1. **Keep the Atlas client-agnostic & read-only**: Do not add client-specific UI columns or personal chat history tables to the L1 SQLite schema.
2. **Respect license isolation strictly**:
   - `human_kos_core.sqlite` accepts **permissive open data only** (CC0, Public Domain, CC BY, ODC-By, MIT).
   - Datasets with **ShareAlike (`CC BY-SA`)** obligations must be packaged as isolated optional extension files (`human_kos_ext/*.sqlite`) with full attribution in [`LICENSE/NOTICE.md`](./LICENSE/NOTICE.md).
   - Non-redistributable / `local_only` sources must never be included in release packages.
3. **Never commit large `.sqlite` binaries or personal data**: Prebuilt databases are distributed exclusively through GitHub Releases with verified `SHA256SUMS.txt`.

---

## Verification Before Opening a PR

If your change touches the offline build pipeline (`kos_build/`):

```bash
python kos_build/run_all.py --step schema-smoke
python kos_build/run_all.py --step probe-a1-a9
```

Make sure all acceptance checks (A1–A9) remain green and no local absolute paths or credentials appear in your diff.
