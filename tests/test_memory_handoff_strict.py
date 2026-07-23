#!/usr/bin/env python3
"""Deterministic fake-HTTP tests for strict Memory role transitions."""

from __future__ import annotations

import copy
import base64
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


def lineage_key() -> dict[str, object]:
    return {
        "schema": mb.ROLE_LINEAGE_KEY_SCHEMA,
        "namespace": "project:strict-fixture",
        "uc": "UC-STRICT-001",
        "artifact_dir": "usecases/UC-STRICT-001",
    }


def evidence_envelope(path: str, parsed: dict[str, object]) -> dict[str, object]:
    raw = canonical(parsed)
    return {
        "path": path,
        "mode": 0o600,
        "bytes_sha256": sha256(raw),
        "bytes_base64": base64.b64encode(raw).decode("ascii"),
        "parsed": copy.deepcopy(parsed),
    }


def checkpoint_payload(
    *,
    root_id: str,
    key: dict[str, object] | None = None,
) -> dict[str, object]:
    key = copy.deepcopy(key or lineage_key())
    lineage_id = sha256(canonical(key))
    receipt_hash = "b" * 64
    previous_receipt_hash = "a" * 64
    receipt = {
        "schema": "bugate.role-evidence/v1",
        "event": "designer_handoff",
        "sequence": 2,
        "uc": str(key["uc"]),
        "artifact_dir": str(key["artifact_dir"]),
        "previous_receipt_sha256": previous_receipt_hash,
        "receipt_sha256": receipt_hash,
        "resulting_state": "awaiting_implementer_acceptance",
    }
    chain = {
        "schema": "bugate.role-chain/v1",
        "state": "awaiting_implementer_acceptance",
        "sequence": 2,
        "head_sha256": receipt_hash,
        "latest_receipts": {
            "designer_handoff": (
                "usecases/UC-STRICT-001/00_role_evidence/receipts/"
                f"000002-designer-handoff-{receipt_hash}.json"
            )
        },
    }
    return {
        "schema": mb.ROLE_LINEAGE_CHECKPOINT_SCHEMA,
        "lineage_key": key,
        "lineage_id": lineage_id,
        "lineage_root_id": root_id,
        "sequence": 2,
        "previous_checkpoint_id": "c" * 64,
        "previous_receipt_sha256": previous_receipt_hash,
        "receipt_sha256": receipt_hash,
        "resulting_state": "awaiting_implementer_acceptance",
        "registry_revision": 2,
        "receipt_envelope": evidence_envelope(
            (
                "usecases/UC-STRICT-001/00_role_evidence/receipts/"
                f"000002-designer-handoff-{receipt_hash}.json"
            ),
            receipt,
        ),
        "chain_envelope": evidence_envelope(
            "usecases/UC-STRICT-001/00_role_evidence/chain.json",
            chain,
        ),
    }


class FakeMemoryState:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []
        self.request_bodies: list[tuple[str, str, dict[str, object]]] = []
        self.post_success_false = False
        self.put_success_false = False
        self.omit_post_id = False
        self.post_id_override: str | None = None
        self.store_under_post_id_override = False
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

    def _record_body(
        self, method: str, path: str, payload: dict[str, object]
    ) -> None:
        with self.state.lock:
            self.state.request_bodies.append(
                (method, path, copy.deepcopy(payload))
            )

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
        self._record_body("POST", path, payload)
        if self.state.post_success_false:
            self._send(200, {"success": False, "message": "injected store failure"})
            return
        content = str(payload.get("content") or "")
        exact_id = sha256(content.encode("utf-8"))
        stored_id = (
            self.state.post_id_override
            if self.state.store_under_post_id_override and self.state.post_id_override
            else exact_id
        )
        with self.state.lock:
            if stored_id not in self.state.records:
                self.state.records[stored_id] = {
                    "content": content,
                    "content_hash": stored_id,
                    "tags": copy.deepcopy(payload.get("tags") or []),
                    "memory_type": payload.get("memory_type"),
                    "metadata": copy.deepcopy(payload.get("metadata") or {}),
                    "created_at_iso": "2026-07-20T00:00:00Z",
                }
            record = copy.deepcopy(self.state.records[stored_id])
        if self.state.omit_post_id:
            self._send(200, {"success": True, "message": "stored but ID omitted"})
            return
        self._send(
            200,
            {
                "success": True,
                "message": "stored",
                "content_hash": self.state.post_id_override or exact_id,
                "memory": {
                    **record,
                    "content_hash": self.state.post_id_override or exact_id,
                },
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
        self._record_body("PUT", path, payload)
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
def isolated_memory_env(url: str):
    keys = (
        "MEMORY_BUS_URL",
        "MEMORY_BUS_PROJECT_TAG",
        "MCP_MEMORY_BASE_DIR",
        "BUGATE_MEMORY_HOME",
        "MCP_API_KEY_AGENT",
        "MCP_API_KEY_HUMAN",
        "MCP_API_KEY",
    )
    old = {key: os.environ.get(key) for key in keys}
    with TemporaryDirectory(prefix="bugate-strict-memory-home-") as tmp:
        memory_home = Path(tmp) / "memory-home"
        memory_home.mkdir(mode=0o700)
        os.environ["MEMORY_BUS_URL"] = url
        os.environ["MEMORY_BUS_PROJECT_TAG"] = "project:strict-fixture"
        os.environ["MCP_MEMORY_BASE_DIR"] = str(memory_home)
        os.environ["BUGATE_MEMORY_HOME"] = str(memory_home)
        for key in ("MCP_API_KEY_AGENT", "MCP_API_KEY_HUMAN", "MCP_API_KEY"):
            os.environ.pop(key, None)
        try:
            yield memory_home
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def fake_memory_service():
    state = FakeMemoryState()
    server = FakeMemoryServer(state)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    with isolated_memory_env(f"http://127.0.0.1:{server.server_port}"):
        mb._PREPARED_ROLE_TRANSITIONS.clear()
        try:
            yield state
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            mb._PREPARED_ROLE_TRANSITIONS.clear()


@contextmanager
def unavailable_service():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with isolated_memory_env(f"http://127.0.0.1:{port}"):
        yield


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

    def test_http_404_is_not_found_but_outage_remains_a_bus_error(self):
        self.assertTrue(issubclass(mb.MemoryHTTPError, mb.MemoryBusError))
        self.assertTrue(issubclass(mb.MemoryNotFound, mb.MemoryBusError))
        with fake_memory_service() as state:
            with self.assertRaises(mb.MemoryNotFound) as missing:
                mb.get_memory_exact("0" * 64)
            self.assertEqual(404, missing.exception.status_code)

            state.http_failure["GET"] = 503
            with self.assertRaises(mb.MemoryHTTPError) as failed:
                mb.get_memory_exact("1" * 64)
            self.assertEqual(503, failed.exception.status_code)
            self.assertNotIsInstance(failed.exception, mb.MemoryNotFound)

        with unavailable_service():
            with self.assertRaises(mb.MemoryBusError) as unavailable:
                mb.get_memory_exact("2" * 64, timeout=0.05)
            self.assertNotIsInstance(unavailable.exception, mb.MemoryHTTPError)
            self.assertNotIsInstance(unavailable.exception, mb.MemoryNotFound)

    def test_lineage_root_is_deterministic_idempotent_and_never_uses_put(self):
        with fake_memory_service() as state:
            key = lineage_key()
            expected_lineage_id = sha256(canonical(key))
            expected_payload = {
                "schema": mb.ROLE_LINEAGE_ROOT_SCHEMA,
                "lineage_key": key,
                "lineage_id": expected_lineage_id,
            }
            expected_content = canonical(expected_payload).decode("utf-8")
            expected_root_id = sha256(expected_content.encode("utf-8"))

            self.assertIsNone(mb.probe_role_lineage_root(key))
            first = mb.ensure_role_lineage_root(key)
            probed = mb.probe_role_lineage_root(key)
            second = mb.ensure_role_lineage_root(key)

            self.assertEqual(expected_lineage_id, first["lineage_id"])
            self.assertEqual(expected_root_id, first["lineage_root_id"])
            self.assertEqual(expected_payload, first["payload"])
            self.assertEqual(first, probed)
            self.assertEqual(first, second)
            self.assertEqual(1, len(state.records))
            self.assertEqual(expected_content, state.records[expected_root_id]["content"])
            self.assertEqual(
                expected_root_id,
                state.records[expected_root_id]["content_hash"],
            )
            self.assertEqual(0, sum(method == "PUT" for method, _ in state.calls))
            post_bodies = [
                body
                for method, path, body in state.request_bodies
                if method == "POST" and path == "/api/memories"
            ]
            self.assertEqual(2, len(post_bodies))
            self.assertTrue(all(body["content"] == expected_content for body in post_bodies))
            for body in post_bodies:
                tags = [str(tag) for tag in body["tags"]]
                self.assertFalse(any("UC-STRICT" in tag for tag in tags))
                self.assertFalse(any(expected_lineage_id in tag for tag in tags))
                self.assertFalse(any("usecases/" in tag for tag in tags))

    def test_lineage_root_rejects_wrong_hash_payload_and_outage(self):
        with fake_memory_service() as state:
            key = lineage_key()
            state.post_id_override = "f" * 64
            with self.assertRaisesRegex(mb.MemoryBusError, "content hash|identity"):
                mb.ensure_role_lineage_root(key)

        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            exact_id = str(root["lineage_root_id"])
            state.records[exact_id]["content"] = canonical(
                {**root["payload"], "lineage_id": "e" * 64}
            ).decode("utf-8")
            with self.assertRaisesRegex(mb.MemoryBusError, "content|payload|lineage"):
                mb.probe_role_lineage_root(key)

        with unavailable_service():
            with self.assertRaises(mb.MemoryBusError):
                mb.probe_role_lineage_root(lineage_key())

    def test_lineage_identity_rejects_absolute_path_like_namespace_and_uc(self):
        invalid = (
            ("/private/project", "UC-1"),
            ("project:/private/project", "UC-1"),
            (r"C:\private\project", "UC-1"),
            ("project:strict-fixture /private/project", "UC-1"),
            (r"project:C:\private\project", "UC-1"),
            ("file:///private/project", "UC-1"),
            ("project:file:///private/project", "UC-1"),
            ("project:strict-fixture", "/private/UC-1"),
            ("project:strict-fixture", "C:/private/UC-1"),
            ("project:strict-fixture", "UC scope /private/UC-1"),
            ("project:strict-fixture", "FILE:///private/UC-1"),
        )
        with fake_memory_service() as state:
            for namespace, uc in invalid:
                with self.subTest(namespace=namespace, uc=uc):
                    key = lineage_key()
                    key["namespace"] = namespace
                    key["uc"] = uc
                    calls_before = list(state.calls)
                    with self.assertRaisesRegex(mb.MemoryBusError, "absolute path"):
                        mb.ensure_role_lineage_root(key)
                    self.assertEqual(calls_before, state.calls)

            for artifact_dir in (
                "C:/private/usecase",
                "C:\\private\\usecase",
                "file:/private/usecase",
                "file:///private/usecase",
            ):
                with self.subTest(artifact_dir=artifact_dir):
                    key = lineage_key()
                    key["artifact_dir"] = artifact_dir
                    calls_before = list(state.calls)
                    with self.assertRaisesRegex(
                        mb.MemoryBusError, "workspace-relative|POSIX"
                    ):
                        mb.ensure_role_lineage_root(key)
                    self.assertEqual(calls_before, state.calls)

    def test_checkpoint_schema_rejects_extension_fields_before_http(self):
        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            payload = checkpoint_payload(
                key=key,
                root_id=str(root["lineage_root_id"]),
            )
            payload["unexpected"] = {"sut_specific": "must-not-be-accepted"}
            calls_before = list(state.calls)
            with self.assertRaisesRegex(mb.MemoryBusError, "unexpected"):
                mb.create_role_lineage_checkpoint(payload)
            self.assertEqual(calls_before, state.calls)

    def test_checkpoint_sequence_requires_exact_predecessor_shape_before_http(self):
        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            payload = checkpoint_payload(
                key=key,
                root_id=str(root["lineage_root_id"]),
            )
            calls_before = list(state.calls)

            first_with_predecessor = copy.deepcopy(payload)
            first_with_predecessor["sequence"] = 1
            first_receipt = first_with_predecessor["receipt_envelope"]["parsed"]
            first_receipt["sequence"] = 1
            first_with_predecessor["receipt_envelope"] = evidence_envelope(
                str(payload["receipt_envelope"]["path"]), first_receipt
            )
            first_chain = first_with_predecessor["chain_envelope"]["parsed"]
            first_chain["sequence"] = 1
            first_with_predecessor["chain_envelope"] = evidence_envelope(
                str(payload["chain_envelope"]["path"]), first_chain
            )
            with self.assertRaisesRegex(mb.MemoryBusError, "first checkpoint"):
                mb.create_role_lineage_checkpoint(first_with_predecessor)

            later_without_predecessor = copy.deepcopy(payload)
            later_without_predecessor["previous_checkpoint_id"] = ""
            later_without_predecessor["previous_receipt_sha256"] = ""
            later_receipt = later_without_predecessor["receipt_envelope"]["parsed"]
            later_receipt["previous_receipt_sha256"] = ""
            later_without_predecessor["receipt_envelope"] = evidence_envelope(
                str(payload["receipt_envelope"]["path"]), later_receipt
            )
            with self.assertRaisesRegex(mb.MemoryBusError, "non-root checkpoint"):
                mb.create_role_lineage_checkpoint(later_without_predecessor)

            self.assertEqual(calls_before, state.calls)

    def test_checkpoint_receipt_envelope_binds_exact_lineage_identity(self):
        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            payload = checkpoint_payload(
                key=key,
                root_id=str(root["lineage_root_id"]),
            )
            wrong = copy.deepcopy(payload)
            receipt = wrong["receipt_envelope"]["parsed"]
            receipt["uc"] = "UC-DIFFERENT"
            wrong["receipt_envelope"] = evidence_envelope(
                str(payload["receipt_envelope"]["path"]), receipt
            )
            calls_before = list(state.calls)
            with self.assertRaisesRegex(
                mb.MemoryBusError, "receipt envelope mismatch for uc"
            ):
                mb.create_role_lineage_checkpoint(wrong)

            wrong_receipt_schema = copy.deepcopy(payload)
            receipt = wrong_receipt_schema["receipt_envelope"]["parsed"]
            receipt["schema"] = "not.role-evidence/v1"
            wrong_receipt_schema["receipt_envelope"] = evidence_envelope(
                str(payload["receipt_envelope"]["path"]), receipt
            )
            with self.assertRaisesRegex(mb.MemoryBusError, "receipt.*schema"):
                mb.create_role_lineage_checkpoint(wrong_receipt_schema)

            wrong_chain_schema = copy.deepcopy(payload)
            chain = wrong_chain_schema["chain_envelope"]["parsed"]
            chain["schema"] = "not.role-chain/v1"
            wrong_chain_schema["chain_envelope"] = evidence_envelope(
                str(payload["chain_envelope"]["path"]), chain
            )
            with self.assertRaisesRegex(mb.MemoryBusError, "chain.*schema"):
                mb.create_role_lineage_checkpoint(wrong_chain_schema)

            for envelope_name in ("receipt_envelope", "chain_envelope"):
                with self.subTest(envelope=envelope_name):
                    outside = copy.deepcopy(payload)
                    outside[envelope_name]["path"] = (
                        "elsewhere/receipts/outside.json"
                        if envelope_name == "receipt_envelope"
                        else "elsewhere/chain.json"
                    )
                    with self.assertRaisesRegex(
                        mb.MemoryBusError, "artifact directory|evidence path"
                    ):
                        mb.create_role_lineage_checkpoint(outside)
            self.assertEqual(calls_before, state.calls)

    def test_immutable_checkpoint_roundtrip_and_never_uses_put(self):
        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            payload = checkpoint_payload(
                key=key,
                root_id=str(root["lineage_root_id"]),
            )
            before = len(state.calls)
            created = mb.create_role_lineage_checkpoint(payload)
            checkpoint_id = str(created["checkpoint_id"])
            fetched = mb.get_role_lineage_checkpoint(checkpoint_id)
            verified = mb.verify_role_lineage_checkpoint(
                state.records[checkpoint_id],
                payload,
                exact_id=checkpoint_id,
            )

            self.assertEqual(payload, created["payload"])
            self.assertEqual(created, fetched)
            self.assertEqual(created, verified)
            self.assertEqual(
                sha256(canonical(payload)),
                checkpoint_id,
            )
            self.assertEqual(
                [("POST", "/api/memories"), ("GET", f"/api/memories/{checkpoint_id}")],
                state.calls[before:before + 2],
            )
            self.assertEqual(0, sum(method == "PUT" for method, _ in state.calls))
            checkpoint_posts = [
                body
                for method, path, body in state.request_bodies
                if method == "POST"
                and path == "/api/memories"
                and body.get("metadata", {}).get("schema")
                == mb.MEMORY_LINEAGE_CHECKPOINT_SCHEMA
            ]
            self.assertEqual(1, len(checkpoint_posts))
            self.assertEqual(
                canonical(payload).decode("utf-8"),
                checkpoint_posts[0]["content"],
            )
            tags = [str(tag) for tag in checkpoint_posts[0]["tags"]]
            self.assertFalse(any("UC-STRICT" in tag for tag in tags))
            self.assertFalse(any(str(payload["receipt_sha256"]) in tag for tag in tags))
            self.assertFalse(any("usecases/" in tag for tag in tags))

    def test_checkpoint_rejects_wrong_hash_payload_404_and_outage(self):
        with fake_memory_service() as state:
            key = lineage_key()
            root = mb.ensure_role_lineage_root(key)
            payload = checkpoint_payload(
                key=key,
                root_id=str(root["lineage_root_id"]),
            )
            broken = copy.deepcopy(payload)
            broken["receipt_envelope"]["bytes_sha256"] = "0" * 64
            posts_before = sum(method == "POST" for method, _ in state.calls)
            with self.assertRaisesRegex(mb.MemoryBusError, "bytes_sha256"):
                mb.create_role_lineage_checkpoint(broken)
            self.assertEqual(
                posts_before,
                sum(method == "POST" for method, _ in state.calls),
            )

            state.post_id_override = "f" * 64
            with self.assertRaisesRegex(mb.MemoryBusError, "content hash|identity"):
                mb.create_role_lineage_checkpoint(payload)
            state.post_id_override = None

            created = mb.create_role_lineage_checkpoint(payload)
            checkpoint_id = str(created["checkpoint_id"])
            state.records[checkpoint_id]["content"] = canonical(
                {**payload, "registry_revision": 999}
            ).decode("utf-8")
            with self.assertRaisesRegex(mb.MemoryBusError, "content|hash|payload"):
                mb.get_role_lineage_checkpoint(checkpoint_id)

            with self.assertRaises(mb.MemoryNotFound):
                mb.get_role_lineage_checkpoint("0" * 64)

        with unavailable_service():
            with self.assertRaises(mb.MemoryBusError):
                mb.get_role_lineage_checkpoint("1" * 64)

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

    def test_transition_rejects_non_content_address_even_when_post_and_get_collude(self):
        with fake_memory_service() as state:
            payload = transition()
            expected_content = mb._role_memory_content(
                "project:strict-fixture", payload
            )
            expected_id = sha256(expected_content.encode("utf-8"))
            arbitrary_id = "f" * 64
            self.assertNotEqual(expected_id, arbitrary_id)
            state.post_id_override = arbitrary_id
            state.store_under_post_id_override = True

            with self.assertRaisesRegex(
                mb.MemoryBusError, "content hash|content address|identity"
            ):
                mb.prepare_role_transition(payload, strict=True)

            self.assertEqual(expected_content, state.records[arbitrary_id]["content"])
            self.assertEqual(arbitrary_id, state.records[arbitrary_id]["content_hash"])
            self.assertEqual(
                [("POST", "/api/memories")],
                state.calls,
            )

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
            rg.verify_precode_semantics = lambda ctx: None
            try:
                with fake_memory_service() as state:
                    fixture = Fixture(root, memory_mode="required")
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
                        pending = rg.status_data(fixture.artifact)
                        self.assertEqual("recovery_pending", pending["integrity_state"])
                        recovered = rg.recover(
                            fixture.artifact,
                            lineage_id=pending["lineage_id"],
                            expected_head=pending["registry_head_sha256"],
                        )
                        receipts = rg.verify_chain(rg.load_context(fixture.artifact))
                        self.assertEqual(
                            [
                                "human_acceptance",
                                "designer_handoff",
                                "evidence_recovery",
                            ],
                            [receipt["event"] for receipt in receipts],
                        )
                        handoff = receipts[1]
                        recovery_receipt = recovered["recovery_receipt"]
                        self.assertEqual(
                            "awaiting_implementer_acceptance",
                            recovery_receipt["resulting_state"],
                        )
                        self.assertEqual(
                            handoff["receipt_sha256"],
                            recovery_receipt["previous_receipt_sha256"],
                        )
                        after_recovery = rg.status_data(fixture.artifact)
                        self.assertTrue(after_recovery["ok"], after_recovery)
                        self.assertEqual("aligned", after_recovery["integrity_state"])
                        self.assertEqual(3, after_recovery["registry_sequence"])
                        self.assertEqual(
                            recovery_receipt["receipt_sha256"],
                            after_recovery["registry_head_sha256"],
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
