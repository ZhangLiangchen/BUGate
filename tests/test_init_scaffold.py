#!/usr/bin/env python3
"""Fresh imported-mode installer acceptance on synthetic temporary repos.

The tests never read, clone, copy, or name a real imported SUT.  Every target
is created from scratch under ``TemporaryDirectory`` and Memory integration is
replaced with a local mock; source inputs come only from BUGate Core itself.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_init  # noqa: E402
import bugate_install_contract as contract  # noqa: E402
import bugate_update_engine as engine  # noqa: E402


def tree_image(root: Path) -> dict[str, dict[str, Any]]:
    """Capture type, mode, bytes, and link target without following symlinks."""

    image: dict[str, dict[str, Any]] = {}
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories + filenames):
            path = current_path / name
            details = os.lstat(path)
            relative = path.relative_to(root).as_posix()
            record: dict[str, Any] = {
                "mode": f"{stat.S_IMODE(details.st_mode):04o}",
            }
            if stat.S_ISLNK(details.st_mode):
                record.update(type="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(details.st_mode):
                record.update(type="directory")
            elif stat.S_ISREG(details.st_mode):
                record.update(type="file", content=path.read_bytes())
            elif stat.S_ISFIFO(details.st_mode):
                record.update(type="fifo")
            else:
                record.update(type="special")
            image[relative] = record
    return image


class FreshInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bugate-init-synthetic-")
        self.base = Path(self.temporary.name)
        self.original_registry = bugate_init.NAMESPACE_REGISTRY
        bugate_init.NAMESPACE_REGISTRY = self.base / "machine-home" / ".bugate" / "namespaces.tsv"

    def tearDown(self) -> None:
        bugate_init.NAMESPACE_REGISTRY = self.original_registry
        self.temporary.cleanup()

    def repo(self, name: str = "synthetic-repo") -> Path:
        target = self.base / name
        target.mkdir()
        return target

    def invoke(
        self,
        target: Path,
        *arguments: str,
        bus_result: list[str] | None = None,
    ) -> tuple[int, str, str, mock.Mock]:
        output = io.StringIO()
        error = io.StringIO()
        bus = mock.Mock(return_value=bus_result or ["memory-bus: synthetic test double"])
        code = 0
        message = ""
        with (
            mock.patch.object(bugate_init, "bus_ensure", bus),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            try:
                code = bugate_init.main([str(target), *arguments])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                message = "" if exc.code is None else str(exc.code)
        return code, output.getvalue(), message + error.getvalue(), bus

    def install(self, target: Path) -> str:
        code, output, error, _bus = self.invoke(target)
        self.assertEqual(code, 0, error)
        return output

    def test_scaffold_templates_are_hygienic_and_governance_stays_off(self) -> None:
        profile = bugate_init.PROFILE_SCAFFOLD.format(
            vendor_dir=".bugate", name="synthetic"
        )
        config = bugate_init.CONFIG_SCAFFOLD.format(vendor_dir=".bugate")
        for label, body in (("profile", profile), ("config", config)):
            with self.subTest(label=label):
                self.assertFalse(
                    [character for character in body if ord(character) < 0x20 and character != "\n"]
                )
        self.assertIn("\\bmy-product-name\\b", profile)
        self.assertEqual(profile.count("\nguarded_path_regex:"), 1)
        self.assertEqual(profile.count("\nrole_governance:"), 1)
        self.assertIn("\nrole_governance:\n  mode: off\n", profile)
        self.assertIn("#   mode: required", profile)
        self.assertIn("#   memory_mode: required", profile)

    def test_hook_blocks_are_the_canonical_identity_bearing_contract(self) -> None:
        identity_re = re.compile(
            r"^BUGATE_HOOK_ID='([^']+)'; export BUGATE_HOOK_ID; "
        )
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                blocks = bugate_init.hook_blocks(".bugate", runtime)
                self.assertEqual(blocks, contract.hook_fragments(".bugate", runtime))
                commands = [
                    hook["command"]
                    for entries in blocks.values()
                    for entry in entries
                    for hook in entry["hooks"]
                ]
                self.assertTrue(commands)
                self.assertTrue(
                    all(identity_re.match(command) for command in commands)
                )
                self.assertTrue(
                    all('[ -n "$ROOT" ] || exit 0;' in command for command in commands)
                )
                self.assertTrue(all("--core" not in command for command in commands))

    def test_fresh_install_writes_deterministic_manifest_lock_last_and_verifies(self) -> None:
        target = self.repo()
        resolved_target = target.resolve()
        writes: list[str] = []
        verify_entry_images: list[dict[str, dict[str, Any]]] = []
        original_write = bugate_init._write_new_file
        original_verify = bugate_init.update_engine.verify_installed

        def recording_write(path: Path, data: bytes, mode: str) -> None:
            original_write(path, data, mode)
            try:
                writes.append(path.relative_to(resolved_target).as_posix())
            except ValueError:
                pass

        def recording_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
            verify_entry_images.append(tree_image(resolved_target))
            return original_verify(*args, **kwargs)

        with (
            mock.patch.object(bugate_init, "_write_new_file", recording_write),
            mock.patch.object(
                bugate_init.update_engine,
                "verify_installed",
                recording_verify,
            ),
        ):
            output = self.install(target)

        manifest_path = target / ".bugate" / contract.INSTALLED_MANIFEST_PATH
        lock_path = target / ".bugate" / contract.INSTALLED_LOCK_PATH
        manifest = json.loads(manifest_path.read_bytes())
        lock = json.loads(lock_path.read_bytes())
        self.assertEqual(
            manifest_path.read_bytes(), contract.canonical_json_bytes(manifest)
        )
        self.assertEqual(lock_path.read_bytes(), contract.installed_lock_bytes(lock))
        contract.validate_current_release_manifest(
            manifest,
            expected_version=bugate_init._installer_version(),
        )
        contract.validate_installed_lock(
            lock,
            release_manifest=manifest,
            vendor_dir=".bugate",
            strict_current=True,
        )
        self.assertIsNone(lock["previous_version"])
        self.assertIsNone(lock["archive_sha256"])
        self.assertEqual(
            lock["archive_verification"], "unavailable-from-unpacked-source"
        )
        self.assertNotIn(str(self.base), lock_path.read_text(encoding="utf-8"))
        self.assertNotIn("timestamp", lock)
        self.assertEqual(writes[-1], ".bugate/bugate.lock.json")
        self.assertEqual(len(verify_entry_images), 1)
        self.assertEqual(tree_image(target), verify_entry_images[0])
        self.assertEqual(engine.verify_installed(target)["decision"], "GO")
        self.assertIn("verify installed lock: GO", output)
        self.assertTrue(os.access(target / ".bugate/bin/bugate-update", os.X_OK))

    def test_fresh_install_preserves_sut_hooks_format_and_mixed_entry(self) -> None:
        target = self.repo()
        claude_path = target / ".claude" / "settings.json"
        codex_path = target / ".codex" / "hooks.json"
        claude_path.parent.mkdir()
        codex_path.parent.mkdir()
        sut_entry = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "echo synthetic-sut-hook"}],
        }
        mixed_entry = {
            "matcher": "Edit|Write",
            "hooks": [
                {"type": "command", "command": "echo synthetic-wrapper"},
                {
                    "type": "command",
                    "command": "/usr/bin/env python3 .bugate/scripts/check_bugate.py",
                },
            ],
        }
        claude_path.write_text(
            '{\n  "sutMeta" : {"keep" : true},\n  "hooks" : {\n'
            '    "PreToolUse" : '
            + json.dumps([sut_entry, mixed_entry], separators=(",", ":"))
            + ',\n    "SyntheticEvent" : [{"hooks":[{"type":"command","command":"echo untouched"}]}]\n'
            "  }\n}\n",
            encoding="utf-8",
        )
        codex_entry = {
            "matcher": "apply_patch",
            "hooks": [{"type": "command", "command": "echo synthetic-codex"}],
        }
        codex_path.write_text(
            json.dumps({"sutTop": [3, 2, 1], "hooks": {"PreToolUse": [codex_entry]}}, indent=4)
            + "\n",
            encoding="utf-8",
        )

        self.install(target)
        claude_bytes = claude_path.read_text(encoding="utf-8")
        claude = json.loads(claude_bytes)
        codex = json.loads(codex_path.read_text(encoding="utf-8"))
        self.assertIn('"sutMeta" : {"keep" : true}', claude_bytes)
        self.assertIn(
            '"SyntheticEvent" : [{"hooks":[{"type":"command","command":"echo untouched"}]}]',
            claude_bytes,
        )
        self.assertEqual(claude["sutMeta"], {"keep": True})
        self.assertEqual(claude["hooks"]["SyntheticEvent"][0]["hooks"][0]["command"], "echo untouched")
        self.assertIn(sut_entry, claude["hooks"]["PreToolUse"])
        self.assertIn(mixed_entry, claude["hooks"]["PreToolUse"])
        self.assertEqual(codex["sutTop"], [3, 2, 1])
        self.assertIn(codex_entry, codex["hooks"]["PreToolUse"])
        self.assertEqual(engine.verify_installed(target)["decision"], "GO")

    def test_any_existing_canonical_hook_identity_fails_closed_before_writes(self) -> None:
        canonical = contract.hook_fragments(".bugate", "claude")["PreToolUse"][0]
        cases = {
            "exact": canonical,
            "spoofed": json.loads(json.dumps(canonical)),
        }
        cases["spoofed"]["hooks"][0]["command"] += "; echo spoofed"
        for index, (label, value) in enumerate(cases.items()):
            with self.subTest(shape=label):
                target = self.repo(f"hook-identity-{index}")
                path = target / ".claude" / "settings.json"
                path.parent.mkdir()
                path.write_text(
                    json.dumps({"hooks": {"PreToolUse": [value]}}, indent=2) + "\n",
                    encoding="utf-8",
                )
                before = tree_image(target)

                code, _output, error, bus = self.invoke(target)

                self.assertNotEqual(code, 0)
                self.assertIn("cannot adopt existing canonical BUGate hook", error)
                self.assertEqual(tree_image(target), before)
                self.assertFalse(bugate_init.NAMESPACE_REGISTRY.exists())
                bus.assert_not_called()

    def test_pure_legacy_hook_is_not_migrated_by_fresh_init(self) -> None:
        target = self.repo()
        path = target / ".claude" / "settings.json"
        path.parent.mkdir()
        legacy = {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": 'ROOT="$(legacy)"; /usr/bin/env python3 "$ROOT/.bugate/scripts/check_bugate.py"',
                }
            ],
        }
        path.write_text(
            json.dumps({"hooks": {"PreToolUse": [legacy]}}, indent=2) + "\n",
            encoding="utf-8",
        )
        before = tree_image(target)

        code, _output, error, bus = self.invoke(target)

        self.assertNotEqual(code, 0)
        self.assertIn("legacy BUGate-only hook", error)
        self.assertIn("use bugate-update", error)
        self.assertEqual(tree_image(target), before)
        bus.assert_not_called()

    def test_existing_exact_workspace_managed_target_is_not_adopted(self) -> None:
        target = self.repo()
        link = target / ".agents" / "skills" / "bugate"
        link.parent.mkdir(parents=True)
        link.symlink_to("../../.bugate/.shared/skills/bugate")
        before = tree_image(target)

        code, _output, error, bus = self.invoke(target)

        self.assertNotEqual(code, 0)
        self.assertIn("without prior lock authority", error)
        self.assertEqual(tree_image(target), before)
        bus.assert_not_called()

    def test_shared_container_drift_after_preflight_is_not_overwritten(self) -> None:
        target = self.repo()
        path = target / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_text(
            '{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo before"}]}]}}\n',
            encoding="utf-8",
        )
        manifest, _source = bugate_init.load_install_manifest(ROOT)
        lock = contract.build_installed_lock(
            manifest,
            previous_version=None,
            archive_sha256=None,
            updater_version=manifest["bugate_version"],
        )
        prepared = bugate_init.prepare_shared_outputs(
            target, lock["installed_projection"]
        )
        path.write_text(
            '{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo drift"}]}]}}\n',
            encoding="utf-8",
        )
        drift = path.read_bytes()

        with self.assertRaisesRegex(SystemExit, "changed after preflight"):
            bugate_init.write_shared_outputs(target, prepared, dry=False)

        self.assertEqual(path.read_bytes(), drift)
        self.assertFalse((target / ".codex/hooks.json").exists())

    def test_late_path_writer_wins_shared_commit_race_without_overwrite(self) -> None:
        target = self.repo()
        path = target / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_text(
            '{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo base"}]}]}}\n',
            encoding="utf-8",
        )
        manifest, _source = bugate_init.load_install_manifest(ROOT)
        lock = contract.build_installed_lock(
            manifest,
            previous_version=None,
            archive_sha256=None,
            updater_version=manifest["bugate_version"],
        )
        prepared = bugate_init.prepare_shared_outputs(
            target, lock["installed_projection"]
        )
        late = b'{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo late"}]}]}}\n'
        original_link = bugate_init.os.link
        injected = False

        def inject_late_writer(
            source: str,
            destination: str,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            nonlocal injected
            if destination == "settings.json" and not injected:
                injected = True
                path.write_bytes(late)
            return original_link(source, destination, *args, **kwargs)

        with (
            mock.patch.object(
                bugate_init.os,
                "link",
                inject_late_writer,
            ),
            self.assertRaisesRegex(SystemExit, "raced during install"),
        ):
            bugate_init.write_shared_outputs(target, prepared, dry=False)

        self.assertTrue(injected)
        self.assertEqual(path.read_bytes(), late)
        self.assertFalse(
            list(path.parent.glob(".settings.json.bugate-init-backup-*"))
        )
        self.assertFalse((target / ".codex/hooks.json").exists())

    def test_partial_installer_staged_write_restores_original_shared_file(self) -> None:
        target = self.repo()
        path = target / ".claude" / "settings.json"
        path.parent.mkdir()
        original = b'{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo original"}]}]}}\n'
        path.write_bytes(original)
        manifest, _source = bugate_init.load_install_manifest(ROOT)
        lock = contract.build_installed_lock(
            manifest,
            previous_version=None,
            archive_sha256=None,
            updater_version=manifest["bugate_version"],
        )
        prepared = bugate_init.prepare_shared_outputs(
            target, lock["installed_projection"]
        )
        original_write = bugate_init.os.write
        injected = False

        def fail_after_partial(descriptor: int, data: Any) -> int:
            nonlocal injected
            if not injected:
                injected = True
                partial = bytes(data[: min(len(data), 19)])
                original_write(descriptor, partial)
                raise OSError("synthetic staged write failure")
            return original_write(descriptor, data)

        with (
            mock.patch.object(bugate_init.os, "write", fail_after_partial),
            self.assertRaisesRegex(SystemExit, "staged write failed"),
        ):
            bugate_init.write_shared_outputs(target, prepared, dry=False)

        self.assertTrue(injected)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(
            list(path.parent.glob(".settings.json.bugate-init-backup-*"))
        )
        staged = list(path.parent.glob(".settings.json.bugate-init-new-*"))
        self.assertEqual(len(staged), 1)
        self.assertTrue(staged[0].read_bytes().startswith(original[:19]))

    def test_absent_shared_partial_cleanup_never_deletes_late_sut_path(self) -> None:
        target = self.repo()
        parent = target / ".claude"
        parent.mkdir()
        path = parent / "settings.json"
        parent_details = os.lstat(parent)
        base = {
            "state": "absent",
            "parent_device": parent_details.st_dev,
            "parent_inode": parent_details.st_ino,
        }
        late = b"late-sut-path-writer\n"
        partial = b"partial-installer-stage\n"
        staged_names: list[str] = []

        def partial_then_late(
            parent_fd: int, name: str, data: bytes, mode: str
        ) -> os.stat_result:
            staged_names.append(name)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(name, flags, 0o644, dir_fd=parent_fd)
            try:
                os.write(descriptor, partial)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            path.write_bytes(late)
            raise OSError("synthetic absent staged failure")

        with (
            mock.patch.object(
                bugate_init,
                "_write_new_file_at",
                partial_then_late,
            ),
            self.assertRaisesRegex(SystemExit, "staged write failed"),
        ):
            bugate_init._replace_bound_file(path, b"installer-final\n", base)

        self.assertEqual(path.read_bytes(), late)
        self.assertEqual(len(staged_names), 1)
        self.assertEqual((parent / staged_names[0]).read_bytes(), partial)

    def test_changed_backup_never_causes_final_path_unlink(self) -> None:
        target = self.repo()
        path = target / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_text(
            '{"hooks":{"SyntheticEvent":[{"hooks":[{"type":"command","command":"echo base"}]}]}}\n',
            encoding="utf-8",
        )
        manifest, _source = bugate_init.load_install_manifest(ROOT)
        lock = contract.build_installed_lock(
            manifest,
            previous_version=None,
            archive_sha256=None,
            updater_version=manifest["bugate_version"],
        )
        prepared = bugate_init.prepare_shared_outputs(
            target, lock["installed_projection"]
        )
        original_read_at = bugate_init._read_regular_at
        backup_reads = 0
        backup_edit = b"open-fd-sut-edit\n"
        late_path = b"late-pathname-sut-edit\n"

        def inject_two_writers(
            parent_fd: int, name: str, *, label: str
        ) -> tuple[bytes, os.stat_result]:
            nonlocal backup_reads
            if label == "shared managed backup":
                backup_reads += 1
                if backup_reads == 2:
                    descriptor = os.open(name, os.O_WRONLY | os.O_TRUNC, dir_fd=parent_fd)
                    try:
                        os.write(descriptor, backup_edit)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    replacement = path.parent / ".synthetic-late-replacement"
                    replacement.write_bytes(late_path)
                    os.replace(replacement, path)
            return original_read_at(parent_fd, name, label=label)

        with (
            mock.patch.object(
                bugate_init,
                "_read_regular_at",
                inject_two_writers,
            ),
            self.assertRaisesRegex(SystemExit, "backup retained"),
        ):
            bugate_init.write_shared_outputs(target, prepared, dry=False)

        self.assertEqual(backup_reads, 2)
        self.assertEqual(path.read_bytes(), late_path)
        backups = list(path.parent.glob(".settings.json.bugate-init-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), backup_edit)

    def test_vendor_root_appearing_after_preflight_is_never_adopted(self) -> None:
        target = self.repo()
        original_preflight = bugate_init._preflight_physical_targets

        def inject_vendor(
            root: Path, projection: Any
        ) -> None:
            original_preflight(root, projection)
            vendor = root / ".bugate"
            vendor.mkdir()
            (vendor / "rogue-unmanaged.py").write_bytes(b"operator-owned\n")

        with mock.patch.object(
            bugate_init,
            "_preflight_physical_targets",
            inject_vendor,
        ):
            code, _output, error, bus = self.invoke(target)

        self.assertNotEqual(code, 0)
        self.assertIn("refusing adoption", error)
        self.assertEqual(
            (target / ".bugate/rogue-unmanaged.py").read_bytes(),
            b"operator-owned\n",
        )
        self.assertFalse((target / ".bugate/bugate.lock.json").exists())
        self.assertFalse((target / "bugate.config.yaml").exists())
        bus.assert_not_called()

    def test_namespace_registry_concurrent_update_is_not_lost_or_overwritten(self) -> None:
        registry = bugate_init.NAMESPACE_REGISTRY
        registry.parent.mkdir(parents=True)
        registry.write_text("project:seed\t/synthetic/seed\n", encoding="utf-8")
        target = self.repo()
        original_replace = bugate_init._replace_bound_file
        concurrent = "project:concurrent\t/synthetic/concurrent\n"

        def inject_registry_change(
            path: Path, content: bytes, base: Any
        ) -> None:
            path.write_text(
                "project:seed\t/synthetic/seed\n" + concurrent,
                encoding="utf-8",
            )
            original_replace(path, content, base)

        with (
            mock.patch.object(
                bugate_init,
                "_replace_bound_file",
                inject_registry_change,
            ),
            self.assertRaisesRegex(SystemExit, "changed after preflight"),
        ):
            bugate_init._register_namespace("project:synthetic-repo", target)

        text = registry.read_text(encoding="utf-8")
        self.assertIn(concurrent, text)
        self.assertNotIn("project:synthetic-repo", text)

        registry.write_text(
            f"project:synthetic-repo\t{self.base / 'other-repo'}\n",
            encoding="utf-8",
        )
        before = registry.read_bytes()
        with self.assertRaisesRegex(SystemExit, "claimed by another repository"):
            bugate_init._register_namespace("project:synthetic-repo", target)
        self.assertEqual(registry.read_bytes(), before)

    def test_second_init_rejects_locked_install_and_preserves_full_tree(self) -> None:
        target = self.repo()
        self.install(target)
        (target / "synthetic-dirty.txt").write_bytes(b"unrelated dirty bytes\n")
        before = tree_image(target)
        registry_before = bugate_init.NAMESPACE_REGISTRY.read_bytes()

        with (
            mock.patch.object(bugate_init, "load_install_manifest") as manifest_loader,
            mock.patch.object(bugate_init, "_memory_namespace") as namespace_loader,
        ):
            code, _output, error, bus = self.invoke(target)

        self.assertNotEqual(code, 0)
        self.assertIn("fresh-install only", error)
        self.assertIn("<unpacked-v0.4.x>/scripts/bugate_update.py plan", error)
        self.assertIn(".bugate/bin/bugate-update plan", error)
        self.assertEqual(tree_image(target), before)
        self.assertEqual(bugate_init.NAMESPACE_REGISTRY.read_bytes(), registry_before)
        manifest_loader.assert_not_called()
        namespace_loader.assert_not_called()
        bus.assert_not_called()

    def test_any_legacy_or_unsafe_vendor_leaf_is_existing_and_zero_write(self) -> None:
        makers = {
            "legacy-directory": lambda path: (
                path.mkdir(),
                (path / "scripts").mkdir(),
                (path / "scripts" / "legacy.py").write_bytes(b"legacy\n"),
            ),
            "regular-file": lambda path: path.write_bytes(b"not a directory\n"),
            "dangling-symlink": lambda path: path.symlink_to("missing-target"),
            "fifo": lambda path: os.mkfifo(path),
        }
        for index, (label, maker) in enumerate(makers.items()):
            with self.subTest(kind=label):
                target = self.repo(f"synthetic-{index}")
                maker(target / ".bugate")
                before = tree_image(target)
                with mock.patch.object(bugate_init, "load_install_manifest") as loader:
                    code, _output, error, bus = self.invoke(target)
                self.assertNotEqual(code, 0)
                self.assertIn("existing BUGate vendor path detected", error)
                self.assertEqual(tree_image(target), before)
                loader.assert_not_called()
                bus.assert_not_called()

    def test_invalid_vendor_dir_is_rejected_without_writes(self) -> None:
        for index, value in enumerate(("../escape", "/absolute", "nested/../escape", "")):
            with self.subTest(value=value):
                target = self.repo(f"invalid-vendor-{index}")
                (target / "synthetic.txt").write_bytes(b"keep\n")
                before = tree_image(target)
                code, _output, _error, bus = self.invoke(
                    target, "--vendor-dir", value
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(tree_image(target), before)
                bus.assert_not_called()

    def test_dry_run_is_zero_write_for_target_registry_and_memory(self) -> None:
        target = self.repo()
        (target / "synthetic-dirty.txt").write_bytes(b"keep me byte-identical\n")
        (target / ".gitignore").write_bytes(b"synthetic-cache/\n")
        before = tree_image(target)

        code, output, error, bus = self.invoke(target, "--dry-run")

        self.assertEqual(code, 0, error)
        self.assertEqual(tree_image(target), before)
        self.assertFalse(bugate_init.NAMESPACE_REGISTRY.exists())
        bus.assert_called_once()
        self.assertTrue(bus.call_args.args[1])
        self.assertIn("would write .bugate/bugate.lock.json last", output)

    def test_fresh_install_preserves_sut_owned_governance_and_unrelated_assets(self) -> None:
        target = self.repo()
        seeded = {
            "bugate.config.yaml": b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
            "bugate.profile.yaml": b"memory:\n  namespace: synthetic:keep\n",
            "docs/usecases/SYNTHETIC-1/case.md": b"synthetic use case\n",
            "00_role_evidence/SYNTHETIC-1/receipt.json": b'{"synthetic":true}\n',
            "bin/bugate-auto": b"#!/bin/sh\nexit 17\n",
            "tests/test_synthetic.py": b"def test_synthetic():\n    assert True\n",
        }
        for relative, content in seeded.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (target / "bin/bugate-auto").chmod(0o755)
        before = {
            relative: tree_image(target)[relative]
            for relative in seeded
        }

        self.install(target)
        after = tree_image(target)

        for relative, expected in before.items():
            with self.subTest(relative=relative):
                self.assertEqual(after[relative], expected)
        self.assertIn("namespace: synthetic:keep", (target / "bugate.profile.yaml").read_text())
        self.assertEqual(engine.verify_installed(target)["decision"], "GO")

    def test_gitignore_contract_contains_update_state_and_preserves_sut_lines(self) -> None:
        target = self.repo()
        own = b"node_modules/\n*.synthetic-log\n/build/\n"
        (target / ".gitignore").write_bytes(own)

        self.install(target)
        text = (target / ".gitignore").read_text(encoding="utf-8")

        self.assertTrue(text.startswith(own.decode("utf-8")))
        self.assertEqual(text.count(contract.GITIGNORE_BEGIN), 1)
        self.assertIn("/.bugate-update/", text)
        self.assertIn("/.bugate/plan.lock", text)
        self.assertNotIn("bugate.config.yaml", text)
        self.assertNotIn("bugate.profile.yaml", text)

    def test_wired_guard_is_inert_outside_workspace_and_active_inside(self) -> None:
        target = self.repo()
        self.install(target)
        settings = json.loads((target / ".claude/settings.json").read_bytes())
        command = next(
            entry["hooks"][0]["command"]
            for entry in settings["hooks"]["PreToolUse"]
            if entry.get("matcher") == "Edit|Write"
        )
        nowhere = self.base / "config-less"
        nowhere.mkdir()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("BUGATE_")
        }
        outside = subprocess.run(
            ["sh", "-c", command],
            cwd=nowhere,
            env=environment,
            input='{"tool_input":{"file_path":"synthetic.py"}}',
            capture_output=True,
            text=True,
        )
        inside = subprocess.run(
            ["sh", "-c", command],
            cwd=target,
            env=environment,
            input='{"tool_input":{"file_path":"synthetic.py"}}',
            capture_output=True,
            text=True,
        )
        self.assertEqual(outside.returncode, 0, outside.stderr)
        self.assertFalse(outside.stderr)
        self.assertEqual(inside.returncode, 0, inside.stderr)

    def test_workspace_skill_links_and_gate_agents_match_projection(self) -> None:
        target = self.repo()
        self.install(target)
        lock = json.loads((target / ".bugate/bugate.lock.json").read_bytes())
        expected = {
            item["target_path"]: item
            for item in lock["installed_projection"]
            if item["scope"] == "workspace"
        }
        for relative, item in expected.items():
            with self.subTest(path=relative):
                path = target / relative
                if item["type"] == "symlink":
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(os.readlink(path), item["target"])
                    self.assertTrue((path / "SKILL.md").is_file())
                else:
                    self.assertEqual(
                        contract.sha256_file(path), item["sha256"]
                    )
                    self.assertEqual(
                        f"{stat.S_IMODE(os.lstat(path).st_mode):04o}", item["mode"]
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
