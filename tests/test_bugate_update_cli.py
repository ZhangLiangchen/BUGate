#!/usr/bin/env python3
"""CLI contract tests for the imported-mode BUGate updater."""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402
import bugate_update as cli  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        content_length: str | None = None,
        read_error: BaseException | None = None,
    ) -> None:
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self._chunks = iter(chunks or [])
        self._read_error = read_error

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self._read_error is not None:
            error = self._read_error
            self._read_error = None
            raise error
        return next(self._chunks, b"")


class BugateUpdateCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="bugate-update-cli-test-"
        )
        self.base = Path(self._temporary.name)
        self.target = self.base / "synthetic-target"
        self.target.mkdir()
        (self.target / "SUT-owned.txt").write_bytes(b"untouched\n")
        self.before = self._target_snapshot()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _target_snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.target).as_posix(): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    def assert_target_unchanged(self) -> None:
        self.assertEqual(self._target_snapshot(), self.before)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return_code = cli.main(argv)
        return return_code, stdout.getvalue(), stderr.getvalue()

    @contextlib.contextmanager
    def prepared_release(self):
        yield SimpleNamespace(
            root=self.base / "synthetic-release",
            manifest={"bugate_version": cli.UPDATER_VERSION},
            archive_sha256=None,
            source_kind="unpacked",
            root_identity=(1, 2),
        )

    def plan(self, *, digest: str = "current-plan", hook_changed: bool = False) -> dict:
        return {
            "schema_version": 1,
            "from_version": "0.4.1",
            "to_version": cli.UPDATER_VERSION,
            "release_digest": "a" * 64,
            "profile_compatibility": {"status": "compatible"},
            "managed_changes": [],
            "hook_changes": [
                {
                    "target_path": ".codex/hooks.json",
                    "event": "PreToolUse",
                }
            ],
            "codex_hook_hash_changed": hook_changed,
            "new_session_required": hook_changed,
            "rollback_available": True,
            "plan_digest": digest,
            "decision": "GO",
        }

    def test_updater_version_is_a_literal_0_4_2(self) -> None:
        self.assertEqual(cli.UPDATER_VERSION, "0.4.2")
        tree = ast.parse((SCRIPTS / "bugate_update.py").read_text(encoding="utf-8"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "UPDATER_VERSION"
                for target in node.targets
            )
        ]
        self.assertEqual(len(assignments), 1)
        self.assertIsInstance(assignments[0].value, ast.Constant)
        self.assertEqual(assignments[0].value.value, "0.4.2")

    def test_wrapper_is_canonical_regular_and_executable(self) -> None:
        wrapper = ROOT / "bin/bugate-update"
        details = os.lstat(wrapper)
        self.assertTrue(stat.S_ISREG(details.st_mode))
        self.assertFalse(stat.S_ISLNK(details.st_mode))
        self.assertEqual(stat.S_IMODE(details.st_mode), 0o755)
        self.assertEqual(wrapper.read_bytes(), contract.BUGATE_UPDATE_WRAPPER_BYTES)
        self.assertEqual(
            hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            contract.BUGATE_UPDATE_WRAPPER_SHA256,
        )

    def test_wrapper_help_and_version_use_the_public_command_identity(self) -> None:
        wrapper = ROOT / "bin/bugate-update"
        help_result = subprocess.run(
            [str(wrapper), "--help"],
            cwd=self.target,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("usage: bugate-update", help_result.stdout)
        self.assertNotIn("bugate_update.py", help_result.stdout)

        version_result = subprocess.run(
            [str(wrapper), "--version"],
            cwd=self.target,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        self.assertEqual(
            version_result.stdout.strip(), f"bugate-update {cli.UPDATER_VERSION}"
        )
        self.assert_target_unchanged()

    def test_parser_exposes_exactly_five_commands(self) -> None:
        parser = cli.build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparsers.choices),
            {"status", "plan", "apply", "verify", "rollback"},
        )

    def test_bootstrap_positional_and_in_repo_syntax_parse(self) -> None:
        parser = cli.build_parser()
        for command in ("plan", "apply"):
            bootstrap = parser.parse_args(
                [command, ".", "--vendor-dir", ".bugate"]
            )
            self.assertEqual(bootstrap.command, command)
            self.assertEqual(bootstrap.target, ".")
            self.assertEqual(bootstrap.vendor_dir, ".bugate")

            installed = parser.parse_args([command, "--to", "0.4.2"])
            self.assertEqual(installed.target, ".")
            self.assertEqual(installed.to, "0.4.2")
        rollback = parser.parse_args(
            ["rollback", "--transaction", "a" * 32]
        )
        self.assertEqual(rollback.target, ".")
        self.assertEqual(rollback.transaction, "a" * 32)

    def test_remote_source_without_to_fails_before_download(self) -> None:
        with mock.patch.object(cli, "_unpacked_release_root", return_value=None), mock.patch.object(
            cli, "_download_release"
        ) as download:
            code, stdout, stderr = self.run_main(["plan", str(self.target)])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("explicit --to VERSION", stderr)
        self.assertIn("no implicit latest", stderr)
        download.assert_not_called()
        self.assertNotIn("/latest", cli.RELEASE_BASE_URL)
        self.assert_target_unchanged()

    def test_latest_and_other_invalid_semver_never_download(self) -> None:
        for version in ("latest", "v0.4.2", "0.4", "01.2.3"):
            with self.subTest(version=version), mock.patch.object(
                cli, "_unpacked_release_root", return_value=None
            ), mock.patch.object(cli, "_download_release") as download:
                code, _stdout, stderr = self.run_main(
                    ["plan", str(self.target), "--to", version]
                )
                self.assertEqual(code, 1)
                self.assertIn("invalid semantic version", stderr)
                download.assert_not_called()
                self.assert_target_unchanged()

    def test_vendor_dir_shell_metacharacters_fail_before_source_resolution(self) -> None:
        for vendor in ('vendor";touch-marker;#', "vendor dir", "vendor\nnext"):
            with self.subTest(vendor=repr(vendor)), mock.patch.object(
                cli, "_prepared_release"
            ) as prepared:
                code, stdout, stderr = self.run_main(
                    ["plan", str(self.target), "--vendor-dir", vendor]
                )
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("vendor_dir", stderr)
                prepared.assert_not_called()
                self.assert_target_unchanged()

    def test_symlink_project_root_is_rejected_before_source_resolution(self) -> None:
        linked = self.base / "linked-target"
        linked.symlink_to(self.target, target_is_directory=True)
        with mock.patch.object(cli, "_prepared_release") as prepared:
            code, stdout, stderr = self.run_main(["plan", str(linked)])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("project root must not be a symlink", stderr)
        prepared.assert_not_called()
        self.assert_target_unchanged()

    def test_explicit_remote_version_builds_only_versioned_asset_urls(self) -> None:
        directory = self.base / "explicit-download"
        directory.mkdir()

        def write_asset(_url: str, path: Path, *, limit: int) -> None:
            self.assertGreater(limit, 0)
            path.write_bytes(b"synthetic download")

        with mock.patch.object(cli, "_download", side_effect=write_asset) as download:
            archive, checksums = cli._download_release(cli.UPDATER_VERSION, directory)
        self.assertEqual(archive.name, f"bugate-{cli.UPDATER_VERSION}.tar.gz")
        self.assertEqual(
            checksums.name, f"bugate-{cli.UPDATER_VERSION}.SHA256SUMS"
        )
        urls = [call.args[0] for call in download.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertTrue(
            all(f"/v{cli.UPDATER_VERSION}/bugate-{cli.UPDATER_VERSION}." in url for url in urls)
        )
        self.assertTrue(all("/latest" not in url for url in urls))

    def test_archive_and_checksums_are_an_atomic_argument_pair(self) -> None:
        cases = (
            ["--archive", str(self.base / "release.tar.gz")],
            ["--checksums", str(self.base / "release.SHA256SUMS")],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), mock.patch.object(
                cli.source, "prepare_archive"
            ) as prepare:
                code, _stdout, stderr = self.run_main(
                    ["plan", str(self.target), *arguments]
                )
                self.assertEqual(code, 1)
                self.assertIn("must be supplied together", stderr)
                prepare.assert_not_called()
                self.assert_target_unchanged()

    def test_apply_dry_run_emits_plan_and_never_opens_transaction(self) -> None:
        plan = self.plan()
        recovery = {
            "recovery_required": True,
            "details": {"status": "applying", "transaction_id": "a" * 32},
        }
        with mock.patch.object(
            cli, "_prepared_release", side_effect=lambda _args: self.prepared_release()
        ), mock.patch.object(cli, "_legacy_manifests", return_value=[]), mock.patch.object(
            cli.transaction, "recovery_status", return_value=recovery
        ), mock.patch.object(
            cli.transaction, "recover_pending"
        ) as recover_pending, mock.patch.object(
            cli.engine, "build_update_plan", return_value=plan
        ) as build_plan, mock.patch.object(
            cli.transaction, "apply_update"
        ) as apply_update, mock.patch.object(
            cli.engine, "validate_plan_base"
        ) as validate_base:
            code, stdout, stderr = self.run_main(
                ["apply", str(self.target), "--dry-run", "--json"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), plan)
        build_plan.assert_called_once()
        self.assertEqual(build_plan.call_args.kwargs["recovery"], recovery)
        recover_pending.assert_not_called()
        apply_update.assert_not_called()
        validate_base.assert_not_called()
        self.assert_target_unchanged()

    def test_verify_reports_pending_recovery_in_json_and_human_output(self) -> None:
        recovery = {
            "recovery_required": True,
            "details": {"status": "applying", "transaction_id": "a" * 32},
            "decision": "NO-GO",
        }
        result = {
            "schema_version": 1,
            "status": "failed",
            "decision": "NO-GO",
            "recovery": recovery,
            "recovery_required": True,
            "failures": [{"error": "transaction recovery is required"}],
        }
        with mock.patch.object(
            cli, "_legacy_manifests", return_value=[]
        ), mock.patch.object(
            cli.transaction, "recovery_status", return_value=recovery
        ), mock.patch.object(
            cli.engine, "verify_installed", return_value=result
        ):
            json_code, stdout, stderr = self.run_main(
                ["verify", str(self.target), "--json"]
            )
            human_code, human, human_stderr = self.run_main(
                ["verify", str(self.target)]
            )
        self.assertEqual(json_code, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertTrue(payload["recovery_required"])
        self.assertEqual(payload["recovery"], recovery)
        self.assertEqual(human_code, 1)
        self.assertEqual(human_stderr, "")
        self.assertIn("Recovery required: yes", human)
        self.assertIn("FAILURE: transaction recovery is required", human)

    def test_real_apply_recovers_once_then_builds_plan_from_stable_state(self) -> None:
        plan = self.plan()
        pending = {
            "recovery_required": True,
            "details": {"status": "applying", "transaction_id": "a" * 32},
        }
        stable = {
            "recovery_required": False,
            "details": None,
            "decision": "GO",
        }
        report = {
            "decision": "GO",
            "status": "committed",
            "no_op": False,
            "transaction_id": "b" * 32,
        }
        with mock.patch.object(
            cli, "_prepared_release", side_effect=lambda _args: self.prepared_release()
        ), mock.patch.object(cli, "_legacy_manifests", return_value=[]), mock.patch.object(
            cli.transaction, "recovery_status", side_effect=[pending, stable]
        ) as recovery_status, mock.patch.object(
            cli.transaction, "recover_pending", return_value={"status": "recovered"}
        ) as recover_pending, mock.patch.object(
            cli.engine, "build_update_plan", return_value=plan
        ) as build_plan, mock.patch.object(
            cli.transaction, "apply_update", return_value=report
        ) as apply_update:
            code, stdout, stderr = self.run_main(
                ["apply", str(self.target), "--json"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), report)
        self.assertEqual(recovery_status.call_count, 2)
        recover_pending.assert_called_once_with(self.target.resolve(), ".bugate")
        build_plan.assert_called_once()
        self.assertEqual(build_plan.call_args.kwargs["recovery"], stable)
        apply_update.assert_called_once()
        self.assert_target_unchanged()

    def test_plan_with_recovery_required_is_strictly_read_only(self) -> None:
        plan = self.plan()
        recovery = {
            "recovery_required": True,
            "details": {"status": "applying", "transaction_id": "a" * 32},
        }
        with mock.patch.object(
            cli, "_prepared_release", side_effect=lambda _args: self.prepared_release()
        ), mock.patch.object(cli, "_legacy_manifests", return_value=[]), mock.patch.object(
            cli.transaction, "recovery_status", return_value=recovery
        ), mock.patch.object(
            cli.transaction, "recover_pending"
        ) as recover_pending, mock.patch.object(
            cli.engine, "build_update_plan", return_value=plan
        ) as build_plan:
            code, stdout, stderr = self.run_main(
                ["plan", str(self.target), "--json"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), plan)
        self.assertEqual(build_plan.call_args.kwargs["recovery"], recovery)
        recover_pending.assert_not_called()
        self.assert_target_unchanged()

    def test_json_error_is_one_machine_readable_no_go_object(self) -> None:
        code, stdout, stderr = self.run_main(
            [
                "plan",
                str(self.target),
                "--archive",
                str(self.base / "release.tar.gz"),
                "--json",
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(
            set(payload), {"schema_version", "command", "decision", "errors"}
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "plan")
        self.assertEqual(payload["decision"], "NO-GO")
        self.assertIsInstance(payload["errors"], list)
        self.assertEqual(len(payload["errors"]), 1)
        self.assert_target_unchanged()

    def test_json_argument_error_and_interrupt_are_machine_readable(self) -> None:
        code, stdout, stderr = self.run_main(
            ["rollback", str(self.target), "--json"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stderr, "")
        parse_error = json.loads(stdout)
        self.assertEqual(parse_error["command"], "rollback")
        self.assertEqual(parse_error["decision"], "NO-GO")
        self.assertIn("--transaction", parse_error["errors"][0])

        with mock.patch.object(cli, "_run", side_effect=KeyboardInterrupt):
            code, stdout, stderr = self.run_main(
                ["status", str(self.target), "--json"]
            )
        self.assertEqual(code, 130)
        self.assertEqual(stderr, "")
        interrupted = json.loads(stdout)
        self.assertEqual(interrupted["command"], "status")
        self.assertEqual(interrupted["decision"], "NO-GO")
        self.assertEqual(interrupted["errors"], ["interrupted"])
        self.assert_target_unchanged()

    def test_saved_plan_digest_mismatch_fails_before_base_or_apply(self) -> None:
        saved = self.base / "saved-plan.json"
        saved.write_text(json.dumps({"plan_digest": "old-plan"}), encoding="utf-8")
        plan = self.plan(digest="new-plan")
        with mock.patch.object(
            cli, "_prepared_release", side_effect=lambda _args: self.prepared_release()
        ), mock.patch.object(cli, "_legacy_manifests", return_value=[]), mock.patch.object(
            cli.transaction, "recovery_status", return_value={"recovery_required": False}
        ), mock.patch.object(
            cli.engine, "build_update_plan", return_value=plan
        ), mock.patch.object(
            cli.engine, "validate_plan_base"
        ) as validate_base, mock.patch.object(
            cli.transaction, "apply_update"
        ) as apply_update:
            code, stdout, stderr = self.run_main(
                [
                    "apply",
                    str(self.target),
                    "--plan",
                    str(saved),
                    "--json",
                ]
            )

        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("saved plan is stale", json.loads(stdout)["errors"][0])
        validate_base.assert_not_called()
        apply_update.assert_not_called()
        self.assert_target_unchanged()

    def test_dry_run_revalidates_saved_plan_instead_of_ignoring_it(self) -> None:
        saved = self.base / "saved-dry-run-plan.json"
        saved.write_text(json.dumps({"plan_digest": "stale"}), encoding="utf-8")
        plan = self.plan(digest="current")
        with mock.patch.object(
            cli, "_prepared_release", side_effect=lambda _args: self.prepared_release()
        ), mock.patch.object(cli, "_legacy_manifests", return_value=[]), mock.patch.object(
            cli.transaction, "recovery_status", return_value={"recovery_required": False}
        ), mock.patch.object(
            cli.engine, "build_update_plan", return_value=plan
        ), mock.patch.object(
            cli.engine, "validate_plan_base"
        ) as validate_base, mock.patch.object(
            cli.transaction, "apply_update"
        ) as apply_update:
            code, stdout, stderr = self.run_main(
                [
                    "apply",
                    str(self.target),
                    "--dry-run",
                    "--plan",
                    str(saved),
                    "--json",
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("saved plan is stale", json.loads(stdout)["errors"][0])
        validate_base.assert_not_called()
        apply_update.assert_not_called()
        self.assert_target_unchanged()

    def test_temporary_source_root_inside_target_is_zero_write_no_go(self) -> None:
        with mock.patch.object(
            cli.tempfile, "gettempdir", return_value=str(self.target)
        ), mock.patch.object(cli.tempfile, "TemporaryDirectory") as temporary:
            code, stdout, stderr = self.run_main(
                ["plan", str(self.target), "--to", cli.UPDATER_VERSION, "--json"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("temporary source root", json.loads(stdout)["errors"][0])
        temporary.assert_not_called()
        self.assert_target_unchanged()

    def test_remote_prepared_source_is_distinct_in_audit_kind(self) -> None:
        args = argparse.Namespace(
            target=str(self.target),
            archive=None,
            checksums=None,
            to=cli.UPDATER_VERSION,
        )
        manifest = {"bugate_version": cli.UPDATER_VERSION}

        def downloads(_version: str, directory: Path) -> tuple[Path, Path]:
            archive = directory / f"bugate-{cli.UPDATER_VERSION}.tar.gz"
            checksums = directory / f"bugate-{cli.UPDATER_VERSION}.SHA256SUMS"
            archive.write_bytes(b"archive")
            checksums.write_bytes(b"checksums")
            return archive, checksums

        with mock.patch.object(
            cli, "_unpacked_release_root", return_value=None
        ), mock.patch.object(
            cli, "_download_release", side_effect=downloads
        ), mock.patch.object(
            cli.source,
            "prepare_archive",
            return_value=SimpleNamespace(
                root=self.base / "prepared-remote",
                manifest=manifest,
                archive_sha256="a" * 64,
                source_kind="archive",
                root_identity=(1, 2),
            ),
        ):
            with cli._prepared_release(args) as prepared:
                self.assertEqual(prepared.source_kind, "remote")
                self.assertEqual(prepared.archive_sha256, "a" * 64)

    def test_newer_target_does_not_reuse_current_vendored_release(self) -> None:
        args = argparse.Namespace(
            target=str(self.target),
            archive=None,
            checksums=None,
            to=cli.UPDATER_VERSION,
        )
        current_root = self.base / "current-vendored-kit"
        current = SimpleNamespace(
            root=current_root,
            manifest={"bugate_version": "0.4.1"},
            archive_sha256=None,
            source_kind="unpacked",
            root_identity=(1, 1),
        )
        downloaded = SimpleNamespace(
            root=self.base / "downloaded-target-kit",
            manifest={"bugate_version": cli.UPDATER_VERSION},
            archive_sha256="b" * 64,
            source_kind="archive",
            root_identity=(2, 2),
        )

        def downloads(_version: str, directory: Path) -> tuple[Path, Path]:
            archive = directory / f"bugate-{cli.UPDATER_VERSION}.tar.gz"
            checksums = directory / f"bugate-{cli.UPDATER_VERSION}.SHA256SUMS"
            archive.write_bytes(b"archive")
            checksums.write_bytes(b"checksums")
            return archive, checksums

        with mock.patch.object(
            cli, "_unpacked_release_root", return_value=current_root
        ), mock.patch.object(
            cli.source, "prepare_unpacked", return_value=current
        ) as prepare_unpacked, mock.patch.object(
            cli, "_download_release", side_effect=downloads
        ) as download, mock.patch.object(
            cli.source, "prepare_archive", return_value=downloaded
        ):
            with cli._prepared_release(args) as prepared:
                self.assertEqual(prepared.source_kind, "remote")
                self.assertEqual(
                    prepared.manifest["bugate_version"], cli.UPDATER_VERSION
                )
        prepare_unpacked.assert_called_once_with(current_root)
        download.assert_called_once()

    def test_retrust_message_requires_actual_codex_hook_hash_change(self) -> None:
        unchanged = self.plan(hook_changed=False)
        changed = self.plan(hook_changed=True)
        self.assertNotIn("re-trust required", cli._human_plan(unchanged))
        self.assertNotIn(
            "re-trust required",
            cli._human_report(unchanged, "Synthetic report"),
        )
        self.assertIn(
            "Codex hook hash changed: re-trust required", cli._human_plan(changed)
        )
        self.assertIn(
            "Codex hook hash changed: re-trust required",
            cli._human_report(changed, "Synthetic report"),
        )

    def test_human_outputs_show_status_and_profile_migration_contract(self) -> None:
        status = {
            "kind": "locked",
            "version": cli.UPDATER_VERSION,
            "vendor_dir": ".bugate",
            "decision": "GO",
        }
        rendered_status = cli._human_status(status)
        self.assertIn("State: locked", rendered_status)
        self.assertIn(f"Installed version: {cli.UPDATER_VERSION}", rendered_status)
        self.assertIn("Vendor dir: .bugate", rendered_status)
        self.assertNotIn("unknown", rendered_status)
        self.assertNotIn("unrecognized", rendered_status)

        plan = self.plan()
        plan["profile_compatibility"] = {
            "status": "compatible",
            "migration": "migration_available",
            "blocking": False,
            "role_governance_activated": False,
        }
        self.assertIn("Profile: migration_available", cli._human_plan(plan))
        report = {
            "decision": "GO",
            "profile_migration": plan["profile_compatibility"],
            "role_governance_activated": False,
        }
        rendered_report = cli._human_report(report, "Synthetic report")
        self.assertIn("Profile: migration_available", rendered_report)
        self.assertIn("Role-governance activation: False", rendered_report)

    def test_absent_status_is_human_no_go_and_nonzero(self) -> None:
        code, stdout, stderr = self.run_main(
            ["status", str(self.target), "--vendor-dir", ".bugate"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("State: absent", stdout)
        self.assertIn("Installed version: unrecognized", stdout)
        self.assertIn("Vendor dir: .bugate", stdout)
        self.assertIn("Decision: NO-GO", stdout)
        self.assert_target_unchanged()

    def test_download_declared_or_streamed_oversize_cleans_partial_file(self) -> None:
        cases = (
            FakeResponse([b"unused"], content_length="5"),
            FakeResponse([b"abc", b"def"]),
            FakeResponse([], content_length="not-an-integer"),
            FakeResponse([b"partial"], read_error=OSError("synthetic read error")),
        )
        for index, response in enumerate(cases):
            destination = self.base / f"download-{index}.bin"
            with self.subTest(case=index), mock.patch.object(
                cli.urllib.request, "urlopen", return_value=response
            ):
                with self.assertRaises(cli.CliError):
                    cli._download("https://invalid.example/asset", destination, limit=4)
                self.assertFalse(destination.exists())

    def test_download_error_never_deletes_a_preexisting_destination(self) -> None:
        destination = self.base / "operator-owned.bin"
        destination.write_bytes(b"keep-me")
        with mock.patch.object(
            cli.urllib.request,
            "urlopen",
            return_value=FakeResponse([b"replacement"]),
        ):
            with self.assertRaises(cli.CliError):
                cli._download(
                    "https://invalid.example/asset", destination, limit=1024
                )
        self.assertEqual(destination.read_bytes(), b"keep-me")

    def test_url_error_and_second_asset_failure_clean_download_set(self) -> None:
        destination = self.base / "failed.bin"
        with mock.patch.object(
            cli.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("synthetic offline"),
        ):
            with self.assertRaises(cli.CliError):
                cli._download(
                    "https://invalid.example/asset", destination, limit=1024
                )
        self.assertFalse(destination.exists())

        downloads = self.base / "downloads"
        downloads.mkdir()

        def fail_second(_url: str, path: Path, *, limit: int) -> None:
            del limit
            if path.suffix == ".gz":
                path.write_bytes(b"verified-archive-placeholder")
                return
            raise cli.CliError("synthetic checksum download failure")

        with mock.patch.object(cli, "_download", side_effect=fail_second):
            with self.assertRaises(cli.CliError):
                cli._download_release(cli.UPDATER_VERSION, downloads)
        self.assertEqual(list(downloads.iterdir()), [])

    def test_download_set_cleanup_preserves_preexisting_operator_files(self) -> None:
        for occupied_name in (
            f"bugate-{cli.UPDATER_VERSION}.tar.gz",
            f"bugate-{cli.UPDATER_VERSION}.SHA256SUMS",
        ):
            directory = self.base / occupied_name.replace(".", "-")
            directory.mkdir()
            occupied = directory / occupied_name
            occupied.write_bytes(b"operator-owned")
            with self.subTest(occupied=occupied_name), mock.patch.object(
                cli.urllib.request,
                "urlopen",
                return_value=FakeResponse([b"new-download"]),
            ):
                with self.assertRaises(cli.CliError):
                    cli._download_release(cli.UPDATER_VERSION, directory)
                self.assertEqual(occupied.read_bytes(), b"operator-owned")
                self.assertEqual(
                    {path.name for path in directory.iterdir()}, {occupied_name}
                )


if __name__ == "__main__":
    unittest.main()
