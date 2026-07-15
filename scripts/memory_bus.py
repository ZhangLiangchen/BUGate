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
- Degrade gracefully: if the service is unreachable, print a clear hint to run
  ``bin/memory-bus-start`` and exit non-fatally (0) so hooks never block work.

Subcommands: session-start, stop, status, note, search, recent, handoff,
archive, lint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
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


class MemoryBusError(RuntimeError):
    pass


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
    # The simple BUGate YAML parser flattens nested keys, so a `memory:` block
    # with a `namespace:` child surfaces as a top-level `namespace` key.
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
        base = bugate_core.parse_simple_yaml((root() / "bugate.config.yaml").read_text(encoding="utf-8"))
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


def cmd_handoff(args: argparse.Namespace) -> int:
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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_handoff)

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
