#!/usr/bin/env python3
"""Regression tests for deterministic BUGate release archives.

Each scenario uses its own temporary git repository, so the release safety
checks never depend on the state of the BUGate development worktree.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_release_archives.py"
VERSION = "0.4.1"


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="bugate-release-test-")
        self.repo = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(name, None)
        (self.repo / "scripts").mkdir()
        (self.repo / ".codex-plugin").mkdir()
        (self.repo / ".claude-plugin").mkdir()
        shutil.copyfile(BUILDER, self.repo / "scripts" / BUILDER.name)
        os.chmod(self.repo / "scripts" / BUILDER.name, 0o755)
        self.write_manifest(".codex-plugin", VERSION)
        self.write_manifest(".claude-plugin", VERSION)
        (self.repo / "README.md").write_text("# release fixture\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").symlink_to("README.md")
        (self.repo / "bin").mkdir()
        executable = self.repo / "bin" / "fixture-tool"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(executable, 0o755)
        (self.repo / ".gitignore").write_text("dist/\n", encoding="utf-8")

        self.git("init", "-q")
        self.git("config", "user.name", "BUGate Release Test")
        self.git("config", "user.email", "bugate-release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.commit("initial fixture")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @property
    def dist(self) -> Path:
        return self.repo / "dist"

    @property
    def asset_paths(self) -> tuple[Path, Path, Path]:
        prefix = f"bugate-{VERSION}"
        return (
            self.dist / f"{prefix}.tar.gz",
            self.dist / f"{prefix}.zip",
            self.dist / f"{prefix}.SHA256SUMS",
        )

    def write_manifest(self, directory: str, version: str) -> None:
        path = self.repo / directory / "plugin.json"
        path.write_text(
            json.dumps({"name": "bugate", "version": version}) + "\n",
            encoding="utf-8",
        )

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=True,
        )

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_builder(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / BUILDER.name), *args],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=False,
        )

    def assert_build_succeeded(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_clean_build_creates_three_assets_and_valid_checksums(self) -> None:
        self.dist.mkdir()
        (self.dist / "ignored-preview.txt").write_text("ignored\n", encoding="utf-8")

        result = self.run_builder("--version", VERSION)

        self.assert_build_succeeded(result)
        tar_path, zip_path, sums_path = self.asset_paths
        self.assertTrue(tar_path.is_file())
        self.assertTrue(zip_path.is_file())
        self.assertTrue(sums_path.is_file())
        expected_sums = "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (tar_path, zip_path)
        )
        self.assertEqual(sums_path.read_text(encoding="ascii"), expected_sums)

        prefix = f"bugate-{VERSION}/"
        with tarfile.open(tar_path, "r:gz") as archive:
            tar_members = archive.getmembers()
        with zipfile.ZipFile(zip_path) as archive:
            zip_infos = archive.infolist()
        tar_names = [member.name for member in tar_members]
        zip_names = [info.filename for info in zip_infos]
        self.assertIn(f"{prefix}README.md", tar_names)
        self.assertEqual(tar_names, zip_names)
        self.assertFalse(any("ignored-preview.txt" in name for name in tar_names))
        tar_by_name = {member.name: member for member in tar_members}
        zip_by_name = {info.filename: info for info in zip_infos}
        self.assertTrue(tar_by_name[f"{prefix}CLAUDE.md"].issym())
        self.assertEqual(tar_by_name[f"{prefix}CLAUDE.md"].linkname, "README.md")
        self.assertEqual(tar_by_name[f"{prefix}bin/fixture-tool"].mode, 0o755)
        self.assertEqual(
            stat.S_IFMT(zip_by_name[f"{prefix}CLAUDE.md"].external_attr >> 16),
            stat.S_IFLNK,
        )
        self.assertEqual(
            stat.S_IMODE(zip_by_name[f"{prefix}bin/fixture-tool"].external_attr >> 16),
            0o755,
        )
        self.assertTrue(
            all(
                member.mtime == 0
                and member.uid == 0
                and member.gid == 0
                and member.uname == ""
                and member.gname == ""
                for member in tar_members
            )
        )
        self.assertTrue(
            all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in zip_infos)
        )

    def test_repeated_build_is_byte_identical(self) -> None:
        first = self.run_builder()
        self.assert_build_succeeded(first)
        original = {path.name: path.read_bytes() for path in self.asset_paths}

        readme = self.repo / "README.md"
        os.utime(readme, (1_900_000_000, 1_900_000_000))
        second = self.run_builder()

        self.assert_build_succeeded(second)
        rebuilt = {path.name: path.read_bytes() for path in self.asset_paths}
        self.assertEqual(rebuilt, original)

    def test_gzip_header_mtime_is_zero(self) -> None:
        result = self.run_builder()
        self.assert_build_succeeded(result)

        header = self.asset_paths[0].read_bytes()[:10]
        self.assertEqual(header[:3], b"\x1f\x8b\x08")
        self.assertEqual(int.from_bytes(header[4:8], "little"), 0)

    def test_tracked_dirty_tree_is_rejected_by_default(self) -> None:
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")

        result = self.run_builder()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty tree", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_untracked_dirty_tree_is_rejected_by_default(self) -> None:
        (self.repo / "untracked.txt").write_text("not ignored\n", encoding="utf-8")

        result = self.run_builder()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty tree", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_development_preview_can_explicitly_include_untracked_files(self) -> None:
        (self.repo / "untracked.txt").write_text("preview only\n", encoding="utf-8")

        result = self.run_builder("--allow-dirty", "--include-untracked")

        self.assert_build_succeeded(result)
        with tarfile.open(self.asset_paths[0], "r:gz") as archive:
            self.assertIn(f"bugate-{VERSION}/untracked.txt", archive.getnames())

    def test_explicit_version_must_match_both_manifests(self) -> None:
        result = self.run_builder("--version", "0.4.2")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match both plugin manifests", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_manifest_version_mismatch_is_rejected(self) -> None:
        self.write_manifest(".claude-plugin", "0.4.2")
        self.commit("make manifest versions inconsistent")

        result = self.run_builder()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugin manifest versions differ", result.stderr)
        self.assertFalse(self.dist.exists())


if __name__ == "__main__":
    unittest.main()
