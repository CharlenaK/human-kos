#!/usr/bin/env python3
"""
Download & SHA-256 verify Human KOS release databases from GitHub Releases.

Zero external dependencies (Python 3.9+ stdlib only).
Uses `gh` CLI automatically when available (works seamlessly for private repos),
or falls back to HTTPS download with optional `GITHUB_TOKEN`.

Examples:
    # Download the core atlas only (~238 MB)
    python scripts/download_release.py --core-only

    # Download core + selected extension packs
    python scripts/download_release.py --packs aat dila cn_rel

    # Download everything (core + all 10 extension packs)
    python scripts/download_release.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_REPO = "CharlenaK/human-kos"
DEFAULT_TAG = "v1.0.0"

ALL_EXT_PACKS = [
    "aat",
    "cn_rel",
    "compendium",
    "dila",
    "edam",
    "glottolog",
    "iscedf",
    "lcsh",
    "periodo",
    "unesco",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_expected_hashes(repo_root: Path) -> dict[str, str]:
    manifest_path = repo_root / "docs" / "MANIFEST.json"
    if not manifest_path.exists():
        return {}
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for item in data.get("files", []):
        p = item.get("path", "")
        if p.endswith(".sqlite"):
            out[Path(p).name] = item["sha256"].lower()
    return out


def find_gh() -> str | None:
    gh = shutil.which("gh")
    if gh:
        return gh
    win_default = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if win_default.exists():
        return str(win_default)
    return None


def download_via_gh(gh: str, repo: str, tag: str, asset_name: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        gh,
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--pattern",
        asset_name,
        "--dir",
        str(dest.parent),
        "--clobber",
    ]
    subprocess.run(cmd, check=True)


def download_via_https(repo: str, tag: str, asset_name: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}/releases/download/{tag}/{asset_name}"
    headers = {"User-Agent": "human-kos-downloader/1.0"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download & verify Human KOS SQLite releases.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub owner/repo (default: {DEFAULT_REPO})")
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"Release tag (default: {DEFAULT_TAG})")
    parser.add_argument("--out-dir", default="data", help="Output directory (default: ./data)")
    parser.add_argument("--core-only", action="store_true", help="Download only human_kos_core.sqlite")
    parser.add_argument("--all", action="store_true", help="Download core + all 10 extension packs")
    parser.add_argument(
        "--packs",
        nargs="*",
        choices=ALL_EXT_PACKS,
        default=[],
        help="Specific extension packs to download alongside the core",
    )
    parser.add_argument("--skip-verify", action="store_true", help="Skip SHA-256 integrity verification")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out_dir)
    ext_dir = out_dir / "human_kos_ext"
    expected_hashes = load_expected_hashes(repo_root)

    targets: list[tuple[str, Path]] = [("human_kos_core.sqlite", out_dir / "human_kos_core.sqlite")]
    if args.all:
        selected = ALL_EXT_PACKS
    elif args.core_only:
        selected = []
    else:
        selected = args.packs

    for pack in selected:
        fname = f"{pack}.sqlite"
        targets.append((fname, ext_dir / fname))

    gh = find_gh()
    print(f"Repository : {args.repo} ({args.tag})")
    print(f"Transport  : {'GitHub CLI (gh)' if gh else 'Direct HTTPS'}")
    print(f"Output dir : {out_dir.resolve()}")
    print(f"Files      : {len(targets)}")

    for asset_name, dest in targets:
        expected = expected_hashes.get(asset_name)
        if dest.exists() and expected and not args.skip_verify:
            if sha256_file(dest) == expected:
                print(f"[SKIP] {asset_name} (already present & SHA-256 verified)")
                continue

        print(f"[GET ] {asset_name} -> {dest}")
        if gh:
            download_via_gh(gh, args.repo, args.tag, asset_name, dest)
        else:
            download_via_https(args.repo, args.tag, asset_name, dest)

        if not args.skip_verify and expected:
            actual = sha256_file(dest)
            if actual != expected:
                print(f"[FAIL] SHA-256 mismatch for {asset_name}: expected {expected}, got {actual}", file=sys.stderr)
                return 1
            print(f"[ OK ] {asset_name} SHA-256 verified")

    print("\nAll requested Human KOS databases are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
