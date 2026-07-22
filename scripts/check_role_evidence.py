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
import stat
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
    latest_completion_snapshot_paths,
    preflight,
    resolve_uc,
    role_phase_owned_paths,
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
    lexical = _lexical_workspace_rel(root, target)
    if lexical is not None:
        return lexical
    canonical = _canonical_workspace_rel(root, target)
    if canonical is not None:
        return canonical
    path = Path(target)
    return path.as_posix() if path.is_absolute() else path.as_posix().lstrip("./")


def _lexical_workspace_rel(root: Path, target: str) -> str | None:
    """Normalize caller spelling without resolving symlink components."""

    path = Path(target)
    if not path.is_absolute():
        normalized = Path(os.path.normpath(path.as_posix()))
        if normalized == Path("..") or (
            normalized.parts and normalized.parts[0] == ".."
        ):
            return None
        return normalized.as_posix()
    normalized = Path(os.path.normpath(path.as_posix()))
    root_path = root.resolve()
    try:
        return normalized.relative_to(root_path).as_posix()
    except ValueError:
        pass

    # Preserve the caller's leaf/ancestor spelling while binding a raw
    # absolute workspace alias to the canonical root by directory identity.
    # This matters on Darwin where tempfile commonly exposes ``/var`` while
    # ``Path.resolve()`` returns ``/private/var``.  Resolving the complete
    # target first would also erase a guarded leaf symlink and turn a lexical
    # governance path into an apparently ordinary target.
    suffix: list[str] = []
    cursor = normalized
    while True:
        if _same_existing_path(cursor, root_path):
            return Path(*reversed(suffix)).as_posix() if suffix else "."
        if cursor.parent == cursor:
            break
        suffix.append(cursor.name)
        cursor = cursor.parent
    return None


def _path(root: Path, target: str) -> Path:
    path = Path(target)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _relative_by_identity(path: Path, parent: Path) -> Path | None:
    """Relativize using directory identity when spelling differs on disk.

    ``Path.relative_to`` is intentionally lexical.  That is insufficient for
    hook ownership on case-insensitive filesystems and for paths that traverse
    a symlink alias.  Walk existing ancestors and bind the path to ``parent``
    by ``samefile`` before returning the still-auditable caller spelling.
    """

    path = path.resolve()
    parent = parent.resolve()
    try:
        return path.relative_to(parent)
    except ValueError:
        pass
    suffix: list[str] = []
    cursor = path
    while True:
        if _same_existing_path(cursor, parent):
            return Path(*reversed(suffix)) if suffix else Path(".")
        if cursor.parent == cursor:
            return None
        suffix.append(cursor.name)
        cursor = cursor.parent


def _canonical_disk_rel(root: Path, path: Path) -> str | None:
    """Return a workspace path with existing directory-entry spelling.

    macOS APFS commonly resolves ``foo`` and ``FOO`` to the same inode while
    preserving the caller spelling in ``Path.resolve()``.  Recover the actual
    directory-entry names so guarded regex classification cannot be bypassed
    by spelling an existing path with different case.
    """

    relative = _relative_by_identity(path, root)
    if relative is None:
        return None
    current = root.resolve()
    canonical: list[str] = []
    for part in relative.parts:
        requested = current / part
        chosen = part
        if current.is_dir() and requested.exists():
            try:
                entries = list(current.iterdir())
            except OSError:
                entries = []
            exact = next((entry for entry in entries if entry.name == part), None)
            if exact is not None:
                chosen = exact.name
            else:
                identities = [
                    entry for entry in entries if _same_existing_path(entry, requested)
                ]
                if len(identities) == 1:
                    chosen = identities[0].name
        canonical.append(chosen)
        current = current / chosen
    return Path(*canonical).as_posix() if canonical else "."


def _alternate_case(name: str) -> str | None:
    for index, char in enumerate(name):
        swapped = char.swapcase()
        if swapped != char:
            return name[:index] + swapped + name[index + 1 :]
    return None


def _filesystem_case_insensitive(path: Path) -> bool:
    """Read-only probe of directory lookup on the workspace filesystem.

    Inspect entries *inside* the workspace/mount.  An explicit alternate-case
    symlink or directory entry is not evidence that the filesystem folds case.
    """

    root = path.resolve()
    try:
        device = root.stat().st_dev
    except OSError:
        return False
    for directory in (root, *root.parents):
        try:
            if directory.stat().st_dev != device or not directory.is_dir():
                continue
            entries = list(directory.iterdir())
        except OSError:
            continue
        names = {entry.name for entry in entries}
        for entry in entries:
            alternate = _alternate_case(entry.name)
            if alternate is None or alternate in names:
                continue
            alias = directory / alternate
            try:
                alias_stat = alias.lstat()
            except OSError:
                return False
            if stat.S_ISLNK(alias_stat.st_mode):
                continue
            if _same_existing_path(entry, alias):
                return True
    return False


def _canonical_workspace_rel(root: Path, target: str) -> str | None:
    try:
        return _canonical_disk_rel(root, _path(root, target))
    except OSError:
        return None


def _workspace_paths_equal(root: Path, left: Path, right: Path) -> bool:
    if _same_existing_path(left, right):
        return True
    left_rel = _relative_by_identity(left, root)
    right_rel = _relative_by_identity(right, root)
    if left_rel is None or right_rel is None:
        return False
    left_text = left_rel.as_posix()
    right_text = right_rel.as_posix()
    if left_text == right_text:
        return True
    return _filesystem_case_insensitive(root) and left_text.casefold() == right_text.casefold()


def _guarded_match_one(
    root: Path,
    config: dict[str, Any],
    value: str,
) -> re.Match[str] | None:
    raw = config.get("guarded_path_regex") or []
    patterns = [raw] if isinstance(raw, str) else raw
    flags = re.IGNORECASE if _filesystem_case_insensitive(root) else 0
    for pattern in patterns:
        regex = re.compile(str(pattern), flags)
        match = regex.search(value)
        if match:
            return match
    return None


def _guarded_match(
    root: Path,
    config: dict[str, Any],
    relpath: str,
    absolute: str,
) -> re.Match[str] | None:
    """Compatibility wrapper for callers that intentionally supply two views."""

    return _guarded_match_one(root, config, relpath) or _guarded_match_one(
        root, config, absolute
    )


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
    raw_before, raw_after = template.split("{uc}", 1)
    match_template = template
    sentinel = "__BUGATE_UC_SENTINEL__"
    template_path = Path(os.path.normpath(template.replace("{uc}", sentinel)))
    if template_path.is_absolute():
        template_rel = _lexical_workspace_rel(root, template_path.as_posix())
        if template_rel is not None:
            match_template = template_rel.replace(sentinel, "{uc}", 1)
    match_template = match_template.strip("/")
    before, after = match_template.split("{uc}", 1)
    pattern = (
        "^"
        + re.escape(before)
        + r"(?P<uc>[^/]+?)"
        + re.escape(after)
        + r"(?:/|$)"
    )
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
        prefix_text = raw_before.rstrip("/")
        prefix = Path(prefix_text) if prefix_text else Path(".")
        parent = prefix if prefix.is_absolute() else root / prefix
        normalized = re.sub(r"[-_]", "", uc).lower()
        matches = sorted(
            item
            for item in parent.iterdir()
            if item.is_dir() and re.sub(r"[-_]", "", item.name).lower() == normalized
        ) if parent.is_dir() else []
        if len(matches) == 1:
            suffix_text = raw_after.strip("/")
            candidate = matches[0] / suffix_text if suffix_text else matches[0]
    return candidate.resolve()


def _configured_artifact(root: Path, config: dict[str, Any]) -> Path | None:
    raw = config.get("artifact_dir") or config.get("artifact_root")
    if not raw:
        return None
    path = Path(str(raw))
    return (path if path.is_absolute() else root / path).resolve()


def _inside(path: Path, parent: Path) -> bool:
    return _relative_by_identity(path, parent) is not None


def _lexical_relative_to_artifact(
    root: Path,
    relpath: str,
    artifact: Path,
) -> Path | None:
    """Bind a lexical workspace path to an artifact without following links."""

    artifact_rel = _canonical_disk_rel(root, artifact)
    if artifact_rel is None:
        return None
    path_parts = Path(relpath).parts
    artifact_parts = Path(artifact_rel).parts
    if len(path_parts) < len(artifact_parts):
        return None
    if _filesystem_case_insensitive(root):
        matches = tuple(part.casefold() for part in path_parts[: len(artifact_parts)]) == tuple(
            part.casefold() for part in artifact_parts
        )
    else:
        matches = path_parts[: len(artifact_parts)] == artifact_parts
    if not matches:
        return None
    remainder = path_parts[len(artifact_parts) :]
    return Path(*remainder) if remainder else Path(".")


def _lexical_template_binding(
    root: Path,
    config: dict[str, Any],
    relpath: str,
) -> tuple[str, Path] | None:
    """Return the caller-spelled UC and artifact-relative suffix.

    ``_template_artifact`` deliberately resolves the selected directory so
    lifecycle verification uses one filesystem identity.  Classification also
    needs the pre-resolution spelling, otherwise ``usecases/UC-002`` symlinked
    to ``UC-001`` silently inherits UC-001's authorization.  A sentinel keeps
    this lexical parsing independent from the concrete UC name.
    """

    template = str(config.get("artifact_dir_template") or "")
    if template.count("{uc}") != 1:
        return None
    sentinel = "__BUGATE_UC_SENTINEL__"
    template_path = Path(os.path.normpath(template.replace("{uc}", sentinel)))
    if template_path.is_absolute():
        template_rel = _lexical_workspace_rel(root, template_path.as_posix())
        if template_rel is None:
            return None
    else:
        if template_path == Path("..") or (
            template_path.parts and template_path.parts[0] == ".."
        ):
            return None
        template_rel = template_path.as_posix()
    template_rel = template_rel.replace(sentinel, "{uc}", 1).strip("/")
    if "{uc}" not in template_rel:
        return None
    before, after = template_rel.split("{uc}", 1)
    flags = re.IGNORECASE if _filesystem_case_insensitive(root) else 0
    match = re.match(
        "^"
        + re.escape(before)
        + r"(?P<uc>[^/]+?)"
        + re.escape(after)
        + r"(?:/(?P<relative>.*))?$",
        relpath,
        flags,
    )
    if match is None:
        return None
    relative_text = match.group("relative") or ""
    relative = Path(relative_text) if relative_text else Path(".")
    return match.group("uc"), relative


def _artifact_phase(
    relative: Path | None,
    config: dict[str, Any],
    *,
    case_insensitive: bool,
) -> str:
    if relative is None or relative == Path(".") or not relative.parts:
        return "other"
    compare = (lambda value: value.casefold()) if case_insensitive else (lambda value: value)
    top_key = compare(relative.parts[0])
    basename_key = compare(relative.name)
    postrun_names = {compare(name) for name in POSTRUN_NAMES}
    if basename_key in postrun_names or top_key in {
        compare("04_execution"),
        compare("05_knowledge"),
        compare("00_post_run"),
    }:
        return "postrun"
    required_names = {compare(name) for name in required_precode_artifacts(config)}
    if (
        basename_key in required_names
        or PRECODE_PREFIX_RE.match(compare(relative.name))
        or top_key in {compare("00_multiview"), compare("00_adversarial")}
    ):
        return "precode"
    return "other"


def _same_file_as_evidence_descendant(path: Path, evidence_dir: Path) -> bool:
    """Detect an external hardlink to any existing role-evidence file."""

    if not path.exists() or not path.is_file() or not evidence_dir.is_dir():
        return False

    def raise_walk_error(exc: OSError) -> None:
        raise exc

    for directory, _subdirs, filenames in os.walk(
        evidence_dir,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        parent = Path(directory)
        for filename in filenames:
            if _same_existing_path(path, parent / filename):
                return True
    return False


def _uc_matches_artifact(
    root: Path,
    config: dict[str, Any],
    captured_uc: str,
    artifact: Path,
) -> bool:
    actual_uc = resolve_uc(root, artifact.resolve(), config)
    if actual_uc == captured_uc:
        return True
    if config.get("uc_dir_resolve") != "normalized-glob":
        return False
    normalize = lambda value: re.sub(r"[-_]", "", value).lower()
    return normalize(actual_uc) == normalize(captured_uc)


def _artifact_candidates(root: Path, config: dict[str, Any]) -> list[Path]:
    """Enumerate configured UC artifact dirs without recursively scanning the repo."""

    candidates: list[Path] = []
    configured = _configured_artifact(root, config)
    if configured is not None:
        candidates.append(configured)
    template = str(config.get("artifact_dir_template") or "")
    if template.count("{uc}") == 1:
        before, after = template.split("{uc}", 1)
        before_path = Path(before.rstrip("/")) if before.rstrip("/") else Path(".")
        parent = before_path if before_path.is_absolute() else root / before_path
        suffix = Path(after.strip("/")) if after.strip("/") else None
        if parent.is_dir() and _inside(parent.resolve(), root.resolve()):
            for child in sorted(parent.iterdir()):
                if not child.is_dir():
                    continue
                candidate = child / suffix if suffix is not None else child
                if candidate.is_dir() and _inside(candidate.resolve(), root.resolve()):
                    candidates.append(candidate.resolve())
    dedup: dict[str, Path] = {}
    for candidate in candidates:
        dedup[candidate.resolve().as_posix()] = candidate.resolve()
    return [dedup[key] for key in sorted(dedup)]


def _workspace_identity_paths(root: Path, target_path: Path) -> list[tuple[str, Path]]:
    """Return workspace-relative entries naming the target file identity."""

    try:
        target_stat = target_path.stat()
    except OSError:
        return []
    if not target_path.is_file():
        return []
    aliases: dict[str, tuple[str, Path]] = {}

    def add(candidate: Path) -> None:
        if not _same_existing_path(target_path, candidate):
            return
        relpath = _canonical_disk_rel(root, candidate)
        if relpath is not None:
            aliases.setdefault(relpath, (relpath, candidate))

    add(target_path)
    if target_stat.st_nlink >= 2:
        def fail_walk(exc: OSError) -> None:
            raise exc

        for directory, subdirs, filenames in os.walk(
            root,
            topdown=True,
            onerror=fail_walk,
            followlinks=False,
        ):
            subdirs[:] = [
                name
                for name in subdirs
                if name not in {".git", ".bugate-update"}
            ]
            for filename in filenames:
                add(Path(directory) / filename)
    return [aliases[key] for key in sorted(aliases)]


def _receipt_store_mentions_paths(
    artifact: Path,
    evidence_dir: str,
    relpaths: set[str],
    *,
    case_insensitive: bool,
) -> bool:
    """Check path association before asking an artifact store to verify itself."""

    if not relpaths:
        return False
    folded_relpaths = {value.casefold() for value in relpaths}

    def matches(value: object) -> bool:
        if not isinstance(value, str):
            return False
        return value in relpaths or (
            case_insensitive and value.casefold() in folded_relpaths
        )

    def path_values(value: object, *, allow_bare: bool) -> list[str]:
        if isinstance(value, str):
            return [value] if allow_bare else []
        if isinstance(value, list):
            values: list[str] = []
            for child in value:
                if isinstance(child, str):
                    if allow_bare:
                        values.append(child)
                else:
                    values.extend(path_values(child, allow_bare=False))
            return values
        if isinstance(value, dict):
            values = [
                child
                for key, child in value.items()
                if key == "path" and isinstance(child, str)
            ]
            values.extend(
                item
                for key, child in value.items()
                if key != "path" and isinstance(child, (dict, list))
                for item in path_values(child, allow_bare=False)
            )
            return values
        return []

    def owned_paths(payload: object) -> list[str]:
        if not isinstance(payload, dict):
            return []
        values: list[str] = []
        if "artifacts" in payload:
            values.extend(path_values(payload["artifacts"], allow_bare=True))
        if "implementation_files" in payload:
            values.extend(
                path_values(payload["implementation_files"], allow_bare=True)
            )
        return values

    def malformed_ownership_mentions(body: bytes) -> bool:
        text = body.decode("utf-8", errors="ignore")
        json_string = r'"(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|[^"\\])*"'

        def value_fragment(start: int) -> str:
            while start < len(text) and text[start].isspace():
                start += 1
            direct = re.match(json_string, text[start:])
            if direct:
                return direct.group(0)
            if start >= len(text) or text[start] not in "[{":
                end = start
                while end < len(text) and text[end] not in ",\n\r}]":
                    end += 1
                return text[start:end]
            stack: list[str] = []
            quoted = False
            escaped = False
            pairs = {"}": "{", "]": "["}
            for index in range(start, len(text)):
                char = text[index]
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                    continue
                if char == '"':
                    quoted = True
                elif char in "[{":
                    stack.append(char)
                elif char in "]}":
                    if not stack or stack[-1] != pairs[char]:
                        return text[start:index]
                    stack.pop()
                    if not stack:
                        return text[start : index + 1]
            return text[start:]

        def decoded_token(token: str) -> str | None:
            try:
                value = json.loads(token)
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, str) else None

        def keyed_string_tokens(fragment: str, key: str) -> list[str]:
            tokens: list[str] = []
            index = 0
            while index < len(fragment):
                match = re.match(json_string, fragment[index:])
                if match is None:
                    index += 1
                    continue
                token = match.group(0)
                end = index + len(token)
                cursor = end
                while cursor < len(fragment) and fragment[cursor].isspace():
                    cursor += 1
                if (
                    decoded_token(token) == key
                    and cursor < len(fragment)
                    and fragment[cursor] == ":"
                ):
                    cursor += 1
                    while cursor < len(fragment) and fragment[cursor].isspace():
                        cursor += 1
                    value_match = re.match(json_string, fragment[cursor:])
                    if value_match is not None:
                        tokens.append(value_match.group(0))
                index = end
            return tokens

        fragments: list[str] = []
        depth = 0
        index = 0
        while index < len(text):
            match = re.match(json_string, text[index:])
            if match is not None:
                token = match.group(0)
                end = index + len(token)
                cursor = end
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                if (
                    depth == 1
                    and decoded_token(token)
                    in {"artifacts", "implementation_files"}
                    and cursor < len(text)
                    and text[cursor] == ":"
                ):
                    fragments.append(value_fragment(cursor + 1))
                index = end
                continue
            if text[index] in "[{":
                depth += 1
            elif text[index] in "]}":
                depth = max(0, depth - 1)
            index += 1

        for fragment in fragments:
            try:
                decoded = json.loads(fragment)
            except json.JSONDecodeError:
                tokens = keyed_string_tokens(fragment, "path")
                stripped = fragment.lstrip()
                direct = re.match(json_string, stripped)
                if direct:
                    tokens.append(direct.group(0))
                if stripped.startswith("[") and "{" not in stripped:
                    tokens.extend(re.findall(json_string, stripped))
                values: list[str] = []
                for token in tokens:
                    value = decoded_token(token)
                    if value is not None:
                        values.append(value)
            else:
                values = path_values(decoded, allow_bare=True)
            if any(matches(value) for value in values):
                return True
        return False
    receipts = artifact / Path(evidence_dir) / "receipts"
    if not receipts.is_dir():
        return False
    for receipt in sorted(receipts.glob("*.json")):
        body = receipt.read_bytes()
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            # A malformed receipt cannot be trusted.  Associate it only when
            # an ownership-bearing slot visibly names this target, so an
            # unrelated command/message cannot become a workspace-wide denial.
            if malformed_ownership_mentions(body):
                return True
            continue
        if any(matches(value) for value in owned_paths(payload)):
            return True
        # ``json.loads`` intentionally keeps the last duplicate object key.
        # Inspect the raw top-level ownership fragments as well so a visible
        # first ``artifacts``/``implementation_files`` owner cannot be erased
        # by a later duplicate key while unrelated run/metadata paths remain
        # outside the ownership grammar.
        if malformed_ownership_mentions(body):
            return True
    return False


def _completion_artifacts_for_path(
    root: Path,
    config: dict[str, Any],
    target_path: Path,
    canonical_relpath: str | None,
    preferred: Path | None,
) -> list[Path]:
    identity_paths = _workspace_identity_paths(root, target_path)
    relpaths = {relpath for relpath, _path_value in identity_paths}
    if canonical_relpath is not None:
        relpaths.add(canonical_relpath)
    candidates = ([preferred] if preferred is not None else []) + _artifact_candidates(
        root, config
    )
    seen: set[str] = set()
    owners: list[Path] = []
    for artifact in candidates:
        key = artifact.resolve().as_posix()
        if key in seen:
            continue
        seen.add(key)
        if not _receipt_store_mentions_paths(
            artifact,
            str(governance_policy(config)["evidence_dir"]),
            relpaths,
            case_insensitive=_filesystem_case_insensitive(root),
        ):
            continue
        snapshots = latest_completion_snapshot_paths(artifact)
        if (canonical_relpath is not None and canonical_relpath in snapshots) or any(
            _workspace_paths_equal(root, target_path, root / snapshot)
            for snapshot in snapshots
        ):
            owners.append(artifact)
    return owners


def _structural_phase_identity_owners(
    root: Path,
    config: dict[str, Any],
    target_path: Path,
) -> list[tuple[str, Path]]:
    """Find deterministic phase surfaces that resolve to ``target_path``.

    This index intentionally does not read or verify receipt stores.  Required
    artifact names and the five structural phase directories are policy-owned
    even when a sibling UC has a malformed chain, so an unrelated malformed
    receipt must not become a workspace-wide denial.  Conversely, editing the
    ordinary target of a structural symlink must not bypass the phase guard.
    """

    owners: dict[tuple[str, str], tuple[str, Path]] = {}
    target_resolved = target_path.resolve(strict=False)
    case_insensitive = _filesystem_case_insensitive(root)

    def add(kind: str, artifact: Path) -> None:
        artifact = artifact.resolve()
        owners[(kind, artifact.as_posix())] = (kind, artifact)

    def same(candidate: Path) -> bool:
        if _same_existing_path(target_path, candidate):
            return True
        if not candidate.is_symlink():
            return False
        candidate_target = candidate.resolve(strict=False).as_posix()
        target_text = target_resolved.as_posix()
        return candidate_target == target_text or (
            case_insensitive and candidate_target.casefold() == target_text.casefold()
        )

    def walk_matches(directory: Path) -> bool:
        if not directory.is_dir() or directory.is_symlink():
            return False

        def raise_walk_error(exc: OSError) -> None:
            raise exc

        for current, subdirs, filenames in os.walk(
            directory,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            subdirs[:] = [
                name
                for name in subdirs
                if not (Path(current) / name).is_symlink()
            ]
            if any(same(Path(current) / name) for name in filenames):
                return True
        return False

    for artifact in _artifact_candidates(root, config):
        for name in required_precode_artifacts(config):
            if same(artifact / name):
                add("precode", artifact)
        if artifact.is_dir():
            for candidate in artifact.iterdir():
                if PRECODE_PREFIX_RE.match(candidate.name) and same(candidate):
                    add("precode", artifact)
        for name in POSTRUN_NAMES:
            if same(artifact / name):
                add("postrun", artifact)
        for name, kind in {
            "00_multiview": "precode",
            "00_adversarial": "precode",
            "04_execution": "postrun",
            "05_knowledge": "postrun",
            "00_post_run": "postrun",
        }.items():
            phase_root = artifact / name
            phase_target = phase_root.resolve(strict=False)
            phase_relative = _relative_by_identity(target_resolved, phase_target)
            if (phase_root.is_dir() or phase_root.is_symlink()) and phase_relative is not None:
                add(kind, artifact)
            elif walk_matches(phase_root):
                add(kind, artifact)
    return [owners[key] for key in sorted(owners)]


def _guarded_symlink_identity_owners(
    root: Path,
    config: dict[str, Any],
    target_path: Path,
) -> list[tuple[str, Path]]:
    """Reverse-index caller-visible guarded aliases without following them."""

    if not (config.get("guarded_path_regex") or []):
        return []
    root = root.resolve()
    target = target_path.resolve(strict=False)
    case_insensitive = _filesystem_case_insensitive(root)
    owners: dict[str, tuple[str, Path]] = {}

    def non_strict_relative(path: Path, parent: Path) -> Path | None:
        path_parts = path.resolve(strict=False).parts
        parent_parts = parent.resolve(strict=False).parts
        if len(path_parts) < len(parent_parts):
            return None
        prefix = path_parts[: len(parent_parts)]
        matches = prefix == parent_parts or (
            case_insensitive
            and tuple(item.casefold() for item in prefix)
            == tuple(item.casefold() for item in parent_parts)
        )
        if not matches:
            return None
        remainder = path_parts[len(parent_parts) :]
        return Path(*remainder) if remainder else Path(".")

    def register(candidate: Path) -> None:
        if not candidate.is_symlink():
            return
        remainder = non_strict_relative(target, candidate.resolve(strict=False))
        if remainder is None:
            return
        try:
            alias_rel = candidate.relative_to(root)
        except ValueError:
            return
        if remainder != Path("."):
            alias_rel /= remainder
        alias_text = alias_rel.as_posix()
        guarded = _guarded_match_one(root, config, alias_text)
        if guarded is None:
            return
        uc = (guarded.groupdict() or {}).get("uc")
        artifact = _template_artifact(
            root,
            config,
            alias_text,
            captured_uc=uc,
        ) or _configured_artifact(root, config)
        if artifact is None:
            raise RoleGovernanceError(
                f"guarded symlink alias {alias_text!r} cannot bind to an artifact"
            )
        if uc and not _uc_matches_artifact(root, config, uc, artifact):
            actual_uc = resolve_uc(root, artifact.resolve(), config)
            raise RoleGovernanceError(
                f"guarded symlink UC {uc!r} disagrees with resolved artifact UC {actual_uc!r}"
            )
        owners[artifact.resolve().as_posix()] = (
            "implementation",
            artifact.resolve(),
        )

    def raise_walk_error(exc: OSError) -> None:
        raise exc

    for current, subdirs, filenames in os.walk(
        root,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        subdirs[:] = [
            name for name in subdirs if name not in {".git", ".bugate-update"}
        ]
        parent = Path(current)
        for name in (*subdirs, *filenames):
            register(parent / name)
    return [owners[key] for key in sorted(owners)]


def _phase_artifacts_for_path(
    root: Path,
    config: dict[str, Any],
    target_path: Path,
    preferred: Path | None,
    *,
    scan_reverse_aliases: bool,
) -> list[tuple[str, Path]]:
    """Find every phase owner of an existing hardlinked target inode."""

    reverse_owners = (
        _structural_phase_identity_owners(root, config, target_path)
        + _guarded_symlink_identity_owners(root, config, target_path)
        if scan_reverse_aliases
        else []
    )
    owners: dict[tuple[str, str], tuple[str, Path]] = {
        (kind, artifact.resolve().as_posix()): (kind, artifact.resolve())
        for kind, artifact in reverse_owners
    }
    identity_paths = _workspace_identity_paths(root, target_path)
    if not identity_paths:
        return [owners[key] for key in sorted(owners)]

    candidates = ([preferred] if preferred is not None else []) + _artifact_candidates(
        root, config
    )
    policy = governance_policy(config)
    for relpath, candidate in identity_paths:
        kind, artifact, _classified = classify_path(
            root,
            config,
            policy,
            relpath,
        )
        if kind in {"precode", "implementation", "postrun"} and artifact is not None:
            owners[(kind, artifact.resolve().as_posix())] = (
                kind,
                artifact.resolve(),
            )

    relpaths = {relpath for relpath, _candidate in identity_paths}
    for artifact in candidates:
        if artifact is None:
            continue
        artifact = artifact.resolve()
        if not _receipt_store_mentions_paths(
            artifact,
            str(policy["evidence_dir"]),
            relpaths,
            case_insensitive=_filesystem_case_insensitive(root),
        ):
            continue
        for relpath, phase in role_phase_owned_paths(artifact).items():
            if _same_existing_path(target_path, root / relpath):
                kind = {
                    "pre_code": "precode",
                    "implementation": "implementation",
                    "post_run": "postrun",
                }[phase]
                owners[(kind, artifact.as_posix())] = (kind, artifact)
    return [owners[key] for key in sorted(owners)]


def classify_path(
    root: Path,
    config: dict[str, Any],
    policy: dict[str, Any],
    target: str,
) -> tuple[str, Path | None, str]:
    """Return ``(kind, artifact_dir, workspace_relative_path)``."""

    relpath = _rel(root, target)
    absolute_path = _path(root, target)
    resolved_relpath = _canonical_workspace_rel(root, target)
    evidence_parts = tuple(Path(policy["evidence_dir"]).parts)
    parts = tuple(Path(relpath).parts)
    case_insensitive = _filesystem_case_insensitive(root)
    if case_insensitive:
        evidence_parts = tuple(part.casefold() for part in evidence_parts)
        parts = tuple(part.casefold() for part in parts)
    if evidence_parts and any(
        parts[index : index + len(evidence_parts)] == evidence_parts
        for index in range(max(0, len(parts) - len(evidence_parts) + 1))
    ):
        return "evidence", None, relpath
    for candidate in _artifact_candidates(root, config):
        evidence_dir = (candidate / Path(policy["evidence_dir"])).resolve()
        if _inside(absolute_path, evidence_dir) or _same_file_as_evidence_descendant(
            absolute_path, evidence_dir
        ):
            return "evidence", candidate, relpath

    lexical_guarded = _guarded_match_one(root, config, relpath)
    resolved_guarded = (
        _guarded_match_one(root, config, resolved_relpath)
        if resolved_relpath is not None
        else None
    )
    if lexical_guarded is not None:
        if resolved_guarded is None:
            raise RoleGovernanceError(
                f"guarded path {relpath!r} resolves outside its guarded surface"
            )
        lexical_uc = (lexical_guarded.groupdict() or {}).get("uc")
        resolved_uc = (resolved_guarded.groupdict() or {}).get("uc")
        if lexical_uc and resolved_uc:
            normalize = lambda value: re.sub(r"[-_]", "", value).lower()
            same_uc = lexical_uc == resolved_uc or (
                config.get("uc_dir_resolve") == "normalized-glob"
                and normalize(lexical_uc) == normalize(resolved_uc)
            )
            if not same_uc:
                raise RoleGovernanceError(
                    f"guarded lexical UC {lexical_uc!r} disagrees with resolved UC {resolved_uc!r}"
                )
        guarded = lexical_guarded
        guarded_relpath = relpath
    else:
        guarded = resolved_guarded
        guarded_relpath = resolved_relpath
    if guarded:
        uc = (guarded.groupdict() or {}).get("uc")
        artifact = _template_artifact(
            root,
            config,
            guarded_relpath or relpath,
            captured_uc=uc,
        )
        if artifact is None:
            artifact = _configured_artifact(root, config)
        if uc and artifact is not None and not _uc_matches_artifact(
            root, config, uc, artifact
        ):
            actual_uc = resolve_uc(root, artifact.resolve(), config)
            raise RoleGovernanceError(
                f"guarded UC {uc!r} disagrees with resolved artifact UC {actual_uc!r}"
            )
        return "implementation", artifact, relpath

    lexical_artifact: Path | None = None
    lexical_relative: Path | None = None
    template_binding = _lexical_template_binding(root, config, relpath)
    if template_binding is not None:
        lexical_uc, lexical_relative = template_binding
        lexical_artifact = _template_artifact(
            root,
            config,
            relpath,
            captured_uc=lexical_uc,
        )
        if lexical_artifact is not None and not _uc_matches_artifact(
            root, config, lexical_uc, lexical_artifact
        ):
            actual_uc = resolve_uc(root, lexical_artifact.resolve(), config)
            raise RoleGovernanceError(
                f"artifact lexical UC {lexical_uc!r} disagrees with resolved artifact UC {actual_uc!r}"
            )
    else:
        lexical_artifact = _configured_artifact(root, config)
        if lexical_artifact is not None:
            lexical_relative = _lexical_relative_to_artifact(
                root, relpath, lexical_artifact
            )

    lexical_kind = _artifact_phase(
        lexical_relative,
        config,
        case_insensitive=case_insensitive,
    )

    resolved_artifact = (
        _template_artifact(root, config, resolved_relpath)
        if resolved_relpath is not None
        else None
    ) or _configured_artifact(root, config)
    resolved_relative = (
        _relative_by_identity(absolute_path, resolved_artifact)
        if resolved_artifact is not None and _inside(absolute_path, resolved_artifact)
        else None
    )
    resolved_kind = _artifact_phase(
        resolved_relative,
        config,
        case_insensitive=case_insensitive,
    )

    if lexical_kind in {"precode", "postrun"}:
        if (
            lexical_artifact is None
            or not _inside(absolute_path, lexical_artifact)
            or resolved_artifact is None
            or not _same_existing_path(lexical_artifact, resolved_artifact)
        ):
            raise RoleGovernanceError(
                f"{lexical_kind} path {relpath!r} resolves outside its artifact surface"
            )
        if resolved_kind in {"precode", "postrun"} and resolved_kind != lexical_kind:
            raise RoleGovernanceError(
                f"{lexical_kind} path {relpath!r} resolves to conflicting {resolved_kind} ownership"
            )
        return lexical_kind, lexical_artifact, relpath
    if resolved_kind in {"precode", "postrun"}:
        return resolved_kind, resolved_artifact, relpath
    return "other", lexical_artifact or resolved_artifact, relpath


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
            if kind == "evidence":
                # Append-only receipts are never an agent-tool editing surface.
                failures.append(
                    f"{relpath}: direct edits to {policy['evidence_dir']}/ are forbidden; use bin/bugate-role"
                )
                continue
            canonical_relpath = _canonical_workspace_rel(root, target)
            completion_owners = _completion_artifacts_for_path(
                root,
                config,
                _path(root, target),
                canonical_relpath,
                artifact,
            )
            if completion_owners:
                for owner in completion_owners:
                    result = preflight(
                        owner,
                        "post_run",
                        require_acceptance=True,
                    )
                    try:
                        owner_label = owner.relative_to(root).as_posix()
                    except ValueError:
                        owner_label = owner.as_posix()
                    prefix = f"{relpath} [completion owner {owner_label}]"
                    messages = [f"{prefix}: {item}" for item in result.errors]
                    if result.allowed:
                        warnings.extend(
                            f"{prefix}: {item}" for item in result.warnings
                        )
                    else:
                        failures.extend(
                            messages or [f"{prefix}: role preflight failed"]
                        )
            phase_owners = _phase_artifacts_for_path(
                root,
                config,
                _path(root, target),
                artifact,
                scan_reverse_aliases=kind == "other",
            )
            for owner_kind, owner in phase_owners:
                if (
                    kind == owner_kind
                    and artifact is not None
                    and _same_existing_path(artifact, owner)
                ):
                    continue
                owner_phase = {
                    "precode": "pre_code",
                    "implementation": "implementation",
                    "postrun": "post_run",
                }[owner_kind]
                result = preflight(
                    owner,
                    owner_phase,
                    require_acceptance=owner_phase in {"implementation", "post_run"},
                )
                try:
                    owner_label = owner.relative_to(root).as_posix()
                except ValueError:
                    owner_label = owner.as_posix()
                prefix = f"{relpath} [{owner_phase} identity owner {owner_label}]"
                messages = [f"{prefix}: {item}" for item in result.errors]
                if result.allowed:
                    warnings.extend(f"{prefix}: {item}" for item in result.warnings)
                else:
                    failures.extend(messages or [f"{prefix}: role preflight failed"])
            if kind == "other":
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
