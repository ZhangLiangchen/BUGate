#!/usr/bin/env python3
"""Generate exact pre-lock imported projections from formal BUGate tags.

Historical installers are never run.  The generator securely extracts the
annotated Core tag, statically reads its literal ownership constants, and calls
only a restricted AST slice containing the pure hook-shape functions in an
isolated Python subprocess.  It never invokes ``main``, scaffold, Memory, or a
real imported repository.
"""
from __future__ import annotations

import ast
import copy
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import bugate_install_contract as contract


LEGACY_MANIFEST_KIND = "prelock-installed-projection"


def _git(repo_root: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )


def _require_annotated_tag(repo_root: Path, tag: str) -> str:
    if tag not in contract.SUPPORTED_LEGACY_TAGS:
        raise contract.ContractError(f"unsupported legacy tag: {tag!r}")
    result = _git(repo_root, "cat-file", "-t", f"refs/tags/{tag}")
    if result.returncode != 0 or result.stdout.strip() != "tag":
        raise contract.ContractError(f"legacy source must be an annotated tag: {tag}")
    commit = _git(repo_root, "rev-parse", f"{tag}^{{commit}}")
    if commit.returncode != 0:
        raise contract.ContractError(commit.stderr.strip() or f"cannot peel legacy tag: {tag}")
    value = commit.stdout.strip()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise contract.ContractError(f"invalid peeled commit for {tag}: {value!r}")
    return value


def _safe_member_name(member: tarfile.TarInfo) -> str:
    raw = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
    return contract.validate_relative_path(raw, field="legacy archive path")


def _safe_extract_tag_archive(data: bytes, destination: Path) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        seen: set[str] = set()
        folded: set[str] = set()
        normalized: list[tuple[str, tarfile.TarInfo]] = []
        for member in members:
            name = _safe_member_name(member)
            if name in seen or name.casefold() in folded:
                raise contract.ContractError(f"duplicate legacy archive path: {name}")
            seen.add(name)
            folded.add(name.casefold())
            if member.islnk():
                raise contract.ContractError(f"hardlinks are forbidden in legacy archive: {name}")
            if not (member.isdir() or member.isfile() or member.issym()):
                raise contract.ContractError(f"unsupported legacy archive entry: {name}")
            if member.issym():
                contract.validate_symlink_target(name, member.linkname)
            normalized.append((name, member))

        for name, member in sorted(normalized, key=lambda item: (item[0].count("/"), item[0])):
            path = destination / name
            for parent in path.parents:
                if parent == destination:
                    break
                if parent.is_symlink():
                    raise contract.ContractError(f"legacy archive parent is a symlink: {name}")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=False)
                os.chmod(path, 0o755)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                path.symlink_to(member.linkname)
                continue
            mode = stat.S_IMODE(member.mode)
            normalized_mode = 0o755 if mode & 0o111 else 0o644
            if mode & 0o7000:
                raise contract.ContractError(f"unsafe legacy file mode {mode:o}: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise contract.ContractError(f"cannot read legacy archive member: {name}")
            path.write_bytes(stream.read())
            os.chmod(path, normalized_mode)
    return sorted(seen)


def _literal_assignment(tree: ast.Module, name: str, *, default: Any = None) -> Any:
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            try:
                return ast.literal_eval(value)
            except (TypeError, ValueError) as exc:
                raise contract.ContractError(
                    f"historical installer {name} is not a literal"
                ) from exc
    return copy.deepcopy(default)


def _function_literal_assignment(
    tree: ast.Module, function_name: str, variable_name: str
) -> Any:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        raise contract.ContractError(f"historical installer lacks {function_name}()")
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in node.targets
        ):
            try:
                return ast.literal_eval(node.value)
            except (TypeError, ValueError) as exc:
                raise contract.ContractError(
                    f"historical installer {function_name}.{variable_name} is not literal"
                ) from exc
    raise contract.ContractError(
        f"historical installer lacks {function_name}.{variable_name}"
    )


_ALLOWED_PURE_HOOK_NODES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Assign,
    ast.Return,
    ast.If,
    ast.IfExp,
    ast.Expr,
    ast.ListComp,
    ast.comprehension,
    ast.Dict,
    ast.List,
    ast.Tuple,
    ast.BinOp,
    ast.Add,
    ast.Compare,
    ast.Eq,
    ast.Constant,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Subscript,
    ast.Attribute,
    ast.Call,
}


def _pure_hook_source(installer: Path) -> str:
    tree = ast.parse(installer.read_text(encoding="utf-8"), filename=str(installer))
    wanted_assignments = {"_ROOT_SNIPPET"}
    wanted_functions = {"_cmd", "_bin_cmd", "hook_blocks"}
    body: list[ast.stmt] = []
    found_functions: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name)
                and target.id in wanted_assignments
                for target in targets
            ):
                try:
                    value = ast.literal_eval(node.value)
                except (TypeError, ValueError) as exc:
                    raise contract.ContractError(
                        "historical hook resolver must be a literal"
                    ) from exc
                if not isinstance(value, str):
                    raise contract.ContractError(
                        "historical hook resolver must be literal text"
                    )
                body.append(copy.deepcopy(node))
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            if node.name in found_functions:
                raise contract.ContractError(
                    f"duplicate historical pure hook function: {node.name}"
                )
            found_functions.add(node.name)
            selected = copy.deepcopy(node)
            selected.decorator_list = []
            selected.returns = None
            for argument in (
                *selected.args.posonlyargs,
                *selected.args.args,
                *selected.args.kwonlyargs,
            ):
                argument.annotation = None
            if selected.args.vararg is not None:
                selected.args.vararg.annotation = None
            if selected.args.kwarg is not None:
                selected.args.kwarg.annotation = None
            body.append(selected)
    if "_cmd" not in found_functions or "hook_blocks" not in found_functions:
        raise contract.ContractError("historical installer lacks pure hook functions")

    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    for node in ast.walk(module):
        if type(node) not in _ALLOWED_PURE_HOOK_NODES:
            raise contract.ContractError(
                f"impure historical hook AST node rejected: {type(node).__name__}"
            )
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise contract.ContractError(
                f"impure historical hook name rejected: {node.id}"
            )
        if isinstance(node, ast.Attribute):
            if not (
                node.attr == "join"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                raise contract.ContractError(
                    f"impure historical hook attribute rejected: {node.attr}"
                )
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in {"_cmd", "_bin_cmd"}:
                    raise contract.ContractError(
                        f"impure historical hook call rejected: {node.func.id}"
                    )
            elif not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
            ):
                raise contract.ContractError("impure historical hook call rejected")
    return ast.unparse(module)


_PURE_HOOK_HELPER = r"""
import ast
import json
import sys

source = sys.stdin.read()
tree = ast.parse(source, filename="<validated-historical-hooks>")
allowed = {
    "Module", "FunctionDef", "arguments", "arg", "Assign", "Return", "If",
    "IfExp", "Expr", "ListComp", "comprehension", "Dict", "List", "Tuple",
    "BinOp", "Add", "Compare", "Eq", "Constant", "JoinedStr", "FormattedValue",
    "Name", "Load", "Store", "Subscript", "Attribute", "Call"
}
for node in ast.walk(tree):
    if type(node).__name__ not in allowed:
        raise SystemExit("validated hook AST contains a forbidden node")
    if isinstance(node, ast.Name) and node.id.startswith("__"):
        raise SystemExit("validated hook AST contains a forbidden name")
    if isinstance(node, ast.Attribute) and not (
        node.attr == "join" and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        raise SystemExit("validated hook AST contains a forbidden attribute")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id not in {"_cmd", "_bin_cmd"}:
                raise SystemExit("validated hook AST contains a forbidden call")
        elif not (
            isinstance(node.func, ast.Attribute) and node.func.attr == "join"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
        ):
            raise SystemExit("validated hook AST contains a forbidden call")
namespace = {"__builtins__": {}}
exec(compile(tree, "<validated-historical-hooks>", "exec"), namespace, namespace)
hook_blocks = namespace.get("hook_blocks")
if not callable(hook_blocks):
    raise SystemExit("historical installer lacks pure hook_blocks")
print(json.dumps({runtime: hook_blocks(".bugate", runtime) for runtime in ("claude", "codex")}, sort_keys=True))
"""


def _isolated_hook_shapes(installer: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    pure_source = _pure_hook_source(installer)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.run(
        [sys.executable, "-I", "-c", _PURE_HOOK_HELPER],
        cwd=installer.parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        input=pure_source,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise contract.ContractError(
            "isolated historical hook projection failed: "
            + (process.stderr.strip() or "no diagnostic")
        )
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise contract.ContractError("historical hook projection returned invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"claude", "codex"}:
        raise contract.ContractError("historical hook projection is incomplete")
    return value


def _legacy_roles(path: str, tree_roots: Iterable[str], single_files: Iterable[str]) -> list[str]:
    installable = (*tree_roots, *single_files)
    if any(
        contract._is_within(path, root) or contract._is_within(root, path)
        for root in installable
    ):
        return ["installable_payload"]
    if path in {".codex-plugin/plugin.json", ".claude-plugin/plugin.json"} or any(
        contract._is_within(metadata, path)
        for metadata in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json")
    ):
        return ["release_metadata"]
    return ["validated_extra"]


def _tree_inventory(
    tree: Path,
    archive_paths: Iterable[str],
    tree_roots: tuple[str, ...],
    single_files: tuple[str, ...],
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for relative in sorted(archive_paths):
        entry = contract._managed_entry(tree, relative)
        entry["roles"] = _legacy_roles(relative, tree_roots, single_files)
        inventory.append(entry)
    return inventory


def _legacy_projection(
    inventory: list[dict[str, Any]],
    *,
    tree_roots: tuple[str, ...],
    single_files: tuple[str, ...],
    skill_names: tuple[str, ...],
    runtimes: tuple[str, ...],
    agent_source_dir: str,
    hooks: Mapping[str, Mapping[str, list[dict[str, Any]]]],
    gitignore_begin: str,
    gitignore_end: str,
    gitignore_template: str,
) -> list[dict[str, Any]]:
    by_path = {item["path"]: item for item in inventory}
    projection = [
        contract._projection_copy(item)
        for item in inventory
        if "installable_payload" in item["roles"]
    ]
    implicit_directories: set[str] = set()
    for installed_path in (*tree_roots, *single_files):
        parent = PurePosixPath(installed_path).parent
        while parent != PurePosixPath("."):
            relative = parent.as_posix()
            if not any(
                relative == root or relative.startswith(root + "/")
                for root in tree_roots
            ):
                implicit_directories.add(relative)
            parent = parent.parent
    for item in projection:
        if (
            item["scope"] == "vendor"
            and item["type"] == "directory"
            and item["source_path"] in implicit_directories
        ):
            item["legacy_mode_policy"] = "created_directory_umask"
    for runtime in runtimes:
        runtime_name = runtime.removeprefix(".")
        for skill in skill_names:
            source = f".shared/skills/{skill}"
            if source not in by_path:
                raise contract.ContractError(f"legacy skill source missing: {source}")
            path = f"{runtime}/skills/{skill}"
            target = f"../../.bugate/{source}"
            contract.validate_symlink_target(path, target)
            projection.append(
                {
                    "id": f"skill:{runtime_name}:{skill}",
                    "scope": "workspace",
                    "source_path": source,
                    "target_path": path,
                    "type": "symlink",
                    "mode": "0777",
                    "target": target,
                    "skill_name": skill,
                }
            )

    agent_sources = sorted(
        (
            item
            for path, item in by_path.items()
            if PurePosixPath(path).parent.as_posix() == agent_source_dir
            and path.endswith(".toml")
            and item["type"] == "file"
        ),
        key=lambda item: item["path"],
    )
    if not agent_sources:
        raise contract.ContractError("legacy installer has no Codex gate-agent sources")
    for source_item in agent_sources:
        name = PurePosixPath(source_item["path"]).name
        projection.append(
            {
                "id": f"agent:codex:{name.removesuffix('.toml')}",
                "scope": "workspace",
                "source_path": source_item["path"],
                "target_path": f".codex/agents/{name}",
                "type": "file",
                "mode": source_item["mode"],
                "sha256": source_item["sha256"],
                "legacy_mode_policy": "copyfile_destination",
            }
        )

    for runtime, target_path in contract.SHARED_HOOK_TARGETS.items():
        for event, entries in hooks[runtime].items():
            for index, value in enumerate(entries):
                semantic_value = {"event": event, "value": value}
                projection.append(
                    {
                        "id": f"legacy-hook:{runtime}:{event}:{index}",
                        "scope": "shared_json_fragment",
                        "runtime": runtime,
                        "target_path": target_path,
                        "event": event,
                        "type": "json_fragment",
                        "value": copy.deepcopy(value),
                        "semantic_digest": contract.semantic_digest(semantic_value),
                    }
                )

    block = gitignore_template.format(
        begin=gitignore_begin,
        end=gitignore_end,
        vendor_dir=".bugate",
    )
    block_value = {"begin": gitignore_begin, "end": gitignore_end, "content": block}
    projection.append(
        {
            "id": "gitignore:bugate-imported-mode",
            "scope": "marked_text_block",
            "target_path": ".gitignore",
            "type": "text_fragment",
            **block_value,
            "semantic_digest": contract.semantic_digest(block_value),
        }
    )
    contract.validate_installed_projection(projection, archive_inventory=inventory)
    return sorted(projection, key=lambda item: item["id"])


def _plugin_version(tree: Path, tag: str) -> str:
    versions: list[str] = []
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        try:
            document = json.loads((tree / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise contract.ContractError(f"invalid historical plugin manifest: {tag}/{relative}") from exc
        versions.append(contract.validate_semver(document.get("version")))
    if len(set(versions)) != 1 or versions[0] != tag.removeprefix("v"):
        raise contract.ContractError(
            f"historical tag/plugin version mismatch: {tag} -> {versions!r}"
        )
    return versions[0]


def generate_legacy_manifest(repo_root: Path, tag: str) -> dict[str, Any]:
    """Generate one sealed, exact pre-lock projection from an annotated tag."""

    root = repo_root.resolve()
    commit = _require_annotated_tag(root, tag)
    archived = _git(root, "archive", "--format=tar", tag, binary=True)
    if archived.returncode != 0:
        stderr = archived.stderr.decode("utf-8", "replace").strip()
        raise contract.ContractError(stderr or f"git archive failed for {tag}")

    with tempfile.TemporaryDirectory(prefix=f"bugate-legacy-{tag}-") as raw:
        tree = Path(raw)
        archive_paths = _safe_extract_tag_archive(archived.stdout, tree)
        version = _plugin_version(tree, tag)
        installer = tree / "scripts" / "bugate_init.py"
        if not installer.is_file():
            raise contract.ContractError(f"historical tag lacks installer: {tag}")
        source = installer.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename=str(installer))
        tree_roots = tuple(_literal_assignment(parsed, "KIT_DIRS", default=()))
        single_files = tuple(_literal_assignment(parsed, "KIT_FILES", default=()))
        agent_source_dir = _literal_assignment(
            parsed,
            "CODEX_AGENTS_KIT_REL",
            default=contract.CODEX_GATE_AGENT_SOURCE_DIR,
        )
        skill_names = tuple(
            _function_literal_assignment(parsed, "link_skills", "skill_names")
        )
        raw_runtimes = _function_literal_assignment(parsed, "link_skills", "runtimes")
        runtimes = tuple(value[0] for value in raw_runtimes)
        gitignore_begin = _literal_assignment(parsed, "GITIGNORE_BEGIN")
        gitignore_end = _literal_assignment(parsed, "GITIGNORE_END")
        gitignore_template = _literal_assignment(parsed, "GITIGNORE_BLOCK")
        for path in (*tree_roots, *single_files, agent_source_dir):
            contract.validate_relative_path(path, field="historical installer path")

        inventory = _tree_inventory(tree, archive_paths, tree_roots, single_files)
        hooks = _isolated_hook_shapes(installer)
        projection = _legacy_projection(
            inventory,
            tree_roots=tree_roots,
            single_files=single_files,
            skill_names=skill_names,
            runtimes=runtimes,
            agent_source_dir=agent_source_dir,
            hooks=hooks,
            gitignore_begin=gitignore_begin,
            gitignore_end=gitignore_end,
            gitignore_template=gitignore_template,
        )
        fingerprint_payload = {
            "installable_inventory": [
                item for item in inventory if "installable_payload" in item["roles"]
            ],
            "installed_projection": projection,
        }
        payload = {
            "schema_version": contract.RELEASE_SCHEMA_VERSION,
            "manifest_kind": LEGACY_MANIFEST_KIND,
            "bugate_version": version,
            "source_tag": tag,
            "source_commit": commit,
            "layout_version": 0,
            "hook_contract_version": 0,
            "profile_schema_compatibility": copy.deepcopy(
                contract.PROFILE_SCHEMA_COMPATIBILITY
            ),
            "archive_inventory": inventory,
            "installed_projection": projection,
            "legacy_layout_fingerprint": contract.semantic_digest(
                fingerprint_payload
            ),
        }
        manifest = contract.seal_document(payload)
        validate_legacy_manifest(manifest, expected_tag=tag)
        return manifest


def validate_legacy_manifest(
    manifest: Mapping[str, Any], *, expected_tag: str | None = None
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise contract.ContractError("legacy manifest must be an object")
    if manifest.get("schema_version") != contract.RELEASE_SCHEMA_VERSION:
        raise contract.ContractError("unsupported legacy manifest schema")
    if manifest.get("manifest_kind") != LEGACY_MANIFEST_KIND:
        raise contract.ContractError("invalid legacy manifest kind")
    tag = manifest.get("source_tag")
    if tag not in contract.SUPPORTED_LEGACY_TAGS:
        raise contract.ContractError(f"unsupported legacy manifest tag: {tag!r}")
    if expected_tag is not None and tag != expected_tag:
        raise contract.ContractError(
            f"legacy manifest tag mismatch: expected {expected_tag}, actual {tag}"
        )
    version = contract.validate_semver(manifest.get("bugate_version"))
    if version != tag.removeprefix("v"):
        raise contract.ContractError("legacy manifest tag/version mismatch")
    commit = manifest.get("source_commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(
        char not in "0123456789abcdef" for char in commit
    ):
        raise contract.ContractError("legacy manifest source_commit is invalid")
    inventory = manifest.get("archive_inventory")
    projection = manifest.get("installed_projection")
    if not isinstance(inventory, list) or not isinstance(projection, list):
        raise contract.ContractError("legacy manifest inventory/projection is invalid")
    # Generic type/path/mode/digest validation remains shared, while historical
    # role assignment is tag-derived rather than today's catalog-derived.
    contract.validate_managed_paths(
        [{key: value for key, value in item.items() if key != "roles"} for item in inventory]
    )
    for item in inventory:
        roles = item.get("roles")
        if not isinstance(roles, list) or not roles or any(
            role not in contract.ARCHIVE_ROLES for role in roles
        ):
            raise contract.ContractError(f"invalid legacy archive roles: {item.get('path')}")
    for item in projection:
        policy = item.get("legacy_mode_policy")
        if policy is None:
            continue
        if policy == "created_directory_umask":
            valid = item.get("scope") == "vendor" and item.get("type") == "directory"
        elif policy == "copyfile_destination":
            valid = (
                item.get("scope") == "workspace"
                and item.get("type") == "file"
                and str(item.get("id", "")).startswith("agent:codex:")
            )
        else:
            valid = False
        if not valid:
            raise contract.ContractError("invalid legacy mode-evidence policy")
    contract.validate_installed_projection(projection, archive_inventory=inventory)
    installable_sources = {
        item["path"]
        for item in inventory
        if "installable_payload" in item["roles"]
    }
    vendor_items = [item for item in projection if item.get("scope") == "vendor"]
    vendor_sources = [item.get("source_path") for item in vendor_items]
    if (
        len(vendor_sources) != len(installable_sources)
        or set(vendor_sources) != installable_sources
    ):
        raise contract.ContractError(
            "legacy installed projection does not exactly cover installable payload"
        )
    for item in vendor_items:
        source = item["source_path"]
        if item.get("target_path") != source or item.get("id") != f"vendor:{source}":
            raise contract.ContractError(
                "legacy vendor projection identity/target differs from archive source"
            )
    fingerprint_payload = {
        "installable_inventory": [
            item for item in inventory if "installable_payload" in item["roles"]
        ],
        "installed_projection": projection,
    }
    expected = contract.semantic_digest(fingerprint_payload)
    if manifest.get("legacy_layout_fingerprint") != expected:
        raise contract.ContractError("legacy layout fingerprint mismatch")
    contract.validate_self_digest(manifest)
    return copy.deepcopy(dict(manifest))


def validate_legacy_manifest_asset(
    data: bytes,
    *,
    expected_tag: str,
    target_release_manifest: Mapping[str, Any],
    actual_mode: str,
) -> dict[str, Any]:
    """Validate one read legacy asset against the target release inventory.

    The caller must have read ``data`` from a regular, non-symlink file and pass
    its normalized executable mode.  A legacy document's own digest proves only
    that it is internally self-consistent; this additional binding prevents a
    different, re-sealed document from being substituted into a prepared target
    release after its inventory was verified.
    """

    if not isinstance(data, bytes):
        raise contract.ContractError("legacy manifest asset must be bytes")
    if expected_tag not in contract.SUPPORTED_LEGACY_TAGS:
        raise contract.ContractError(
            f"unsupported legacy manifest tag: {expected_tag!r}"
        )
    if actual_mode not in {"0644", "0755"}:
        raise contract.ContractError("legacy manifest asset mode is invalid")

    release = contract.validate_release_manifest(target_release_manifest)
    relative = f"{contract.LEGACY_MANIFEST_DIR}/{expected_tag}.json"
    source = next(
        (item for item in release["archive_inventory"] if item["path"] == relative),
        None,
    )
    if source is None:
        raise contract.ContractError(
            f"target release inventory lacks legacy manifest asset: {expected_tag}"
        )
    if (
        source.get("type") != "file"
        or source.get("mode") != actual_mode
        or source.get("sha256") != contract.sha256_bytes(data)
        or source.get("roles") != ["release_metadata"]
    ):
        raise contract.ContractError(
            f"legacy manifest asset differs from target release inventory: {expected_tag}"
        )

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = child
        return value

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise contract.ContractError(
            f"invalid legacy manifest asset JSON: {expected_tag}"
        ) from exc
    if not isinstance(document, dict):
        raise contract.ContractError(
            f"legacy manifest asset must be an object: {expected_tag}"
        )
    return validate_legacy_manifest(document, expected_tag=expected_tag)


def generate_all_legacy_manifests(
    repo_root: Path,
    tags: Iterable[str] = contract.SUPPORTED_LEGACY_TAGS,
) -> dict[str, bytes]:
    overlays: dict[str, bytes] = {}
    for tag in tags:
        manifest = generate_legacy_manifest(repo_root, tag)
        path = f"{contract.LEGACY_MANIFEST_DIR}/{tag}.json"
        overlays[path] = contract.canonical_json_bytes(manifest)
    expected = {
        f"{contract.LEGACY_MANIFEST_DIR}/{tag}.json"
        for tag in contract.SUPPORTED_LEGACY_TAGS
    }
    if tuple(tags) == contract.SUPPORTED_LEGACY_TAGS and set(overlays) != expected:
        raise contract.ContractError("legacy manifest set is incomplete")
    return overlays
