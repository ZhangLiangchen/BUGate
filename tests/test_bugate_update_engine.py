from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bugate_install_contract as contract  # noqa: E402
import bugate_update_engine as engine  # noqa: E402


def write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


def full_tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    """Capture directories, files, symlinks, targets, bytes, and POSIX mode."""

    result: dict[str, tuple[str, bytes | str, int]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            path = current_path / name
            details = os.lstat(path)
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(details.st_mode):
                result[relative] = (
                    "symlink",
                    os.readlink(path),
                    stat.S_IMODE(details.st_mode),
                )
            else:
                result[relative] = (
                    "directory",
                    b"",
                    stat.S_IMODE(details.st_mode),
                )
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = current_path / name
            details = os.lstat(path)
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(details.st_mode):
                result[relative] = (
                    "symlink",
                    os.readlink(path),
                    stat.S_IMODE(details.st_mode),
                )
            elif stat.S_ISREG(details.st_mode):
                result[relative] = (
                    "file",
                    path.read_bytes(),
                    stat.S_IMODE(details.st_mode),
                )
            else:
                result[relative] = (
                    "special",
                    b"",
                    stat.S_IMODE(details.st_mode),
                )
    return result


def release_tree(
    base: Path,
    version: str,
    marker: bytes = b"one\n",
    *,
    updater_minimum_version: str = "0.4.2",
) -> tuple[Path, dict]:
    root = base / f"release-{version}"
    for relative in contract.VENDOR_TREE_ROOTS:
        (root / relative).mkdir(parents=True, exist_ok=True)
    write(root / "scripts/bugate_update.py", b'UPDATER_VERSION = "0.4.2"\n' + marker)
    for relative in contract.UPDATER_WORKER_FILES:
        if relative == "scripts/bugate_update.py":
            continue
        write(root / relative, f"# synthetic {relative}\n".encode())
    write(root / "scripts/runtime.py", marker)
    write(root / "bin/bugate-update", contract.BUGATE_UPDATE_WRAPPER_BYTES, 0o755)
    for skill in contract.SKILL_NAMES:
        write(root / f".shared/skills/{skill}/SKILL.md", f"# {skill}\n".encode())
    for name in contract.CODEX_GATE_AGENT_NAMES:
        write(root / contract.CODEX_GATE_AGENT_SOURCE_DIR / name, f"name={name}\n".encode())
    write(root / "docs/SETUP-OPTIONAL.md", b"setup\n")
    manifest = contract.build_release_manifest(
        root, version, updater_minimum_version=updater_minimum_version
    )
    contract.validate_current_release_manifest(manifest, expected_version=version)
    return root, manifest


def materialize_install(
    project: Path,
    release: Path,
    manifest: dict,
    *,
    vendor: str = ".bugate",
    previous_version: str | None = None,
) -> dict:
    write(
        project / "bugate.config.yaml",
        b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
    )
    write(
        project / "bugate.profile.yaml",
        b"role_governance:\n  mode: off\nmemory:\n  namespace: project:synthetic\n",
    )
    lock = contract.build_installed_lock(
        manifest,
        previous_version=previous_version,
        archive_sha256=None,
        vendor_dir=vendor,
        updater_version="0.4.2",
    )
    projection = lock["installed_projection"]
    for item in sorted(
        projection,
        key=lambda value: (value["type"] != "directory", value["target_path"].count("/")),
    ):
        scope = item["scope"]
        target = project / item["target_path"]
        if scope in {"shared_json_fragment", "marked_text_block"}:
            continue
        if item["id"] == "metadata:installed-lock":
            continue
        if item["id"] == "metadata:installed-release-manifest":
            write(target, contract.canonical_json_bytes(manifest))
            continue
        if item["type"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, int(item["mode"], 8))
        elif item["type"] == "file":
            write(target, (release / item["source_path"]).read_bytes(), int(item["mode"], 8))
        elif item["type"] == "symlink":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(item["target"])
    for target_path in sorted(
        {item["target_path"] for item in projection if item["scope"] == "shared_json_fragment"}
    ):
        result = engine.merge_hook_file(
            None,
            prior_projection=[],
            new_projection=projection,
            target_path=target_path,
        )
        write(project / target_path, result.content)
    block = next(item for item in projection if item["scope"] == "marked_text_block")
    write(
        project / block["target_path"],
        engine.merge_marked_block(None, prior_item=None, new_item=block).content,
    )
    write(project / vendor / contract.INSTALLED_LOCK_PATH, contract.installed_lock_bytes(lock))
    return lock


def legacy_manifest(payload: bytes = b"legacy\n", *, mode_policy: bool = False) -> dict:
    digest = contract.sha256_bytes(payload)
    inventory = [
        {
            "path": "scripts/legacy.py",
            "type": "file",
            "mode": "0644",
            "sha256": digest,
            "roles": ["installable_payload"],
        }
    ]
    hook_value = {
        "hooks": [
            {
                "type": "command",
                "command": 'ROOT=x; /usr/bin/env python3 "$ROOT/.bugate/scripts/legacy.py"',
            }
        ]
    }
    block = "# begin legacy\n/.bugate/plan.lock\n# end legacy\n"
    projection = [
        {
            "id": "vendor:scripts/legacy.py",
            "scope": "vendor",
            "source_path": "scripts/legacy.py",
            "target_path": "scripts/legacy.py",
            "type": "file",
            "mode": "0644",
            "sha256": digest,
        },
        {
            "id": "legacy-hook:codex:PreToolUse:0",
            "scope": "shared_json_fragment",
            "runtime": "codex",
            "target_path": ".codex/hooks.json",
            "event": "PreToolUse",
            "type": "json_fragment",
            "value": hook_value,
            "semantic_digest": contract.semantic_digest(
                {"event": "PreToolUse", "value": hook_value}
            ),
        },
        {
            "id": "gitignore:bugate-imported-mode",
            "scope": "marked_text_block",
            "target_path": ".gitignore",
            "type": "text_fragment",
            "begin": "# begin legacy",
            "end": "# end legacy",
            "content": block,
            "semantic_digest": contract.semantic_digest(
                {"begin": "# begin legacy", "end": "# end legacy", "content": block}
            ),
        },
    ]
    if mode_policy:
        projection.append(
            {
                "id": "agent:codex:legacy",
                "scope": "workspace",
                "source_path": "scripts/legacy.py",
                "target_path": ".codex/agents/legacy.toml",
                "type": "file",
                "mode": "0644",
                "sha256": digest,
                "legacy_mode_policy": "copyfile_destination",
            }
        )
    fingerprint = contract.semantic_digest(
        {"installable_inventory": inventory, "installed_projection": projection}
    )
    return contract.seal_document(
        {
            "schema_version": contract.RELEASE_SCHEMA_VERSION,
            "manifest_kind": "prelock-installed-projection",
            "bugate_version": "0.3.2",
            "source_tag": "v0.3.2",
            "source_commit": "1" * 40,
            "layout_version": 0,
            "hook_contract_version": 0,
            "profile_schema_compatibility": copy.deepcopy(
                contract.PROFILE_SCHEMA_COMPATIBILITY
            ),
            "archive_inventory": inventory,
            "installed_projection": projection,
            "legacy_layout_fingerprint": fingerprint,
        }
    )


def versioned_legacy_manifest(version: str, payload: bytes, marker: str) -> dict:
    """Build a second valid legacy fingerprint for mixed-layout negatives."""

    manifest = copy.deepcopy(legacy_manifest(payload))
    manifest["bugate_version"] = version
    manifest["source_tag"] = f"v{version}"
    manifest["source_commit"] = ("1" if version.endswith(".1") else "2") * 40
    hook = next(
        item
        for item in manifest["installed_projection"]
        if item["scope"] == "shared_json_fragment"
    )
    hook["value"]["hooks"][0]["command"] += f" --legacy-{marker}"
    hook["semantic_digest"] = contract.semantic_digest(
        {"event": hook["event"], "value": hook["value"]}
    )
    block = next(
        item
        for item in manifest["installed_projection"]
        if item["scope"] == "marked_text_block"
    )
    block["content"] = block["content"].replace(
        block["end"], f"# legacy {marker}\n{block['end']}"
    )
    block["semantic_digest"] = contract.semantic_digest(
        {
            "begin": block["begin"],
            "end": block["end"],
            "content": block["content"],
        }
    )
    manifest["legacy_layout_fingerprint"] = contract.semantic_digest(
        {
            "installable_inventory": manifest["archive_inventory"],
            "installed_projection": manifest["installed_projection"],
        }
    )
    return contract.seal_document(manifest)


class UpdateEngineTests(unittest.TestCase):
    def test_legacy_copyfile_mode_policy_accepts_installer_preserved_mode(self) -> None:
        manifest = legacy_manifest(mode_policy=True)
        projection = engine.render_legacy_projection(manifest)
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            write(project / ".bugate/scripts/legacy.py", b"legacy\n")
            agent = next(
                item for item in projection if item["id"] == "agent:codex:legacy"
            )
            write(project / agent["target_path"], b"legacy\n", 0o600)
            hook = next(
                item for item in projection if item["scope"] == "shared_json_fragment"
            )
            write(
                project / hook["target_path"],
                (json.dumps({"hooks": {hook["event"]: [hook["value"]]}}) + "\n").encode(),
            )
            block = next(
                item for item in projection if item["scope"] == "marked_text_block"
            )
            write(project / block["target_path"], block["content"].encode())
            state = engine.detect_installed_state(project, legacy_manifests=[manifest])
            self.assertEqual((state.kind, state.go), ("legacy", True))
            adopted_agent = next(
                item for item in state.projection if item["id"] == "agent:codex:legacy"
            )
            self.assertEqual(adopted_agent["mode"], "0600")

    def test_locked_plan_accepts_exact_target_or_already_deleted_items(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)

            new_release, _new_manifest = release_tree(base, "0.4.3", b"new\n")
            os.chmod(new_release / "scripts/runtime.py", 0o755)
            new_manifest = contract.build_release_manifest(
                new_release, "0.4.3", updater_minimum_version="0.4.2"
            )

            # Simulate a previously interrupted/manual exact target placement:
            # target bytes + target mode are already present, while an obsolete
            # old-owned file is already absent. The verified old lock remains
            # the ownership baseline; neither item needs another operation.
            runtime = project / ".bugate/scripts/runtime.py"
            write(runtime, (new_release / "scripts/runtime.py").read_bytes(), 0o755)
            obsolete = project / ".bugate/docs/SETUP-OPTIONAL.md"
            obsolete.unlink()
            (new_release / "docs/SETUP-OPTIONAL.md").unlink()
            new_manifest = contract.build_release_manifest(
                new_release, "0.4.3", updater_minimum_version="0.4.2"
            )

            state = engine.detect_installed_state(project)
            self.assertEqual(state.kind, "locked")
            self.assertFalse(state.go)
            plan = engine.build_update_plan(
                project, ".bugate", new_manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "GO")
            self.assertNotIn("installed_state_conflict", plan["no_go_reasons"])
            by_id = {item["id"]: item for item in plan["managed_changes"]}
            self.assertEqual(
                by_id["vendor:scripts/runtime.py"]["classification"], "unchanged"
            )
            self.assertEqual(
                by_id["vendor:docs/SETUP-OPTIONAL.md"]["classification"],
                "unchanged",
            )
            operated = {item["id"] for item in plan["transaction_operations"]}
            self.assertNotIn("vendor:scripts/runtime.py", operated)
            self.assertNotIn("vendor:docs/SETUP-OPTIONAL.md", operated)
            engine.validate_plan_base(project, ".bugate", plan)

    def test_locked_plan_accepts_exact_target_symlink_and_shared_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            target_release, target_manifest = release_tree(base, "0.4.3", b"new\n")

            # Give the trusted old manifest distinct, still self-consistent
            # hook/block content so current target content exercises the dual
            # semantic baseline rather than an unchanged canonical fragment.
            modified_old = copy.deepcopy(old_manifest)
            old_hook = next(
                item
                for item in modified_old["installed_projection"]
                if item["scope"] == "shared_json_fragment"
            )
            old_hook["value"]["synthetic_old_marker"] = True
            old_hook["semantic_digest"] = contract.semantic_digest(
                {"event": old_hook["event"], "value": old_hook["value"]}
            )
            old_block = next(
                item
                for item in modified_old["installed_projection"]
                if item["scope"] == "marked_text_block"
            )
            old_block["content"] = old_block["content"].replace(
                old_block["end"], "# synthetic old line\n" + old_block["end"]
            )
            old_block["semantic_digest"] = contract.semantic_digest(
                {
                    "begin": old_block["begin"],
                    "end": old_block["end"],
                    "content": old_block["content"],
                }
            )
            modified_old = contract.seal_document(modified_old)

            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, modified_old)
            old_projection = contract.render_installed_projection(modified_old)
            target_projection = contract.render_installed_projection(target_manifest)
            for target_path in sorted(
                {
                    item["target_path"]
                    for item in target_projection
                    if item["scope"] == "shared_json_fragment"
                }
            ):
                current = project / target_path
                merged = engine.merge_hook_file(
                    current.read_bytes(),
                    prior_projection=old_projection,
                    new_projection=target_projection,
                    target_path=target_path,
                )
                write(current, merged.content)
            old_block_rendered = next(
                item for item in old_projection if item["scope"] == "marked_text_block"
            )
            new_block_rendered = next(
                item for item in target_projection if item["scope"] == "marked_text_block"
            )
            block_path = project / new_block_rendered["target_path"]
            write(
                block_path,
                engine.merge_marked_block(
                    block_path.read_bytes(),
                    prior_item=old_block_rendered,
                    new_item=new_block_rendered,
                ).content,
            )

            # A target release may also change a managed path's type. Replace a
            # regular old file with the exact verified target symlink.
            runtime_source = target_release / "scripts/runtime.py"
            runtime_source.unlink()
            runtime_source.symlink_to("bugate_update.py")
            target_manifest = contract.build_release_manifest(
                target_release, "0.4.3", updater_minimum_version="0.4.2"
            )
            self.assertEqual(
                next(
                    item["type"]
                    for item in target_manifest["installed_projection"]
                    if item["id"] == "vendor:scripts/runtime.py"
                ),
                "symlink",
            )
            runtime = project / ".bugate/scripts/runtime.py"
            runtime.unlink()
            runtime.symlink_to("bugate_update.py")

            plan = engine.build_update_plan(
                project, ".bugate", target_manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "GO")
            by_id = {item["id"]: item for item in plan["managed_changes"]}
            self.assertEqual(
                by_id["vendor:scripts/runtime.py"]["classification"], "unchanged"
            )
            self.assertTrue(
                all(
                    item["classification"] == "unchanged"
                    for item in plan["managed_changes"]
                    if item["scope"] in {"shared_json_fragment", "marked_text_block"}
                )
            )
            engine.validate_plan_base(project, ".bugate", plan)

    def test_locked_plan_still_rejects_content_matching_neither_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)
            _target_release, target_manifest = release_tree(base, "0.4.3", b"new\n")
            write(project / ".bugate/scripts/runtime.py", b"third-party drift\n")
            plan = engine.build_update_plan(
                project, ".bugate", target_manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "NO-GO")
            changed = next(
                item
                for item in plan["managed_changes"]
                if item["id"] == "vendor:scripts/runtime.py"
            )
            self.assertEqual(changed["classification"], "locally_modified")
            self.assertTrue(changed["blocking"])

    def test_strict_current_manifest_rejects_workspace_ownership_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            _release, manifest = release_tree(base, "0.4.2")
            poisoned = copy.deepcopy(manifest)
            poisoned["installed_projection"].append(
                {
                    "id": "workspace:forbidden",
                    "scope": "workspace",
                    "source_path": "scripts/runtime.py",
                    "target_path": "AGENTS.md",
                    "type": "file",
                    "mode": "0644",
                    "sha256": next(
                        item["sha256"]
                        for item in manifest["archive_inventory"]
                        if item["path"] == "scripts/runtime.py"
                    ),
                }
            )
            poisoned = contract.seal_document(poisoned)
            path = base / "manifest.json"
            write(path, contract.canonical_json_bytes(poisoned))
            with self.assertRaises(engine.UpdateEngineError):
                engine.load_release_manifest(path)

    def test_custom_vendor_legacy_render_and_exact_detection(self) -> None:
        manifest = legacy_manifest()
        projection = engine.render_legacy_projection(manifest, "vendor-kit")
        vendor_item = next(item for item in projection if item["scope"] == "vendor")
        hook = next(item for item in projection if item["scope"] == "shared_json_fragment")
        block = next(item for item in projection if item["scope"] == "marked_text_block")
        self.assertEqual(vendor_item["target_path"], "vendor-kit/scripts/legacy.py")
        self.assertIn("$ROOT/vendor-kit/scripts/", hook["value"]["hooks"][0]["command"])
        self.assertIn("/vendor-kit/plan.lock", block["content"])
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            project = base / "project"
            project.mkdir()
            write(project / "vendor-kit/scripts/legacy.py", b"legacy\n")
            write(
                project / ".codex/hooks.json",
                (json.dumps({"hooks": {"PreToolUse": [hook["value"]]}}, indent=2) + "\n").encode(),
            )
            write(project / ".gitignore", block["content"].encode())
            state = engine.detect_installed_state(project, "vendor-kit", [manifest])
            self.assertEqual((state.kind, state.version, state.go), ("legacy", "0.3.2", True))
            verified = engine.verify_installed(
                project, "vendor-kit", legacy_manifests=[manifest]
            )
            self.assertEqual(verified["decision"], "GO")
            self.assertEqual(verified["installed_kind"], "legacy")
            self.assertFalse(verified["lock_based"])
            write(project / "bugate.config.yaml", b"bugate:\n  version: '0.1'\n")
            write(project / "bugate.profile.yaml", b"role_governance:\n  mode: off\n")
            _release, target = release_tree(base, "0.4.2")
            plan = engine.build_update_plan(
                project,
                "vendor-kit",
                target,
                legacy_manifests=[manifest],
                updater_version="0.4.2",
            )
            self.assertEqual(plan["decision"], "GO")
            self.assertTrue(plan["rollback_available"])
            self.assertEqual(plan["from_state_manifest"]["source_tag"], "v0.3.2")
            self.assertEqual(
                plan["from_state_manifest"]["self_digest"], manifest["self_digest"]
            )
            self.assertEqual(plan["from_state_manifest"], manifest)
            self.assertNotIn(str(project), json.dumps(plan, ensure_ascii=False))
            self.assertIn("stale_managed_files", plan)
            self.assertIn("local_modifications", plan)
            write(project / "vendor-kit/scripts/legacy.py", b"local\n")
            state = engine.detect_installed_state(project, "vendor-kit", [manifest])
            self.assertEqual((state.kind, state.go), ("conflict", False))
            self.assertIn("expected", json.dumps(state.to_dict()))
            failed_verify = engine.verify_installed(
                project, "vendor-kit", legacy_manifests=[manifest]
            )
            self.assertEqual(failed_verify["decision"], "NO-GO")

    def test_unknown_and_mixed_legacy_layouts_fail_closed_without_writes(self) -> None:
        first = versioned_legacy_manifest("0.3.1", b"legacy-one\n", "one")
        second = versioned_legacy_manifest("0.3.2", b"legacy-two\n", "two")
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            _release, target_manifest = release_tree(base, "0.4.2")

            unknown = base / "unknown"
            unknown.mkdir()
            write(unknown / ".bugate/scripts/unrecognized.py", b"unknown\n")
            unknown_before = full_tree_snapshot(unknown)
            unknown_state = engine.detect_installed_state(
                unknown, legacy_manifests=[first, second]
            )
            self.assertEqual((unknown_state.kind, unknown_state.go), ("conflict", False))
            self.assertEqual(
                {item["candidate_version"] for item in unknown_state.diagnostics},
                {"0.3.1", "0.3.2"},
            )
            unknown_plan = engine.build_update_plan(
                unknown,
                ".bugate",
                target_manifest,
                legacy_manifests=[first, second],
                updater_version="0.4.2",
            )
            self.assertEqual(unknown_plan["decision"], "NO-GO")
            self.assertIn("installed_state_conflict", unknown_plan["no_go_reasons"])
            self.assertFalse((unknown / ".bugate-update").exists())
            self.assertEqual(full_tree_snapshot(unknown), unknown_before)

            mixed = base / "mixed"
            mixed.mkdir()
            first_projection = engine.render_legacy_projection(first)
            second_projection = engine.render_legacy_projection(second)
            first_file = next(
                item for item in first_projection if item["scope"] == "vendor"
            )
            write(mixed / first_file["target_path"], b"legacy-one\n")
            second_hook = next(
                item
                for item in second_projection
                if item["scope"] == "shared_json_fragment"
            )
            write(
                mixed / second_hook["target_path"],
                (
                    json.dumps(
                        {"hooks": {second_hook["event"]: [second_hook["value"]]}},
                        indent=2,
                    )
                    + "\n"
                ).encode(),
            )
            second_block = next(
                item
                for item in second_projection
                if item["scope"] == "marked_text_block"
            )
            write(mixed / second_block["target_path"], second_block["content"].encode())
            mixed_before = full_tree_snapshot(mixed)
            mixed_state = engine.detect_installed_state(
                mixed, legacy_manifests=[first, second]
            )
            self.assertEqual((mixed_state.kind, mixed_state.go), ("conflict", False))
            diagnostics = {item["candidate_version"]: item for item in mixed_state.diagnostics}
            self.assertEqual(set(diagnostics), {"0.3.1", "0.3.2"})
            self.assertGreater(diagnostics["0.3.1"]["mismatch_count"], 0)
            self.assertGreater(diagnostics["0.3.2"]["mismatch_count"], 0)
            mixed_plan = engine.build_update_plan(
                mixed,
                ".bugate",
                target_manifest,
                legacy_manifests=[first, second],
                updater_version="0.4.2",
            )
            self.assertEqual(mixed_plan["decision"], "NO-GO")
            self.assertIn("installed_state_conflict", mixed_plan["no_go_reasons"])
            self.assertFalse((mixed / ".bugate-update").exists())
            self.assertEqual(full_tree_snapshot(mixed), mixed_before)

    def test_plan_classifies_every_managed_change_shape_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            write(old_release / "scripts/update-shape.py", b"old update\n")
            write(old_release / "scripts/type-shape.py", b"old type\n")
            old_manifest = contract.build_release_manifest(
                old_release, "0.4.2", updater_minimum_version="0.4.2"
            )
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)

            new_release, _ = release_tree(base, "0.4.3", b"old\n")
            write(new_release / "scripts/new-managed.py", b"added\n")
            (new_release / "docs/SETUP-OPTIONAL.md").unlink()
            write(new_release / "scripts/update-shape.py", b"new update\n")
            (new_release / "scripts/type-shape.py").symlink_to("bugate_update.py")
            os.chmod(new_release / "scripts/runtime.py", 0o755)
            new_manifest = contract.build_release_manifest(
                new_release, "0.4.3", updater_minimum_version="0.4.2"
            )
            plan = engine.build_update_plan(
                project, ".bugate", new_manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "GO")
            by_id = {item["id"]: item for item in plan["managed_changes"]}
            expected = {
                "vendor:scripts/new-managed.py": "add",
                "vendor:docs/SETUP-OPTIONAL.md": "delete",
                "vendor:scripts/update-shape.py": "update",
                "vendor:scripts/runtime.py": "permission_changed",
                "vendor:scripts/type-shape.py": "type_changed",
            }
            self.assertEqual(
                {item_id: by_id[item_id]["classification"] for item_id in expected},
                expected,
            )
            self.assertEqual(
                set(plan["stale_managed_files"]),
                {".bugate/docs", ".bugate/docs/SETUP-OPTIONAL.md"},
            )
            operated = {item["id"] for item in plan["transaction_operations"]}
            self.assertTrue(set(expected).issubset(operated))
            engine.validate_plan_base(project, ".bugate", plan)

    def test_locked_verify_same_version_plan_is_zero_write_and_preserves_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            lock = materialize_install(project, release, manifest)
            before = {
                path.relative_to(project).as_posix(): (
                    os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
                )
                for path in project.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            plan = engine.build_update_plan(
                project,
                ".bugate",
                manifest,
                updater_version="0.4.2",
            )
            after = {
                path.relative_to(project).as_posix(): (
                    os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
                )
                for path in project.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(before, after)
            self.assertEqual(plan["decision"], "GO")
            self.assertTrue(plan["no_op"])
            self.assertTrue(plan["preserve_installed_lock"])
            self.assertEqual(plan["installed_lock_candidate"], lock)
            self.assertEqual(plan["from_state_manifest"], manifest)
            engine.validate_plan_base(project, ".bugate", plan)
            verified = engine.verify_installed(project)
            self.assertEqual(verified["decision"], "GO")
            self.assertEqual(verified["installed_kind"], "locked")
            self.assertTrue(verified["lock_based"])

    def test_update_plan_is_deterministic_and_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)
            _new_release, new_manifest = release_tree(base, "0.4.3", b"new\n")
            first = engine.build_update_plan(
                project, ".bugate", new_manifest, updater_version="0.4.2"
            )
            second = engine.build_update_plan(
                project, ".bugate", new_manifest, updater_version="0.4.2"
            )
            self.assertEqual(first, second)
            self.assertEqual(first["decision"], "GO")
            self.assertEqual(
                first["installed_lock_candidate"]["updater_version"], "0.4.3"
            )
            self.assertTrue(any(item["classification"] == "update" for item in first["managed_changes"]))
            write(project / ".bugate/scripts/runtime.py", b"drift\n")
            with self.assertRaisesRegex(engine.UpdateEngineError, "drift"):
                engine.validate_plan_base(project, ".bugate", first)

    def test_launcher_below_target_manifest_minimum_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)
            _new_release, new_manifest = release_tree(
                base,
                "0.4.3",
                b"new\n",
                updater_minimum_version="0.4.3",
            )
            with self.assertRaisesRegex(engine.UpdateEngineError, "below required minimum"):
                engine.build_update_plan(
                    project,
                    ".bugate",
                    new_manifest,
                    updater_version="0.4.2",
                )

    def test_profile_migration_required_blocks_plan_without_writing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            profile_before = (project / "bugate.profile.yaml").read_bytes()
            write(project / "bugate.config.yaml", b"bugate:\n  version: invalid\n")
            plan = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "NO-GO")
            self.assertEqual(plan["profile_compatibility"]["status"], "migration_required")
            self.assertEqual((project / "bugate.profile.yaml").read_bytes(), profile_before)
            self.assertNotIn(str(project), json.dumps(plan, ensure_ascii=False))

    def test_profile_selector_normalization_and_fail_closed_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            profiles = project / "profiles"
            profiles.mkdir()
            profile = profiles / "legacy.yaml"
            profile.write_bytes((project / "bugate.profile.yaml").read_bytes())
            (project / "bugate.profile.yaml").unlink()

            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\nprofile: ./profiles/legacy.yaml\n",
            )
            profile_before = profile.read_bytes()
            compatible = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(compatible["decision"], "GO")
            self.assertEqual(compatible["migration_status"], "migration_available")
            self.assertEqual(profile.read_bytes(), profile_before)

            write(
                project / "bugate.config.yaml",
                (
                    "bugate:\n  version: '0.1'\nprofile: '"
                    + profile.resolve().as_posix()
                    + "'\n"
                ).encode(),
            )
            absolute_in_root = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(absolute_in_root["decision"], "GO")
            self.assertEqual(
                absolute_in_root["profile_base_observations"][1]["target_path"],
                "profiles/legacy.yaml",
            )
            self.assertNotIn(
                str(project), json.dumps(absolute_in_root, ensure_ascii=False)
            )

            unsafe_selectors = (
                "profiles/../profiles/legacy.yaml",
                (base / "outside-profile.yaml").as_posix(),
                " profiles/legacy.yaml ",
            )
            for selector in unsafe_selectors:
                with self.subTest(selector=selector):
                    write(
                        project / "bugate.config.yaml",
                        f"bugate:\n  version: '0.1'\nprofile: '{selector}'\n".encode(),
                    )
                    blocked = engine.build_update_plan(
                        project, ".bugate", manifest, updater_version="0.4.2"
                    )
                    self.assertEqual(blocked["decision"], "NO-GO")
                    self.assertEqual(
                        blocked["profile_compatibility"]["status"],
                        "migration_required",
                    )
                    rendered = json.dumps(blocked["profile_compatibility"])
                    self.assertNotIn(str(project), rendered)
                    self.assertNotIn(selector, rendered)

            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\nprofile: profiles/legacy.yaml\n",
            )
            profile.write_text(
                "role_governance:\n  mode:\n    - required\n",
                encoding="utf-8",
            )
            malformed = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(malformed["decision"], "NO-GO")
            self.assertEqual(
                malformed["profile_compatibility"]["reason"],
                "role governance configuration is malformed",
            )
            self.assertNotIn("required", malformed["profile_compatibility"]["reason"])

            profile.unlink()
            missing = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(missing["decision"], "NO-GO")
            self.assertEqual(
                missing["profile_compatibility"]["reason"],
                "selected profile is missing",
            )
            backing = profiles / "backing.yaml"
            write(backing, b"role_governance:\n  mode: off\n")
            profile.symlink_to("backing.yaml")
            linked = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(linked["decision"], "NO-GO")
            self.assertEqual(
                linked["profile_compatibility"]["reason"],
                "selected profile must be a regular file",
            )

    def test_profile_selector_parent_symlink_exchange_is_pinned_and_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            profiles = project / "profiles"
            profiles.mkdir()
            selected = profiles / "legacy.yaml"
            write(
                selected,
                b"version: '9.9'\nrole_governance:\n  mode: required\n",
            )
            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\nprofile: profiles/legacy.yaml\n",
            )
            external = base / "external-profiles"
            external.mkdir()
            write(
                external / "legacy.yaml",
                b"role_governance:\n  mode: off\nexternal_marker: never-read\n",
            )
            before = {
                path.relative_to(project).as_posix(): (
                    os.readlink(path).encode()
                    if path.is_symlink()
                    else path.read_bytes()
                )
                for path in project.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            displaced = base / "displaced-profiles"
            real_open = os.open
            exchanged = False

            def exchange_before_profile_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal exchanged
                spelling = os.fsdecode(path)
                if not exchanged and (
                    spelling == "profiles" or Path(spelling) == selected
                ):
                    os.rename(profiles, displaced)
                    profiles.symlink_to(external, target_is_directory=True)
                    exchanged = True
                return real_open(path, flags, mode, dir_fd=dir_fd)

            try:
                with mock.patch.object(
                    engine.os, "open", side_effect=exchange_before_profile_open
                ):
                    plan = engine.build_update_plan(
                        project, ".bugate", manifest, updater_version="0.4.2"
                    )
            finally:
                if profiles.is_symlink():
                    profiles.unlink()
                if displaced.exists():
                    os.rename(displaced, profiles)

            self.assertTrue(exchanged)
            self.assertEqual(plan["decision"], "NO-GO")
            self.assertEqual(
                plan["profile_compatibility"]["status"], "migration_required"
            )
            self.assertIn("migration_required", plan["no_go_reasons"])
            rendered = json.dumps(plan, ensure_ascii=False)
            self.assertNotIn("never-read", rendered)
            self.assertNotIn(str(external), rendered)
            self.assertFalse((project / ".bugate-update").exists())
            after = {
                path.relative_to(project).as_posix(): (
                    os.readlink(path).encode()
                    if path.is_symlink()
                    else path.read_bytes()
                )
                for path in project.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(before, after)

    def test_profile_bindings_are_revalidated_after_parent_and_leaf_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            profiles = project / "profiles"
            profiles.mkdir()
            selected = profiles / "legacy.yaml"
            write(selected, b"version: '0.1'\nrole_governance:\n  mode: off\n")
            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\nprofile: profiles/legacy.yaml\n",
            )
            baseline = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(baseline["decision"], "GO")
            real_open = os.open

            def parent_exchange(*, symlink: bool, planning: bool) -> None:
                replacement = base / (
                    "replacement-symlink" if symlink else "replacement-physical"
                )
                replacement.mkdir()
                write(
                    replacement / "legacy.yaml",
                    b"version: '9.9'\nrole_governance:\n  mode: required\n",
                )
                displaced = base / (
                    "displaced-symlink" if symlink else "displaced-physical"
                )
                exchanged = False

                def after_parent_open(
                    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    nonlocal exchanged
                    descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                    if not exchanged and os.fsdecode(path) == "profiles":
                        exchanged = True
                        os.rename(profiles, displaced)
                        if symlink:
                            profiles.symlink_to(replacement, target_is_directory=True)
                        else:
                            os.rename(replacement, profiles)
                    return descriptor

                try:
                    with mock.patch.object(
                        engine.os, "open", side_effect=after_parent_open
                    ):
                        if planning:
                            result = engine.build_update_plan(
                                project,
                                ".bugate",
                                manifest,
                                updater_version="0.4.2",
                            )
                            self.assertEqual(result["decision"], "NO-GO")
                            self.assertEqual(
                                result["profile_compatibility"]["status"],
                                "migration_required",
                            )
                        else:
                            with self.assertRaisesRegex(
                                engine.UpdateEngineError,
                                "profile/config base drift",
                            ):
                                engine.validate_plan_base(
                                    project, ".bugate", baseline
                                )
                finally:
                    if profiles.is_symlink():
                        profiles.unlink()
                    elif profiles.exists() and profiles != displaced:
                        os.rename(profiles, replacement)
                    if displaced.exists():
                        os.rename(displaced, profiles)
                    if replacement.exists():
                        (replacement / "legacy.yaml").unlink()
                        replacement.rmdir()
                self.assertTrue(exchanged)

            parent_exchange(symlink=False, planning=True)
            parent_exchange(symlink=False, planning=False)
            parent_exchange(symlink=True, planning=False)

            for leaf in (project / "bugate.config.yaml", selected):
                with self.subTest(leaf=leaf.name):
                    displaced_leaf = base / f"displaced-{leaf.name}"
                    exchanged = False

                    def after_leaf_open(
                        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> int:
                        nonlocal exchanged
                        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                        if not exchanged and os.fsdecode(path) == leaf.name:
                            exchanged = True
                            os.rename(leaf, displaced_leaf)
                            write(leaf, b"bugate:\n  version: '9.9'\n")
                        return descriptor

                    try:
                        with mock.patch.object(
                            engine.os, "open", side_effect=after_leaf_open
                        ), self.assertRaisesRegex(
                            engine.UpdateEngineError,
                            "profile/config base drift",
                        ):
                            engine.validate_plan_base(project, ".bugate", baseline)
                    finally:
                        if leaf.exists():
                            leaf.unlink()
                        if displaced_leaf.exists():
                            os.rename(displaced_leaf, leaf)
                    self.assertTrue(exchanged)

            for leaf in (project / "bugate.config.yaml", selected):
                with self.subTest(inplace=leaf.name):
                    original = leaf.read_bytes()
                    mutated = False
                    real_revalidate = engine._revalidate_regular_beneath_binding

                    def mutate_before_revalidate(*args: Any, **kwargs: Any) -> None:
                        nonlocal mutated
                        parts = args[1]
                        if not mutated and parts[-1] == leaf.name:
                            mutated = True
                            write(leaf, b"bugate:\n  version: '9.9'\n")
                        return real_revalidate(*args, **kwargs)

                    try:
                        with mock.patch.object(
                            engine,
                            "_revalidate_regular_beneath_binding",
                            side_effect=mutate_before_revalidate,
                        ), self.assertRaisesRegex(
                            engine.UpdateEngineError,
                            "profile/config base drift",
                        ):
                            engine.validate_plan_base(project, ".bugate", baseline)
                    finally:
                        write(leaf, original)
                    self.assertTrue(mutated)

    def test_profile_inputs_reject_fifo_without_reading_unused_profile(self) -> None:
        for selected_input in ("config", "profile"):
            with self.subTest(selected_input=selected_input), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                release, manifest = release_tree(base, "0.4.2")
                project = base / "project"
                project.mkdir()
                materialize_install(project, release, manifest)
                if selected_input == "config":
                    target = project / "bugate.config.yaml"
                else:
                    write(
                        project / "bugate.config.yaml",
                        b"bugate:\n  version: '0.1'\nprofile: bugate.profile.yaml\n",
                    )
                    target = project / "bugate.profile.yaml"
                target.unlink()
                os.mkfifo(target, 0o600)

                plan = engine.build_update_plan(
                    project, ".bugate", manifest, updater_version="0.4.2"
                )

                self.assertEqual(plan["decision"], "NO-GO")
                self.assertEqual(
                    plan["profile_compatibility"]["status"],
                    "migration_required",
                )
                self.assertTrue(stat.S_ISFIFO(os.lstat(target).st_mode))

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\n",
            )
            unused = project / "bugate.profile.yaml"
            unused.unlink()
            os.mkfifo(unused, 0o600)
            plan = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "GO")
            self.assertEqual(
                [item["target_path"] for item in plan["profile_base_observations"]],
                ["bugate.config.yaml"],
            )

    def test_status_absent_is_no_go_and_locked_fields_are_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            absent = base / "absent"
            absent.mkdir()
            status = engine.get_status(absent, "vendor-kit")
            self.assertEqual(status["kind"], "absent")
            self.assertEqual(status["vendor_dir"], "vendor-kit")
            self.assertEqual(status["decision"], "NO-GO")
            self.assertEqual(
                status["no_go_reasons"], ["existing_installation_not_found"]
            )

            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            locked = engine.get_status(project)
            self.assertEqual(locked["kind"], "locked")
            self.assertEqual(locked["version"], "0.4.2")
            self.assertEqual(locked["vendor_dir"], ".bugate")
            self.assertEqual(locked["decision"], "GO")

    def test_profile_compatibility_uses_the_merged_runtime_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\n"
                b"profile: bugate.profile.yaml\n"
                b"role_governance:\n  mode: required\n",
            )
            write(project / "bugate.profile.yaml", b"memory:\n  namespace: project:synthetic\n")
            required = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(required["decision"], "GO")
            self.assertEqual(required["migration_status"], "compatible")
            self.assertTrue(
                required["profile_compatibility"]["role_governance_activated"]
            )
            write(
                project / "bugate.profile.yaml",
                b"memory:\n  namespace: project:changed\n",
            )
            with self.assertRaisesRegex(
                engine.UpdateEngineError, "profile/config base drift"
            ):
                engine.validate_plan_base(project, ".bugate", required)

            write(
                project / "bugate.profile.yaml",
                b"version: '9.9'\nrole_governance:\n  mode: required\n",
            )
            incompatible = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(incompatible["decision"], "NO-GO")
            self.assertEqual(
                incompatible["profile_compatibility"]["reason"],
                "profile schema is outside the release compatibility range",
            )

            write(
                project / "bugate.config.yaml",
                b"bugate:\n  version: '0.1'\n"
                b"profile: bugate.profile.yaml\n"
                b"guarded_path_regex: '[unterminated'\n",
            )
            write(project / "bugate.profile.yaml", b"role_governance:\n  mode: required\n")
            malformed = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(malformed["decision"], "NO-GO")
            self.assertEqual(
                malformed["profile_compatibility"]["reason"],
                "role governance configuration is malformed",
            )

    def test_unselected_profile_file_does_not_affect_runtime_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            write(project / "bugate.config.yaml", b"bugate:\n  version: '0.1'\n")
            write(
                project / "bugate.profile.yaml",
                b"version: '9.9'\nrole_governance:\n  mode: required\n",
            )
            plan = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "GO")
            self.assertEqual(plan["migration_status"], "migration_available")
            self.assertNotEqual(
                plan["profile_compatibility"]["status"], "migration_required"
            )
            self.assertFalse(
                plan["profile_compatibility"]["role_governance_activated"]
            )
            observed_paths = {
                item["target_path"]
                for item in plan["profile_compatibility"]["base_observations"]
            }
            self.assertEqual(observed_paths, {"bugate.config.yaml"})

    def test_hook_read_failure_is_returned_as_structured_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            hook = Path(raw) / "hooks.json"
            write(hook, b'{"hooks": {}}\n')
            with mock.patch.object(
                engine.os,
                "read",
                side_effect=PermissionError("synthetic unreadable hook"),
            ):
                document, error, digest, mode = engine._load_hook_document(
                    hook.parent, hook.name
                )
            self.assertIsNone(document)
            self.assertIn("unavailable or unsafe", error)
            self.assertIsNone(digest)
            self.assertIsNone(mode)

    def test_full_plan_structures_unsafe_hook_containers_as_no_go(self) -> None:
        for case in (
            "nonregular",
            "fifo",
            "mode-000-unreadable",
            "invalid-json",
            "nonfinite-json",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                release, manifest = release_tree(base, "0.4.2")
                project = base / "project"
                project.mkdir()
                materialize_install(project, release, manifest)
                hook = project / ".codex/hooks.json"
                original = hook.read_bytes()
                read_patch = None
                if case == "nonregular":
                    hook.unlink()
                    hook.mkdir()
                    write(hook / "sut-owned.txt", b"preserve\n")
                elif case == "fifo":
                    hook.unlink()
                    os.mkfifo(hook, 0o600)
                elif case == "mode-000-unreadable":
                    os.chmod(hook, 0)
                    real_read = engine._read_regular_image

                    def unreadable(path: Path, *, label: str) -> tuple[bytes, str]:
                        if Path(path) == hook:
                            raise engine.UpdateEngineError(
                                "shared hook file is unavailable or unsafe"
                            )
                        return real_read(path, label=label)

                    read_patch = mock.patch.object(
                        engine, "_read_regular_image", side_effect=unreadable
                    )
                elif case == "invalid-json":
                    write(hook, b'{"hooks": {"PreToolUse": [}\n')
                else:
                    write(
                        hook,
                        b'{"synthetic_nonfinite": NaN, "hooks": {}}\n',
                    )

                context = read_patch if read_patch is not None else mock.patch.object(
                    engine, "_read_regular_image", wraps=engine._read_regular_image
                )
                with context:
                    plan = engine.build_update_plan(
                        project, ".bugate", manifest, updater_version="0.4.2"
                    )

                self.assertEqual(plan["decision"], "NO-GO")
                self.assertIn("hook_ownership_conflict", plan["no_go_reasons"])
                self.assertTrue(
                    any(
                        item.get("target_path") == ".codex/hooks.json"
                        and item.get("conflict")
                        for item in plan["hook_changes"]
                    )
                )
                json.dumps(plan, ensure_ascii=False)
                self.assertFalse((project / ".bugate-update").exists())
                if case == "nonregular":
                    self.assertEqual(
                        (hook / "sut-owned.txt").read_bytes(), b"preserve\n"
                    )
                elif case == "fifo":
                    self.assertTrue(stat.S_ISFIFO(os.lstat(hook).st_mode))
                elif case == "mode-000-unreadable":
                    os.chmod(hook, 0o644)
                    self.assertEqual(hook.read_bytes(), original)
                elif case == "invalid-json":
                    self.assertEqual(
                        hook.read_bytes(), b'{"hooks": {"PreToolUse": [}\n'
                    )
                else:
                    self.assertIn(b"NaN", hook.read_bytes())

    def test_hook_merge_preserves_sut_entries_and_rejects_spoof_or_mixed(self) -> None:
        old = contract._hook_projection(".bugate")
        new = contract._hook_projection("vendor-kit")
        target = ".codex/hooks.json"
        old_items = [item for item in old if item["target_path"] == target]
        sut = {"matcher": "shell", "hooks": [{"type": "command", "command": "./sut-check"}]}
        document = {"custom": {"kept": True}, "hooks": {}}
        for item in old_items:
            document["hooks"].setdefault(item["event"], []).append(item["value"])
        document["hooks"]["PreToolUse"].insert(0, sut)
        raw = (json.dumps(document, indent=4) + "\n").replace(
            '"custom": {', '"custom" : {'
        ).replace('"matcher": "shell"', '"matcher" : "shell"').encode()
        merged = engine.merge_hook_file(
            raw, prior_projection=old, new_projection=new, target_path=target
        )
        value = json.loads(merged.content)
        self.assertEqual(value["custom"], {"kept": True})
        self.assertIn(sut, value["hooks"]["PreToolUse"])
        self.assertIn(b'"custom" : {', merged.content)
        self.assertIn(b'"matcher" : "shell"', merged.content)
        target_order = copy.deepcopy(document)
        target_order["hooks"]["PreToolUse"].append(
            target_order["hooks"]["PreToolUse"].pop(0)
        )
        target_order_bytes = json.dumps(target_order, indent=2).encode()
        already_target = engine.merge_hook_file(
            target_order_bytes,
            prior_projection=old,
            new_projection=old,
            target_path=target,
        )
        self.assertEqual(already_target.content, target_order_bytes)
        self.assertFalse(already_target.changed)
        spoof = copy.deepcopy(document)
        command = spoof["hooks"]["PreToolUse"][1]["hooks"][0]["command"]
        spoof["hooks"]["PreToolUse"][1]["hooks"][0]["command"] = command + " --sut-mixed"
        with self.assertRaises(engine.OwnershipConflict):
            engine.merge_hook_file(
                json.dumps(spoof).encode(),
                prior_projection=old,
                new_projection=new,
                target_path=target,
            )
        missing_old = {"hooks": {item["event"]: [] for item in old_items}}
        already_deleted = engine.merge_hook_file(
            json.dumps(missing_old).encode(),
            prior_projection=old,
            new_projection=[],
            target_path=target,
        )
        self.assertEqual(json.loads(already_deleted.content), missing_old)
        self.assertFalse(already_deleted.changed)
        event_absent = {"hooks": {"SutEvent": [sut]}}
        absent_result = engine.merge_hook_file(
            json.dumps(event_absent).encode(),
            prior_projection=old,
            new_projection=[],
            target_path=target,
        )
        self.assertEqual(json.loads(absent_result.content), event_absent)
        self.assertFalse(absent_result.changed)
        no_hooks_key = {"custom": {"kept": True}}
        no_hooks_result = engine.merge_hook_file(
            json.dumps(no_hooks_key).encode(),
            prior_projection=old,
            new_projection=[],
            target_path=target,
        )
        self.assertEqual(json.loads(no_hooks_result.content), no_hooks_key)
        self.assertFalse(no_hooks_result.changed)

    def test_locked_plan_accepts_absent_hook_container_when_target_owns_none(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)
            _target_release, target_manifest = release_tree(base, "0.4.3", b"new\n")
            target_manifest = copy.deepcopy(target_manifest)
            target_manifest["installed_projection"] = [
                item
                for item in target_manifest["installed_projection"]
                if not (
                    item.get("scope") == "shared_json_fragment"
                    and item.get("target_path") == ".codex/hooks.json"
                )
            ]
            target_manifest = contract.seal_document(target_manifest)
            (project / ".codex/hooks.json").unlink()

            with mock.patch.object(
                contract,
                "validate_current_release_manifest",
                side_effect=lambda value: contract.validate_release_manifest(value),
            ):
                plan = engine.build_update_plan(
                    project,
                    ".bugate",
                    target_manifest,
                    updater_version="0.4.2",
                )
            self.assertEqual(plan["decision"], "GO")
            codex = next(
                item
                for item in plan["hook_changes"]
                if item["target_path"] == ".codex/hooks.json"
            )
            self.assertFalse(codex["changed"])
            self.assertIsNone(codex["before_sha256"])
            self.assertIsNone(codex["after_sha256"])
            self.assertFalse(plan["codex_hook_hash_changed"])

    def test_known_hook_identity_is_rejected_across_event_and_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            lock = materialize_install(project, release, manifest)
            projection = lock["installed_projection"]
            foreign = copy.deepcopy(
                next(
                    item["value"]
                    for item in projection
                    if item.get("scope") == "shared_json_fragment"
                    and item.get("target_path") == ".claude/settings.json"
                )
            )
            codex_path = project / ".codex/hooks.json"
            document = json.loads(codex_path.read_bytes())
            document["hooks"]["SyntheticWrongEvent"] = [foreign]
            write(codex_path, (json.dumps(document, indent=2) + "\n").encode())

            state = engine.detect_installed_state(project)
            self.assertEqual(state.kind, "locked")
            self.assertFalse(state.go)
            self.assertIn("unowned shape", json.dumps(state.diagnostics))
            verified = engine.verify_installed(project)
            self.assertEqual(verified["decision"], "NO-GO")
            plan = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            self.assertEqual(plan["decision"], "NO-GO")
            self.assertIn("hook_ownership_conflict", plan["no_go_reasons"])

    def test_marked_block_merge_is_surgical(self) -> None:
        old_content = "# begin\nold\n# end\n"
        new_content = "# begin\nnew\n# end\n"
        old = {
            "id": "block", "scope": "marked_text_block", "target_path": ".gitignore",
            "type": "text_fragment", "begin": "# begin", "end": "# end", "content": old_content,
            "semantic_digest": contract.semantic_digest({"begin": "# begin", "end": "# end", "content": old_content}),
        }
        new = {**old, "content": new_content, "semantic_digest": contract.semantic_digest({"begin": "# begin", "end": "# end", "content": new_content})}
        raw = b"sut-before\n" + old_content.encode() + b"sut-after\n"
        result = engine.merge_marked_block(raw, prior_item=old, new_item=new)
        self.assertEqual(result.content, b"sut-before\n" + new_content.encode() + b"sut-after\n")
        renamed_content = "# new begin\nnew\n# new end\n"
        renamed = {
            **new,
            "begin": "# new begin",
            "end": "# new end",
            "content": renamed_content,
            "semantic_digest": contract.semantic_digest(
                {
                    "begin": "# new begin",
                    "end": "# new end",
                    "content": renamed_content,
                }
            ),
        }
        renamed_result = engine.merge_marked_block(
            raw, prior_item=old, new_item=renamed
        )
        self.assertEqual(
            renamed_result.content,
            b"sut-before\n" + renamed_content.encode() + b"sut-after\n",
        )

    def test_shared_materialization_rejects_post_validation_container_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            old = contract._hook_projection(".bugate")
            new = contract._hook_projection("vendor-kit")
            target = ".codex/hooks.json"
            old_items = [item for item in old if item["target_path"] == target]
            new_items = [item for item in new if item["target_path"] == target]
            initial = engine.merge_hook_file(
                None,
                prior_projection=[],
                new_projection=old_items,
                target_path=target,
            ).content
            write(project / target, initial)
            observations = engine.observe_projection(project, old_items)
            changes = [
                {
                    "id": old_item["id"],
                    "scope": "shared_json_fragment",
                    "target_path": target,
                    "classification": "hook_refresh",
                    "base": observation,
                    "old": old_item,
                    "new": next(
                        item for item in new_items if item["id"] == old_item["id"]
                    ),
                }
                for old_item, observation in zip(old_items, observations, strict=True)
            ]
            plan = {"managed_changes": changes}
            document = json.loads(initial)
            document["sut"] = {"changed": True}
            write(project / target, json.dumps(document).encode())
            with self.assertRaisesRegex(
                engine.OwnershipConflict, "shared container changed"
            ):
                engine.materialize_shared_outputs(project, plan)

    def test_plan_base_rejects_workspace_root_inode_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            release, manifest = release_tree(base, "0.4.2")
            project = base / "project"
            project.mkdir()
            materialize_install(project, release, manifest)
            plan = engine.build_update_plan(
                project, ".bugate", manifest, updater_version="0.4.2"
            )
            displaced = base / "displaced-project"
            os.rename(project, displaced)
            shutil.copytree(displaced, project, symlinks=True)
            with self.assertRaisesRegex(
                engine.UpdateEngineError, "workspace root identity drift"
            ):
                engine.validate_plan_base(project, ".bugate", plan)

    def test_non_directory_ancestor_is_reported_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            write(project / ".bugate", b"not-a-directory\n")
            item = {
                "id": "vendor:scripts/a.py", "scope": "vendor",
                "source_path": "scripts/a.py", "target_path": ".bugate/scripts/a.py",
                "type": "file", "mode": "0644", "sha256": "0" * 64,
            }
            observed = engine.observe_projection(project, [item])
            self.assertEqual(observed[0]["status"], "conflict")
            self.assertIn("not a directory", observed[0]["error"])

    def test_error_alias_and_missing_legacy_root_are_explicit(self) -> None:
        self.assertIs(engine.UpdateError, engine.UpdateEngineError)
        with self.assertRaisesRegex(engine.UpdateEngineError, "release_root"):
            engine.load_legacy_manifests(None)

    def test_transaction_material_has_physical_images_and_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            old_release, old_manifest = release_tree(base, "0.4.2", b"old\n")
            project = base / "project"
            project.mkdir()
            materialize_install(project, old_release, old_manifest)
            new_release, new_manifest = release_tree(base, "0.4.3", b"new\n")
            plan = engine.build_update_plan(
                project, ".bugate", new_manifest, updater_version="0.4.2"
            )
            outputs = engine.materialize_shared_outputs(project, plan)
            material = engine.transaction_material(
                plan, new_release, shared_outputs=outputs
            )
            self.assertTrue(material["operations"])
            self.assertTrue(all(set(item) == {"id", "target_path", "pre", "post"} for item in material["operations"]))
            manifest_id = "metadata:installed-release-manifest"
            lock_id = "metadata:installed-lock"
            self.assertEqual(
                material["payload_bytes"][manifest_id],
                contract.canonical_json_bytes(new_manifest),
            )
            self.assertEqual(
                material["payload_bytes"][lock_id],
                contract.installed_lock_bytes(plan["installed_lock_candidate"]),
            )


if __name__ == "__main__":
    unittest.main()
