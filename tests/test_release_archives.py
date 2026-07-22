#!/usr/bin/env python3
"""Integration tests for deterministic, manifest-bearing release archives."""
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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUILDER = SCRIPTS / "build_release_archives.py"
CONTRACT = SCRIPTS / "bugate_install_contract.py"
LEGACY = SCRIPTS / "bugate_legacy_manifest.py"
VERSION = "0.4.2"
LEGACY_TAGS = ("v0.3.0", "v0.3.1", "v0.3.2", "v0.3.4", "v0.3.5", "v0.4.0", "v0.4.1")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import build_release_archives as builder_module  # noqa: E402


SYNTHETIC_INSTALLER = '''\
KIT_DIRS = ["scripts", "bin", ".shared/skills/bugate", ".shared/skills/bugate-full-check", ".shared/skills/bugate-import"]
KIT_FILES = ["docs/SETUP-OPTIONAL.md"]
CODEX_AGENTS_KIT_REL = ".shared/skills/bugate/adapters/codex/agents"
GITIGNORE_BEGIN = "# >>> BUGate imported-mode ignores (managed by bugate_init.py) >>>"
GITIGNORE_END = "# <<< BUGate imported-mode ignores <<<"
GITIGNORE_BLOCK = "{begin}\\n/{vendor_dir}/plan.lock\\n{end}\\n"
_ROOT_SNIPPET = "ROOT=fixture; "

def _cmd(vendor_dir: str, script: str, *args: str) -> str:
    tail = (" " + " ".join(args)) if args else ""
    return _ROOT_SNIPPET + f"python3 {vendor_dir}/scripts/{script}{tail}"

def _bin_cmd(vendor_dir: str, command: str, *args: str) -> str:
    tail = (" " + " ".join(args)) if args else ""
    return _ROOT_SNIPPET + f"{vendor_dir}/bin/{command}{tail}"

def hook_blocks(vendor_dir: str, runtime: str) -> dict:
    return {
        "PreToolUse": [{"matcher": "apply_patch" if runtime == "codex" else "Edit|Write", "hooks": [{"type": "command", "command": _cmd(vendor_dir, "check_bugate.py")}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _cmd(vendor_dir, "bugate_prompt_reminder.py")}]}],
    }

def link_skills(target, vendor_dir, dry, force):
    skill_names = ("bugate", "bugate-full-check", "bugate-import")
    runtimes = ((".claude", "claude"), (".agents", "agents"), (".codex", "codex"))
    return skill_names, runtimes
'''


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="bugate-release-test-")
        self.repo = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(name, None)

        for directory in (
            "scripts",
            "bin",
            ".codex-plugin",
            ".claude-plugin",
            ".shared/skills/bugate/adapters/codex/agents",
            ".shared/skills/bugate-full-check",
            ".shared/skills/bugate-import",
            "docs",
        ):
            (self.repo / directory).mkdir(parents=True, exist_ok=True)
        for source in (BUILDER, CONTRACT, LEGACY):
            shutil.copyfile(source, self.repo / "scripts" / source.name)
            os.chmod(self.repo / "scripts" / source.name, 0o755 if source == BUILDER else 0o644)
        (self.repo / "scripts" / "bugate_init.py").write_text(
            SYNTHETIC_INSTALLER, encoding="utf-8"
        )
        for name in ("check_bugate.py", "bugate_prompt_reminder.py"):
            (self.repo / "scripts" / name).write_text(f"# {name}\n", encoding="utf-8")
        for skill in ("bugate", "bugate-full-check", "bugate-import"):
            path = self.repo / ".shared" / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {skill}\n", encoding="utf-8")
        for name in ("brief-gate.toml", "inventory-gate.toml", "testability-gate.toml"):
            (self.repo / ".shared/skills/bugate/adapters/codex/agents" / name).write_text(
                f'name = "{name}"\n', encoding="utf-8"
            )
        (self.repo / "docs/SETUP-OPTIONAL.md").write_text("# setup\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# release fixture\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").symlink_to("README.md")
        executable = self.repo / "bin" / "fixture-tool"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(executable, 0o755)
        (self.repo / ".gitignore").write_text("dist/\n", encoding="utf-8")

        self.git("init", "-q")
        self.git("config", "user.name", "BUGate Release Test")
        self.git("config", "user.email", "bugate-release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        for tag in LEGACY_TAGS:
            self.write_plugin_manifests(tag.removeprefix("v"))
            self.commit(f"synthetic {tag}")
            self.git("tag", "-a", tag, "-m", f"synthetic {tag}")

        self.write_plugin_manifests(VERSION)
        (self.repo / "scripts/bugate_update.py").write_text(
            (
                "import bugate_install_contract\n"
                "import bugate_update_engine\n"
                "import bugate_update_source\n"
                "import bugate_update_transaction\n"
                f'UPDATER_VERSION = "{VERSION}"\n'
                "def build_parser():\n    return None\n"
                "def main(argv=None):\n    return 0\n"
            ),
            encoding="utf-8",
        )
        for name in (
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_update_transaction.py",
            "bugate_core.py",
        ):
            (self.repo / "scripts" / name).write_text(
                f'"""Synthetic {name} worker."""\n', encoding="utf-8"
            )
        wrapper = self.repo / "bin/bugate-update"
        wrapper.write_bytes(builder_module.contract.BUGATE_UPDATE_WRAPPER_BYTES)
        os.chmod(wrapper, 0o755)
        self.commit("current synthetic release")

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

    def write_plugin_manifests(self, version: str) -> None:
        for directory in (".codex-plugin", ".claude-plugin"):
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
        self.git("commit", "-q", "--allow-empty", "-m", message)

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

    def archive_names(self) -> tuple[list[str], list[str]]:
        with tarfile.open(self.asset_paths[0], "r:gz") as archive:
            tar_names = [member.name.rstrip("/") for member in archive.getmembers()]
        with zipfile.ZipFile(self.asset_paths[1]) as archive:
            zip_names = [info.filename.rstrip("/") for info in archive.infolist()]
        return tar_names, zip_names

    def manifest_from_tar(self) -> dict:
        prefix = f"bugate-{VERSION}/"
        with tarfile.open(self.asset_paths[0], "r:gz") as archive:
            stream = archive.extractfile(prefix + ".bugate-release/manifest.json")
            self.assertIsNotNone(stream)
            return json.loads(stream.read())

    def test_clean_build_creates_atomic_assets_manifest_and_checksums(self) -> None:
        self.dist.mkdir()
        (self.dist / "ignored-preview.txt").write_text("ignored\n", encoding="utf-8")

        result = self.run_builder("--version", VERSION)

        self.assert_build_succeeded(result)
        tar_path, zip_path, sums_path = self.asset_paths
        self.assertTrue(all(path.is_file() for path in self.asset_paths))
        expected_sums = "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (tar_path, zip_path)
        )
        self.assertEqual(sums_path.read_text(encoding="ascii"), expected_sums)

        tar_names, zip_names = self.archive_names()
        self.assertEqual(tar_names, zip_names)
        prefix = f"bugate-{VERSION}/"
        self.assertIn(prefix + "README.md", tar_names)
        self.assertIn(prefix + ".bugate-release/manifest.json", tar_names)
        for tag in LEGACY_TAGS:
            self.assertIn(prefix + f".bugate-release/legacy/{tag}.json", tar_names)
        self.assertFalse(any("ignored-preview.txt" in name for name in tar_names))

        manifest = self.manifest_from_tar()
        self.assertEqual(manifest["bugate_version"], VERSION)
        self.assertEqual(manifest["archive_prefix"], f"bugate-{VERSION}")
        self.assertEqual(
            manifest["updater_minimum_version"],
            builder_module.contract.UPDATER_PROTOCOL_MINIMUM_VERSION,
        )
        self.assertFalse(builder_module._publish_state_dir(self.dist).exists())
        self.assertEqual(
            {item["path"] for item in manifest["archive_inventory"]},
            {name.removeprefix(prefix) for name in tar_names},
        )
        updater = next(
            item
            for item in manifest["archive_inventory"]
            if item["path"] == "scripts/bugate_update.py"
        )
        self.assertEqual(
            updater["roles"], ["installable_payload", "release_metadata"]
        )

    def test_symlink_and_executable_modes_are_preserved_in_both_formats(self) -> None:
        result = self.run_builder()
        self.assert_build_succeeded(result)
        prefix = f"bugate-{VERSION}/"
        with tarfile.open(self.asset_paths[0], "r:gz") as archive:
            tar_by_name = {member.name.rstrip("/"): member for member in archive.getmembers()}
        with zipfile.ZipFile(self.asset_paths[1]) as archive:
            zip_by_name = {info.filename.rstrip("/"): info for info in archive.infolist()}
        self.assertTrue(tar_by_name[prefix + "CLAUDE.md"].issym())
        self.assertEqual(tar_by_name[prefix + "CLAUDE.md"].linkname, "README.md")
        self.assertEqual(tar_by_name[prefix + "bin/bugate-update"].mode, 0o755)
        self.assertEqual(
            stat.S_IFMT(zip_by_name[prefix + "CLAUDE.md"].external_attr >> 16),
            stat.S_IFLNK,
        )
        self.assertEqual(
            stat.S_IMODE(zip_by_name[prefix + "bin/bugate-update"].external_attr >> 16),
            0o755,
        )

    def test_repeated_build_is_byte_identical(self) -> None:
        first = self.run_builder()
        self.assert_build_succeeded(first)
        original = {path.name: path.read_bytes() for path in self.asset_paths}
        os.utime(self.repo / "README.md", (1_900_000_000, 1_900_000_000))
        second = self.run_builder()
        self.assert_build_succeeded(second)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.asset_paths}, original
        )

    def test_gzip_header_and_archive_timestamps_are_deterministic(self) -> None:
        result = self.run_builder()
        self.assert_build_succeeded(result)
        header = self.asset_paths[0].read_bytes()[:10]
        self.assertEqual(header[:3], b"\x1f\x8b\x08")
        self.assertEqual(int.from_bytes(header[4:8], "little"), 0)
        with tarfile.open(self.asset_paths[0], "r:gz") as archive:
            self.assertTrue(all(member.mtime == 0 for member in archive.getmembers()))
        with zipfile.ZipFile(self.asset_paths[1]) as archive:
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))

    def test_tracked_dirty_tree_is_rejected_without_partial_assets(self) -> None:
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty tree", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_untracked_dirty_tree_is_rejected_without_partial_assets(self) -> None:
        (self.repo / "untracked.txt").write_text("not ignored\n", encoding="utf-8")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dirty tree", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_development_preview_can_explicitly_include_untracked_files(self) -> None:
        (self.repo / "untracked.txt").write_text("preview only\n", encoding="utf-8")
        result = self.run_builder("--allow-dirty", "--include-untracked")
        self.assert_build_succeeded(result)
        tar_names, _ = self.archive_names()
        self.assertIn(f"bugate-{VERSION}/untracked.txt", tar_names)

    def test_explicit_version_must_match_both_manifests(self) -> None:
        result = self.run_builder("--version", "0.4.3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match both plugin manifests", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_invalid_semver_is_rejected_before_output(self) -> None:
        result = self.run_builder("--version", "latest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid semantic version", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_plugin_manifest_mismatch_is_rejected(self) -> None:
        path = self.repo / ".claude-plugin/plugin.json"
        path.write_text(json.dumps({"name": "bugate", "version": "0.4.3"}) + "\n")
        self.commit("make plugin versions inconsistent")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugin manifest versions differ", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_updater_version_mismatch_is_rejected(self) -> None:
        (self.repo / "scripts/bugate_update.py").write_text(
            'UPDATER_VERSION = "0.4.3"\n', encoding="utf-8"
        )
        self.commit("mismatch updater version")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release version mismatch", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_missing_executable_wrapper_is_rejected(self) -> None:
        wrapper = self.repo / "bin/bugate-update"
        os.chmod(wrapper, 0o644)
        self.commit("remove executable mode")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("executable bin/bugate-update", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_missing_updater_worker_module_is_rejected(self) -> None:
        worker = self.repo / "scripts/bugate_update_transaction.py"
        worker.unlink()
        self.commit("remove updater worker module")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete updater worker bundle", result.stderr)
        self.assertIn("bugate_update_transaction.py", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_executable_but_noncanonical_wrapper_is_rejected(self) -> None:
        wrapper = self.repo / "bin/bugate-update"
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(wrapper, 0o755)
        self.commit("replace wrapper with empty success")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical updater dispatch contract", result.stderr)
        self.assertFalse(self.dist.exists())

    def test_failed_rebuild_preserves_existing_assets(self) -> None:
        first = self.run_builder()
        self.assert_build_succeeded(first)
        original = {path.name: path.read_bytes() for path in self.asset_paths}
        (self.repo / "scripts/bugate_update.py").write_text(
            'UPDATER_VERSION = "invalid"\n', encoding="utf-8"
        )
        self.commit("invalid updater metadata")
        failed = self.run_builder()
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.asset_paths}, original
        )
        self.assertFalse(any(self.repo.glob(".bugate-release-stage-*")))

    def test_mid_publish_failure_restores_all_previous_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-atomic-publish-") as raw:
            root = Path(raw)
            stage = root / "stage"
            output = root / "dist"
            stage.mkdir()
            output.mkdir()
            names = ("release.tar.gz", "release.zip", "release.SHA256SUMS")
            staged = tuple(stage / name for name in names)
            for index, path in enumerate(staged):
                path.write_bytes(f"new-{index}".encode("ascii"))
            for index, name in enumerate(names):
                (output / name).write_bytes(f"old-{index}".encode("ascii"))

            real_replace = os.replace
            publish_failed = False

            def fail_during_second_publish(source, destination):
                nonlocal publish_failed
                source_path = Path(source)
                if (
                    not publish_failed
                    and source_path.parent == stage
                    and source_path.name == "release.zip"
                ):
                    publish_failed = True
                    raise OSError("synthetic mid-publish failure")
                return real_replace(source, destination)

            with mock.patch.object(
                builder_module.os,
                "replace",
                side_effect=fail_during_second_publish,
            ):
                with self.assertRaisesRegex(OSError, "mid-publish"):
                    builder_module._publish_atomically(staged, output)

            self.assertEqual(
                {name: (output / name).read_bytes() for name in names},
                {name: f"old-{index}".encode("ascii") for index, name in enumerate(names)},
            )
            self.assertEqual(sorted(path.name for path in output.iterdir()), sorted(names))
            self.assertFalse(builder_module._publish_state_dir(output).exists())

    def test_restore_failure_retains_durable_backup_and_next_recovery_restores_mapping(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-durable-publish-") as raw:
            root = Path(raw)
            stage = root / "stage"
            output = root / "dist"
            stage.mkdir()
            output.mkdir()
            names = ("release.tar.gz", "release.zip", "release.SHA256SUMS")
            staged = tuple(stage / name for name in names)
            expected_old = {
                name: f"old-{index}".encode("ascii")
                for index, name in enumerate(names)
            }
            for index, path in enumerate(staged):
                path.write_bytes(f"new-{index}".encode("ascii"))
            for name, payload in expected_old.items():
                (output / name).write_bytes(payload)

            state = builder_module._publish_state_dir(output)
            real_replace = os.replace
            publish_failed = False
            restore_failed = False

            def fail_publish_then_restore(source, destination):
                nonlocal publish_failed, restore_failed
                source_path = Path(source)
                if (
                    not publish_failed
                    and source_path.parent == stage
                    and source_path.name == "release.zip"
                ):
                    publish_failed = True
                    raise OSError("synthetic publish failure")
                if (
                    publish_failed
                    and not restore_failed
                    and source_path.parent == state / "backup"
                    and source_path.name == "release.tar.gz"
                ):
                    restore_failed = True
                    raise OSError("synthetic restore failure")
                return real_replace(source, destination)

            with mock.patch.object(
                builder_module.os,
                "replace",
                side_effect=fail_publish_then_restore,
            ):
                with self.assertRaisesRegex(RuntimeError, "backup and journal retained"):
                    builder_module._publish_atomically(staged, output)

            self.assertTrue((state / "journal.json").is_file())
            self.assertEqual(
                {
                    name: (state / "backup" / name).read_bytes()
                    for name in names
                },
                expected_old,
            )
            builder_module._recover_pending_publish(output)
            self.assertFalse(state.exists())
            self.assertEqual(
                {name: (output / name).read_bytes() for name in names},
                expected_old,
            )


if __name__ == "__main__":
    unittest.main()
