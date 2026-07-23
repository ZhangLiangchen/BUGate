#!/usr/bin/env python3
"""SUT-neutral memory-bus driver for the BUGate core.

Thin, stdlib-only wrapper over a locally running mcp-memory-service HTTP API.
The memory service itself (``mcp-memory-service`` + its ONNX model) is installed
and launched by the USER; this driver only talks to it.

Design rules:
- No SUT specifics. The project namespace/tag is a profile/env value, never
  hardcoded. Resolution order: env ``MEMORY_BUS_PROJECT_TAG`` -> config
  ``memory.namespace`` (from ``bugate.config.yaml``) -> default ``project:bugate``.
- The bus is MACHINE-level, not repo-level: one local service instance whose
  data home resolves system-wide (see ``memory_home``). N workspaces share it
  and are isolated by namespace tag, never by per-project databases.
- The bus is a generic BUGate component, not SUT-only: any subcommand takes
  ``--core`` (record/read in BUGate's own base-config namespace, ignoring the
  active imported SUT profile) or ``--namespace X`` (an explicit tag). One DB,
  tag-partitioned, so BUGate-core memory and SUT memory never cross-pollute.
- Standard library only (urllib, json, os, argparse, pathlib, datetime).
- Degrade gracefully for ordinary recall/bookkeeping commands.  Explicit
  ``--strict`` role-transition boundaries fail non-zero so an unavailable or
  unverifiable Memory service can never unlock a local lifecycle receipt.

Subcommands: session-start, stop, status, note, search, recent, get, handoff,
accept-handoff, verify-handoff, archive, lint.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bugate_core  # noqa: E402  (local sibling module)

DEFAULT_URL = "http://localhost:8000"
DEFAULT_PROJECT_TAG = "project:bugate"
DEFAULT_MEMORY_HOME = Path.home() / ".bugate" / "memory-bus"
START_HINT = "Start it with bin/memory-bus-start (or bin/memory-bus-ensure)."

VALID_AGENTS = ("builder", "designer", "implementer", "reviewer", "human", "agent")
VALID_TYPES = ("progress", "finding", "blocker", "decision", "handoff")
VALID_STATUS = ("draft", "confirmed", "obsolete")

ROLE_TRANSITION_SCHEMA = "bugate.role-transition/v1"
MEMORY_TRANSITION_SCHEMA = "bugate.memory-role-transition/v1"
ROLE_EVIDENCE_SCHEMA = "bugate.role-evidence/v1"
ROLE_CHAIN_SCHEMA = "bugate.role-chain/v1"
ROLE_LINEAGE_KEY_SCHEMA = "bugate.role-lineage-key/v1"
ROLE_LINEAGE_ROOT_SCHEMA = "bugate.role-lineage-root/v1"
ROLE_LINEAGE_CHECKPOINT_SCHEMA = "bugate.role-lineage-checkpoint/v1"
MEMORY_LINEAGE_ROOT_SCHEMA = "bugate.memory-role-lineage-root/v1"
MEMORY_LINEAGE_CHECKPOINT_SCHEMA = "bugate.memory-role-lineage-checkpoint/v1"
_ABSOLUTE_IDENTITY_TEXT_RE = re.compile(
    r"(?:^|[\s='\"])/(?:[^\s'\"]+)"
)
ACCEPTANCE_HANDOFFS = {
    "implementer_acceptance": ("designer_handoff", "pre_code", "implementation"),
    "reviewer_acceptance": ("implementer_handoff", "implementation", "post_run"),
}

# prepare_role_transition() and finalize_role_transition() run in the same role
# command.  Retaining the exact record verified by prepare lets finalize perform
# the required POST -> exact GET -> PUT -> exact GET sequence without inserting
# an unverified read between the two transaction halves.  The key includes the
# service URL so independent local services cannot collide.
_PREPARED_ROLE_TRANSITIONS: dict[tuple[str, str], dict[str, Any]] = {}


class MemoryBusError(RuntimeError):
    pass


class MemoryHTTPError(MemoryBusError):
    """A syntactically valid HTTP response with a non-success status."""

    def __init__(
        self,
        status_code: int,
        method: str,
        path: str,
        detail: str = "",
    ) -> None:
        self.status_code = int(status_code)
        self.status = self.status_code
        self.method = method
        self.path = path
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Memory HTTP {self.status_code} for {method} {path}{suffix}")


class MemoryNotFound(MemoryHTTPError):
    """An exact Memory identifier is absent, rather than the service being down."""


# --------------------------------------------------------------------------- #
# Root / config / env / namespace                                             #
# --------------------------------------------------------------------------- #

def root() -> Path:
    try:
        return bugate_core.find_root()
    except SystemExit:
        return Path.cwd().resolve()


def project_tag() -> str:
    """SUT-neutral project namespace/tag.

    env MEMORY_BUS_PROJECT_TAG > config memory.namespace > DEFAULT_PROJECT_TAG.
    """
    env = os.environ.get("MEMORY_BUS_PROJECT_TAG", "").strip()
    if env:
        return env
    try:
        config = bugate_core.load_config()
    except Exception:
        config = {}
    memory = config.get("memory")
    if isinstance(memory, dict):
        value = str(memory.get("namespace") or "").strip()
        if value:
            return value
    # load_config canonicalizes the legacy top-level alias alongside the nested
    # memory.namespace value, so old profiles remain readable here.
    value = str(config.get("namespace") or "").strip()
    if value:
        return value
    return DEFAULT_PROJECT_TAG


def core_project_tag() -> str:
    """BUGate's OWN namespace, independent of any active SUT profile.

    The memory bus is a generic BUGate component: it must hold BUGate's own
    governance memory, not only the SUT's. An imported SUT profile overrides
    ``memory.namespace`` to the SUT tag, so this reads the BASE
    ``bugate.config.yaml`` directly (no profile merge) to recover the core tag,
    falling back to DEFAULT_PROJECT_TAG. Selected via the ``--core`` flag.
    """
    try:
        base = bugate_core.parse_nested_yaml(
            (root() / "bugate.config.yaml").read_text(encoding="utf-8")
        )
        memory = base.get("memory")
        if isinstance(memory, dict):
            value = str(memory.get("namespace") or "").strip()
            if value:
                return value
        # Legacy v0.3.x configs exposed namespace as a top-level scalar.
        value = str(base.get("namespace") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return DEFAULT_PROJECT_TAG


def memory_home() -> Path:
    """System-level bus data home shared by every workspace on this machine.

    Resolution: ``MCP_MEMORY_BASE_DIR`` (the service's own env var, highest)
    > ``BUGATE_MEMORY_HOME`` > ``~/.bugate/memory-bus``. Wrappers and clients
    all resolve through this order, so any repo that starts the service lands
    on the same directory by construction (no per-repo split-brain databases).
    """
    for env in ("MCP_MEMORY_BASE_DIR", "BUGATE_MEMORY_HOME"):
        value = os.environ.get(env, "").strip()
        if value:
            return Path(value).expanduser()
    return DEFAULT_MEMORY_HOME


def load_local_env() -> None:
    """Load client API keys from client.env without overriding the env.

    Search order: the system-level home (``memory_home()/client.env``) first,
    then the legacy per-repo ``.memory_bus/client.env`` as a deprecated
    fallback (a stderr hint asks for a move to the system home). Real env vars
    always win — file values never overwrite existing ``os.environ`` entries.
    """
    system_path = memory_home() / "client.env"
    legacy_path = root() / ".memory_bus" / "client.env"
    if system_path.exists():
        env_path = system_path
    elif legacy_path.exists():
        env_path = legacy_path
        if system_path != legacy_path:
            print(
                f"memory-bus: using legacy client.env at {legacy_path}; "
                f"move it to {system_path} (system-level bus home).",
                file=sys.stderr,
            )
    else:
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def base_url() -> str:
    return os.environ.get("MEMORY_BUS_URL", DEFAULT_URL).rstrip("/")


def auth_headers(agent: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("MCP_API_KEY_AGENT") or os.environ.get("MCP_API_KEY_HUMAN") or os.environ.get("MCP_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token
    if agent:
        headers["X-Agent-ID"] = agent
    return headers


# --------------------------------------------------------------------------- #
# HTTP                                                                          #
# --------------------------------------------------------------------------- #

def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    agent: str | None = None,
    timeout: float = 3.0,
) -> Any:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url()}{path}",
        data=data,
        method=method,
        headers=auth_headers(agent),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            if isinstance(parsed, dict):
                candidate = parsed.get("detail") or parsed.get("message") or parsed.get("error")
                if isinstance(candidate, (str, int, float, bool)):
                    detail = str(candidate)[:240]
        except (OSError, ValueError, json.JSONDecodeError):
            detail = ""
        is_exact_get = method == "GET" and path.startswith("/api/memories/")
        error_type = MemoryNotFound if exc.code == 404 and is_exact_get else MemoryHTTPError
        raise error_type(exc.code, method, path, detail) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise MemoryBusError(str(exc)) from exc


def service_available() -> bool:
    try:
        data = request_json("GET", "/api/health", timeout=1.5)
    except MemoryBusError:
        return False
    # Service-signature check: port 8000 is heavily squatted in dev
    # environments; a foreign app answering 200 with arbitrary JSON must not
    # masquerade as the memory bus. mcp-memory-service /api/health always
    # carries a "status" field.
    return isinstance(data, dict) and "status" in data


def warn_unavailable() -> None:
    print(
        f"Memory service unavailable at {base_url()}. {START_HINT}",
        file=sys.stderr,
    )


def self_heal_service() -> bool:
    """Best-effort: bring the REQUIRED memory service back up when it is down.

    The memory bus is a core BUGate component, so an outage should self-heal
    (diagnose + restart) rather than silently degrade. This fires
    ``bin/memory-bus-ensure`` in the background (which reuses a healthy service,
    restarts a crashed one, or installs it once on a first run) and returns
    quickly. Never raises and never blocks the caller — if healing can't run,
    the caller still degrades gracefully for this turn while the next turn
    recovers.
    """
    ensure = Path(__file__).resolve().parents[1] / "bin" / "memory-bus-ensure"
    if not ensure.exists():
        return False
    try:
        import subprocess

        subprocess.Popen(
            [str(ensure)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Record accessors (the service nests records under varying keys)             #
# --------------------------------------------------------------------------- #

def _unwrap(memory: dict[str, Any]) -> dict[str, Any]:
    nested = memory.get("memory")
    return nested if isinstance(nested, dict) else memory


def memory_id(memory: dict[str, Any]) -> str:
    memory = _unwrap(memory)
    for key in ("id", "memory_id", "uuid", "content_hash"):
        if memory.get(key):
            return str(memory[key])
    return "<unknown>"


def memory_tags(memory: dict[str, Any]) -> list[str]:
    memory = _unwrap(memory)
    tags = memory.get("tags") or memory.get("labels") or []
    return [str(t) for t in tags] if isinstance(tags, list) else []


def memory_content(memory: dict[str, Any]) -> str:
    memory = _unwrap(memory)
    return str(memory.get("content") or memory.get("text") or "")


def memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    memory = _unwrap(memory)
    metadata = memory.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def memory_created(memory: dict[str, Any]) -> str:
    memory = _unwrap(memory)
    for key in ("created_at_iso", "created_at", "createdAt", "timestamp", "updated_at"):
        if memory.get(key):
            return str(memory[key])
    return ""


def memory_epoch(memory: dict[str, Any]) -> float:
    memory = _unwrap(memory)
    for key in ("created_at", "timestamp", "updated_at"):
        value = memory.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    iso = memory_created(memory)
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


def unwrap_results(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [_unwrap(x) for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("memories", "results", "items", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [_unwrap(x) for x in value if isinstance(x, dict)]
    return []


def has_all_tags(memory: dict[str, Any], required: set[str]) -> bool:
    return required.issubset(set(memory_tags(memory)))


def has_any_tag(memory: dict[str, Any], candidates: set[str]) -> bool:
    return bool(set(memory_tags(memory)) & candidates)


def first_tag_value(tags: list[str], prefix: str) -> str | None:
    needle = f"{prefix}:"
    for tag in tags:
        if tag.startswith(needle):
            return tag[len(needle):]
    return None


# --------------------------------------------------------------------------- #
# Reads                                                                         #
# --------------------------------------------------------------------------- #

def semantic_search(query: str, tags: list[str] | None, limit: int) -> list[dict[str, Any]]:
    results = unwrap_results(request_json("POST", "/api/search", {"query": query, "n_results": limit}))
    if tags:
        required = set(tags)
        results = [m for m in results if has_all_tags(m, required)]
    return results[:limit]


def tag_search(tags: list[str], limit: int, match_all: bool = True) -> list[dict[str, Any]]:
    payload = {"tags": tags, "match_all": match_all}
    return unwrap_results(request_json("POST", "/api/search/by-tag", payload))[:limit]


def list_project_memories(limit: int) -> list[dict[str, Any]]:
    tag = project_tag()
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < limit:
        params = urllib.parse.urlencode(
            {"page": str(page), "page_size": str(min(100, limit - len(out))), "tag": tag, "tag_match": "all"}
        )
        batch = unwrap_results(request_json("GET", f"/api/memories?{params}"))
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out[:limit]


def search_memories(query: str, tags: list[str], limit: int) -> list[dict[str, Any]]:
    """Best-effort search with fallbacks across known endpoint shapes."""
    for attempt in (
        lambda: semantic_search(query, tags, limit),
        lambda: tag_search(tags, limit, match_all=True),
    ):
        try:
            results = attempt()
            if results:
                return results[:limit]
        except MemoryBusError:
            continue
    params = urllib.parse.urlencode({"page_size": str(min(max(limit, 1), 100)), "tag": project_tag(), "tag_match": "any"})
    try:
        return unwrap_results(request_json("GET", f"/api/memories?{params}"))[:limit]
    except MemoryBusError as exc:
        raise MemoryBusError(f"search failed: {exc}") from exc


def recent_for_agent(agent: str, limit: int) -> list[dict[str, Any]]:
    tag = project_tag()
    target = {f"msg:to-{agent}", "msg:broadcast"}
    status = {"status:draft", "status:confirmed"}
    raw = list_project_memories(max(limit * 8, 80))
    filtered = [
        m for m in raw
        if has_all_tags(m, {tag}) and has_any_tag(m, target) and has_any_tag(m, status)
    ]
    filtered.sort(key=memory_epoch, reverse=True)
    return filtered[:limit]


# --------------------------------------------------------------------------- #
# Writes                                                                        #
# --------------------------------------------------------------------------- #

def post_memory(content: str, tags: list[str], agent: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    mem_type = first_tag_value(tags, "type") or "progress"
    type_map = {
        "blocker": "error",
        "decision": "decision",
        "finding": "observation",
        "handoff": "communication",
        "progress": "milestone",
    }
    payload = {
        "content": content,
        "tags": tags,
        "memory_type": type_map.get(mem_type, "observation"),
        # Distinct conversation_id avoids the service collapsing similar
        # cross-agent progress/handoff entries by semantic dedup.
        "conversation_id": f"bugate-memory-bus-{agent}-{datetime.now(timezone.utc).timestamp()}",
        "metadata": dict(metadata or {}),
    }
    data = request_json("POST", "/api/memories", payload, agent=agent)
    return data if isinstance(data, dict) else {"response": data}


def build_tags(
    agent: str,
    mem_type: str,
    status: str,
    scope: str | None = None,
    task: str | None = None,
    to: str | None = None,
    broadcast: bool = False,
    extra: list[str] | None = None,
) -> list[str]:
    tags = [project_tag(), f"agent:{agent}", f"type:{mem_type}", f"status:{status}"]
    if scope:
        tags.append(f"scope:{scope}")
    if task:
        tags.append(f"task:{task}")
    if to:
        tags.append(f"msg:to-{to}")
    elif broadcast:
        tags.append("msg:broadcast")
    tags.extend(extra or [])
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


# --------------------------------------------------------------------------- #
# Exact role-transition boundary                                                #
# --------------------------------------------------------------------------- #

def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MemoryBusError(f"role transition is not canonical JSON: {exc}") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_sha256(value: Any, label: str, *, allow_empty: bool = False) -> str:
    candidate = str(value or "")
    if allow_empty and not candidate:
        return ""
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
        raise MemoryBusError(f"{label} must be a lowercase 64-character SHA-256")
    return candidate


def _workspace_relative_posix_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MemoryBusError(f"{label} must be a non-empty workspace-relative path")
    if "\\" in value:
        raise MemoryBusError(f"{label} must use POSIX separators")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or re.match(r"^file:", value, flags=re.IGNORECASE) is not None
        or value != path.as_posix()
    ):
        raise MemoryBusError(f"{label} must be a canonical workspace-relative POSIX path")
    if any(part in ("", ".", "..") for part in path.parts):
        raise MemoryBusError(f"{label} must not escape or alias the workspace")
    return value


def _lineage_identity_text(value: Any, label: str) -> str:
    """Reject machine/path identity while preserving exact namespace text."""

    if not isinstance(value, str) or not value or not value.strip():
        raise MemoryBusError(f"{label} must be a non-empty exact string")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise MemoryBusError(f"{label} must not contain control separators")
    if (
        value.startswith(("/", "\\"))
        or _ABSOLUTE_IDENTITY_TEXT_RE.search(value)
        or re.search(r"(?:^|:)\/(?:[^/]|$)", value)
        or re.search(r"(?:^|:)[A-Za-z]:[\\/]", value)
        or re.search(r"(?:^|:)[\\/]{2}[^\\/]", value)
        or re.search(r"(?:^|:)file:", value, flags=re.IGNORECASE)
        or PureWindowsPath(value).is_absolute()
    ):
        raise MemoryBusError(f"{label} must not contain an absolute path")
    return value


def _validate_role_lineage_key(
    lineage_key: dict[str, Any],
    *,
    require_active_namespace: bool = True,
) -> dict[str, Any]:
    if not isinstance(lineage_key, dict):
        raise MemoryBusError("role lineage key must be an object")
    required = {"schema", "namespace", "uc", "artifact_dir"}
    if set(lineage_key) != required:
        missing = sorted(required - set(lineage_key))
        extra = sorted(set(lineage_key) - required)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise MemoryBusError("invalid role lineage key fields: " + "; ".join(details))
    if lineage_key.get("schema") != ROLE_LINEAGE_KEY_SCHEMA:
        raise MemoryBusError(f"role lineage key schema must be {ROLE_LINEAGE_KEY_SCHEMA}")
    namespace = _lineage_identity_text(
        lineage_key.get("namespace"), "role lineage key namespace"
    )
    _lineage_identity_text(lineage_key.get("uc"), "role lineage key uc")
    _workspace_relative_posix_path(lineage_key.get("artifact_dir"), "lineage artifact_dir")
    if require_active_namespace and namespace != project_tag():
        raise MemoryBusError(
            "role lineage namespace mismatch: "
            f"expected {project_tag()!r}, got {namespace!r}"
        )
    # Return a detached value so callers cannot mutate a verified identity by
    # retaining a reference to the input mapping.
    return copy.deepcopy(lineage_key)


def build_role_lineage_key(
    namespace: str,
    uc: str,
    artifact_dir: str,
) -> dict[str, Any]:
    """Build the SUT-neutral, deterministic identity key for one governed UC."""

    key = {
        "schema": ROLE_LINEAGE_KEY_SCHEMA,
        "namespace": namespace,
        "uc": uc,
        "artifact_dir": artifact_dir,
    }
    return _validate_role_lineage_key(key, require_active_namespace=False)


def role_lineage_id(lineage_key: dict[str, Any]) -> str:
    key = _validate_role_lineage_key(lineage_key, require_active_namespace=False)
    return _sha256(_canonical_json(key))


def _role_lineage_root_payload(
    lineage_key: dict[str, Any],
    lineage_id: str | None = None,
) -> dict[str, Any]:
    key = _validate_role_lineage_key(lineage_key)
    computed = role_lineage_id(key)
    supplied = str(lineage_id or computed)
    if supplied != computed:
        raise MemoryBusError(
            f"role lineage ID mismatch: expected {computed}, got {supplied or '<missing>'}"
        )
    return {
        "schema": ROLE_LINEAGE_ROOT_SCHEMA,
        "lineage_key": key,
        "lineage_id": computed,
    }


def _canonical_content(value: dict[str, Any]) -> str:
    return _canonical_json(value).decode("utf-8")


def _role_lineage_root_id(payload: dict[str, Any]) -> str:
    return _sha256(_canonical_json(payload))


def _require_post_content_hash(
    response: dict[str, Any],
    expected: str,
    operation: str,
) -> str:
    returned: list[str] = []
    for candidate in (response, response.get("memory")):
        if isinstance(candidate, dict) and candidate.get("content_hash"):
            value = str(candidate["content_hash"])
            if value not in returned:
                returned.append(value)
    if not returned:
        fallback = memory_id(response)
        if fallback != "<unknown>" and fallback:
            returned.append(fallback)
    if not returned:
        raise MemoryBusError(f"{operation} response is missing content hash")
    mismatched = [value for value in returned if value != expected]
    if mismatched:
        raise MemoryBusError(
            f"{operation} content hash mismatch: expected {expected}, "
            f"got {', '.join(mismatched)}"
        )
    return expected


def _role_lineage_tags(kind: str) -> list[str]:
    if kind not in ("root", "checkpoint"):
        raise MemoryBusError(f"unknown role lineage Memory kind: {kind}")
    # Namespace and this fixed vocabulary are the only tags. UC identifiers,
    # paths, receipt/checkpoint hashes, sequences, and revisions stay in
    # immutable content or metadata and are never searched by tag.
    return build_tags(
        "agent",
        "decision",
        "confirmed",
        scope=f"role-lineage-{kind}",
        broadcast=True,
    )


def verify_role_lineage_root(
    record: dict[str, Any],
    lineage_key: dict[str, Any],
    lineage_id: str | None = None,
    *,
    exact_id: str | None = None,
) -> dict[str, Any]:
    """Verify one deterministic lineage root returned by an exact GET."""

    expected_payload = _role_lineage_root_payload(lineage_key, lineage_id)
    expected_content = _canonical_content(expected_payload)
    expected_id = _role_lineage_root_id(expected_payload)
    if exact_id is not None and str(exact_id) != expected_id:
        raise MemoryBusError(
            f"role lineage root exact ID mismatch: expected {expected_id}, got {exact_id}"
        )
    actual_id = memory_id(record)
    if actual_id != expected_id:
        raise MemoryBusError(
            f"role lineage root Memory ID mismatch: expected {expected_id}, got {actual_id}"
        )
    actual_content = memory_content(record)
    if _sha256(actual_content.encode("utf-8")) != expected_id:
        raise MemoryBusError("role lineage root content hash does not match its exact ID")
    if actual_content != expected_content:
        raise MemoryBusError("role lineage root content does not exactly match expected payload")
    metadata = memory_metadata(record)
    checks = {
        "schema": MEMORY_LINEAGE_ROOT_SCHEMA,
        "lineage_schema": ROLE_LINEAGE_ROOT_SCHEMA,
        "namespace": expected_payload["lineage_key"]["namespace"],
        "lineage_id": expected_payload["lineage_id"],
    }
    for key, wanted in checks.items():
        if metadata.get(key) != wanted:
            raise MemoryBusError(
                f"role lineage root metadata mismatch for {key}: "
                f"expected {wanted!r}, got {metadata.get(key)!r}"
            )
    expected_tags = set(_role_lineage_tags("root"))
    if set(memory_tags(record)) != expected_tags:
        raise MemoryBusError("role lineage root Memory tags do not exactly match contract")
    return {
        "namespace": expected_payload["lineage_key"]["namespace"],
        "lineage_id": expected_payload["lineage_id"],
        "lineage_root_id": expected_id,
        "memory_id": expected_id,
        "content_sha256": expected_id,
        "payload": expected_payload,
        "status": "verified",
    }


def probe_role_lineage_root(
    lineage_key: dict[str, Any],
    lineage_id: str | None = None,
    *,
    agent: str = "agent",
    timeout: float = 3.0,
) -> dict[str, Any] | None:
    """Probe a deterministic root by exact ID; only a real 404 means absent."""

    payload = _role_lineage_root_payload(lineage_key, lineage_id)
    exact_id = _role_lineage_root_id(payload)
    try:
        record = get_memory_exact(exact_id, agent=agent, timeout=timeout)
    except MemoryNotFound:
        return None
    return verify_role_lineage_root(
        record,
        payload["lineage_key"],
        payload["lineage_id"],
        exact_id=exact_id,
    )


def ensure_role_lineage_root(
    lineage_key: dict[str, Any],
    lineage_id: str | None = None,
    *,
    agent: str = "agent",
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Idempotently POST a deterministic root, then verify it by exact GET."""

    payload = _role_lineage_root_payload(lineage_key, lineage_id)
    content = _canonical_content(payload)
    exact_id = _sha256(content.encode("utf-8"))
    metadata = {
        "schema": MEMORY_LINEAGE_ROOT_SCHEMA,
        "lineage_schema": ROLE_LINEAGE_ROOT_SCHEMA,
        "namespace": payload["lineage_key"]["namespace"],
        "lineage_id": payload["lineage_id"],
    }
    response = _require_success(
        request_json(
            "POST",
            "/api/memories",
            {
                "content": content,
                "tags": _role_lineage_tags("root"),
                "memory_type": "decision",
                "conversation_id": f"bugate-role-lineage-root-{payload['lineage_id']}",
                "metadata": metadata,
            },
            agent=agent,
            timeout=timeout,
        ),
        "role lineage root POST",
    )
    _require_post_content_hash(response, exact_id, "role lineage root POST")
    record = get_memory_exact(exact_id, agent=agent, timeout=timeout)
    return verify_role_lineage_root(
        record,
        payload["lineage_key"],
        payload["lineage_id"],
        exact_id=exact_id,
    )


def _validate_evidence_envelope(
    envelope: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise MemoryBusError(f"{label} must be an object")
    required = {"path", "mode", "bytes_sha256", "bytes_base64", "parsed"}
    if set(envelope) != required:
        missing = sorted(required - set(envelope))
        extra = sorted(set(envelope) - required)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise MemoryBusError(f"invalid {label} fields: " + "; ".join(details))
    _workspace_relative_posix_path(envelope.get("path"), f"{label} path")
    mode = envelope.get("mode")
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        raise MemoryBusError(f"{label} mode must be integer permission bits")
    expected_hash = _require_sha256(envelope.get("bytes_sha256"), f"{label} bytes_sha256")
    encoded = envelope.get("bytes_base64")
    if not isinstance(encoded, str) or not encoded:
        raise MemoryBusError(f"{label} bytes_base64 must be a non-empty string")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise MemoryBusError(f"{label} bytes_base64 is invalid") from exc
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise MemoryBusError(f"{label} bytes_base64 is not canonical")
    if _sha256(raw) != expected_hash:
        raise MemoryBusError(f"{label} bytes_sha256 does not match bytes_base64")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryBusError(f"{label} bytes are not a UTF-8 JSON document") from exc
    parsed = envelope.get("parsed")
    if not isinstance(parsed, dict) or not isinstance(decoded, dict):
        raise MemoryBusError(f"{label} parsed value and bytes must be JSON objects")
    if decoded != parsed:
        raise MemoryBusError(f"{label} parsed value does not exactly match bytes")
    return copy.deepcopy(parsed)


def _validate_role_lineage_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MemoryBusError("role lineage checkpoint must be an object")
    required = {
        "schema",
        "lineage_key",
        "lineage_id",
        "lineage_root_id",
        "sequence",
        "previous_checkpoint_id",
        "previous_receipt_sha256",
        "receipt_sha256",
        "resulting_state",
        "registry_revision",
        "receipt_envelope",
        "chain_envelope",
    }
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - required)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise MemoryBusError(
            "invalid role lineage checkpoint fields: " + "; ".join(details)
        )
    if payload.get("schema") != ROLE_LINEAGE_CHECKPOINT_SCHEMA:
        raise MemoryBusError(
            f"role lineage checkpoint schema must be {ROLE_LINEAGE_CHECKPOINT_SCHEMA}"
        )
    key = _validate_role_lineage_key(payload.get("lineage_key"))
    lineage_id = _require_sha256(payload.get("lineage_id"), "lineage_id")
    computed_lineage_id = role_lineage_id(key)
    if lineage_id != computed_lineage_id:
        raise MemoryBusError(
            f"checkpoint lineage ID mismatch: expected {computed_lineage_id}, got {lineage_id}"
        )
    root_payload = _role_lineage_root_payload(key, lineage_id)
    expected_root_id = _role_lineage_root_id(root_payload)
    root_id = _require_sha256(payload.get("lineage_root_id"), "lineage_root_id")
    if root_id != expected_root_id:
        raise MemoryBusError(
            f"checkpoint lineage root ID mismatch: expected {expected_root_id}, got {root_id}"
        )
    sequence = payload.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise MemoryBusError("checkpoint sequence must be a positive integer")
    revision = payload.get("registry_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise MemoryBusError("checkpoint registry_revision must be a non-negative integer")
    previous_checkpoint_id = _require_sha256(
        payload.get("previous_checkpoint_id"),
        "previous_checkpoint_id",
        allow_empty=True,
    )
    previous_receipt = _require_sha256(
        payload.get("previous_receipt_sha256"),
        "previous_receipt_sha256",
        allow_empty=True,
    )
    if sequence == 1 and (previous_checkpoint_id or previous_receipt):
        raise MemoryBusError(
            "first checkpoint must have empty predecessor IDs"
        )
    if sequence > 1 and (not previous_checkpoint_id or not previous_receipt):
        raise MemoryBusError(
            "non-root checkpoint must bind both predecessor IDs"
        )
    receipt_hash = _require_sha256(payload.get("receipt_sha256"), "receipt_sha256")
    resulting_state = payload.get("resulting_state")
    if not isinstance(resulting_state, str) or not resulting_state:
        raise MemoryBusError("checkpoint resulting_state must be a non-empty string")
    receipt = _validate_evidence_envelope(
        payload.get("receipt_envelope"), "checkpoint receipt_envelope"
    )
    chain = _validate_evidence_envelope(
        payload.get("chain_envelope"), "checkpoint chain_envelope"
    )
    receipt_path = PurePosixPath(payload["receipt_envelope"]["path"])
    chain_path = PurePosixPath(payload["chain_envelope"]["path"])
    artifact_path = PurePosixPath(key["artifact_dir"])

    def below_artifact(path: PurePosixPath) -> bool:
        return (
            len(path.parts) > len(artifact_path.parts)
            and path.parts[: len(artifact_path.parts)] == artifact_path.parts
        )

    if not below_artifact(receipt_path) or not below_artifact(chain_path):
        raise MemoryBusError(
            "checkpoint evidence path must stay below the lineage artifact directory"
        )
    if receipt.get("schema") != ROLE_EVIDENCE_SCHEMA:
        raise MemoryBusError(
            f"checkpoint receipt envelope schema must be {ROLE_EVIDENCE_SCHEMA}"
        )
    if chain.get("schema") != ROLE_CHAIN_SCHEMA:
        raise MemoryBusError(
            f"checkpoint chain envelope schema must be {ROLE_CHAIN_SCHEMA}"
        )
    event = receipt.get("event")
    if not isinstance(event, str) or not event:
        raise MemoryBusError("checkpoint receipt envelope event must be non-empty")
    expected_receipt_name = (
        f"{sequence:06d}-{event.replace('_', '-')}-{receipt_hash}.json"
    )
    if (
        receipt_path.name != expected_receipt_name
        or receipt_path.parent.name != "receipts"
        or receipt_path.parent.parent != chain_path.parent
        or chain_path.name != "chain.json"
    ):
        raise MemoryBusError(
            "checkpoint receipt/chain evidence paths do not match the lineage layout"
        )
    receipt_checks = {
        "sequence": sequence,
        "uc": key["uc"],
        "artifact_dir": key["artifact_dir"],
        "previous_receipt_sha256": previous_receipt,
        "receipt_sha256": receipt_hash,
        "resulting_state": resulting_state,
    }
    for field, wanted in receipt_checks.items():
        if receipt.get(field) != wanted:
            raise MemoryBusError(
                f"checkpoint receipt envelope mismatch for {field}: "
                f"expected {wanted!r}, got {receipt.get(field)!r}"
            )
    chain_checks = {
        "sequence": sequence,
        "head_sha256": receipt_hash,
        "state": resulting_state,
    }
    for field, wanted in chain_checks.items():
        if chain.get(field) != wanted:
            raise MemoryBusError(
                f"checkpoint chain envelope mismatch for {field}: "
                f"expected {wanted!r}, got {chain.get(field)!r}"
            )
    latest = chain.get("latest_receipts")
    if not isinstance(latest, dict) or latest.get(event) != receipt_path.as_posix():
        raise MemoryBusError(
            "checkpoint chain latest_receipts does not bind the receipt evidence path"
        )
    if payload["receipt_envelope"]["path"] == payload["chain_envelope"]["path"]:
        raise MemoryBusError("checkpoint receipt and chain envelopes must use distinct paths")
    # Canonicalization here rejects non-JSON extension values before any POST.
    _canonical_json(payload)
    result = copy.deepcopy(payload)
    result["lineage_key"] = key
    result["lineage_id"] = lineage_id
    result["lineage_root_id"] = root_id
    result["previous_checkpoint_id"] = previous_checkpoint_id
    result["previous_receipt_sha256"] = previous_receipt
    result["receipt_sha256"] = receipt_hash
    return result


def verify_role_lineage_checkpoint(
    record: dict[str, Any],
    expected: dict[str, Any] | None = None,
    *,
    exact_id: str | None = None,
) -> dict[str, Any]:
    """Verify immutable checkpoint content and its exact content address."""

    content = memory_content(record)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MemoryBusError("role lineage checkpoint content is not JSON") from exc
    payload = _validate_role_lineage_checkpoint(parsed)
    canonical_content = _canonical_content(payload)
    if content != canonical_content:
        raise MemoryBusError("role lineage checkpoint content is not canonical JSON")
    content_id = _sha256(content.encode("utf-8"))
    actual_id = memory_id(record)
    if actual_id != content_id:
        raise MemoryBusError(
            f"role lineage checkpoint content hash mismatch: expected {content_id}, got {actual_id}"
        )
    if exact_id is not None and str(exact_id) != content_id:
        raise MemoryBusError(
            f"role lineage checkpoint exact ID mismatch: expected {exact_id}, got {content_id}"
        )
    if expected is not None:
        expected_payload = _validate_role_lineage_checkpoint(expected)
        if _canonical_json(expected_payload) != _canonical_json(payload):
            raise MemoryBusError("role lineage checkpoint payload does not exactly match expected")
    metadata = memory_metadata(record)
    checks = {
        "schema": MEMORY_LINEAGE_CHECKPOINT_SCHEMA,
        "checkpoint_schema": ROLE_LINEAGE_CHECKPOINT_SCHEMA,
        "namespace": payload["lineage_key"]["namespace"],
        "lineage_id": payload["lineage_id"],
        "lineage_root_id": payload["lineage_root_id"],
        "sequence": payload["sequence"],
        "registry_revision": payload["registry_revision"],
    }
    for key, wanted in checks.items():
        if metadata.get(key) != wanted:
            raise MemoryBusError(
                f"role lineage checkpoint metadata mismatch for {key}: "
                f"expected {wanted!r}, got {metadata.get(key)!r}"
            )
    if set(memory_tags(record)) != set(_role_lineage_tags("checkpoint")):
        raise MemoryBusError("role lineage checkpoint Memory tags do not exactly match contract")
    return {
        "namespace": payload["lineage_key"]["namespace"],
        "lineage_id": payload["lineage_id"],
        "lineage_root_id": payload["lineage_root_id"],
        "checkpoint_id": content_id,
        "memory_id": content_id,
        "content_sha256": content_id,
        "sequence": payload["sequence"],
        "registry_revision": payload["registry_revision"],
        "resulting_state": payload["resulting_state"],
        "payload": payload,
        "status": "verified",
    }


def get_role_lineage_checkpoint(
    exact_id: str,
    *,
    agent: str = "agent",
    timeout: float = 3.0,
) -> dict[str, Any]:
    checkpoint_id = _require_sha256(exact_id, "checkpoint exact ID")
    record = get_memory_exact(checkpoint_id, agent=agent, timeout=timeout)
    return verify_role_lineage_checkpoint(record, exact_id=checkpoint_id)


def create_role_lineage_checkpoint(
    payload: dict[str, Any],
    *,
    agent: str = "agent",
    timeout: float = 3.0,
) -> dict[str, Any]:
    """POST one immutable checkpoint and prove it with an exact GET; never PUT."""

    checkpoint = _validate_role_lineage_checkpoint(payload)
    content = _canonical_content(checkpoint)
    exact_id = _sha256(content.encode("utf-8"))
    metadata = {
        "schema": MEMORY_LINEAGE_CHECKPOINT_SCHEMA,
        "checkpoint_schema": ROLE_LINEAGE_CHECKPOINT_SCHEMA,
        "namespace": checkpoint["lineage_key"]["namespace"],
        "lineage_id": checkpoint["lineage_id"],
        "lineage_root_id": checkpoint["lineage_root_id"],
        "sequence": checkpoint["sequence"],
        "registry_revision": checkpoint["registry_revision"],
    }
    response = _require_success(
        request_json(
            "POST",
            "/api/memories",
            {
                "content": content,
                "tags": _role_lineage_tags("checkpoint"),
                "memory_type": "decision",
                "conversation_id": f"bugate-role-lineage-checkpoint-{exact_id}",
                "metadata": metadata,
            },
            agent=agent,
            timeout=timeout,
        ),
        "role lineage checkpoint POST",
    )
    _require_post_content_hash(response, exact_id, "role lineage checkpoint POST")
    record = get_memory_exact(exact_id, agent=agent, timeout=timeout)
    return verify_role_lineage_checkpoint(record, checkpoint, exact_id=exact_id)


def _require_success(data: Any, operation: str) -> dict[str, Any]:
    """Require the service's explicit mutation acknowledgement.

    The Memory service deliberately uses HTTP 200 with ``success: false`` for
    some storage failures.  A strict boundary must treat that as a failed
    mutation rather than trusting the status code alone.
    """

    if not isinstance(data, dict):
        raise MemoryBusError(f"{operation} returned a non-object response")
    if data.get("success") is not True:
        message = str(data.get("message") or data.get("error") or "success was not true")
        raise MemoryBusError(f"{operation} failed: {message}")
    return data


def get_memory_exact(
    exact_id: str,
    *,
    agent: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Read one record by exact content hash and verify the returned identity."""

    exact_id = str(exact_id or "").strip()
    if not exact_id or exact_id == "<unknown>":
        raise MemoryBusError("exact Memory GET requires a non-empty content hash")
    encoded = urllib.parse.quote(exact_id, safe="")
    data = request_json("GET", f"/api/memories/{encoded}", agent=agent, timeout=timeout)
    if isinstance(data, dict) and data.get("success") is False:
        message = str(data.get("message") or data.get("error") or "success was false")
        raise MemoryBusError(f"exact Memory GET failed: {message}")
    if not isinstance(data, dict):
        raise MemoryBusError("exact Memory GET returned a non-object response")
    record = _unwrap(data)
    if not isinstance(record, dict):
        raise MemoryBusError("exact Memory GET did not return a memory object")
    actual_id = memory_id(record)
    if actual_id == "<unknown>":
        raise MemoryBusError("exact Memory GET response is missing content hash")
    if actual_id != exact_id:
        raise MemoryBusError(
            f"exact Memory GET identity mismatch: expected {exact_id}, got {actual_id}"
        )
    return record


def update_memory_metadata_exact(
    exact_id: str,
    updates: dict[str, Any],
    *,
    agent: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Merge metadata, require PUT success, then exact-GET the stored result.

    Some Memory Service/storage versions merge the supplied ``metadata`` map;
    others replace it.  Reading and sending the complete merged map preserves
    the transition contract under both semantics.
    """

    if not isinstance(updates, dict) or not updates:
        raise MemoryBusError("exact Memory update requires non-empty metadata")
    exact_id = str(exact_id or "").strip()
    if not exact_id:
        raise MemoryBusError("exact Memory update requires a content hash")
    current = get_memory_exact(exact_id, agent=agent, timeout=timeout)
    merged_metadata = copy.deepcopy(memory_metadata(current))
    merged_metadata.update(copy.deepcopy(updates))
    encoded = urllib.parse.quote(exact_id, safe="")
    data = request_json(
        "PUT",
        f"/api/memories/{encoded}",
        {"metadata": merged_metadata},
        agent=agent,
        timeout=timeout,
    )
    response = _require_success(data, "exact Memory metadata update")
    response_id = memory_id(response)
    if response_id == "<unknown>" or not response_id:
        raise MemoryBusError("exact Memory metadata update response is missing content hash")
    if response_id != exact_id:
        raise MemoryBusError(
            f"exact Memory update identity mismatch: expected {exact_id}, got {response_id}"
        )
    stored = get_memory_exact(exact_id, agent=agent, timeout=timeout)
    actual_metadata = memory_metadata(stored)
    for key, wanted in merged_metadata.items():
        if actual_metadata.get(key) != wanted:
            raise MemoryBusError(
                f"exact Memory update did not persist metadata field {key}"
            )
    return stored


def _transition_hash(payload: dict[str, Any]) -> str:
    value = copy.deepcopy(payload)
    value.pop("transition_sha256", None)
    return _sha256(_canonical_json(value))


def _validate_transition_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise MemoryBusError("role transition payload must be an object")
    if payload.get("schema") != ROLE_TRANSITION_SCHEMA:
        raise MemoryBusError(
            f"role transition schema must be {ROLE_TRANSITION_SCHEMA}"
        )
    for key in ("event", "uc", "phase"):
        if not isinstance(payload.get(key), str) or not str(payload[key]).strip():
            raise MemoryBusError(f"role transition is missing non-empty {key}")
    for key in ("from_role", "to_role"):
        if not isinstance(payload.get(key), str):
            raise MemoryBusError(f"role transition {key} must be a string")
    supplied = str(payload.get("transition_sha256") or "")
    computed = _transition_hash(payload)
    if supplied != computed:
        raise MemoryBusError(
            f"transition hash mismatch: expected {computed}, got {supplied or '<missing>'}"
        )


def _transition_agent(payload: dict[str, Any]) -> str:
    actor = payload.get("actor")
    actor_role = str(actor.get("role") or "") if isinstance(actor, dict) else ""
    candidate = actor_role or str(payload.get("from_role") or "")
    return candidate if candidate in VALID_AGENTS else "agent"


def _role_memory_content(namespace: str, payload: dict[str, Any]) -> str:
    return (
        "BUGate auditable role transition\n"
        f"namespace={namespace}\n"
        f"event={payload['event']}\n"
        f"transition_sha256={payload['transition_sha256']}"
    )


def _role_memory_tags(namespace: str, payload: dict[str, Any]) -> list[str]:
    agent = _transition_agent(payload)
    to_role = str(payload.get("to_role") or "")
    # UC, phase, hashes, sessions, and artifact paths are intentionally kept in
    # metadata: they are high-cardinality values and must not explode the tag
    # vocabulary.
    return build_tags(
        agent,
        "handoff",
        "confirmed",
        scope="role-transition",
        to=to_role or None,
        broadcast=not bool(to_role),
    )


def _role_memory_metadata(
    namespace: str,
    payload: dict[str, Any],
    *,
    verified_at: str,
) -> dict[str, Any]:
    return {
        "schema": MEMORY_TRANSITION_SCHEMA,
        "namespace": namespace,
        "event": payload["event"],
        "uc": payload["uc"],
        "phase": payload["phase"],
        "from_role": payload["from_role"],
        "to_role": payload["to_role"],
        "transition_sha256": payload["transition_sha256"],
        "role_transition": copy.deepcopy(payload),
        "receipt_sha256": "",
        "verified_at": verified_at,
    }


def _stored_transition(record: dict[str, Any]) -> dict[str, Any]:
    transition = memory_metadata(record).get("role_transition")
    if not isinstance(transition, dict):
        raise MemoryBusError("Memory record is missing role_transition metadata")
    return transition


def _validate_role_memory(
    record: dict[str, Any],
    expected: dict[str, Any],
    *,
    exact_id: str,
    receipt_sha256: str | None = None,
) -> dict[str, Any]:
    _validate_transition_payload(expected)
    namespace = project_tag()
    expected_content = _role_memory_content(namespace, expected)
    expected_content_id = _sha256(expected_content.encode("utf-8"))
    if exact_id != expected_content_id:
        raise MemoryBusError(
            "role transition exact ID is not its content address: "
            f"expected {expected_content_id}, got {exact_id}"
        )
    actual_id = memory_id(record)
    if actual_id != exact_id:
        raise MemoryBusError(
            f"role transition Memory ID mismatch: expected {exact_id}, got {actual_id}"
        )
    metadata = memory_metadata(record)
    if metadata.get("schema") != MEMORY_TRANSITION_SCHEMA:
        raise MemoryBusError("role transition Memory schema mismatch")
    checks = {
        "namespace": namespace,
        "event": expected["event"],
        "uc": expected["uc"],
        "phase": expected["phase"],
        "from_role": expected["from_role"],
        "to_role": expected["to_role"],
        "transition_sha256": expected["transition_sha256"],
    }
    for key, wanted in checks.items():
        if metadata.get(key) != wanted:
            raise MemoryBusError(
                f"role transition metadata mismatch for {key}: "
                f"expected {wanted!r}, got {metadata.get(key)!r}"
            )
    stored = _stored_transition(record)
    _validate_transition_payload(stored)
    if _canonical_json(stored) != _canonical_json(expected):
        raise MemoryBusError("stored role transition payload does not exactly match expected")
    required_tags = set(_role_memory_tags(namespace, expected))
    missing_tags = sorted(required_tags - set(memory_tags(record)))
    if missing_tags:
        raise MemoryBusError(
            "role transition Memory tags mismatch; missing: " + ", ".join(missing_tags)
        )
    if memory_content(record) != expected_content:
        raise MemoryBusError("role transition Memory content mismatch")
    verified_at = str(metadata.get("verified_at") or "")
    if not verified_at:
        raise MemoryBusError("role transition Memory metadata is missing verified_at")
    if receipt_sha256 is not None and metadata.get("receipt_sha256") != receipt_sha256:
        raise MemoryBusError(
            "role transition receipt hash mismatch: "
            f"expected {receipt_sha256}, got {metadata.get('receipt_sha256')!r}"
        )
    return {
        "namespace": namespace,
        "memory_id": exact_id,
        "verified_at": verified_at,
    }


def _validate_acceptance_handoff(payload: dict[str, Any]) -> dict[str, Any] | None:
    contract = ACCEPTANCE_HANDOFFS.get(str(payload.get("event") or ""))
    if contract is None:
        return None
    expected_event, expected_source_phase, expected_target_phase = contract
    if payload.get("phase") != expected_target_phase:
        raise MemoryBusError(
            f"{payload['event']} phase must be {expected_target_phase}"
        )
    handoff_id = str(payload.get("handoff_memory_id") or "").strip()
    receipt_hash = str(payload.get("handoff_receipt_sha256") or "").strip()
    if not handoff_id:
        raise MemoryBusError("acceptance transition is missing handoff_memory_id")
    if not receipt_hash:
        raise MemoryBusError("acceptance transition is missing handoff_receipt_sha256")
    record = get_memory_exact(handoff_id, agent=_transition_agent(payload))
    transition = _stored_transition(record)
    if transition.get("event") != expected_event:
        raise MemoryBusError(
            f"acceptance handoff event mismatch: expected {expected_event}, "
            f"got {transition.get('event')!r}"
        )
    comparisons = {
        "namespace": project_tag(),
        "uc": payload["uc"],
        "phase": expected_source_phase,
        "from_role": payload["from_role"],
        "to_role": payload["to_role"],
    }
    metadata = memory_metadata(record)
    for key, wanted in comparisons.items():
        actual = metadata.get(key)
        if actual != wanted:
            raise MemoryBusError(
                f"acceptance handoff mismatch for {key}: expected {wanted!r}, got {actual!r}"
            )
    # This validates namespace, structural tags, the full stored transition,
    # and the transition hash before checking the local-receipt anchor.
    return _validate_role_memory(
        record,
        transition,
        exact_id=handoff_id,
        receipt_sha256=receipt_hash,
    )


def prepare_role_transition(payload: dict[str, Any], strict: bool) -> dict[str, Any]:
    """POST a stable transition and prove it with an exact GET.

    Acceptance events first exact-GET and verify the referenced handoff.  The
    ``strict`` flag is part of the public adapter contract; validation errors
    always raise so the role-governance caller can either fail closed
    (required) or mark the transition explicitly unanchored (best effort).
    """

    del strict  # failure policy is owned by the caller; this adapter never lies.
    _validate_transition_payload(payload)
    _validate_acceptance_handoff(payload)
    namespace = project_tag()
    verified_at = _utc_now()
    metadata = _role_memory_metadata(namespace, payload, verified_at=verified_at)
    content = _role_memory_content(namespace, payload)
    content_id = _sha256(content.encode("utf-8"))
    tags = _role_memory_tags(namespace, payload)
    agent = _transition_agent(payload)
    response = _require_success(
        request_json(
            "POST",
            "/api/memories",
            {
                "content": content,
                "tags": tags,
                "memory_type": "communication",
                "conversation_id": (
                    f"bugate-role-transition-{namespace}-{payload['transition_sha256']}"
                ),
                "metadata": metadata,
            },
            agent=agent,
        ),
        "role transition POST",
    )
    _require_post_content_hash(response, content_id, "role transition POST")
    exact_id = memory_id(response)
    if exact_id == "<unknown>" or not exact_id:
        raise MemoryBusError("role transition POST response is missing content hash")
    record = get_memory_exact(exact_id, agent=agent)
    result = _validate_role_memory(record, payload, exact_id=exact_id)
    _PREPARED_ROLE_TRANSITIONS[(base_url(), exact_id)] = copy.deepcopy(record)
    return result


def finalize_role_transition(
    memory_id: str,
    receipt_sha256: str,
    expected: dict[str, Any],
    strict: bool,
) -> dict[str, Any]:
    """Bind the local receipt hash with PUT, then prove it by exact GET."""

    del strict
    _validate_transition_payload(expected)
    exact_id = str(memory_id or "").strip()
    receipt_hash = str(receipt_sha256 or "").strip()
    if len(receipt_hash) != 64 or any(ch not in "0123456789abcdef" for ch in receipt_hash):
        raise MemoryBusError("receipt_sha256 must be a lowercase 64-character SHA-256")
    agent = _transition_agent(expected)
    cache_key = (base_url(), exact_id)
    record = _PREPARED_ROLE_TRANSITIONS.get(cache_key)
    if record is None:
        # Public callers should prepare first.  The fallback remains fail-safe
        # for recovery tooling that has restarted between the two halves.
        record = get_memory_exact(exact_id, agent=agent)
    stable = _validate_role_memory(record, expected, exact_id=exact_id)
    existing = str(memory_metadata(record).get("receipt_sha256") or "")
    if existing and existing != receipt_hash:
        raise MemoryBusError(
            "role transition already has a different receipt hash; refusing overwrite"
        )
    if existing == receipt_hash:
        _PREPARED_ROLE_TRANSITIONS.pop(cache_key, None)
        return {
            **stable,
            "receipt_sha256": receipt_hash,
            "status": "verified",
        }
    updated = update_memory_metadata_exact(
        exact_id,
        {"receipt_sha256": receipt_hash, "receipt_bound_at": _utc_now()},
        agent=agent,
    )
    result = _validate_role_memory(
        updated,
        expected,
        exact_id=exact_id,
        receipt_sha256=receipt_hash,
    )
    _PREPARED_ROLE_TRANSITIONS.pop(cache_key, None)
    return {
        **result,
        "receipt_sha256": receipt_hash,
        "status": "verified",
    }


def verify_role_transition(receipt: dict[str, Any], strict: bool) -> dict[str, Any]:
    """Verify the local receipt and its bidirectional exact Memory anchor."""

    del strict
    if not isinstance(receipt, dict):
        raise MemoryBusError("role receipt must be an object")
    supplied_receipt_hash = str(receipt.get("receipt_sha256") or "")
    receipt_value = copy.deepcopy(receipt)
    receipt_value.pop("receipt_sha256", None)
    computed_receipt_hash = _sha256(_canonical_json(receipt_value))
    if supplied_receipt_hash != computed_receipt_hash:
        raise MemoryBusError(
            "local role receipt hash mismatch: "
            f"expected {computed_receipt_hash}, got {supplied_receipt_hash or '<missing>'}"
        )
    anchor = receipt.get("memory")
    if not isinstance(anchor, dict):
        raise MemoryBusError("role receipt is missing Memory anchor")
    exact_id = str(anchor.get("memory_id") or "").strip()
    if not exact_id:
        raise MemoryBusError("role receipt Memory anchor is missing memory_id")
    if anchor.get("namespace") != project_tag():
        raise MemoryBusError(
            "role receipt Memory namespace mismatch: "
            f"expected {project_tag()!r}, got {anchor.get('namespace')!r}"
        )
    record = get_memory_exact(exact_id, agent=_transition_agent(receipt))
    transition = _stored_transition(record)
    _validate_transition_payload(transition)
    for key, wanted in transition.items():
        if key == "schema":
            continue
        if receipt.get(key) != wanted:
            raise MemoryBusError(
                f"local receipt does not match Memory transition field {key}"
            )
    result = _validate_role_memory(
        record,
        transition,
        exact_id=exact_id,
        receipt_sha256=supplied_receipt_hash,
    )
    if anchor.get("verified_at") != result["verified_at"]:
        raise MemoryBusError("role receipt verified_at does not match exact Memory record")
    _validate_acceptance_handoff(transition)
    return {
        **result,
        "receipt_sha256": supplied_receipt_hash,
        "status": "verified",
    }


# --------------------------------------------------------------------------- #
# Formatting                                                                    #
# --------------------------------------------------------------------------- #

def format_memories(memories: list[dict[str, Any]], limit: int) -> str:
    lines = []
    for memory in memories[:limit]:
        content = memory_content(memory).strip()
        first_line = content.splitlines()[0] if content else "<empty>"
        tag_preview = ", ".join(memory_tags(memory))
        lines.append(f"- `{memory_id(memory)}` {memory_created(memory)} [{tag_preview}]\n  {first_line}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands                                                                      #
# --------------------------------------------------------------------------- #

def cmd_status(args: argparse.Namespace) -> int:
    try:
        data = request_json("GET", "/api/health", timeout=args.timeout)
    except MemoryBusError as exc:
        if args.json:
            print(json.dumps({"ok": False, "url": base_url(), "namespace": project_tag(), "error": str(exc)}))
        else:
            print(f"Memory service unavailable at {base_url()}: {exc}", file=sys.stderr)
            print(START_HINT, file=sys.stderr)
        return 0 if args.no_fail else 1
    if args.json:
        print(json.dumps({"ok": True, "url": base_url(), "namespace": project_tag(), "response": data}, ensure_ascii=False))
    else:
        print(f"Memory service OK at {base_url()} (namespace: {project_tag()})")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    if args.agent not in VALID_AGENTS:
        print(f"invalid --agent: {args.agent} (expected one of {', '.join(VALID_AGENTS)})", file=sys.stderr)
        return 2
    if args.type not in VALID_TYPES:
        print(f"invalid --type: {args.type} (expected one of {', '.join(VALID_TYPES)})", file=sys.stderr)
        return 2
    if args.status not in VALID_STATUS:
        print(f"invalid --status: {args.status} (expected one of {', '.join(VALID_STATUS)})", file=sys.stderr)
        return 2
    if not service_available():
        warn_unavailable()
        return 0
    tags = build_tags(args.agent, args.type, args.status, args.scope, args.task, args.to, args.broadcast, args.tag)
    metadata: dict[str, Any] = {
        "created_by": args.agent,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.artifact:
        metadata["artifact_paths"] = args.artifact
    for item in args.metadata or []:
        if "=" not in item:
            print(f"invalid --metadata item, expected key=value: {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        if key:
            metadata[key] = value
    try:
        resp = post_memory(args.msg, tags, args.agent, metadata)
    except MemoryBusError as exc:
        print(f"Memory write failed: {exc}", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        print(f"Recorded {args.type}/{args.status} as `{memory_id(resp)}` [{project_tag()}]")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if not service_available():
        warn_unavailable()
        return 0
    tags = [project_tag()] + (args.tag or [])
    try:
        memories = search_memories(args.query, tags, args.limit)
    except MemoryBusError as exc:
        print(f"Memory search failed: {exc}", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(memories, ensure_ascii=False, indent=2))
    elif memories:
        print(format_memories(memories, args.limit))
    else:
        print("No matching memory entries.")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    """Read one exact content hash; optionally make failure gate-significant."""

    try:
        record = get_memory_exact(args.id, timeout=args.timeout)
    except MemoryBusError as exc:
        print(f"Exact Memory read failed: {exc}", file=sys.stderr)
        if not args.strict:
            print(START_HINT, file=sys.stderr)
        return 1 if args.strict else 0
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(format_memories([record], 1))
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    if not service_available():
        warn_unavailable()
        return 0
    try:
        memories = recent_for_agent(args.agent, args.limit)
    except MemoryBusError as exc:
        print(f"Memory read failed: {exc}", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(memories, ensure_ascii=False, indent=2))
    elif memories:
        if not args.no_header:
            print(f"# {args.agent} recent — {len(memories)} entries, newest first\n")
        print(format_memories(memories, args.limit))
    else:
        print(f"No pending memory entries for {args.agent}.")
    return 0


def _cli_transition(
    args: argparse.Namespace,
    *,
    event: str,
    handoff_id: str = "",
    handoff_receipt_sha256: str = "",
) -> dict[str, Any]:
    missing = [name for name in ("uc", "phase") if not str(getattr(args, name, "") or "").strip()]
    if missing:
        raise MemoryBusError(
            "strict role transition requires " + ", ".join(f"--{name}" for name in missing)
        )
    from_role = str(getattr(args, "from_agent", "") or "").strip()
    to_role = str(getattr(args, "to", "") or "").strip()
    payload: dict[str, Any] = {
        "schema": ROLE_TRANSITION_SCHEMA,
        "event": event,
        "uc": str(args.uc).strip(),
        "artifact_dir": str(getattr(args, "artifact_dir", "") or ""),
        "phase": str(args.phase).strip(),
        "from_role": from_role,
        "to_role": to_role,
        "actor": {
            "role": to_role if event.endswith("_acceptance") else from_role,
            "runtime": str(getattr(args, "runtime", "unknown") or "unknown"),
            "session_id": str(getattr(args, "session_id", "") or ""),
        },
        "message": str(getattr(args, "msg", "") or ""),
    }
    scope = str(getattr(args, "scope", "") or "")
    task = str(getattr(args, "task", "") or "")
    artifacts = list(getattr(args, "artifact", None) or [])
    if scope:
        payload["scope"] = scope
    if task:
        payload["task"] = task
    if artifacts:
        payload["artifact_paths"] = artifacts
    if handoff_id:
        payload["handoff_memory_id"] = handoff_id
        payload["handoff_receipt_sha256"] = handoff_receipt_sha256
    supplied = str(getattr(args, "transition_sha256", "") or "").strip()
    payload["transition_sha256"] = supplied or _transition_hash(payload)
    return payload


def _print_transition_result(args: argparse.Namespace, result: dict[str, Any], label: str) -> None:
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{label} verified as `{result['memory_id']}` [{result['namespace']}]")


def cmd_handoff(args: argparse.Namespace) -> int:
    if getattr(args, "strict", False):
        try:
            event = str(args.event or f"{args.from_agent}_handoff")
            payload = _cli_transition(args, event=event)
            result = prepare_role_transition(payload=payload, strict=True)
            if args.receipt_sha256:
                result = finalize_role_transition(
                    memory_id=result["memory_id"],
                    receipt_sha256=args.receipt_sha256,
                    expected=payload,
                    strict=True,
                )
        except MemoryBusError as exc:
            print(f"Strict handoff failed: {exc}", file=sys.stderr)
            return 1
        _print_transition_result(args, result, f"Strict handoff {args.from_agent} -> {args.to}")
        return 0
    if not service_available():
        warn_unavailable()
        return 0
    tags = build_tags(args.from_agent, "handoff", args.status, args.scope, args.task, to=args.to, extra=args.tag)
    metadata: dict[str, Any] = {
        "created_by": args.from_agent,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.artifact:
        metadata["artifact_paths"] = args.artifact
    try:
        resp = post_memory(args.msg, tags, args.from_agent, metadata)
    except MemoryBusError as exc:
        print(f"Handoff write failed: {exc}", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        print(f"Handoff {args.from_agent} -> {args.to} recorded as `{memory_id(resp)}`")
    return 0


def cmd_accept_handoff(args: argparse.Namespace) -> int:
    try:
        event = str(args.event or f"{args.to}_acceptance")
        payload = _cli_transition(
            args,
            event=event,
            handoff_id=args.handoff_id,
            handoff_receipt_sha256=args.handoff_receipt_sha256,
        )
        result = prepare_role_transition(payload=payload, strict=args.strict)
        if args.receipt_sha256:
            result = finalize_role_transition(
                memory_id=result["memory_id"],
                receipt_sha256=args.receipt_sha256,
                expected=payload,
                strict=args.strict,
            )
    except MemoryBusError as exc:
        print(f"Accept-handoff Memory operation failed: {exc}", file=sys.stderr)
        return 1 if args.strict else 0
    _print_transition_result(args, result, f"Acceptance {args.from_agent} -> {args.to}")
    return 0


def cmd_verify_handoff(args: argparse.Namespace) -> int:
    try:
        if args.receipt_file:
            receipt_path = Path(args.receipt_file).expanduser()
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise MemoryBusError("--receipt-file must contain one JSON object")
            anchor = receipt.get("memory")
            anchor_id = str(anchor.get("memory_id") or "") if isinstance(anchor, dict) else ""
            if anchor_id != args.id:
                raise MemoryBusError(
                    f"receipt Memory ID mismatch: expected {args.id}, got {anchor_id or '<missing>'}"
                )
            result = verify_role_transition(receipt=receipt, strict=args.strict)
        else:
            if args.strict and not args.receipt_sha256:
                raise MemoryBusError(
                    "strict verify-handoff requires --receipt-sha256 or --receipt-file"
                )
            record = get_memory_exact(args.id, timeout=args.timeout)
            transition = _stored_transition(record)
            _validate_transition_payload(transition)
            expected_values = {
                "event": args.event,
                "uc": args.uc,
                "phase": args.phase,
                "from_role": args.from_agent,
                "to_role": args.to,
                "transition_sha256": args.transition_sha256,
            }
            metadata = memory_metadata(record)
            for key, wanted in expected_values.items():
                if wanted is not None and metadata.get(key) != wanted:
                    raise MemoryBusError(
                        f"verify-handoff mismatch for {key}: "
                        f"expected {wanted!r}, got {metadata.get(key)!r}"
                    )
            result = _validate_role_memory(
                record,
                transition,
                exact_id=args.id,
                receipt_sha256=args.receipt_sha256 or None,
            )
            _validate_acceptance_handoff(transition)
            result = {
                **result,
                "receipt_sha256": str(metadata.get("receipt_sha256") or ""),
                "status": "verified",
            }
    except (MemoryBusError, OSError, json.JSONDecodeError) as exc:
        print(f"Verify-handoff failed: {exc}", file=sys.stderr)
        return 1 if args.strict else 0
    _print_transition_result(args, result, "Handoff")
    return 0


def cmd_session_start(args: argparse.Namespace) -> int:
    if not service_available():
        # Required component down → self-heal (restart in the background), then
        # give it a moment; still non-blocking so the session never stalls.
        warn_unavailable()
        if self_heal_service():
            print("Memory service was down; triggered self-heal (bin/memory-bus-ensure) — recovering in the background.", file=sys.stderr)
            for _ in range(6):
                time.sleep(0.5)
                if service_available():
                    break
        if not service_available():
            return 0
    try:
        targeted = recent_for_agent(args.agent, args.limit)
        confirmed = search_memories(
            "current progress blockers active decisions handoff",
            [project_tag(), "status:confirmed"],
            args.limit,
        )
    except MemoryBusError:
        return 0
    by_id: dict[str, dict[str, Any]] = {}
    for memory in targeted + confirmed:
        by_id[memory_id(memory)] = memory
    memories = list(by_id.values())[: args.limit]
    if not memories:
        print(f"No memory context for {args.agent} ({project_tag()}).")
        return 0
    print(
        f"Memory context for this session ({project_tag()}, agent={args.agent}):\n"
        f"{format_memories(memories, args.limit)}"
    )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Best-effort hourly heartbeat so dashboards reflect activity.

    Safe to wire to a per-turn Stop hook: self-throttles to at most one heartbeat
    per hour per agent. Never blocks the turn. Disable with MEMORY_BUS_STOP_WRITE=0.
    """
    if os.environ.get("MEMORY_BUS_STOP_WRITE") == "0":
        return 0
    if not service_available():
        print(f"Memory service unavailable; skipped stop bookkeeping for {args.agent}.", file=sys.stderr)
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00 UTC")
    # Self-throttle: skip if this agent already beat this hour (Stop fires per turn).
    try:
        for memory in list_project_memories(60):
            tags = memory_tags(memory)
            if "scope:heartbeat" in tags and f"agent:{args.agent}" in tags and stamp in memory_content(memory):
                return 0
    except MemoryBusError:
        pass
    content = f"{args.agent} session heartbeat {stamp} in {root().name}."
    tags = build_tags(args.agent, "progress", "draft", scope="heartbeat", broadcast=True)
    try:
        post_memory(content, tags, args.agent)
    except MemoryBusError as exc:
        print(f"heartbeat write failed for {args.agent}: {exc}", file=sys.stderr)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    """Back up every memory under the active namespace to a local JSON file.

    DE-SUT: default output is <memory_home>/backups/ — the system-level bus
    home outside any working tree, so no SUT-specific export location is
    hardcoded and nothing lands in a repo.
    """
    if not service_available():
        warn_unavailable()
        return 0
    try:
        memories = list_project_memories(args.limit)
    except MemoryBusError as exc:
        print(f"Memory archive failed: {exc}", file=sys.stderr)
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.out:
        out_path = Path(args.out).expanduser()
        if not out_path.is_absolute():
            out_path = (Path.cwd() / out_path).resolve()
    else:
        out_path = memory_home() / "backups" / f"memory_backup_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "namespace": project_tag(),
        "count": len(memories),
        "memories": memories,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Archived {len(memories)} memory entries [{project_tag()}]")
    print(out_path)
    return 0


# --------------------------------------------------------------------------- #
# Lint (SUT-neutral, structurally-grounded governance)                          #
# --------------------------------------------------------------------------- #

# Allowed tag prefixes are derived generically from this script's own tag
# vocabulary (see build_tags): namespace key + the structural prefixes the
# driver itself emits. No SUT-specific tag families are hardcoded.
NAMESPACE_PREFIX = "project"
GOVERNED_TAG_PREFIXES = (NAMESPACE_PREFIX, "agent", "type", "status", "scope", "task", "msg")
EVIDENCE_KEYS = ("artifact_paths", "refs", "evidence", "source_url", "source", "incident_path")
HIGH_CARDINALITY_THRESHOLD = 8


def lint_findings(content: str, tags: list[str], metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Return SUT-neutral governance findings for one memory record.

    Every rule is grounded in the driver's own constants/structure, never in
    SUT-specific tag families.
    """
    findings: list[dict[str, str]] = []

    def add(severity: str, code: str, message: str) -> None:
        findings.append({"severity": severity, "code": code, "message": message})

    namespace = project_tag()
    tag_set = set(tags)

    # Required structural tags: namespace + agent: + type: + status:.
    if namespace not in tag_set:
        add("error", "missing_namespace", f"required namespace tag missing: {namespace}")
    for prefix in ("agent", "type", "status"):
        if first_tag_value(tags, prefix) is None:
            add("error", "missing_tag", f"required tag prefix missing: {prefix}:*")

    # Enum validation against the driver's own constants.
    agent = first_tag_value(tags, "agent")
    mem_type = first_tag_value(tags, "type")
    status = first_tag_value(tags, "status")
    if agent is not None and agent not in VALID_AGENTS:
        add("error", "invalid_agent", f"agent must be one of {sorted(VALID_AGENTS)}: {agent}")
    if mem_type is not None and mem_type not in VALID_TYPES:
        add("error", "invalid_type", f"type must be one of {sorted(VALID_TYPES)}: {mem_type}")
    if status is not None and status not in VALID_STATUS:
        add("error", "invalid_status", f"status must be one of {sorted(VALID_STATUS)}: {status}")

    # Allowed-prefix check derived from the driver's own vocabulary (warn only:
    # the service or callers may attach other structured prefixes).
    for tag in tags:
        prefix = tag.split(":", 1)[0] if ":" in tag else tag
        if tag != namespace and prefix not in GOVERNED_TAG_PREFIXES:
            add("warning", "unknown_tag_prefix", f"tag prefix outside the driver vocabulary: {tag}")

    # Confirmed finding/decision should carry some evidence/artifact reference.
    if status == "confirmed" and mem_type in {"finding", "decision"}:
        if not any(metadata.get(key) for key in EVIDENCE_KEYS):
            add(
                "warning",
                "confirmed_without_evidence",
                f"confirmed {mem_type} should carry evidence/artifact metadata "
                f"(one of {', '.join(EVIDENCE_KEYS)})",
            )

    # Handoff should declare a target.
    if mem_type == "handoff" and not any(
        tag.startswith("msg:to-") or tag == "msg:broadcast" for tag in tags
    ):
        add("warning", "handoff_without_target", "handoff should include msg:to-<agent> or msg:broadcast")

    return findings


def cardinality_warnings(memories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Warn when a single tag key explodes into too many distinct values."""
    values_by_key: dict[str, set[str]] = {}
    for memory in memories:
        for tag in memory_tags(memory):
            if ":" not in tag:
                continue
            key, value = tag.split(":", 1)
            values_by_key.setdefault(key, set()).add(value)
    findings: list[dict[str, str]] = []
    for key, values in sorted(values_by_key.items()):
        if key == "msg":
            # msg:to-<agent>/msg:broadcast legitimately fan out by agent.
            continue
        if len(values) > HIGH_CARDINALITY_THRESHOLD:
            findings.append(
                {
                    "severity": "warning",
                    "code": "high_cardinality_tag",
                    "message": (
                        f"tag key '{key}:' has {len(values)} distinct values "
                        f"(> {HIGH_CARDINALITY_THRESHOLD}); high-cardinality data "
                        f"belongs in metadata, not tags"
                    ),
                }
            )
    return findings


def cmd_lint(args: argparse.Namespace) -> int:
    if not service_available():
        warn_unavailable()
        return 0
    try:
        memories = list_project_memories(args.limit)
    except MemoryBusError as exc:
        print(f"Memory lint failed during list: {exc}", file=sys.stderr)
        return 0

    report: list[dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    for memory in memories:
        findings = lint_findings(memory_content(memory), memory_tags(memory), memory_metadata(memory))
        errors = [f for f in findings if f["severity"] == "error"]
        warnings = [f for f in findings if f["severity"] == "warning"]
        error_count += len(errors)
        warning_count += len(warnings)
        shown = findings if args.include_warnings else errors
        if shown:
            report.append(
                {
                    "id": memory_id(memory),
                    "created": memory_created(memory),
                    "tags": memory_tags(memory),
                    "findings": shown,
                    "preview": (memory_content(memory).strip().splitlines() or ["<empty>"])[0],
                }
            )

    corpus_warnings = cardinality_warnings(memories)
    warning_count += len(corpus_warnings)

    if args.json:
        print(
            json.dumps(
                {
                    "namespace": project_tag(),
                    "memories_checked": len(memories),
                    "errors": error_count,
                    "warnings": warning_count,
                    "items": report,
                    "corpus_warnings": corpus_warnings if args.include_warnings else [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"Memory lint [{project_tag()}]: checked={len(memories)} "
            f"errors={error_count} warnings={warning_count}"
        )
        for item in report[: args.show]:
            print(f"- {item['id']} {item['created']}")
            for finding in item["findings"]:
                print(f"  [{finding['severity']}] {finding['code']}: {finding['message']}")
            print(f"  {item['preview']}")
        if len(report) > args.show:
            print(f"... {len(report) - args.show} more memories with findings")
        if args.include_warnings:
            for finding in corpus_warnings:
                print(f"- corpus [{finding['severity']}] {finding['code']}: {finding['message']}")

    # Exit non-zero only on hard violations (invalid enum / missing tag).
    return 1 if error_count else 0


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def add_namespace_opts(p: argparse.ArgumentParser) -> None:
    """Let any subcommand target a namespace other than the active profile's.

    ``--core`` selects BUGate's own namespace (base config, ignoring the active
    SUT profile); ``--namespace X`` selects an explicit tag. Both win over the
    profile by exporting MEMORY_BUS_PROJECT_TAG in main().
    """
    p.add_argument("--namespace", help="target an explicit memory namespace/tag (overrides the active SUT profile)")
    p.add_argument("--core", action="store_true", help="use BUGate's own core namespace, not the active SUT's")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SUT-neutral memory-bus driver for BUGate")
    parser.add_argument("--url", help="memory service URL (default: MEMORY_BUS_URL or http://localhost:8000)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="health-check the memory service")
    p.add_argument("--json", action="store_true")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--no-fail", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("note", help="write a memory entry")
    p.add_argument("--agent", required=True, help=f"one of {', '.join(VALID_AGENTS)}")
    p.add_argument("--type", required=True, help=f"one of {', '.join(VALID_TYPES)}")
    p.add_argument("--msg", required=True)
    p.add_argument("--status", default="draft", help=f"one of {', '.join(VALID_STATUS)}")
    p.add_argument("--scope")
    p.add_argument("--task")
    p.add_argument("--to", help="address this note to an agent (msg:to-<agent>)")
    p.add_argument("--broadcast", action="store_true")
    p.add_argument("--tag", action="append", help="extra raw tag (repeatable)")
    p.add_argument("--artifact", action="append", help="artifact path for metadata (repeatable)")
    p.add_argument("--metadata", action="append", help="extra metadata key=value (repeatable)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("search", help="query memories (semantic + tag fallbacks)")
    p.add_argument("--query", required=True)
    p.add_argument("--tag", action="append")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="read one memory by exact content hash")
    p.add_argument("--id", required=True, help="exact memory content hash")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--strict", action="store_true", help="return non-zero on any read or identity failure")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("recent", help="newest agent-visible memories (broadcast or addressed)")
    p.add_argument("--agent", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-header", action="store_true")
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("handoff", help="record a handoff to another agent")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--msg", required=True)
    p.add_argument("--status", default="draft")
    p.add_argument("--scope", default="global")
    p.add_argument("--task")
    p.add_argument("--tag", action="append")
    p.add_argument("--artifact", action="append")
    p.add_argument("--strict", action="store_true", help="use POST/exact-GET strict role-transition storage")
    p.add_argument("--event", help="strict transition event (default: <from>_handoff)")
    p.add_argument("--uc", help="strict transition UC identifier (stored in metadata)")
    p.add_argument("--phase", help="strict transition source phase")
    p.add_argument("--artifact-dir", help="strict transition artifact directory")
    p.add_argument("--runtime", default="unknown", help="declared actor runtime")
    p.add_argument("--session-id", help="declared actor session ID")
    p.add_argument("--transition-sha256", help="precomputed transition hash (otherwise computed)")
    p.add_argument("--receipt-sha256", help="bind and exact-verify a local receipt hash")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("accept-handoff", help="exact-verify a handoff and record its acceptance")
    p.add_argument("--handoff-id", required=True, help="exact handoff Memory content hash")
    p.add_argument("--handoff-receipt-sha256", required=True, help="local handoff receipt hash already bound in Memory")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--uc", required=True)
    p.add_argument("--phase", required=True, choices=("implementation", "post_run"))
    p.add_argument("--msg", required=True)
    p.add_argument("--event", help="acceptance event (default: <to>_acceptance)")
    p.add_argument("--artifact-dir")
    p.add_argument("--scope", default="global")
    p.add_argument("--task")
    p.add_argument("--artifact", action="append")
    p.add_argument("--runtime", default="unknown")
    p.add_argument("--session-id")
    p.add_argument("--transition-sha256", help="precomputed acceptance transition hash")
    p.add_argument("--receipt-sha256", help="bind and exact-verify the acceptance receipt hash")
    p.add_argument("--strict", action="store_true", help="return non-zero on any unavailable/write/exact-match failure")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_accept_handoff)

    p = sub.add_parser("verify-handoff", help="exact-verify a role-transition Memory anchor")
    p.add_argument("--id", required=True, help="exact Memory content hash")
    p.add_argument("--receipt-file", help="local role receipt JSON to verify bidirectionally")
    p.add_argument("--receipt-sha256", help="expected bound receipt hash")
    p.add_argument("--event")
    p.add_argument("--from", dest="from_agent")
    p.add_argument("--to")
    p.add_argument("--uc")
    p.add_argument("--phase")
    p.add_argument("--transition-sha256")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--strict", action="store_true", help="return non-zero on any mismatch or service failure")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify_handoff)

    p = sub.add_parser("session-start", help="print recent context for an agent")
    p.add_argument("--agent", required=True)
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=cmd_session_start)

    p = sub.add_parser("stop", help="stop-hook bookkeeping (hourly heartbeat)")
    p.add_argument("--agent", required=True)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("archive", help="back up namespace memories to a local JSON file")
    p.add_argument("--out", help="output path (default: <bus-home>/backups/memory_backup_<ts>.json)")
    p.add_argument("--limit", type=int, default=10000, help="max memories to archive")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("lint", help="validate namespace memories against SUT-neutral governance rules")
    p.add_argument("--include-warnings", action="store_true", help="report warnings as well as hard violations")
    p.add_argument("--limit", type=int, default=10000, help="max memories to check")
    p.add_argument("--show", type=int, default=20, help="max flagged memories to print in text mode")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_lint)

    for subparser in sub.choices.values():
        add_namespace_opts(subparser)
    return parser


def main() -> int:
    load_local_env()
    parser = build_parser()
    args = parser.parse_args()
    if args.url:
        os.environ["MEMORY_BUS_URL"] = args.url
    # Namespace selection (wins over the active profile for this invocation).
    if getattr(args, "core", False):
        os.environ["MEMORY_BUS_PROJECT_TAG"] = core_project_tag()
    elif getattr(args, "namespace", None):
        os.environ["MEMORY_BUS_PROJECT_TAG"] = args.namespace
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
