#!/usr/bin/env python3
"""Deterministic fake-HTTP tests for strict Memory role transitions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import memory_bus as mb  # noqa: E402


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def transition(
    event: str = "designer_handoff",
    *,
    phase: str = "pre_code",
    from_role: str = "designer",
    to_role: str = "implementer",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": mb.ROLE_TRANSITION_SCHEMA,
        "event": event,
        "uc": "UC-STRICT-001",
        "artifact_dir": "usecases/UC-STRICT-001",
        "phase": phase,
        "from_role": from_role,
        "to_role": to_role,
        "actor": {
            "role": to_role if event.endswith("_acceptance") else from_role,
            "runtime": "codex",
            "session_id": f"{event}-session",
        },
        "profile": {"path": "bugate.profile.yaml", "sha256": "a" * 64},
        "artifacts": [],
        "dispatch": {},
        "human_acceptance": {},
        "previous_receipt_sha256": "",
        "idempotency_sha256": "b" * 64,
        **extra,
    }
    payload["transition_sha256"] = mb._transition_hash(payload)
    return payload


def role_receipt(
    payload: dict[str, object], prepared: dict[str, object]
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": "bugate.role-evidence/v1",
        **{key: copy.deepcopy(value) for key, value in payload.items() if key != "schema"},
        "sequence": 1,
        "created_at": "2026-07-20T00:00:00Z",
        "memory": copy.deepcopy(prepared),
        "resulting_state": "awaiting_implementer_acceptance",
    }
    receipt["receipt_sha256"] = sha256(canonical(receipt))
    return receipt


class FakeMemoryState:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        self.post_success_false = False
        self.put_success_false = False
        self.omit_post_id = False
        self.http_failure: dict[str, int] = {}
        self.delay_get_seconds = 0.0
        self.lock = threading.Lock()


class FakeMemoryHandler(BaseHTTPRequestHandler):
    server: "FakeMemoryServer"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    @property
    def state(self) -> FakeMemoryState:
        return self.server.state

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise AssertionError("fake server expected an object body")
        return value

    def _send(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Expected in the timeout test: the client has already closed.
            return

    def _begin(self, method: str) -> tuple[str, bool]:
        path = urlsplit(self.path).path
        with self.state.lock:
            self.state.calls.append((method, path))
            failure = self.state.http_failure.get(method)
        if failure:
            self._send(failure, {"detail": "injected fake HTTP failure"})
            return path, False
        return path, True

    def do_GET(self) -> None:  # noqa: N802
        path, proceed = self._begin("GET")
        if not proceed:
            return
        if path == "/api/health":
            self._send(200, {"status": "healthy"})
            return
        if self.state.delay_get_seconds:
            time.sleep(self.state.delay_get_seconds)
        prefix = "/api/memories/"
        if not path.startswith(prefix):
            self._send(404, {"detail": "not found"})
            return
        exact_id = unquote(path[len(prefix) :])
        with self.state.lock:
            record = copy.deepcopy(self.state.records.get(exact_id))
        if record is None:
            self._send(404, {"detail": "Memory not found"})
            return
        self._send(200, record)

    def do_POST(self) -> None:  # noqa: N802
        path, proceed = self._begin("POST")
        if not proceed:
            return
        if path != "/api/memories":
            self._send(404, {"detail": "not found"})
            return
        payload = self._body()
        if self.state.post_success_false:
            self._send(200, {"success": False, "message": "injected store failure"})
            return
        content = str(payload.get("content") or "")
        exact_id = sha256(content.encode("utf-8"))
        with self.state.lock:
            if exact_id not in self.state.records:
                self.state.records[exact_id] = {
                    "content": content,
                    "content_hash": exact_id,
                    "tags": copy.deepcopy(payload.get("tags") or []),
                    "memory_type": payload.get("memory_type"),
                    "metadata": copy.deepcopy(payload.get("metadata") or {}),
                    "created_at_iso": "2026-07-20T00:00:00Z",
                }
            record = copy.deepcopy(self.state.records[exact_id])
        if self.state.omit_post_id:
            self._send(200, {"success": True, "message": "stored but ID omitted"})
            return
        self._send(
            200,
            {
                "success": True,
                "message": "stored",
                "content_hash": exact_id,
                "memory": record,
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        path, proceed = self._begin("PUT")
        if not proceed:
            return
        prefix = "/api/memories/"
        if not path.startswith(prefix):
            self._send(404, {"detail": "not found"})
            return
        exact_id = unquote(path[len(prefix) :])
        payload = self._body()
        if self.state.put_success_false:
            self._send(
                200,
                {
                    "success": False,
                    "message": "injected metadata failure",
                    "content_hash": exact_id,
                },
            )
            return
        with self.state.lock:
            record = self.state.records.get(exact_id)
            if record is None:
                self._send(404, {"detail": "Memory not found"})
                return
            # Deliberately REPLACE metadata.  The client must send a complete
            # merged mapping, not rely on a service-specific merge behavior.
            record["metadata"] = copy.deepcopy(payload.get("metadata") or {})
            stored = copy.deepcopy(record)
        self._send(
            200,
            {
                "success": True,
                "message": "updated",
                "content_hash": exact_id,
                "memory": stored,
            },
        )


class FakeMemoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: FakeMemoryState):
        super().__init__(("127.0.0.1", 0), FakeMemoryHandler)
        self.state = state


@contextmanager
def fake_memory_service():
    state = FakeMemoryState()
    server = FakeMemoryServer(state)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    old = {
        key: os.environ.get(key)
        for key in ("MEMORY_BUS_URL", "MEMORY_BUS_PROJECT_TAG", "MCP_API_KEY_AGENT")
    }
    os.environ["MEMORY_BUS_URL"] = f"http://127.0.0.1:{server.server_port}"
    os.environ["MEMORY_BUS_PROJECT_TAG"] = "project:strict-fixture"
    mb._PREPARED_ROLE_TRANSITIONS.clear()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        mb._PREPARED_ROLE_TRANSITIONS.clear()
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def unavailable_service():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    old_url = os.environ.get("MEMORY_BUS_URL")
    old_namespace = os.environ.get("MEMORY_BUS_PROJECT_TAG")
    os.environ["MEMORY_BUS_URL"] = f"http://127.0.0.1:{port}"
    os.environ["MEMORY_BUS_PROJECT_TAG"] = "project:strict-fixture"
    try:
        yield
    finally:
        if old_url is None:
            os.environ.pop("MEMORY_BUS_URL", None)
        else:
            os.environ["MEMORY_BUS_URL"] = old_url
        if old_namespace is None:
            os.environ.pop("MEMORY_BUS_PROJECT_TAG", None)
        else:
            os.environ["MEMORY_BUS_PROJECT_TAG"] = old_namespace


class StrictMemoryHandoffTests(unittest.TestCase):
    def _anchor(self, payload: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        prepared = mb.prepare_role_transition(payload=payload, strict=True)
        receipt = role_receipt(payload, prepared)
        finalized = mb.finalize_role_transition(
            memory_id=str(prepared["memory_id"]),
            receipt_sha256=str(receipt["receipt_sha256"]),
            expected=payload,
            strict=True,
        )
        self.assertEqual(receipt["receipt_sha256"], finalized["receipt_sha256"])
        return prepared, receipt

    def test_required_happy_path_put_preserves_metadata_and_exact_verifies(self):
        with fake_memory_service() as state:
            payload = transition()
            prepared, receipt = self._anchor(payload)
            verified = mb.verify_role_transition(receipt=receipt, strict=True)

            exact_id = str(prepared["memory_id"])
            record = state.records[exact_id]
            metadata = record["metadata"]
            self.assertEqual(mb.MEMORY_TRANSITION_SCHEMA, metadata["schema"])
            self.assertEqual(payload, metadata["role_transition"])
            self.assertEqual(receipt["receipt_sha256"], metadata["receipt_sha256"])
            self.assertEqual("verified", verified["status"])
            self.assertEqual(
                ["POST", "GET", "GET", "PUT", "GET", "GET"],
                [method for method, _ in state.calls],
            )
            tags = record["tags"]
            self.assertNotIn("uc:UC-STRICT-001", tags)
            self.assertFalse(any(str(tag).startswith("transition:") for tag in tags))

    def test_acceptance_exact_gets_and_validates_handoff_before_post(self):
        with fake_memory_service() as state:
            handoff = transition()
            handoff_prepared, handoff_receipt = self._anchor(handoff)
            acceptance = transition(
                "implementer_acceptance",
                phase="implementation",
                from_role="designer",
                to_role="implementer",
                handoff_memory_id=handoff_prepared["memory_id"],
                handoff_receipt_sha256=handoff_receipt["receipt_sha256"],
            )
            before = len(state.calls)
            accepted = mb.prepare_role_transition(acceptance, strict=True)
            calls = state.calls[before:]
            self.assertEqual(
                [
                    ("GET", f"/api/memories/{handoff_prepared['memory_id']}"),
                    ("POST", "/api/memories"),
                    ("GET", f"/api/memories/{accepted['memory_id']}"),
                ],
                calls,
            )

    def test_acceptance_rejects_namespace_roles_uc_phase_transition_and_receipt_mismatch(self):
        with fake_memory_service() as state:
            handoff = transition()
            prepared, receipt = self._anchor(handoff)
            exact_id = str(prepared["memory_id"])
            original = copy.deepcopy(state.records[exact_id])
            acceptance = transition(
                "implementer_acceptance",
                phase="implementation",
                from_role="designer",
                to_role="implementer",
                handoff_memory_id=exact_id,
                handoff_receipt_sha256=receipt["receipt_sha256"],
            )

            mutations = {
                "namespace": lambda record: record["metadata"].__setitem__(
                    "namespace", "project:wrong"
                ),
                "from_role": lambda record: record["metadata"].__setitem__(
                    "from_role", "reviewer"
                ),
                "to_role": lambda record: record["metadata"].__setitem__(
                    "to_role", "reviewer"
                ),
                "uc": lambda record: record["metadata"].__setitem__("uc", "UC-WRONG"),
                "phase": lambda record: record["metadata"].__setitem__(
                    "phase", "implementation"
                ),
                "transition": lambda record: record["metadata"]["role_transition"].__setitem__(
                    "uc", "UC-TAMPERED"
                ),
                "receipt": lambda record: record["metadata"].__setitem__(
                    "receipt_sha256", "f" * 64
                ),
                "schema": lambda record: record["metadata"].__setitem__(
                    "schema", "wrong-schema"
                ),
                "verified_at": lambda record: record["metadata"].__setitem__(
                    "verified_at", ""
                ),
                "tags": lambda record: record.__setitem__("tags", []),
                "content": lambda record: record.__setitem__("content", "tampered"),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    state.records[exact_id] = copy.deepcopy(original)
                    mutate(state.records[exact_id])
                    posts_before = sum(method == "POST" for method, _ in state.calls)
                    with self.assertRaises(mb.MemoryBusError):
                        mb.prepare_role_transition(acceptance, strict=True)
                    self.assertEqual(
                        posts_before,
                        sum(method == "POST" for method, _ in state.calls),
                    )

            with self.assertRaises(mb.MemoryBusError):
                mb.get_memory_exact("0" * 64)

    def test_success_false_missing_id_http_error_and_timeout_fail_strict(self):
        with fake_memory_service() as state:
            state.post_success_false = True
            with self.assertRaisesRegex(mb.MemoryBusError, "success|store failure"):
                mb.prepare_role_transition(transition(), strict=True)

        with fake_memory_service() as state:
            state.omit_post_id = True
            with self.assertRaisesRegex(mb.MemoryBusError, "missing content hash"):
                mb.prepare_role_transition(transition(), strict=True)

        with fake_memory_service() as state:
            state.http_failure["POST"] = 503
            with self.assertRaises(mb.MemoryBusError):
                mb.prepare_role_transition(transition(), strict=True)

        with fake_memory_service() as state:
            payload = transition()
            prepared = mb.prepare_role_transition(payload, strict=True)
            state.put_success_false = True
            with self.assertRaisesRegex(mb.MemoryBusError, "metadata failure"):
                mb.finalize_role_transition(
                    str(prepared["memory_id"]), "c" * 64, payload, strict=True
                )

        with fake_memory_service() as state:
            payload = transition()
            prepared = mb.prepare_role_transition(payload, strict=True)
            state.delay_get_seconds = 0.2
            with self.assertRaises(mb.MemoryBusError):
                mb.get_memory_exact(str(prepared["memory_id"]), timeout=0.02)

    def test_unavailable_strict_fails_but_ordinary_commands_remain_best_effort(self):
        with unavailable_service():
            with self.assertRaises(mb.MemoryBusError):
                mb.prepare_role_transition(transition(), strict=True)

            stderr = StringIO()
            with redirect_stderr(stderr):
                handoff_rc = mb.cmd_handoff(
                    SimpleNamespace(
                        strict=False,
                        from_agent="designer",
                        to="implementer",
                        msg="ordinary",
                        status="draft",
                        scope="global",
                        task=None,
                        tag=None,
                        artifact=None,
                        json=False,
                    )
                )
                note_rc = mb.cmd_note(
                    SimpleNamespace(
                        agent="agent",
                        type="progress",
                        status="draft",
                        scope=None,
                        task=None,
                        to=None,
                        broadcast=False,
                        tag=None,
                        artifact=None,
                        metadata=None,
                        msg="ordinary",
                        json=False,
                    )
                )
                stop_rc = mb.cmd_stop(SimpleNamespace(agent="agent"))
                original_heal = mb.self_heal_service
                mb.self_heal_service = lambda: False
                try:
                    session_start_rc = mb.cmd_session_start(
                        SimpleNamespace(agent="agent", limit=1)
                    )
                finally:
                    mb.self_heal_service = original_heal
            self.assertEqual(
                (0, 0, 0, 0),
                (handoff_rc, note_rc, stop_rc, session_start_rc),
            )
            self.assertNotIn("secret", stderr.getvalue().lower())

    def test_retry_is_idempotent_and_conflicting_receipt_cannot_overwrite(self):
        with fake_memory_service() as state:
            payload = transition()
            first = mb.prepare_role_transition(payload, strict=True)
            first_final = mb.finalize_role_transition(
                str(first["memory_id"]), "d" * 64, payload, strict=True
            )
            retry = mb.prepare_role_transition(payload, strict=True)
            retry_final = mb.finalize_role_transition(
                str(retry["memory_id"]), "d" * 64, payload, strict=True
            )
            self.assertEqual(first, retry)
            self.assertEqual(first_final, retry_final)
            self.assertEqual(1, len(state.records))
            self.assertEqual(1, sum(method == "PUT" for method, _ in state.calls))

            mb.prepare_role_transition(payload, strict=True)
            with self.assertRaisesRegex(mb.MemoryBusError, "different receipt hash"):
                mb.finalize_role_transition(
                    str(first["memory_id"]), "e" * 64, payload, strict=True
                )
            self.assertEqual(
                "d" * 64,
                state.records[str(first["memory_id"])]["metadata"]["receipt_sha256"],
            )

    def test_role_governance_required_integration_and_no_local_receipt_on_failure(self):
        import role_governance as rg
        from test_role_governance import Fixture, role_env

        old_project = os.environ.get("BUGATE_PROJECT_ROOT")
        old_profile = os.environ.pop("BUGATE_PROFILE", None)
        old_semantics = rg.verify_precode_semantics
        with TemporaryDirectory(prefix="bugate-role-memory-integration-") as tmp:
            root = Path(tmp)
            os.environ["BUGATE_PROJECT_ROOT"] = str(root)
            fixture = Fixture(root, memory_mode="required")
            rg.verify_precode_semantics = lambda ctx: None
            try:
                with fake_memory_service() as state:
                    with role_env("designer", "designer-session"):
                        rg.approve(fixture.artifact, approved_by="qa-owner")
                        before = rg.load_chain(rg.load_context(fixture.artifact))["sequence"]
                        state.post_success_false = True
                        with self.assertRaisesRegex(
                            rg.RoleGovernanceError, "strict Memory transition failed"
                        ):
                            rg.handoff(
                                fixture.artifact,
                                phase="pre_code",
                                to_role="implementer",
                            )
                        state.post_success_false = False
                        chain = rg.load_chain(rg.load_context(fixture.artifact))
                        self.assertEqual(before, chain["sequence"])
                        self.assertNotIn("designer_handoff", chain["latest_receipts"])
                        handoff = rg.handoff(
                            fixture.artifact,
                            phase="pre_code",
                            to_role="implementer",
                        )
                    with role_env("implementer", "implementer-session"):
                        accepted = rg.accept(
                            fixture.artifact,
                            phase="implementation",
                            handoff_id=handoff["memory"]["memory_id"],
                        )
                    self.assertEqual("implementer_acceptance", accepted["event"])
                    self.assertEqual("implementation_unlocked", accepted["resulting_state"])
            finally:
                rg.verify_precode_semantics = old_semantics
                if old_project is None:
                    os.environ.pop("BUGATE_PROJECT_ROOT", None)
                else:
                    os.environ["BUGATE_PROJECT_ROOT"] = old_project
                if old_profile is not None:
                    os.environ["BUGATE_PROFILE"] = old_profile

    def test_cli_contract_and_strict_nonzero_on_unavailable(self):
        parser = mb.build_parser()
        parsed = parser.parse_args(["get", "--id", "abc", "--strict"])
        self.assertTrue(parsed.strict)
        parsed = parser.parse_args(
            [
                "accept-handoff",
                "--handoff-id",
                "abc",
                "--handoff-receipt-sha256",
                "a" * 64,
                "--from",
                "designer",
                "--to",
                "implementer",
                "--uc",
                "UC-1",
                "--phase",
                "implementation",
                "--msg",
                "accept",
                "--strict",
            ]
        )
        self.assertTrue(parsed.strict)
        with unavailable_service(), redirect_stderr(StringIO()):
            strict_args = parser.parse_args(
                [
                    "handoff",
                    "--from",
                    "designer",
                    "--to",
                    "implementer",
                    "--msg",
                    "handoff",
                    "--uc",
                    "UC-1",
                    "--phase",
                    "pre_code",
                    "--strict",
                ]
            )
            self.assertEqual(1, strict_args.func(strict_args))

    def test_core_project_tag_supports_nested_and_legacy_config(self):
        original_root = mb.root
        try:
            with self.subTest(shape="nested"):
                with TemporaryDirectory(prefix="bugate-memory-core-") as tmp:
                    path = Path(tmp)
                    (path / "bugate.config.yaml").write_text(
                        "memory:\n  namespace: project:nested\n", encoding="utf-8"
                    )
                    mb.root = lambda: path
                    self.assertEqual("project:nested", mb.core_project_tag())
            with self.subTest(shape="legacy"):
                with TemporaryDirectory(prefix="bugate-memory-core-") as tmp:
                    path = Path(tmp)
                    (path / "bugate.config.yaml").write_text(
                        "namespace: project:legacy\n", encoding="utf-8"
                    )
                    mb.root = lambda: path
                    self.assertEqual("project:legacy", mb.core_project_tag())
        finally:
            mb.root = original_root


if __name__ == "__main__":
    unittest.main(verbosity=2)
