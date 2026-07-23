#!/usr/bin/env python3
"""Claude/Codex hook tests for role phase classification and drift relocking."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import role_governance as rg  # noqa: E402
from test_role_governance import PRECODE, Fixture, fake_memory, role_env  # noqa: E402


HOOK = ROOT / "scripts" / "check_role_evidence.py"


class _MemoryTrapServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, _MemoryTrapHandler)
        self.calls: list[tuple[str, str, bytes]] = []


class _MemoryTrapHandler(http.server.BaseHTTPRequestHandler):
    def _record(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.calls.append((self.command, self.path, body))  # type: ignore[attr-defined]
        self.send_response(204)
        self.end_headers()

    do_DELETE = _record
    do_GET = _record
    do_HEAD = _record
    do_PATCH = _record
    do_POST = _record
    do_PUT = _record

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_hook(
    root: Path,
    payload: dict,
    *,
    role: str = "",
    session: str = "",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["BUGATE_PROJECT_ROOT"] = str(root)
    env.pop("BUGATE_PROFILE", None)
    if role:
        env["BUGATE_AGENT_ROLE"] = role
    else:
        env.pop("BUGATE_AGENT_ROLE", None)
    if session:
        env["BUGATE_SESSION_ID"] = session
    else:
        env.pop("BUGATE_SESSION_ID", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )


def claude(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def codex(path: str) -> dict:
    patch = f"*** Begin Patch\n*** Update File: {path}\n@@\n-old\n+new\n*** End Patch"
    return {"tool_name": "apply_patch", "tool_input": {"input": patch}}


class RoleEvidenceHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-role-hook-")
        self.extra_tmp_contexts: list[tempfile.TemporaryDirectory[str]] = []
        self.root = Path(self.tmp_ctx.name)
        self.old_project = os.environ.get("BUGATE_PROJECT_ROOT")
        self.old_profile = os.environ.pop("BUGATE_PROFILE", None)
        self.old_memory_homes = {
            key: os.environ.get(key)
            for key in ("MCP_MEMORY_BASE_DIR", "BUGATE_MEMORY_HOME")
        }
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.root)
        memory_home = self.root / "memory-home"
        memory_home.mkdir(mode=0o700)
        os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
        os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
        self.original_prepare = rg._memory_prepare
        self.original_verify = rg._memory_verify
        self.original_lineage_root = rg._memory_ensure_lineage_root
        self.original_lineage_probe = rg._memory_probe_lineage_root
        self.original_checkpoint_create = rg._memory_create_checkpoint
        self.original_checkpoint_get = rg._memory_get_checkpoint
        self.original_semantics = rg.verify_precode_semantics
        self.fake_roots: dict[str, dict] = {}
        self.fake_checkpoints: dict[str, dict] = {}

        def fake_root(_ctx, key):
            payload = {
                "schema": "bugate.role-lineage-root/v1",
                "lineage_key": key.as_dict(),
                "lineage_id": key.lineage_id,
            }
            exact_id = rg.sha256_bytes(rg.canonical_json(payload))
            value = {
                "namespace": key.namespace,
                "lineage_id": key.lineage_id,
                "lineage_root_id": exact_id,
                "memory_id": exact_id,
                "content_sha256": exact_id,
                "payload": payload,
                "status": "verified",
            }
            self.fake_roots[key.lineage_id] = value
            return json.loads(json.dumps(value))

        def fake_root_probe(_ctx, key):
            value = self.fake_roots.get(key.lineage_id)
            return json.loads(json.dumps(value)) if value is not None else None

        def fake_checkpoint(_ctx, payload):
            exact_id = rg.sha256_bytes(rg.canonical_json(payload))
            value = {
                "namespace": payload["lineage_key"]["namespace"],
                "lineage_id": payload["lineage_id"],
                "lineage_root_id": payload["lineage_root_id"],
                "checkpoint_id": exact_id,
                "memory_id": exact_id,
                "content_sha256": exact_id,
                "sequence": payload["sequence"],
                "registry_revision": payload["registry_revision"],
                "resulting_state": payload["resulting_state"],
                "payload": json.loads(json.dumps(payload)),
                "status": "verified",
            }
            self.fake_checkpoints[exact_id] = value
            return json.loads(json.dumps(value))

        rg._memory_prepare = fake_memory
        rg._memory_verify = lambda ctx, receipt: None
        rg._memory_ensure_lineage_root = fake_root
        rg._memory_probe_lineage_root = fake_root_probe
        rg._memory_create_checkpoint = fake_checkpoint
        rg._memory_get_checkpoint = lambda _ctx, checkpoint_id: json.loads(
            json.dumps(self.fake_checkpoints[checkpoint_id])
        )
        rg.verify_precode_semantics = lambda ctx: None
        self.fx = Fixture(self.root)

    def use_required_fixture(self) -> None:
        extra = tempfile.TemporaryDirectory(prefix="bugate-role-hook-required-")
        self.extra_tmp_contexts.append(extra)
        self.root = Path(extra.name)
        memory_home = self.root / "memory-home"
        memory_home.mkdir(mode=0o700)
        os.environ["BUGATE_PROJECT_ROOT"] = str(self.root)
        os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
        os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
        self.fx = Fixture(self.root, memory_mode="required")

    def tearDown(self):
        rg._memory_prepare = self.original_prepare
        rg._memory_verify = self.original_verify
        rg._memory_ensure_lineage_root = self.original_lineage_root
        rg._memory_probe_lineage_root = self.original_lineage_probe
        rg._memory_create_checkpoint = self.original_checkpoint_create
        rg._memory_get_checkpoint = self.original_checkpoint_get
        rg.verify_precode_semantics = self.original_semantics
        if self.old_project is None:
            os.environ.pop("BUGATE_PROJECT_ROOT", None)
        else:
            os.environ["BUGATE_PROJECT_ROOT"] = self.old_project
        if self.old_profile is not None:
            os.environ["BUGATE_PROFILE"] = self.old_profile
        for key, value in self.old_memory_homes.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("BUGATE_AGENT_ROLE", None)
        os.environ.pop("BUGATE_SESSION_ID", None)
        for extra in reversed(self.extra_tmp_contexts):
            extra.cleanup()
        self.tmp_ctx.cleanup()

    def test_precode_unset_wrong_role_and_both_payload_shapes(self):
        target = "usecases/UC-001/01_business_brief.md"
        unset = run_hook(self.root, claude(target))
        self.assertEqual(2, unset.returncode)
        self.assertIn("BUGATE_AGENT_ROLE is unset", unset.stderr)
        wrong = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, wrong.returncode)
        self.assertIn("not allowed in pre_code", wrong.stderr)
        for payload in (claude(target), codex(target)):
            allowed = run_hook(self.root, payload, role="designer", session="d-1")
            self.assertEqual(0, allowed.returncode, allowed.stderr)

    def test_case_or_identity_aliases_preserve_phase_classification(self):
        canonical_report = self.fx.artifact / "04_execution_report.md"
        canonical_report.write_text("gate_status: draft\n", encoding="utf-8")
        case_report = self.fx.artifact / "04_EXECUTION_REPORT.MD"
        case_insensitive = case_report.exists() and os.path.samefile(
            canonical_report, case_report
        )
        targets = {
            "post_run": [
                "usecases/UC-001/04_EXECUTION_REPORT.MD",
                "usecases/UC-001/04_EXECUTION/new.txt",
            ],
            "pre_code": [
                "usecases/UC-001/00_MULTIVIEW/new.md",
                "usecases/UC-001/00_ADVERSARIAL/new.yaml",
            ],
        }
        if not case_insensitive:
            for phase_targets in targets.values():
                for target in phase_targets:
                    for payload in (claude(target), codex(target)):
                        with self.subTest(
                            kind="case-sensitive independent phase path",
                            target=target,
                            tool=payload["tool_name"],
                        ):
                            allowed = run_hook(
                                self.root,
                                payload,
                                role="implementer",
                                session="i-independent",
                            )
                            self.assertEqual(0, allowed.returncode, allowed.stderr)
            aliases = {
                "04_EXECUTION_REPORT.MD": canonical_report,
                "04_EXECUTION": self.fx.artifact / "04_execution",
                "00_MULTIVIEW": self.fx.artifact / "00_multiview",
                "00_ADVERSARIAL": self.fx.artifact / "00_adversarial",
            }
            for name, canonical in aliases.items():
                if canonical.suffix:
                    (self.fx.artifact / name).symlink_to(canonical)
                else:
                    canonical.mkdir(exist_ok=True)
                    (self.fx.artifact / name).symlink_to(
                        canonical, target_is_directory=True
                    )

        for phase, phase_targets in targets.items():
            for target in phase_targets:
                for payload in (claude(target), codex(target)):
                    with self.subTest(phase=phase, target=target, tool=payload["tool_name"]):
                        blocked = run_hook(
                            self.root,
                            payload,
                            role="implementer",
                            session="i-wrong",
                        )
                        self.assertEqual(2, blocked.returncode, blocked.stderr)
                        self.assertIn(f"not allowed in {phase}", blocked.stderr)

        guarded_targets = ["tests/TEST_UC-001.PY", "TESTS/TEST_UC-001.PY"]
        if not case_insensitive:
            for target in guarded_targets:
                for payload in (claude(target), codex(target)):
                    with self.subTest(
                        kind="case-sensitive independent guarded path",
                        target=target,
                        tool=payload["tool_name"],
                    ):
                        allowed = run_hook(self.root, payload)
                        self.assertEqual(0, allowed.returncode, allowed.stderr)
            canonical_implementation = self.root / "tests" / "test_UC-001.py"
            canonical_implementation.write_text("fixture\n", encoding="utf-8")
            (self.root / "tests" / "TEST_UC-001.PY").symlink_to(
                canonical_implementation
            )
            (self.root / "TESTS").symlink_to(
                self.root / "tests", target_is_directory=True
            )
            guarded_targets = ["tests/TEST_UC-001.PY", "TESTS/test_UC-001.py"]
        for target in guarded_targets:
            for payload in (claude(target), codex(target)):
                with self.subTest(
                    kind="case-or-identity guarded path",
                    target=target,
                    tool=payload["tool_name"],
                ):
                    blocked = run_hook(self.root, payload)
                    self.assertEqual(2, blocked.returncode, blocked.stderr)

    def test_evidence_direct_edit_always_blocked_when_enabled(self):
        target = "usecases/UC-001/00_role_evidence/chain.json"
        for payload in (claude(target), codex(target)):
            result = run_hook(self.root, payload, role="designer", session="d-1")
            self.assertEqual(2, result.returncode)
            self.assertIn("direct edits", result.stderr)
        multi = {
            "tool_name": "apply_patch",
            "tool_input": {
                "input": (
                    "*** Begin Patch\n"
                    "*** Update File: notes.md\n@@\n-old\n+new\n"
                    f"*** Update File: {target}\n@@\n-old\n+new\n"
                    "*** End Patch"
                )
            },
        }
        result = run_hook(self.root, multi, role="designer", session="d-1")
        self.assertEqual(2, result.returncode)
        self.assertIn(target, result.stderr)
        evidence_dir = self.fx.artifact / "00_role_evidence"
        evidence_dir.mkdir(exist_ok=True)
        (self.root / "evidence-alias").symlink_to(evidence_dir, target_is_directory=True)
        for payload in (claude("evidence-alias/chain.json"), codex("evidence-alias/chain.json")):
            alias = run_hook(
                self.root,
                payload,
                role="designer",
                session="d-1",
            )
            self.assertEqual(2, alias.returncode, alias.stderr)
            self.assertIn("direct edits", alias.stderr)

    def test_implementation_unlock_and_artifact_profile_drift_relock(self):
        target = "tests/test_UC-001.py"
        locked = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, locked.returncode)
        self.assertIn("acceptance missing", locked.stderr)

        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(self.fx.artifact, phase="pre_code", to_role="implementer")
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["receipt_sha256"],
            )
        allowed = run_hook(self.root, claude(target), role="implementer", session="i-1")
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        stolen = run_hook(self.root, codex(target), role="implementer", session="i-2")
        self.assertEqual(2, stolen.returncode)
        self.assertIn("different BUGATE_SESSION_ID", stolen.stderr)

        brief = self.fx.artifact / "01_business_brief.md"
        old = brief.read_bytes()
        brief.write_bytes(old + b"drift\n")
        drift = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, drift.returncode)
        self.assertIn("artifact drift", drift.stderr)
        brief.write_bytes(old)

        profile = self.root / "bugate.profile.yaml"
        old_profile = profile.read_bytes()
        profile.write_bytes(old_profile + b"# drift\n")
        drift = run_hook(self.root, codex(target), role="implementer", session="i-1")
        self.assertEqual(2, drift.returncode)
        self.assertIn("profile hash/path drifted", drift.stderr)

    def test_deleted_valid_lineage_blocks_hook_without_memory_http_or_state_writes(self):
        target = "tests/test_UC-001.py"
        with role_env("designer", "d-deletion"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                self.fx.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        with role_env("implementer", "i-deletion"):
            acceptance = rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["receipt_sha256"],
            )

        context = rg.load_context(self.fx.artifact)
        key = rg._lineage_key(context)
        registry = rg.lineage_registry.LineageRegistry()
        record_before = registry.require_lineage(key)
        chain_path = self.fx.artifact / "00_role_evidence" / "chain.json"
        chain_before = json.loads(chain_path.read_bytes())
        self.assertEqual("aligned", rg.status_data(self.fx.artifact)["integrity_state"])
        self.assertEqual(acceptance["receipt_sha256"], chain_before["head_sha256"])
        self.assertEqual(chain_before["head_sha256"], record_before.head_sha256)
        self.assertEqual(chain_before["sequence"], record_before.sequence)

        evidence_dir = chain_path.parent
        shutil.rmtree(evidence_dir)
        workspace_after_deletion = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
        memory_home = Path(os.environ["BUGATE_MEMORY_HOME"])
        registry_bytes_after_deletion = {
            path.relative_to(memory_home).as_posix(): path.read_bytes()
            for path in sorted(memory_home.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

        server = _MemoryTrapServer(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        memory_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(f"{memory_url}/trap-armed", timeout=2) as response:
                self.assertEqual(204, response.status)
            self.assertEqual([("GET", "/trap-armed", b"")], server.calls)
            server.calls.clear()

            blocked = run_hook(
                self.root,
                codex(target),
                role="implementer",
                session="i-deletion",
                extra_env={
                    "MEMORY_BUS_URL": memory_url,
                    "MCP_API_KEY_AGENT": "synthetic-memory-trap-token",
                },
            )
            self.assertEqual(2, blocked.returncode, blocked.stderr)
            self.assertIn("integrity_state=history_missing", blocked.stderr)
            self.assertEqual(
                0,
                len(server.calls),
                f"PreToolUse must remain local-only, got {server.calls!r}",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertFalse(evidence_dir.exists(), "hook must not recreate deleted evidence")
        workspace_after_hook = {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
        self.assertEqual(workspace_after_deletion, workspace_after_hook)
        self.assertEqual(
            registry_bytes_after_deletion,
            {
                path.relative_to(memory_home).as_posix(): path.read_bytes()
                for path in sorted(memory_home.rglob("*"))
                if path.is_file() and not path.is_symlink()
            },
        )
        record_after = rg.lineage_registry.LineageRegistry().require_lineage(key)
        self.assertEqual(record_before, record_after)
        self.assertEqual(acceptance["receipt_sha256"], record_after.head_sha256)

    def test_exact_checkpoint_drift_blocks_hook_without_memory_http(self):
        self.use_required_fixture()
        target = "tests/test_UC-001.py"
        with role_env("designer", "d-exact-drift"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                self.fx.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        with role_env("implementer", "i-exact-drift"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["memory"]["memory_id"],
            )
        context = rg.load_context(self.fx.artifact)
        key = rg._lineage_key(context)
        registry = rg.lineage_registry.LineageRegistry()
        record_before = registry.require_lineage(key)
        active_before = registry.list_active_transactions(key)
        evidence = self.fx.artifact / "00_role_evidence"
        older_receipt = sorted((evidence / "receipts").glob("*.json"))[0]
        older_receipt.write_text(
            json.dumps(json.loads(older_receipt.read_bytes()), separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(evidence / "chain.json", 0o666)
        mutated_receipt = older_receipt.read_bytes()
        mutated_chain_mode = (evidence / "chain.json").stat().st_mode & 0o7777

        server = _MemoryTrapServer(("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        memory_url = f"http://127.0.0.1:{server.server_port}"
        try:
            blocked = run_hook(
                self.root,
                codex(target),
                role="implementer",
                session="i-exact-drift",
                extra_env={
                    "MEMORY_BUS_URL": memory_url,
                    "MCP_API_KEY_AGENT": "synthetic-memory-trap-token",
                },
            )
            self.assertEqual(2, blocked.returncode, blocked.stderr)
            self.assertIn("integrity_state=history_diverged", blocked.stderr)
            self.assertEqual([], server.calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(mutated_receipt, older_receipt.read_bytes())
        self.assertEqual(
            mutated_chain_mode,
            (evidence / "chain.json").stat().st_mode & 0o7777,
        )
        self.assertEqual(record_before, registry.require_lineage(key))
        self.assertEqual(active_before, registry.list_active_transactions(key))

    def test_receipt_directory_symlink_blocks_hook_without_memory_http_or_writes(self):
        self.use_required_fixture()
        target = "tests/test_UC-001.py"
        with role_env("designer", "d-symlink-store"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                self.fx.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        with role_env("implementer", "i-symlink-store"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["memory"]["memory_id"],
            )
        context = rg.load_context(self.fx.artifact)
        key = rg._lineage_key(context)
        registry = rg.lineage_registry.LineageRegistry()
        record_before = registry.require_lineage(key)
        active_before = registry.list_active_transactions(key)
        receipts = self.fx.artifact / "00_role_evidence" / "receipts"

        with tempfile.TemporaryDirectory(prefix="bugate-role-external-receipts-") as outside:
            external = Path(outside) / "receipts"
            receipts.rename(external)
            receipts.symlink_to(external, target_is_directory=True)
            external_before = {
                path.name: path.read_bytes()
                for path in sorted(external.glob("*.json"))
            }
            server = _MemoryTrapServer(("127.0.0.1", 0))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            memory_url = f"http://127.0.0.1:{server.server_port}"
            try:
                blocked = run_hook(
                    self.root,
                    codex(target),
                    role="implementer",
                    session="i-symlink-store",
                    extra_env={
                        "MEMORY_BUS_URL": memory_url,
                        "MCP_API_KEY_AGENT": "synthetic-memory-trap-token",
                    },
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("integrity_state=history_diverged", blocked.stderr)
                self.assertIn("symlink", blocked.stderr)
                self.assertEqual([], server.calls)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.assertEqual(
                external_before,
                {
                    path.name: path.read_bytes()
                    for path in sorted(external.glob("*.json"))
                },
            )
            self.assertTrue(receipts.is_symlink())
            self.assertEqual(record_before, registry.require_lineage(key))
            self.assertEqual(active_before, registry.list_active_transactions(key))

    def test_lexical_phase_symlink_escapes_fail_closed_for_both_hook_shapes(self):
        notes = self.root / "notes.txt"
        notes.write_text("ordinary fixture notes\n", encoding="utf-8")
        implementation = self.root / "tests" / "test_UC-001.py"

        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                self.fx.artifact,
                phase="pre_code",
                to_role="implementer",
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )

        implementation.symlink_to(notes)
        guarded_targets = [
            implementation.relative_to(self.root).as_posix(),
            implementation.as_posix(),
        ]
        for target in guarded_targets:
            for payload in (claude(target), codex(target)):
                with self.subTest(kind="guarded escape", target=target, tool=payload["tool_name"]):
                    blocked = run_hook(
                        self.root,
                        payload,
                        role="implementer",
                        session="i-1",
                    )
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn("guarded surface", blocked.stderr)

        implementation.unlink()
        implementation.symlink_to(self.root / "missing-implementation.py")
        for payload in (claude("tests/test_UC-001.py"), codex("tests/test_UC-001.py")):
            with self.subTest(kind="dangling guarded escape", tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="implementer",
                    session="i-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("guarded surface", blocked.stderr)

        for payload in (claude("missing-implementation.py"), codex("missing-implementation.py")):
            with self.subTest(kind="reverse dangling guarded", tool=payload["tool_name"]):
                blocked = run_hook(self.root, payload)
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("implementation identity owner", blocked.stderr)

        implementation.unlink()
        with tempfile.TemporaryDirectory(prefix="bugate-role-hook-outside-") as outside:
            external_missing_implementation = Path(outside) / "missing-implementation.py"
            implementation.symlink_to(external_missing_implementation)
            for payload in (
                claude(external_missing_implementation.as_posix()),
                codex(external_missing_implementation.as_posix()),
            ):
                with self.subTest(
                    kind="reverse external dangling guarded",
                    tool=payload["tool_name"],
                ):
                    blocked = run_hook(self.root, payload)
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn("implementation identity owner", blocked.stderr)
            implementation.unlink()

        implementation.write_text("def test_fixture(): pass\n", encoding="utf-8")
        with role_env("implementer", "i-1"):
            implementer_handoff = rg.handoff(
                self.fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[implementation],
            )
        with role_env("reviewer", "r-1"):
            rg.accept(
                self.fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )

        brief = self.fx.artifact / "01_business_brief.md"
        brief.unlink()
        brief.symlink_to(notes)
        multiview_escape = self.fx.artifact / "00_multiview" / "escape.md"
        multiview_escape.symlink_to(notes)
        report_escape = self.fx.artifact / "04_execution_report.md"
        report_escape.symlink_to(notes)
        execution_dir = self.fx.artifact / "04_execution"
        execution_dir.mkdir()
        execution_escape = execution_dir / "raw.log"
        execution_escape.symlink_to(notes)
        dangling_precode = self.fx.artifact / "00_multiview" / "dangling.md"
        dangling_precode.symlink_to(self.root / "missing-precode.md")
        dangling_postrun = execution_dir / "dangling.log"
        dangling_postrun.symlink_to(self.root / "missing-postrun.log")

        phase_targets = {
            "precode": (
                "designer",
                "d-1",
                [
                    brief,
                    multiview_escape,
                    dangling_precode,
                ],
            ),
            "postrun": (
                "reviewer",
                "r-1",
                [
                    report_escape,
                    execution_escape,
                    dangling_postrun,
                ],
            ),
        }
        for kind, (role, session, paths) in phase_targets.items():
            for path in paths:
                target = path.relative_to(self.root).as_posix()
                for payload in (claude(target), codex(target)):
                    with self.subTest(kind=kind, target=target, tool=payload["tool_name"]):
                        blocked = run_hook(
                            self.root,
                            payload,
                            role=role,
                            session=session,
                        )
                        self.assertEqual(2, blocked.returncode, blocked.stderr)
                        self.assertIn("artifact surface", blocked.stderr)

        reverse_dangling_targets = {
            "precode": self.root / "missing-precode.md",
            "postrun": self.root / "missing-postrun.log",
        }
        for kind, missing_target in reverse_dangling_targets.items():
            for payload in (
                claude(missing_target.relative_to(self.root).as_posix()),
                codex(missing_target.relative_to(self.root).as_posix()),
            ):
                with self.subTest(
                    kind=f"reverse dangling {kind}",
                    tool=payload["tool_name"],
                ):
                    blocked = run_hook(self.root, payload)
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    owner_phase = "pre_code" if kind == "precode" else "post_run"
                    self.assertIn(f"{owner_phase} identity owner", blocked.stderr)

        with tempfile.TemporaryDirectory(prefix="bugate-role-hook-outside-") as outside:
            outside_path = Path(outside)
            external_precode = outside_path / "missing-precode.md"
            external_postrun = outside_path / "missing-postrun.log"
            external_precode_alias = self.fx.artifact / "00_multiview" / "external.md"
            external_postrun_alias = execution_dir / "external.log"
            external_precode_alias.symlink_to(external_precode)
            external_postrun_alias.symlink_to(external_postrun)
            for kind, missing_target in {
                "precode": external_precode,
                "postrun": external_postrun,
            }.items():
                for payload in (
                    claude(missing_target.as_posix()),
                    codex(missing_target.as_posix()),
                ):
                    with self.subTest(
                        kind=f"reverse external dangling {kind}",
                        tool=payload["tool_name"],
                    ):
                        blocked = run_hook(self.root, payload)
                        self.assertEqual(2, blocked.returncode, blocked.stderr)
                        owner_phase = "pre_code" if kind == "precode" else "post_run"
                        self.assertIn(f"{owner_phase} identity owner", blocked.stderr)

        # The ordinary target is also phase-owned by structural symlink
        # identity.  Editing it directly must not bypass the same preflight.
        for payload in (claude("notes.txt"), codex("notes.txt")):
            with self.subTest(kind="reverse structural identity", tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="reviewer",
                    session="r-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("identity owner", blocked.stderr)

        ordinary_target = self.root / "ordinary-target.txt"
        ordinary_target.write_text("ordinary unowned target\n", encoding="utf-8")
        ordinary_alias = self.root / "ordinary-alias.txt"
        ordinary_alias.symlink_to(ordinary_target)
        for payload in (claude("ordinary-alias.txt"), codex("ordinary-alias.txt")):
            with self.subTest(kind="ordinary symlink", tool=payload["tool_name"]):
                allowed = run_hook(self.root, payload)
                self.assertEqual(0, allowed.returncode, allowed.stderr)

        ordinary_missing = self.root / "ordinary-missing.txt"
        ordinary_dangling = self.root / "ordinary-dangling.txt"
        ordinary_dangling.symlink_to(ordinary_missing)
        for target in ("ordinary-dangling.txt", "ordinary-missing.txt"):
            for payload in (claude(target), codex(target)):
                with self.subTest(
                    kind="ordinary dangling symlink",
                    target=target,
                    tool=payload["tool_name"],
                ):
                    allowed = run_hook(self.root, payload)
                    self.assertEqual(0, allowed.returncode, allowed.stderr)

    def test_phase_owned_hardlinks_keep_their_original_owner(self):
        aliases = self.root / "phase-hardlinks"
        aliases.mkdir()
        implementation = self.fx.implementation
        implementation.write_text("fixture\n", encoding="utf-8")
        targets = {
            "pre_code": (self.fx.artifact / "01_business_brief.md", aliases / "brief.md"),
            "implementation": (implementation, aliases / "implementation.py"),
        }
        for source, alias in targets.values():
            os.link(source, alias)
            self.assertTrue(os.path.samefile(source, alias))

        for phase, (_source, alias) in targets.items():
            target = alias.relative_to(self.root).as_posix()
            for payload in (claude(target), codex(target)):
                with self.subTest(phase=phase, tool=payload["tool_name"]):
                    blocked = run_hook(
                        self.root,
                        payload,
                        role="reviewer",
                        session="r-wrong",
                    )
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn(f"{phase} identity owner", blocked.stderr)

    def test_unrelated_hardlink_ignores_malformed_sibling_role_store(self):
        notes = self.root / "notes.txt"
        alias = self.root / "notes-hardlink.txt"
        notes.write_text("ordinary fixture notes\n", encoding="utf-8")
        os.link(notes, alias)
        target = alias.relative_to(self.root).as_posix()
        malformed = (
            self.root
            / "usecases"
            / "UC-002"
            / "00_role_evidence"
            / "chain.json"
        )
        malformed.parent.mkdir(parents=True)
        malformed.write_text("{}\n", encoding="utf-8")
        receipts = malformed.parent / "receipts"
        receipts.mkdir()
        (receipts / "000001-reviewer-completion-fixture.json").write_text(
            json.dumps(
                {
                    "artifacts": [
                        {
                            "path": "runlogs/other.log",
                            "sha256": target,
                            "gate_status": target,
                            "metadata": [target],
                        }
                    ],
                    "implementation_files": [],
                    "run": {"command_summary": target},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (receipts / "broken-unrelated.json").write_text(
            '{"run":{"command_summary":'
            + json.dumps(target)
            + '},\n',
            encoding="utf-8",
        )

        for payload in (claude(target), codex(target)):
            with self.subTest(tool=payload["tool_name"]):
                allowed = run_hook(self.root, payload)
                self.assertEqual(0, allowed.returncode, allowed.stderr)

    def test_resolved_artifact_alias_cannot_unlock_a_different_guarded_uc(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["receipt_sha256"],
            )

        (self.root / "usecases" / "UC-002").symlink_to(
            "UC-001", target_is_directory=True
        )
        artifact_target = "usecases/UC-002/01_business_brief.md"
        for payload in (claude(artifact_target), codex(artifact_target)):
            with self.subTest(kind="artifact phase", tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="designer",
                    session="d-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("artifact lexical UC 'UC-002'", blocked.stderr)
                self.assertIn("resolved artifact UC 'UC-001'", blocked.stderr)

        target = "tests/test_UC-002.py"
        for payload in (claude(target), codex(target)):
            with self.subTest(tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="implementer",
                    session="i-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn(target, blocked.stderr)
                self.assertIn("UC-002", blocked.stderr)
                self.assertIn("UC-001", blocked.stderr)
                self.assertIn("artifact", blocked.stderr.lower())
                self.assertRegex(
                    blocked.stderr.lower(), r"(?:does not match|disagrees|mismatch)"
                )

    def test_copied_receipt_chain_cannot_unlock_guarded_uc_through_hook(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=handoff["receipt_sha256"],
            )

        copied_artifact = self.root / "usecases" / "UC-002"
        shutil.copytree(self.fx.artifact, copied_artifact)
        target = "tests/test_UC-002.py"
        for payload in (claude(target), codex(target)):
            with self.subTest(tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="implementer",
                    session="i-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn(target, blocked.stderr)
                self.assertIn(
                    "receipt UC does not match active context", blocked.stderr
                )

    def test_postrun_requires_reviewer_acceptance(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            self.fx.implementation.write_text("def test_fixture(): pass\n", encoding="utf-8")
            implementer_handoff = rg.handoff(
                self.fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[self.fx.implementation],
            )
        target = "usecases/UC-001/04_execution_report.md"
        locked = run_hook(self.root, claude(target), role="reviewer", session="r-1")
        self.assertEqual(2, locked.returncode)
        self.assertIn("reviewer acceptance missing", locked.stderr)
        with role_env("reviewer", "r-1"):
            rg.accept(
                self.fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
        allowed = run_hook(self.root, codex(target), role="reviewer", session="r-1")
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        stolen = run_hook(self.root, claude(target), role="reviewer", session="r-2")
        self.assertEqual(2, stolen.returncode)
        self.assertIn("different BUGATE_SESSION_ID", stolen.stderr)

    def test_closed_completion_blocks_postrun_writes_for_both_hook_shapes(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            self.fx.implementation.write_text(
                "def test_fixture(): pass\n", encoding="utf-8"
            )
            implementer_handoff = rg.handoff(
                self.fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[self.fx.implementation],
            )
        with role_env("reviewer", "r-1"):
            rg.accept(
                self.fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
            for name in sorted(rg.POSTRUN_NAMES):
                (self.fx.artifact / name).write_text(
                    "gate_status: passed\nfixture: postrun\n", encoding="utf-8"
                )
            log = self.root / "runlogs" / "execution.log"
            log.parent.mkdir()
            log.write_text("fixture run\n", encoding="utf-8")
            rg.complete(
                self.fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )
        (self.root / "runlogs" / "sub").mkdir()
        (self.root / "runlogs" / "alias.log").symlink_to("execution.log")

        for target in (
            "usecases/UC-001/04_execution_report.md",
            "runlogs/execution.log",
            "runlogs/sub/../execution.log",
            "runlogs/alias.log",
        ):
            for payload in (claude(target), codex(target)):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="reviewer",
                    session="r-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("post-run is closed", blocked.stderr)

        unrelated = self.root / "runlogs" / "sut-owned.log"
        unrelated.write_text("unrelated fixture\n", encoding="utf-8")
        for payload in (claude("runlogs/sut-owned.log"), codex("runlogs/sut-owned.log")):
            allowed = run_hook(
                self.root,
                payload,
                role="reviewer",
                session="r-1",
            )
            self.assertEqual(0, allowed.returncode, allowed.stderr)

    def test_existing_evidence_and_completion_aliases_block_both_hook_shapes(self):
        with role_env("designer", "d-1"):
            rg.approve(self.fx.artifact, approved_by="qa-owner")
            designer_handoff = rg.handoff(
                self.fx.artifact, phase="pre_code", to_role="implementer"
            )
        with role_env("implementer", "i-1"):
            rg.accept(
                self.fx.artifact,
                phase="implementation",
                handoff_id=designer_handoff["receipt_sha256"],
            )
            self.fx.implementation.write_text(
                "def test_fixture(): pass\n", encoding="utf-8"
            )
            implementer_handoff = rg.handoff(
                self.fx.artifact,
                phase="implementation",
                to_role="reviewer",
                implementation_files=[self.fx.implementation],
            )
        with role_env("reviewer", "r-1"):
            rg.accept(
                self.fx.artifact,
                phase="post_run",
                handoff_id=implementer_handoff["receipt_sha256"],
            )
            for name in sorted(rg.POSTRUN_NAMES):
                (self.fx.artifact / name).write_text(
                    "gate_status: passed\nfixture: postrun\n", encoding="utf-8"
                )
            log = self.root / "runlogs" / "execution.log"
            log.parent.mkdir()
            log.write_text("fixture run\n", encoding="utf-8")
            rg.complete(
                self.fx.artifact,
                phase="post_run",
                run_command="fixture runner",
                exit_code=0,
                evidence_files=[log],
                final_gate_status="passed",
            )

        chain = self.fx.artifact / "00_role_evidence" / "chain.json"
        case_chain = self.fx.artifact / "00_ROLE_EVIDENCE" / "CHAIN.JSON"
        case_insensitive = case_chain.exists() and os.path.samefile(chain, case_chain)
        if case_insensitive:
            evidence_alias = case_chain
            completion_alias = log.with_name("EXECUTION.LOG")
            self.assertTrue(completion_alias.exists())
            self.assertTrue(os.path.samefile(log, completion_alias))
        else:
            evidence_alias_dir = self.root / "role-evidence-identity-alias"
            evidence_alias_dir.symlink_to(
                chain.parent, target_is_directory=True
            )
            evidence_alias = evidence_alias_dir / chain.name
            completion_alias = log.with_name("execution-hardlink.log")
            os.link(log, completion_alias)
            self.assertTrue(os.path.samefile(chain, evidence_alias))
            self.assertTrue(os.path.samefile(log, completion_alias))

        evidence_target = evidence_alias.relative_to(self.root).as_posix()
        evidence_hardlink = self.root / "chain-hardlink.json"
        os.link(chain, evidence_hardlink)
        self.assertTrue(os.path.samefile(chain, evidence_hardlink))
        for target in (
            evidence_target,
            evidence_hardlink.relative_to(self.root).as_posix(),
        ):
            for payload in (claude(target), codex(target)):
                with self.subTest(kind="role evidence", tool=payload["tool_name"]):
                    blocked = run_hook(
                        self.root,
                        payload,
                        role="reviewer",
                        session="r-1",
                    )
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn("direct edits", blocked.stderr)

        completion_target = completion_alias.relative_to(self.root).as_posix()
        for payload in (claude(completion_target), codex(completion_target)):
            with self.subTest(kind="completion evidence", tool=payload["tool_name"]):
                blocked = run_hook(
                    self.root,
                    payload,
                    role="reviewer",
                    session="r-1",
                )
                self.assertEqual(2, blocked.returncode, blocked.stderr)
                self.assertIn("post-run is closed", blocked.stderr)

        completion_receipt = sorted(
            (
                self.fx.artifact
                / "00_role_evidence"
                / "receipts"
            ).glob("*-reviewer-completion-*.json")
        )[-1]
        completion_receipt_body = completion_receipt.read_bytes()
        for malformed_artifacts in (
            {"path": "runlogs/execution.log"},
            "runlogs/execution.log",
        ):
            malformed_completion = json.loads(completion_receipt_body)
            malformed_completion["artifacts"] = malformed_artifacts
            completion_receipt.write_text(
                json.dumps(
                    malformed_completion,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                for payload in (claude(completion_target), codex(completion_target)):
                    with self.subTest(
                        kind="schema-malformed owning completion",
                        shape=type(malformed_artifacts).__name__,
                        tool=payload["tool_name"],
                    ):
                        blocked = run_hook(
                            self.root,
                            payload,
                            role="reviewer",
                            session="r-1",
                        )
                        self.assertEqual(2, blocked.returncode, blocked.stderr)
                        self.assertIn("receipt", blocked.stderr.lower())
            finally:
                completion_receipt.write_bytes(completion_receipt_body)

        for raw_shape, raw_body in (
            (
                "invalid-utf8",
                b'\xff{"artifacts":{"path":"runlogs/execution.log"}}',
            ),
            (
                "escaped-slash",
                b'{"artifacts":"runlogs\\/execution.log",',
            ),
            (
                "unicode-escape",
                b'{"artifacts":"\\u0072unlogs/execution.log",',
            ),
            (
                "escaped-ownership-keys",
                b'{"\\u0061rtifacts":{"\\u0070ath":"runlogs/execution.log"},',
            ),
            (
                "duplicate-ownership-key",
                b'{"artifacts":"runlogs/execution.log","artifacts":[]}',
            ),
        ):
            completion_receipt.write_bytes(raw_body)
            try:
                for payload in (claude(completion_target), codex(completion_target)):
                    with self.subTest(
                        kind="raw-malformed owning completion",
                        shape=raw_shape,
                        tool=payload["tool_name"],
                    ):
                        blocked = run_hook(
                            self.root,
                            payload,
                            role="reviewer",
                            session="r-1",
                        )
                        self.assertEqual(2, blocked.returncode, blocked.stderr)
                        self.assertIn("receipt", blocked.stderr.lower())
            finally:
                completion_receipt.write_bytes(completion_receipt_body)

        for raw_shape, raw_body in (
            (
                "run-path-is-not-ownership",
                b'{"run":{"path":"runlogs/execution.log"},',
            ),
            (
                "metadata-path-is-not-ownership",
                b'{"metadata":{"path":"runlogs/execution.log"},',
            ),
            (
                "duplicate-run-key-is-not-ownership",
                b'{"run":{"path":"runlogs/execution.log"},"run":{}}',
            ),
        ):
            completion_receipt.write_bytes(raw_body)
            try:
                for payload in (claude(completion_target), codex(completion_target)):
                    with self.subTest(
                        kind="raw-malformed non-owner",
                        shape=raw_shape,
                        tool=payload["tool_name"],
                    ):
                        allowed = run_hook(
                            self.root,
                            payload,
                            role="reviewer",
                            session="r-1",
                        )
                        self.assertEqual(0, allowed.returncode, allowed.stderr)
            finally:
                completion_receipt.write_bytes(completion_receipt_body)

        with tempfile.TemporaryDirectory(prefix="bugate-role-hook-external-") as outside:
            external_hardlink = Path(outside) / "execution-hardlink.log"
            os.link(log, external_hardlink)
            self.assertTrue(os.path.samefile(log, external_hardlink))
            for payload in (claude(str(external_hardlink)), codex(str(external_hardlink))):
                with self.subTest(kind="external completion", tool=payload["tool_name"]):
                    blocked = run_hook(
                        self.root,
                        payload,
                        role="reviewer",
                        session="r-1",
                    )
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn("post-run is closed", blocked.stderr)

        if completion_alias != log and completion_alias.exists():
            completion_alias.unlink()
        if log.exists():
            log.unlink()
        dangling = self.root / "runlogs" / "deleted-evidence-alias.log"
        dangling.symlink_to("execution.log")
        deleted_targets = [
            "runlogs/execution.log",
            "runlogs/deleted-evidence-alias.log",
        ]
        if case_insensitive:
            deleted_targets.append("runlogs/EXECUTION.LOG")
        for target in deleted_targets:
            for payload in (claude(target), codex(target)):
                with self.subTest(
                    kind="deleted completion",
                    target=target,
                    tool=payload["tool_name"],
                ):
                    blocked = run_hook(
                        self.root,
                        payload,
                        role="reviewer",
                        session="r-1",
                    )
                    self.assertEqual(2, blocked.returncode, blocked.stderr)
                    self.assertIn("required evidence file is missing", blocked.stderr)

    def test_shared_completion_evidence_checks_every_uc_owner(self):
        second_artifact = self.root / "usecases" / "UC-002"
        second_artifact.mkdir(parents=True)
        for name in PRECODE:
            extra = (
                "dispatch_mode: real_peer_dispatch\n"
                if name == "03b_adversarial_cases.yaml"
                else ""
            )
            (second_artifact / name).write_text(
                f"gate_status: passed\n{extra}fixture: {name}\n",
                encoding="utf-8",
            )
        second_multi = second_artifact / "00_multiview"
        second_multi.mkdir()
        (second_multi / "divergence_report.md").write_text(
            "---\ngate_status: passed\ndispatch_mode: real_peer_dispatch\n---\n# Fixture\n",
            encoding="utf-8",
        )
        second_implementation = self.root / "tests" / "test_UC-002.py"
        shared_log = self.root / "runlogs" / "shared.log"
        shared_log.parent.mkdir()
        shared_log.write_text("shared fixture run\n", encoding="utf-8")
        second_identity = rg.lineage_identity(second_artifact)
        rg.lineage_init(
            second_artifact,
            lineage_id=second_identity["lineage_id"],
        )

        def reach_reviewer(artifact: Path, implementation: Path, token: str) -> None:
            with role_env("designer", f"d-{token}"):
                rg.approve(artifact, approved_by="qa-owner")
                designer_handoff = rg.handoff(
                    artifact, phase="pre_code", to_role="implementer"
                )
            with role_env("implementer", f"i-{token}"):
                rg.accept(
                    artifact,
                    phase="implementation",
                    handoff_id=designer_handoff["receipt_sha256"],
                )
                implementation.write_text(
                    "def test_fixture(): pass\n", encoding="utf-8"
                )
                implementer_handoff = rg.handoff(
                    artifact,
                    phase="implementation",
                    to_role="reviewer",
                    implementation_files=[implementation],
                )
            with role_env("reviewer", "r-shared"):
                rg.accept(
                    artifact,
                    phase="post_run",
                    handoff_id=implementer_handoff["receipt_sha256"],
                )

        reach_reviewer(self.fx.artifact, self.fx.implementation, "one")
        with role_env("reviewer", "r-shared"):
            for name in sorted(rg.POSTRUN_NAMES):
                (self.fx.artifact / name).write_text(
                    "gate_status: draft\nfixture: postrun\n", encoding="utf-8"
                )
            rg.complete(
                self.fx.artifact,
                phase="post_run",
                run_command="fixture runner one",
                exit_code=1,
                evidence_files=[shared_log],
                final_gate_status="failed",
            )

        reach_reviewer(second_artifact, second_implementation, "two")
        with role_env("reviewer", "r-shared"):
            for name in sorted(rg.POSTRUN_NAMES):
                (second_artifact / name).write_text(
                    "gate_status: passed\nfixture: postrun\n", encoding="utf-8"
                )
            rg.complete(
                second_artifact,
                phase="post_run",
                run_command="fixture runner two",
                exit_code=0,
                evidence_files=[shared_log],
                final_gate_status="passed",
            )

        for payload in (claude("runlogs/shared.log"), codex("runlogs/shared.log")):
            blocked = run_hook(
                self.root,
                payload,
                role="reviewer",
                session="r-shared",
            )
            self.assertEqual(2, blocked.returncode, blocked.stderr)
            self.assertIn("completion owner usecases/UC-002", blocked.stderr)
            self.assertIn("post-run is closed", blocked.stderr)

    def test_advisory_warns_but_evidence_protection_remains_hard(self):
        profile = self.root / "bugate.profile.yaml"
        body = profile.read_text(encoding="utf-8").replace(
            "  mode: required", "  mode: advisory"
        )
        profile.write_text(body, encoding="utf-8")
        ordinary = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="implementer",
            session="i-1",
        )
        self.assertEqual(0, ordinary.returncode, ordinary.stderr)
        self.assertIn("WARNING", ordinary.stderr)
        evidence = run_hook(
            self.root,
            codex("usecases/UC-001/00_role_evidence/chain.json"),
            role="implementer",
            session="i-1",
        )
        self.assertEqual(2, evidence.returncode)

    def test_malformed_required_fails_closed_and_advisory_warns(self):
        profile = self.root / "bugate.profile.yaml"
        original = profile.read_text(encoding="utf-8")
        malformed = original.replace("  session_id_required: true", "  session_id_required: yes")
        profile.write_text(malformed, encoding="utf-8")
        required = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="designer",
            session="d-1",
        )
        self.assertEqual(2, required.returncode)
        self.assertIn("must be boolean", required.stderr)
        profile.write_text(malformed.replace("  mode: required", "  mode: advisory"), encoding="utf-8")
        advisory = run_hook(
            self.root,
            claude("usecases/UC-001/01_business_brief.md"),
            role="designer",
            session="d-1",
        )
        self.assertEqual(0, advisory.returncode, advisory.stderr)
        self.assertIn("malformed advisory", advisory.stderr)

    def test_mode_off_is_noop_including_role_evidence_path(self):
        profile = self.root / "bugate.profile.yaml"
        profile.write_text(
            profile.read_text(encoding="utf-8").replace("  mode: required", "  mode: off"),
            encoding="utf-8",
        )
        result = run_hook(
            self.root,
            codex("usecases/UC-001/00_role_evidence/chain.json"),
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
