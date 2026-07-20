#!/usr/bin/env python3
"""Build deterministic BUGate release archives.

This creates the low-cost GitHub Release assets:

    dist/bugate-<version>.tar.gz
    dist/bugate-<version>.zip
    dist/bugate-<version>.SHA256SUMS

Archives are built from git-tracked files and preserve symlinks. Run from a
clean tree for an actual release.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "dist"
DEFAULT_EPOCH = 0
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ArchiveEntry:
    path: Path
    mode: int


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
    codex_value = read_json(ROOT / ".codex-plugin" / "plugin.json").get("version")
    claude_value = read_json(ROOT / ".claude-plugin" / "plugin.json").get("version")
    if not isinstance(codex_value, str) or not isinstance(claude_value, str):
        raise SystemExit("plugin manifests must both declare a version")
    codex = codex_value.strip()
    claude = claude_value.strip()
    if not codex or not claude:
        raise SystemExit("plugin manifests must both declare a version")
    if codex != claude:
        raise SystemExit(f"plugin manifest versions differ: codex={codex!r}, claude={claude!r}")
    return codex


def is_dirty() -> bool:
    result = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git status failed")
    return bool(result.stdout.strip())


def git_modes() -> dict[Path, int]:
    result = run_git("ls-files", "--stage", "-z")
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git ls-files --stage failed")

    modes: dict[Path, int] = {}
    for raw in result.stdout.split("\0"):
        if not raw or "\t" not in raw:
            continue
        metadata, name = raw.split("\t", 1)
        fields = metadata.split()
        if len(fields) != 3 or fields[2] != "0":
            continue
        modes[Path(name)] = 0o755 if fields[0] == "100755" else 0o644
    return modes


def git_files(include_untracked: bool) -> list[ArchiveEntry]:
    args = ["ls-files", "-z", "--cached"]
    if include_untracked:
        args.extend(["--others", "--exclude-standard"])
    result = run_git(*args)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git ls-files failed")

    tracked_modes = git_modes()
    files: list[ArchiveEntry] = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        rel = Path(raw)
        path = ROOT / rel
        if not (path.is_file() or path.is_symlink()):
            continue
        st = os.lstat(path)
        mode = tracked_modes.get(rel)
        if mode is None:
            mode = 0o755 if stat.S_IMODE(st.st_mode) & 0o111 else 0o644
        files.append(ArchiveEntry(rel, mode))
    return sorted(files, key=lambda entry: entry.path.as_posix())


def add_tar_entry(
    tar: tarfile.TarFile, entry: ArchiveEntry, arc_prefix: str, mtime: int
) -> None:
    src = ROOT / entry.path
    arcname = f"{arc_prefix}/{entry.path.as_posix()}"
    st = os.lstat(src)
    info = tarfile.TarInfo(arcname)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = mtime
    if stat.S_ISLNK(st.st_mode):
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(src)
        info.mode = 0o777
        tar.addfile(info)
        return
    info.type = tarfile.REGTYPE
    info.mode = entry.mode
    info.size = st.st_size
    with src.open("rb") as fh:
        tar.addfile(info, fh)


def add_zip_entry(zipf: zipfile.ZipFile, entry: ArchiveEntry, arc_prefix: str) -> None:
    src = ROOT / entry.path
    arcname = f"{arc_prefix}/{entry.path.as_posix()}"
    st = os.lstat(src)
    info = zipfile.ZipInfo(arcname)
    info.date_time = ZIP_EPOCH
    if stat.S_ISLNK(st.st_mode):
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        data = os.readlink(src).encode("utf-8")
        zipf.writestr(
            info,
            data,
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        )
        return
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | entry.mode) << 16
    zipf.writestr(
        info,
        src.read_bytes(),
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(path: Path, archives: tuple[Path, Path]) -> None:
    lines = [f"{file_sha256(archive)}  {archive.name}\n" for archive in archives]
    path.write_bytes("".join(lines).encode("ascii"))


def build(
    version: str,
    out_dir: Path,
    *,
    include_untracked: bool,
    allow_dirty: bool,
) -> tuple[Path, Path, Path]:
    if not allow_dirty and is_dirty():
        raise SystemExit("refusing to build release archives from a dirty tree; commit first or pass --allow-dirty")

    files = git_files(include_untracked)
    if not files:
        raise SystemExit("no files selected for archive")

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"bugate-{version}"
    tar_path = out_dir / f"{prefix}.tar.gz"
    zip_path = out_dir / f"{prefix}.zip"
    sums_path = out_dir / f"{prefix}.SHA256SUMS"

    with tar_path.open("wb") as raw_tar:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_tar,
            mtime=DEFAULT_EPOCH,
        ) as compressed_tar:
            with tarfile.open(
                fileobj=compressed_tar, mode="w", format=tarfile.PAX_FORMAT
            ) as tar:
                for entry in files:
                    add_tar_entry(tar, entry, prefix, DEFAULT_EPOCH)

    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zipf:
        for entry in files:
            add_zip_entry(zipf, entry, prefix)

    write_checksums(sums_path, (tar_path, zip_path))
    return tar_path, zip_path, sums_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="release version (default: plugin manifest version)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory (default: dist)")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow tracked or untracked changes (development preview only)",
    )
    parser.add_argument("--include-untracked", action="store_true", help="include untracked non-ignored files (development only)")
    args = parser.parse_args(argv)

    required_version = manifest_version()
    requested_version = args.version.strip() if args.version is not None else required_version
    version = requested_version[1:] if requested_version.startswith("v") else requested_version
    if not version:
        raise SystemExit("version must not be empty")
    if version != required_version:
        raise SystemExit(
            f"requested version {version!r} does not match both plugin manifests "
            f"({required_version!r})"
        )

    tar_path, zip_path, sums_path = build(
        version,
        Path(args.out_dir).resolve(),
        include_untracked=args.include_untracked,
        allow_dirty=args.allow_dirty,
    )
    print(f"built {tar_path.relative_to(ROOT) if tar_path.is_relative_to(ROOT) else tar_path}")
    print(f"built {zip_path.relative_to(ROOT) if zip_path.is_relative_to(ROOT) else zip_path}")
    print(f"built {sums_path.relative_to(ROOT) if sums_path.is_relative_to(ROOT) else sums_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
