#!/usr/bin/env python3
"""PreToolUse guard for BUGate lifecycle roles and local role evidence.

The guard understands Claude ``Edit``/``Write`` payloads and Codex
``apply_patch`` payloads.  It deliberately performs local receipt/hash checks
only; strict Memory verification happens at transition boundaries.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bugate_core import find_root, load_config, required_precode_artifacts  # noqa: E402
from role_governance import (  # noqa: E402
    POSTRUN_NAMES,
    PRECODE_PREFIX_RE,
    RoleGovernanceError,
    governance_mode_hint,
    governance_policy,
    preflight,
)


PATCH_PATH_RE = re.compile(
    r"^\*\*\*\s+(?:(?:Update|Add|Delete)\s+File|Move\s+to):\s+(.+?)\s*$",
    re.MULTILINE,
)


def _collect_payload_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path", "filePath", "input", "patch"} and isinstance(item, str):
                found.append(item)
            found.extend(_collect_payload_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_payload_strings(item))
    return found


def collect_paths(stdin_text: str) -> set[str]:
    if not stdin_text.strip():
        return set()
    try:
        payload: Any = json.loads(stdin_text)
    except json.JSONDecodeError:
        payload = stdin_text
    values = _collect_payload_strings(payload) if not isinstance(payload, str) else [payload]
    paths: set[str] = set()
    for value in values:
        matches = list(PATCH_PATH_RE.finditer(value))
        if matches:
            paths.update(match.group(1).strip() for match in matches)
        elif "\n" not in value and len(value) < 1000 and value.strip():
            paths.add(value.strip())
    return paths


def _rel(root: Path, target: str) -> str:
    path = Path(target)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return Path(target).as_posix().lstrip("./")


def _path(root: Path, target: str) -> Path:
    path = Path(target)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _guarded_match(config: dict[str, Any], relpath: str, absolute: str) -> re.Match[str] | None:
    raw = config.get("guarded_path_regex") or []
    patterns = [raw] if isinstance(raw, str) else raw
    for value in patterns:
        regex = re.compile(str(value))
        match = regex.search(relpath) or regex.search(absolute)
        if match:
            return match
    return None


def _template_artifact(
    root: Path,
    config: dict[str, Any],
    relpath: str,
    *,
    captured_uc: str | None = None,
) -> Path | None:
    template = str(config.get("artifact_dir_template") or "")
    if "{uc}" not in template:
        return None
    before, after = template.split("{uc}", 1)
    before = before.strip("/")
    after = after.strip("/")
    pattern = "^"
    if before:
        pattern += re.escape(before) + "/"
    pattern += r"(?P<uc>[^/]+)"
    if after:
        pattern += "/" + re.escape(after)
    pattern += r"(?:/|$)"
    match = re.match(pattern, relpath)
    uc = captured_uc or (match.group("uc") if match else None)
    if not uc:
        return None
    if captured_uc and match:
        artifact_uc = match.group("uc")
        if artifact_uc != captured_uc:
            normalize = lambda value: re.sub(r"[-_]", "", value).lower()
            if config.get("uc_dir_resolve") != "normalized-glob" or normalize(artifact_uc) != normalize(captured_uc):
                return None
    candidate = Path(template.replace("{uc}", uc))
    candidate = candidate if candidate.is_absolute() else root / candidate
    if config.get("uc_dir_resolve") == "normalized-glob" and not candidate.exists():
        prefix = Path(before) if before else Path(".")
        parent = prefix if prefix.is_absolute() else root / prefix
        normalized = re.sub(r"[-_]", "", uc).lower()
        matches = sorted(
            item
            for item in parent.iterdir()
            if item.is_dir() and re.sub(r"[-_]", "", item.name).lower() == normalized
        ) if parent.is_dir() else []
        if len(matches) == 1:
            candidate = matches[0] / after if after else matches[0]
    return candidate.resolve()


def _configured_artifact(root: Path, config: dict[str, Any]) -> Path | None:
    raw = config.get("artifact_dir") or config.get("artifact_root")
    if not raw:
        return None
    path = Path(str(raw))
    return (path if path.is_absolute() else root / path).resolve()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def classify_path(
    root: Path,
    config: dict[str, Any],
    policy: dict[str, Any],
    target: str,
) -> tuple[str, Path | None, str]:
    """Return ``(kind, artifact_dir, workspace_relative_path)``."""

    relpath = _rel(root, target)
    absolute_path = _path(root, target)
    evidence_parts = tuple(Path(policy["evidence_dir"]).parts)
    parts = tuple(Path(relpath).parts)
    if evidence_parts and any(
        parts[index : index + len(evidence_parts)] == evidence_parts
        for index in range(max(0, len(parts) - len(evidence_parts) + 1))
    ):
        return "evidence", None, relpath

    guarded = _guarded_match(config, relpath, absolute_path.as_posix())
    if guarded:
        uc = (guarded.groupdict() or {}).get("uc")
        artifact = _template_artifact(root, config, relpath, captured_uc=uc)
        if artifact is None:
            artifact = _configured_artifact(root, config)
        return "implementation", artifact, relpath

    artifact = _template_artifact(root, config, relpath) or _configured_artifact(root, config)
    if artifact is None or not _inside(absolute_path, artifact):
        return "other", None, relpath
    relative = absolute_path.relative_to(artifact)
    if not relative.parts:
        return "other", artifact, relpath
    top = relative.parts[0]
    basename = absolute_path.name
    if basename in POSTRUN_NAMES or top in {"04_execution", "05_knowledge", "00_post_run"}:
        return "postrun", artifact, relpath
    if (
        basename in set(required_precode_artifacts(config))
        or PRECODE_PREFIX_RE.match(basename)
        or top in {"00_multiview", "00_adversarial"}
    ):
        return "precode", artifact, relpath
    return "other", artifact, relpath


def check_paths(
    paths: set[str],
    *,
    root: Path,
    config: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    policy = policy or governance_policy(config)
    if policy["mode"] == "off":
        return [], []
    failures: list[str] = []
    warnings: list[str] = []
    for target in sorted(paths):
        try:
            kind, artifact, relpath = classify_path(root, config, policy, target)
            if kind == "other":
                continue
            if kind == "evidence":
                # Append-only receipts are never an agent-tool editing surface.
                failures.append(
                    f"{relpath}: direct edits to {policy['evidence_dir']}/ are forbidden; use bin/bugate-role"
                )
                continue
            if artifact is None:
                message = f"{relpath}: cannot bind guarded path to one UC artifact directory"
                (failures if policy["mode"] == "required" else warnings).append(message)
                continue
            phase = {
                "precode": "pre_code",
                "implementation": "implementation",
                "postrun": "post_run",
            }[kind]
            result = preflight(
                artifact,
                phase,
                require_acceptance=phase in {"implementation", "post_run"},
            )
            messages = [f"{relpath}: {item}" for item in result.errors]
            if result.allowed:
                warnings.extend(f"{relpath}: {item}" for item in result.warnings)
            else:
                failures.extend(messages or [f"{relpath}: role preflight failed"])
        except (RoleGovernanceError, re.error, OSError) as exc:
            message = f"{target}: {exc}"
            (failures if policy["mode"] == "required" else warnings).append(message)
    return failures, warnings


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    try:
        root = find_root(Path.cwd().resolve())
        config = load_config(root, os.environ.get("BUGATE_PROFILE"))
        try:
            policy = governance_policy(config)
        except RoleGovernanceError as exc:
            hint = governance_mode_hint(config)
            if hint == "off":
                return 0
            if hint != "advisory":
                raise
            # Advisory config mistakes warn and allow ordinary surfaces.  The
            # append-only evidence directory remains protected even in advisory
            # mode, using a safe default if its configured path is malformed.
            policy = governance_policy({"role_governance": {"mode": "advisory"}})
            raw = config.get("role_governance")
            evidence = raw.get("evidence_dir") if isinstance(raw, dict) else None
            if isinstance(evidence, str):
                candidate = Path(evidence)
                if evidence.strip() and not candidate.is_absolute() and ".." not in candidate.parts:
                    policy["evidence_dir"] = candidate.as_posix()
            print(f"BUGate role-governance WARNING: malformed advisory config: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"BUGate role-evidence guard fail-closed: invalid configuration: {exc}", file=sys.stderr)
        return 2
    if policy["mode"] == "off":
        return 0
    paths = {item.strip() for item in argv if item.strip()}
    if not sys.stdin.isatty():
        try:
            paths |= collect_paths(sys.stdin.read())
        except OSError:
            pass
    if not paths:
        return 0
    failures, warnings = check_paths(paths, root=root, config=config, policy=policy)
    for warning in warnings:
        print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
    if not failures:
        return 0
    print("BUGate role-evidence guard BLOCKED:", file=sys.stderr)
    for failure in failures:
        print(f"  - {failure}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
