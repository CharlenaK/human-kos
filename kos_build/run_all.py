"""Single-command build, packaging, and verification entry point for L1 KOS Database II (A8)."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kos_build.build.assemble_release_kit import assemble_release_kit
from kos_build.build.pack_profiles import (
    build_product_1_full_local,
    build_product_2_full_dist,
    build_product_3_core_and_ext,
)
from kos_build.config import (
    DATA_REPO_DIR,
    GATE_POLICIES,
    KOS_BUILD_DIR,
    OUTPUT_DIR,
    RELEASE_DIR,
    SCHEMA_DIR,
    SCHEMA_VERSION,
)
from kos_build.probes.test_schema_smoke import run_smoke


def _run_script(rel_script: str, uv_pkgs: list[str] | None = None, extra_args: list[str] | None = None) -> None:
    script = str(KOS_BUILD_DIR / rel_script)
    args = extra_args or []
    uv = shutil.which("uv")
    if uv_pkgs and uv:
        cmd = [uv, "run"]
        for pkg in uv_pkgs:
            cmd.extend(["--with", pkg])
        cmd.extend(["python", script, *args])
    else:
        cmd = [sys.executable, script, *args]
    subprocess.run(cmd, check=True)


def cmd_check_env() -> dict:
    con = sqlite3.connect(":memory:")
    opts = [r[0] for r in con.execute("PRAGMA compile_options")]
    con.close()
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "sqlite3": sqlite3.sqlite_version,
        "fts5": any("FTS5" in o for o in opts),
        "rtree": any("RTREE" in o for o in opts),
        "uv_available": bool(shutil.which("uv")),
        "sqlite3_cli_available": bool(shutil.which("sqlite3")),
        "data_repo_exists": DATA_REPO_DIR.exists(),
        "schema_dir_exists": SCHEMA_DIR.exists(),
        "gate_policies": GATE_POLICIES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Single-command build & verification pipeline for L1 Human KOS Database II."
    )
    parser.add_argument(
        "--step",
        choices=[
            "check-env",
            "schema-smoke",
            "extract-aux",
            "staging",
            "layout",
            "product1",
            "product2",
            "product3",
            "release-kit",
            "probe-a1-a9",
            "all",
        ],
        default="check-env",
        help="Pipeline stage to execute (`all` runs the full reproducible build through D7 + A1-A9).",
    )
    args = parser.parse_args(argv)

    if args.step == "check-env":
        print(json.dumps(cmd_check_env(), ensure_ascii=False, indent=2))
        return 0
    if args.step == "schema-smoke":
        print(json.dumps(run_smoke(), ensure_ascii=False, indent=2))
        return 0
    if args.step in ("extract-aux", "all"):
        _run_script("etl/extract_aei.py")
        _run_script("etl/extract_oa_cooccur.py", uv_pkgs=["pyarrow"])
        _run_script("etl/extract_yago_taxonomy.py", uv_pkgs=["opencc-python-reimplemented"])
    if args.step in ("staging", "all"):
        _run_script("etl/stage_sources.py", uv_pkgs=["opencc-python-reimplemented"])
    if args.step in ("layout", "all"):
        _run_script(
            "etl/compute_embeddings_layout.py",
            uv_pkgs=["umap-learn", "onnxruntime", "transformers", "scipy", "numpy"],
        )
    if args.step in ("product1", "all"):
        print(json.dumps(build_product_1_full_local(), ensure_ascii=False, indent=2))
    if args.step in ("product2", "all"):
        print(json.dumps(build_product_2_full_dist(), ensure_ascii=False, indent=2))
        _run_script("etl/build_cn_rel.py", uv_pkgs=["opencc-python-reimplemented"])
    if args.step in ("product3", "all"):
        print(json.dumps(build_product_3_core_and_ext(), ensure_ascii=False, indent=2))
        cn_src = OUTPUT_DIR / "cn_rel.sqlite"
        if cn_src.exists():
            shutil.copy2(cn_src, OUTPUT_DIR / "human_kos_ext" / "cn_rel.sqlite")
    if args.step in ("release-kit", "all"):
        print(json.dumps(assemble_release_kit(), ensure_ascii=False, indent=2))
    if args.step in ("probe-a1-a9", "all"):
        _run_script("probes/probe_a1_a9.py", extra_args=["--mode", "full_a1_a9"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
