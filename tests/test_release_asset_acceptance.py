#!/usr/bin/env python3
"""Focused fail-closed tests for the archive-native release gate."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import accept_release_assets as acceptance


class ReleaseAssetAcceptanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="bugate-release-asset-contract-"
        )
        self.root = Path(self.temporary.name)
        self.tar = self.root / "bugate-0.4.2.tar.gz"
        self.zip = self.root / "bugate-0.4.2.zip"
        self.sums = self.root / "bugate-0.4.2.SHA256SUMS"
        self.tar.write_bytes(b"synthetic tar bytes")
        self.zip.write_bytes(b"synthetic zip bytes")
        self.write_sums()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_sums(self) -> None:
        self.sums.write_text(
            "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in (self.tar, self.zip)
            ),
            encoding="ascii",
        )

    def test_checksum_set_requires_exact_pair_and_matching_bytes(self) -> None:
        records = acceptance._parse_checksums(self.sums, (self.tar, self.zip))
        self.assertEqual(set(records), {self.tar.name, self.zip.name})
        self.assertEqual(records[self.tar.name], hashlib.sha256(self.tar.read_bytes()).hexdigest())

        self.tar.write_bytes(b"drift")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "checksum mismatch"):
            acceptance._parse_checksums(self.sums, (self.tar, self.zip))

    def test_checksum_set_rejects_duplicate_extra_and_unsafe_names(self) -> None:
        digest = "a" * 64
        cases = {
            "duplicate": f"{digest}  {self.tar.name}\n{digest}  {self.tar.name}\n",
            "extra": (
                f"{digest}  {self.tar.name}\n{digest}  {self.zip.name}\n"
                f"{digest}  extra.tar.gz\n"
            ),
            "unsafe": f"{digest}  ../{self.tar.name}\n{digest}  {self.zip.name}\n",
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                self.sums.write_text(content, encoding="ascii")
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance._parse_checksums(self.sums, (self.tar, self.zip))

    def test_pollution_scan_rejects_cache_secret_and_machine_path(self) -> None:
        release = self.root / "release"
        release.mkdir()
        cases = (
            ("scripts/__pycache__/worker.pyc", b"bytecode", ()),
            (
                "scripts/key.txt",
                b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----\nnot-a-real-key\n",
                (),
            ),
            (
                "docs/path.txt",
                b"generated under /synthetic/machine/home/project\n",
                (b"/synthetic/machine/home",),
            ),
        )
        for relative, payload, markers in cases:
            with self.subTest(relative=relative):
                for child in sorted(release.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                path = release / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                manifest = {
                    "archive_inventory": [
                        {"path": relative, "type": "file", "mode": "0644"}
                    ]
                }
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance._scan_release_tree(
                        release,
                        manifest,
                        machine_markers=markers,
                    )

    def test_pollution_scan_accepts_synthetic_fixture_path_not_current_home(self) -> None:
        release = self.root / "clean-release"
        path = release / "tests/test_fixture.py"
        path.parent.mkdir(parents=True)
        path.write_text("CACHE = '/Users/somedev/work/cache/'\n", encoding="utf-8")
        result = acceptance._scan_release_tree(
            release,
            {
                "archive_inventory": [
                    {"path": "tests/test_fixture.py", "type": "file", "mode": "0644"}
                ]
            },
            machine_markers=(str(Path.home()).encode(),),
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["cache_secret_machine_path_findings"], 0)

    def test_argument_error_is_one_machine_readable_document_and_exit_two(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = acceptance.main(["--version", "0.4.2"])
        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision"], "NO-GO")
        self.assertEqual(payload["status"], "argument_error")

    def test_report_destination_inside_core_is_rejected_before_stdout(self) -> None:
        output = io.StringIO()
        destination = acceptance.REPO / ".release-acceptance-unsafe.json"
        destination.unlink(missing_ok=True)
        with contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "outside the Core checkout",
            ):
                acceptance._emit({"decision": "GO"}, str(destination))
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(destination.exists())

    def test_nested_report_parent_inside_core_is_rejected_with_zero_directory_write(self) -> None:
        parent = acceptance.REPO / f".release-acceptance-unsafe-{os.getpid()}"
        destination = parent / "nested/report.json"
        self.assertFalse(parent.exists() or parent.is_symlink())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "outside the Core checkout",
            ):
                acceptance._emit({"decision": "GO"}, str(destination))
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(parent.exists() or parent.is_symlink())

    def test_report_parent_symlink_resolving_into_core_is_zero_write_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        core_link = outside / "core-link"
        core_link.symlink_to(acceptance.REPO, target_is_directory=True)
        core_child = acceptance.REPO / f".release-acceptance-resolved-{os.getpid()}"
        destination = core_link / core_child.name / "nested/report.json"
        self.assertFalse(core_child.exists() or core_child.is_symlink())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "resolves inside the Core checkout",
            ):
                acceptance._emit({"decision": "GO"}, str(destination))
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(core_child.exists() or core_child.is_symlink())

    def test_safe_nested_report_parent_is_created_only_after_boundary_checks(self) -> None:
        destination = self.root / "safe-report-parent/nested/report.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            acceptance._emit({"decision": "GO"}, str(destination))
        self.assertEqual(json.loads(output.getvalue()), {"decision": "GO"})
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8")),
            {"decision": "GO"},
        )

    def _hook_fixture(self, project: Path):
        target = ".codex/hooks.json"
        owned_value = {
            "matcher": "apply_patch",
            "hooks": [{"type": "command", "command": ".bugate/scripts/check.py"}],
        }
        sut_value = {
            "matcher": "synthetic-owned",
            "hooks": [{"type": "command", "command": "./bin/synthetic-hook"}],
        }
        owned = [
            {
                "id": "hook:codex:pre-tool-use",
                "scope": "shared_json_fragment",
                "target_path": target,
                "event": "PreToolUse",
                "value": owned_value,
                "semantic_digest": acceptance.contract.semantic_digest(
                    {"event": "PreToolUse", "value": owned_value}
                ),
            }
        ]
        document = {
            "synthetic_owned": {"preserve": True},
            "hooks": {"PreToolUse": [sut_value, owned_value]},
        }
        raw = (json.dumps(document, indent=2) + "\n").encode()
        path = project / target
        acceptance._write(path, raw)
        expectations = {
            target: {
                "sut_semantic_projection": acceptance._sut_hook_semantic_projection(
                    raw,
                    owned,
                    target_path=target,
                ),
                "markers": (),
            }
        }
        return path, document, owned, expectations

    def test_hook_projection_rejects_duplicate_missing_rewritten_and_duplicate_owned(self) -> None:
        project = self.root / "hook-project"
        project.mkdir()
        path, original, owned, expectations = self._hook_fixture(project)
        acceptance._assert_hook_preservation(project, expectations, owned)

        cases = {}
        duplicate_sut = copy.deepcopy(original)
        duplicate_sut["hooks"]["PreToolUse"].insert(
            1,
            copy.deepcopy(duplicate_sut["hooks"]["PreToolUse"][0]),
        )
        cases["duplicate_sut"] = duplicate_sut
        missing_sut = copy.deepcopy(original)
        del missing_sut["hooks"]["PreToolUse"][0]
        cases["missing_sut"] = missing_sut
        rewritten_sut = copy.deepcopy(original)
        rewritten_sut["hooks"]["PreToolUse"][0]["hooks"][0]["command"] += " --changed"
        cases["rewritten_sut"] = rewritten_sut
        duplicate_owned = copy.deepcopy(original)
        duplicate_owned["hooks"]["PreToolUse"].append(
            copy.deepcopy(duplicate_owned["hooks"]["PreToolUse"][-1])
        )
        cases["duplicate_owned"] = duplicate_owned

        for label, document in cases.items():
            with self.subTest(label=label):
                path.write_text(json.dumps(document) + "\n", encoding="utf-8")
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance._assert_hook_preservation(
                        project,
                        expectations,
                        owned,
                    )

    def test_passing_full_check_that_mutates_sut_state_is_no_go(self) -> None:
        project = self.root / "full-check-mutation-project"
        project.mkdir()
        sut_paths = acceptance._populate_sut_owned(project)
        _hook, _document, owned, expectations = self._hook_fixture(project)
        marked = {
            "target_path": ".gitignore",
            "begin": "# >>> synthetic BUGate block >>>",
            "end": "# <<< synthetic BUGate block <<<",
            "content": (
                "# >>> synthetic BUGate block >>>\n"
                "/.bugate-update/\n"
                "# <<< synthetic BUGate block <<<\n"
            ),
        }
        acceptance._write(
            project / ".gitignore",
            (
                "# SUT prefix\n"
                + marked["content"]
                + "# SUT suffix\n"
            ).encode(),
        )
        acceptance._initialize_dirty_git_repo(project)
        sut_before = acceptance._selected_snapshot(project, sut_paths)
        gitignore_outside = acceptance._outside_marked_block(
            (project / ".gitignore").read_bytes(),
            marked,
        )

        def fake_passing_full_check(*_args, **_kwargs):
            config = project / "bugate.config.yaml"
            config.write_bytes(config.read_bytes() + b"# illicit full-check mutation\n")
            return {"status": "passed", "result": "PASS"}

        with mock.patch.object(
            acceptance,
            "_run_imported_full_check",
            side_effect=fake_passing_full_check,
        ):
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "SUT-owned assets changed after imported full-check",
            ):
                acceptance._run_imported_full_check_with_preservation(
                    project,
                    self.root / "full-check-base",
                    {},
                    object(),  # patched full-check never reads the server
                    mode="smoke",
                    timeout_seconds=60,
                    sut_paths=sut_paths,
                    sut_before=sut_before,
                    hook_expectations=expectations,
                    owned_hook_projection=owned,
                    marked=marked,
                    gitignore_outside=gitignore_outside,
                )

    def test_full_check_runtime_contract_is_explicit(self) -> None:
        parser = acceptance.build_parser()
        base = [
            "--tar",
            str(self.tar),
            "--zip",
            str(self.zip),
            "--checksums",
            str(self.sums),
            "--version",
            "0.4.2",
        ]
        default = parser.parse_args(base)
        self.assertEqual(default.full_check_mode, "smoke")
        self.assertEqual(default.full_check_archive, "tar")
        real = parser.parse_args(
            [
                *base,
                "--full-check-mode",
                "full",
                "--full-check-archive",
                "both",
                "--full-check-timeout",
                "1800",
            ]
        )
        self.assertEqual(real.full_check_mode, "full")
        self.assertEqual(real.full_check_archive, "both")
        self.assertEqual(real.full_check_timeout, 1800)

    def _strict_transition_records(self) -> list[dict[str, object]]:
        events = (
            ("human_acceptance", "pre_code"),
            ("evidence_recovery", "pre_code"),
            ("designer_handoff", "pre_code"),
            ("implementer_acceptance", "implementation"),
            ("implementer_handoff", "implementation"),
            ("reviewer_acceptance", "post_run"),
            ("reviewer_completion", "post_run"),
        )
        lineage_id = "1" * 64
        records: list[dict[str, object]] = []
        for sequence, (event, phase) in enumerate(events):
            expected_head = "" if sequence == 0 else f"{sequence:x}" * 64
            records.append(
                {
                    "metadata": {
                        "role_transition": {
                            "schema": "bugate.role-transition/v1",
                            "event": event,
                            "phase": phase,
                            "previous_receipt_sha256": expected_head,
                            "transition_sha256": f"{sequence + 8:x}" * 64,
                            "lineage": {
                                "schema": "bugate.role-lineage-precondition/v1",
                                "lineage_id": lineage_id,
                                "expected_head_sha256": expected_head,
                                "expected_sequence": sequence,
                                "expected_revision": sequence,
                            },
                        }
                    }
                }
            )
        return records

    def test_strict_memory_contract_accepts_recovery_augmented_seven_event_chain(self) -> None:
        transitions = acceptance._validate_strict_transition_records(
            reversed(self._strict_transition_records())
        )
        self.assertEqual(
            [transition["event"] for transition in transitions],
            [
                "human_acceptance",
                "evidence_recovery",
                "designer_handoff",
                "implementer_acceptance",
                "implementer_handoff",
                "reviewer_acceptance",
                "reviewer_completion",
            ],
        )
        self.assertEqual(
            [transition["lineage"]["expected_sequence"] for transition in transitions],
            list(range(7)),
        )

    def test_strict_memory_contract_rejects_missing_reordered_or_diverged_lineage(self) -> None:
        cases = {
            "missing": self._strict_transition_records()[:-1],
            "event_order_diverged": self._strict_transition_records(),
            "phase_diverged": self._strict_transition_records(),
            "revision_diverged": self._strict_transition_records(),
            "head_diverged": self._strict_transition_records(),
            "lineage_diverged": self._strict_transition_records(),
        }
        cases["event_order_diverged"][1]["metadata"]["role_transition"][
            "event"
        ] = "designer_handoff"
        cases["phase_diverged"][2]["metadata"]["role_transition"][
            "phase"
        ] = "implementation"
        cases["revision_diverged"][3]["metadata"]["role_transition"]["lineage"][
            "expected_revision"
        ] = 99
        cases["head_diverged"][4]["metadata"]["role_transition"]["lineage"][
            "expected_head_sha256"
        ] = "f" * 64
        cases["lineage_diverged"][5]["metadata"]["role_transition"]["lineage"][
            "lineage_id"
        ] = "2" * 64

        for label, records in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance._validate_strict_transition_records(records)


if __name__ == "__main__":
    unittest.main()
