#!/usr/bin/env python3
"""Build BUGate phase-1 release archives.

This creates the low-cost GitHub Release assets:

    dist/bugate-<version>.tar.gz
    dist/bugate-<version>.zip

Archives are built from git-tracked files and preserve symlinks. Run from a
clean tree for an actual release.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "dist"
DEFAULT_EPOCH = 0


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_version() -> str:
    codex = read_json(ROOT / ".codex-plugin" / "plugin.json").get("version")
    claude = read_json(ROOT / ".claude-plugin" / "plugin.json").get("version")
    if not codex or not claude:
        raise SystemExit("plugin manifests must both declare a version")
    if codex != claude:
        raise SystemExit(f"plugin manifest versions differ: codex={codex!r}, claude={claude!r}")
    return str(codex)


def is_dirty(include_untracked: bool) -> bool:
    args = ["status", "--porcelain"]
    if not include_untracked:
        args.append("--untracked-files=no")
    return bool(run_git(*args).stdout.strip())


def git_files(include_untracked: bool) -> list[Path]:
    args = ["ls-files", "-z"]
    if include_untracked:
        args.extend(["--cached", "--others", "--exclude-standard"])
    result = run_git(*args)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git ls-files failed")
    files: list[Path] = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        path = ROOT / raw
        if path.is_file() or path.is_symlink():
            files.append(Path(raw))
    return sorted(files, key=lambda p: p.as_posix())


def add_tar_entry(tar: tarfile.TarFile, rel: Path, arc_prefix: str, mtime: int) -> None:
    src = ROOT / rel
    arcname = f"{arc_prefix}/{rel.as_posix()}"
    st = os.lstat(src)
    if stat.S_ISLNK(st.st_mode):
        info = tarfile.TarInfo(arcname)
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(src)
        info.mode = 0o777
        info.mtime = mtime
        tar.addfile(info)
        return
    info = tar.gettarinfo(str(src), arcname)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = mtime
    with src.open("rb") as fh:
        tar.addfile(info, fh)


def add_zip_entry(zipf: zipfile.ZipFile, rel: Path, arc_prefix: str, mtime: int) -> None:
    src = ROOT / rel
    arcname = f"{arc_prefix}/{rel.as_posix()}"
    st = os.lstat(src)
    info = zipfile.ZipInfo(arcname)
    # ZIP timestamps cannot represent years before 1980.
    info.date_time = (1980, 1, 1, 0, 0, 0)
    if stat.S_ISLNK(st.st_mode):
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipf.writestr(info, os.readlink(src).encode("utf-8"))
        return
    mode = stat.S_IMODE(st.st_mode)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    zipf.writestr(info, src.read_bytes())


def build(version: str, out_dir: Path, *, include_untracked: bool, allow_dirty: bool) -> tuple[Path, Path]:
    if not allow_dirty and is_dirty(include_untracked):
        raise SystemExit("refusing to build release archives from a dirty tree; commit first or pass --allow-dirty")

    files = git_files(include_untracked)
    if not files:
        raise SystemExit("no files selected for archive")

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"bugate-{version}"
    tar_path = out_dir / f"{prefix}.tar.gz"
    zip_path = out_dir / f"{prefix}.zip"

    with tarfile.open(tar_path, "w:gz") as tar:
        for rel in files:
            add_tar_entry(tar, rel, prefix, DEFAULT_EPOCH)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for rel in files:
            add_zip_entry(zipf, rel, prefix, DEFAULT_EPOCH)

    return tar_path, zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=manifest_version(), help="release version (default: plugin manifest version)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory (default: dist)")
    parser.add_argument("--allow-dirty", action="store_true", help="allow uncommitted tracked changes")
    parser.add_argument("--include-untracked", action="store_true", help="include untracked non-ignored files (development only)")
    args = parser.parse_args(argv)

    version = args.version.strip().lstrip("v")
    if not version:
        raise SystemExit("version must not be empty")

    tar_path, zip_path = build(
        version,
        Path(args.out_dir).resolve(),
        include_untracked=args.include_untracked,
        allow_dirty=args.allow_dirty,
    )
    print(f"built {tar_path.relative_to(ROOT) if tar_path.is_relative_to(ROOT) else tar_path}")
    print(f"built {zip_path.relative_to(ROOT) if zip_path.is_relative_to(ROOT) else zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
