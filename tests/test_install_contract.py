#!/usr/bin/env python3
"""Strict unit and negative tests for the imported-install data contract.

All release roots in this module are synthetic and are created at runtime.  No
imported SUT repository, profile, hook file, test, or business artifact is read.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402


def _write(root: Path, relative: str, text: str, mode: int = 0o644) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _physical_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    elif isinstance(value, str):
        yield value


class InstallContractTests(unittest.TestCase):
    """Exercise the canonical manifest and installed-lock boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="bugate-install-contract-core-"
        )
        cls.source_root = Path(cls._temporary.name)

        # Minimal but structurally complete BUGate Core release surface.
        for tree in contract.VENDOR_TREE_ROOTS:
            (cls.source_root / tree).mkdir(parents=True, exist_ok=True)
        for name in (
            "bugate_install_contract.py",
            "bugate_update.py",
            "bugate_update_transaction.py",
            "bugate_update_engine.py",
            "bugate_update_source.py",
            "bugate_legacy_manifest.py",
            "bugate_core.py",
            "check_bugate.py",
            "check_plan_lock.py",
            "check_role_evidence.py",
            "check_agent_role_paths.py",
            "bugate_prompt_reminder.py",
            "memory_bus.py",
        ):
            _write(cls.source_root, f"scripts/{name}", f"# synthetic {name}\n")
        _write(
            cls.source_root,
            "bin/bugate-update",
            "#!/bin/sh\nexec python3 \"$(dirname \"$0\")/../scripts/bugate_update.py\" \"$@\"\n",
            0o755,
        )
        _write(cls.source_root, "bin/bugate-role", "#!/bin/sh\nexit 0\n", 0o755)
        for skill in contract.SKILL_NAMES:
            _write(
                cls.source_root,
                f".shared/skills/{skill}/SKILL.md",
                f"# synthetic {skill}\n",
            )
        for name in contract.CODEX_GATE_AGENT_NAMES:
            _write(
                cls.source_root,
                f"{contract.CODEX_GATE_AGENT_SOURCE_DIR}/{name}",
                f"name = \"synthetic-{name}\"\n",
            )
        for relative in contract.VENDOR_SINGLE_FILES:
            _write(cls.source_root, relative, "# synthetic setup\n")

        # Exercise source symlink handling without leaving the synthetic root.
        (cls.source_root / "scripts/runtime-link").symlink_to("check_bugate.py")
        _write(
            cls.source_root,
            ".codex-plugin/plugin.json",
            json.dumps({"name": "bugate", "version": "0.4.2"}) + "\n",
        )
        _write(
            cls.source_root,
            ".claude-plugin/plugin.json",
            json.dumps({"name": "bugate", "version": "0.4.2"}) + "\n",
        )
        _write(cls.source_root, "bugate.config.yaml", "bugate:\n  version: '0.1'\n")
        _write(cls.source_root, "README.md", "# Synthetic Core release\n")

        cls.selected_paths = _physical_files(cls.source_root)
        cls.manifest = contract.build_release_manifest(
            cls.source_root,
            "0.4.2",
            selected_paths=cls.selected_paths,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _mutated_manifest(
        self, mutation: Callable[[dict[str, Any]], None]
    ) -> dict[str, Any]:
        document = copy.deepcopy(self.manifest)
        mutation(document)
        return contract.seal_document(document)

    def _assert_manifest_rejected(
        self, mutation: Callable[[dict[str, Any]], None]
    ) -> None:
        with self.assertRaises(contract.ContractError):
            contract.validate_release_manifest(
                self._mutated_manifest(mutation), strict_current=True
            )

    def test_strict_semver_accepts_only_semver_2(self) -> None:
        valid = (
            "0.0.0",
            "0.4.2",
            "1.0.0-rc.1",
            "2.3.4-alpha.beta-7+build.9.sha",
        )
        invalid = (
            "v0.4.2",
            "0.4",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3-01",
            "1.2.3-",
            "1.2.3+",
            "1.2.3 latest",
            " latest ",
            "",
            None,
            402,
        )
        for version in valid:
            with self.subTest(valid=version):
                self.assertEqual(contract.validate_semver(version), version)
        for version in invalid:
            with self.subTest(invalid=version):
                with self.assertRaises(contract.ContractError):
                    contract.validate_semver(version)  # type: ignore[arg-type]

    def test_canonical_json_and_self_digest_are_deterministic(self) -> None:
        left = {"z": [3, 2, 1], "a": {"beta": 2, "alpha": 1}}
        right = {"a": {"alpha": 1, "beta": 2}, "z": [3, 2, 1]}
        left_bytes = contract.canonical_json_bytes(left)
        self.assertEqual(left_bytes, contract.canonical_json_bytes(right))
        self.assertTrue(left_bytes.endswith(b"\n"))
        self.assertNotIn(b": ", left_bytes)

        sealed = contract.seal_document(left)
        self.assertEqual(sealed["self_digest"], contract.compute_self_digest(sealed))
        self.assertEqual(contract.validate_self_digest(sealed), sealed["self_digest"])
        tampered = copy.deepcopy(sealed)
        tampered["a"]["alpha"] = 99
        with self.assertRaises(contract.ContractError):
            contract.validate_self_digest(tampered)

    def test_release_manifest_same_input_is_byte_identical(self) -> None:
        second = contract.build_release_manifest(
            self.source_root,
            "0.4.2",
            selected_paths=list(reversed(self.selected_paths)),
        )
        self.assertEqual(self.manifest, second)
        self.assertEqual(
            contract.canonical_json_bytes(self.manifest),
            contract.canonical_json_bytes(second),
        )
        self.assertEqual(
            self.manifest["self_digest"], contract.compute_self_digest(self.manifest)
        )

    def test_archive_inventory_is_complete_and_role_classified(self) -> None:
        inventory = self.manifest["archive_inventory"]
        by_path = {item["path"]: item for item in inventory}
        self.assertTrue(set(self.selected_paths).issubset(by_path))
        self.assertIn(contract.RELEASE_MANIFEST_PATH, by_path)
        self.assertEqual(len(by_path), len(inventory))
        self.assertEqual(len({path.casefold() for path in by_path}), len(by_path))

        for item in inventory:
            with self.subTest(path=item["path"]):
                self.assertIsInstance(item["roles"], list)
                self.assertTrue(item["roles"])
                self.assertEqual(len(item["roles"]), len(set(item["roles"])))
                self.assertTrue(set(item["roles"]).issubset(contract.ARCHIVE_ROLES))
                self.assertEqual(
                    item["roles"],
                    [role for role in contract.ARCHIVE_ROLES if role in item["roles"]],
                )
        self.assertEqual(
            set(by_path["scripts/bugate_update.py"]["roles"]),
            {"installable_payload", "release_metadata"},
        )
        self.assertIn(
            "release_metadata", by_path[".codex-plugin/plugin.json"]["roles"]
        )
        self.assertEqual(by_path["README.md"]["roles"], ["validated_extra"])
        self.assertEqual(
            by_path[contract.RELEASE_MANIFEST_PATH]["digest_ref"], "self_digest"
        )

    def test_archive_roles_cannot_be_used_to_expand_install_ownership(self) -> None:
        inventory = copy.deepcopy(self.manifest["archive_inventory"])
        readme = next(item for item in inventory if item["path"] == "README.md")
        self.assertEqual(readme["roles"], ["validated_extra"])
        readme["roles"] = ["installable_payload"]
        with self.assertRaises(contract.ContractError):
            contract.validate_archive_inventory(inventory, strict_current=True)

        def mutate(document: dict[str, Any]) -> None:
            item = next(
                entry
                for entry in document["archive_inventory"]
                if entry["path"] == "README.md"
            )
            item["roles"] = ["installable_payload"]

        self._assert_manifest_rejected(mutate)

    def test_installed_projection_is_complete_and_has_unique_stable_ids(self) -> None:
        projection = self.manifest["installed_projection"]
        self.assertEqual(
            {item["scope"] for item in projection}, set(contract.PROJECTION_SCOPES)
        )
        ids = [item["id"] for item in projection]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len({identity.casefold() for identity in ids}))

        installable_sources = {
            item["path"]
            for item in self.manifest["archive_inventory"]
            if "installable_payload" in item["roles"]
        }
        projected_sources = {
            item["source_path"]
            for item in projection
            if isinstance(item.get("source_path"), str)
        }
        self.assertTrue(installable_sources.issubset(projected_sources))

    def test_release_manifest_rejects_incomplete_or_noncanonical_projection(self) -> None:
        projection = self.manifest["installed_projection"]
        required_ids = (
            next(item["id"] for item in projection if item["scope"] == "vendor"),
            next(item["id"] for item in projection if item["id"].startswith("skill:")),
            next(item["id"] for item in projection if item["id"].startswith("agent:")),
            next(
                item["id"]
                for item in projection
                if item["scope"] == "shared_json_fragment"
            ),
            next(item["id"] for item in projection if item["scope"] == "marked_text_block"),
            next(item["id"] for item in projection if item["scope"] == "generated_metadata"),
        )
        for identity in required_ids:
            def remove(
                document: dict[str, Any], identity: str = identity
            ) -> None:
                document["installed_projection"] = [
                    item
                    for item in document["installed_projection"]
                    if item["id"] != identity
                ]

            with self.subTest(missing=identity):
                self._assert_manifest_rejected(remove)

        def add_extra(document: dict[str, Any]) -> None:
            item = copy.deepcopy(
                next(
                    entry
                    for entry in document["installed_projection"]
                    if entry["scope"] == "vendor" and entry["type"] == "directory"
                )
            )
            item["id"] = "vendor:undeclared-extra"
            item["target_path"] = "undeclared-extra"
            document["installed_projection"].append(item)

        def retarget_workspace_item(document: dict[str, Any]) -> None:
            item = next(
                entry
                for entry in document["installed_projection"]
                if entry["id"].startswith("agent:")
            )
            item["target_path"] = "AGENTS.md"

        def rewrite_marked_block(document: dict[str, Any]) -> None:
            item = next(
                entry
                for entry in document["installed_projection"]
                if entry["scope"] == "marked_text_block"
            )
            item["content"] += "# foreign line\n"
            item["semantic_digest"] = contract.semantic_digest(
                {
                    "begin": item["begin"],
                    "end": item["end"],
                    "content": item["content"],
                }
            )

        for label, mutation in (
            ("extra", add_extra),
            ("workspace-retarget", retarget_workspace_item),
            ("marked-block-rewrite", rewrite_marked_block),
        ):
            with self.subTest(noncanonical=label):
                self._assert_manifest_rejected(mutation)

    def test_rendered_projection_applies_only_the_declared_vendor_prefix(self) -> None:
        rendered = contract.render_installed_projection(
            self.manifest, "third_party/bugate"
        )
        original = {item["id"]: item for item in self.manifest["installed_projection"]}
        for item in rendered:
            target = item["target_path"]
            contract.validate_relative_path(target)
            if item["scope"] in {"vendor", "generated_metadata"}:
                self.assertTrue(target.startswith("third_party/bugate/"), item)
            else:
                self.assertEqual(target, original[item["id"]]["target_path"])
        skill_links = [
            item
            for item in rendered
            if item["scope"] == "workspace" and item["type"] == "symlink"
        ]
        self.assertTrue(skill_links)
        self.assertTrue(
            all("third_party/bugate/.shared/skills/" in item["target"] for item in skill_links)
        )
        with self.assertRaises(contract.ContractError):
            contract.render_installed_projection(self.manifest, "../escape")

    def test_projection_covers_file_directory_symlink_fragments_and_metadata(self) -> None:
        inventory = {item["path"]: item for item in self.manifest["archive_inventory"]}
        self.assertEqual(inventory["scripts/bugate_update.py"]["type"], "file")
        self.assertEqual(inventory["bin/bugate-update"]["mode"], "0755")
        self.assertEqual(inventory["scripts/runtime-link"]["type"], "symlink")
        self.assertEqual(inventory["scripts/runtime-link"]["target"], "check_bugate.py")

        projection = self.manifest["installed_projection"]
        self.assertTrue(
            any(item["scope"] == "vendor" and item["type"] == "directory" for item in projection)
        )
        self.assertTrue(
            any(item["scope"] == "vendor" and item["type"] == "file" for item in projection)
        )
        self.assertTrue(
            any(item["scope"] == "vendor" and item["type"] == "symlink" for item in projection)
        )
        self.assertTrue(any(item["scope"] == "shared_json_fragment" for item in projection))
        generated = [item for item in projection if item["scope"] == "generated_metadata"]
        self.assertGreaterEqual(len(generated), 2)
        self.assertTrue(all(isinstance(item.get("derivation"), str) for item in generated))
        for item in generated:
            source = item.get("source_path")
            if source is not None:
                self.assertIn("release_metadata", inventory[source]["roles"])

    def test_projection_physical_items_are_bound_to_exact_archive_sources(self) -> None:
        inventory = self.manifest["archive_inventory"]
        projection = self.manifest["installed_projection"]

        vendor_file = copy.deepcopy(
            next(
                item
                for item in projection
                if item["scope"] == "vendor"
                and item["type"] == "file"
                and item["mode"] == "0644"
            )
        )
        vendor_directory = copy.deepcopy(
            next(
                item
                for item in projection
                if item["scope"] == "vendor" and item["type"] == "directory"
            )
        )
        vendor_symlink = copy.deepcopy(
            next(
                item
                for item in projection
                if item["scope"] == "vendor" and item["type"] == "symlink"
            )
        )
        workspace_file = copy.deepcopy(
            next(
                item
                for item in projection
                if item["scope"] == "workspace" and item["type"] == "file"
            )
        )

        cases: list[tuple[str, dict[str, Any]]] = []
        changed_hash = copy.deepcopy(vendor_file)
        changed_hash["sha256"] = "0" * 64
        cases.append(("vendor-file-hash", changed_hash))

        changed_mode = copy.deepcopy(vendor_file)
        changed_mode["mode"] = "0755"
        cases.append(("vendor-file-mode", changed_mode))

        changed_type = copy.deepcopy(vendor_file)
        changed_type.pop("sha256")
        changed_type.update({"type": "symlink", "mode": "0777", "target": "../bin"})
        cases.append(("vendor-file-type", changed_type))

        directory_type = copy.deepcopy(vendor_directory)
        directory_type.update(
            {"type": "file", "mode": "0644", "sha256": "1" * 64}
        )
        cases.append(("vendor-directory-type", directory_type))

        changed_target = copy.deepcopy(vendor_symlink)
        changed_target["target"] = "check_plan_lock.py"
        cases.append(("vendor-symlink-target", changed_target))

        workspace_hash = copy.deepcopy(workspace_file)
        workspace_hash["sha256"] = "2" * 64
        cases.append(("workspace-file-hash", workspace_hash))

        workspace_mode = copy.deepcopy(workspace_file)
        workspace_mode["mode"] = "0755"
        cases.append(("workspace-file-mode", workspace_mode))

        for label, item in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                contract.ContractError, "does not match archive source"
            ):
                contract.validate_installed_projection(
                    [item], archive_inventory=inventory
                )

    def test_workspace_symlink_requires_explicit_skill_directory_mapping(self) -> None:
        inventory = self.manifest["archive_inventory"]
        skill = copy.deepcopy(
            next(
                item
                for item in self.manifest["installed_projection"]
                if item["id"] == "skill:codex:bugate"
            )
        )

        wrong_source = copy.deepcopy(skill)
        wrong_source["source_path"] = ".shared/skills/bugate-full-check"
        wrong_target_path = copy.deepcopy(skill)
        wrong_target_path["target_path"] = ".codex/skills/bugate-full-check"
        wrong_link_target = copy.deepcopy(skill)
        wrong_link_target["target"] = "../../.bugate/.shared/skills/bugate-full-check"
        relocated_vendor = copy.deepcopy(skill)
        relocated_vendor["target"] = "../../other/.shared/skills/bugate"
        file_source = copy.deepcopy(skill)
        file_source["source_path"] = ".shared/skills/bugate/SKILL.md"

        for label, item in (
            ("source", wrong_source),
            ("target-path", wrong_target_path),
            ("link-target", wrong_link_target),
            ("relocated-vendor", relocated_vendor),
            ("non-directory-source", file_source),
        ):
            with self.subTest(label=label), self.assertRaises(contract.ContractError):
                contract.validate_installed_projection(
                    [item], archive_inventory=inventory
                )

    def test_archive_paths_must_be_normalized_relative_paths(self) -> None:
        for path in ("/absolute", "../escape", "a/../b", "a//b", "a\\b", ""):
            with self.subTest(path=path):
                with self.assertRaises(contract.ContractError):
                    contract.validate_relative_path(path)

        self.assertEqual(contract.validate_vendor_dir(".bugate"), ".bugate")
        self.assertEqual(
            contract.validate_vendor_dir("third_party/bugate-kit"),
            "third_party/bugate-kit",
        )
        for vendor in (
            'vendor";touch-marker;#',
            "vendor dir",
            "vendor\nnext",
            "vendor$HOME",
            "vendor`cmd`",
        ):
            with self.subTest(vendor_dir=repr(vendor)), self.assertRaises(
                contract.ContractError
            ):
                contract.validate_vendor_dir(vendor)

        for path in ("../archive", "/archive", "a/./archive"):
            def mutate(document: dict[str, Any], path: str = path) -> None:
                document["archive_inventory"][0]["path"] = path

            with self.subTest(manifest_path=path):
                self._assert_manifest_rejected(mutate)

    def test_projection_source_and_target_paths_must_be_relative(self) -> None:
        vendor_index = next(
            index
            for index, item in enumerate(self.manifest["installed_projection"])
            if item["scope"] == "vendor" and "source_path" in item
        )
        for field, path in (("source_path", "../source"), ("target_path", "/target")):
            def mutate(
                document: dict[str, Any], field: str = field, path: str = path
            ) -> None:
                document["installed_projection"][vendor_index][field] = path

            with self.subTest(field=field):
                self._assert_manifest_rejected(mutate)

    def test_symlink_escape_is_rejected_in_inventory_and_projection(self) -> None:
        with self.assertRaises(contract.ContractError):
            contract.validate_symlink_target("link", "../outside")
        self.assertEqual(
            contract.validate_symlink_target(
                ".codex/skills/bugate", "../../.bugate/.shared/skills/bugate"
            ),
            "../../.bugate/.shared/skills/bugate",
        )

        inventory_index = next(
            index
            for index, item in enumerate(self.manifest["archive_inventory"])
            if item["type"] == "symlink"
        )
        projection_index = next(
            index
            for index, item in enumerate(self.manifest["installed_projection"])
            if item["type"] == "symlink"
        )

        def escape_inventory(document: dict[str, Any]) -> None:
            document["archive_inventory"][inventory_index]["target"] = "../../../outside"

        def escape_projection(document: dict[str, Any]) -> None:
            document["installed_projection"][projection_index]["target"] = "../../../../outside"

        self._assert_manifest_rejected(escape_inventory)
        self._assert_manifest_rejected(escape_projection)

    def test_duplicate_and_case_conflicting_archive_paths_are_rejected(self) -> None:
        def duplicate(document: dict[str, Any]) -> None:
            document["archive_inventory"].append(
                copy.deepcopy(document["archive_inventory"][0])
            )

        def case_conflict(document: dict[str, Any]) -> None:
            item = copy.deepcopy(document["archive_inventory"][0])
            item["path"] = item["path"].swapcase()
            document["archive_inventory"].append(item)

        self._assert_manifest_rejected(duplicate)
        self._assert_manifest_rejected(case_conflict)

    def test_archive_inventory_rejects_non_directory_ancestors(self) -> None:
        for ancestor_type in ("file", "symlink"):
            inventory = copy.deepcopy(self.manifest["archive_inventory"])
            scripts = next(item for item in inventory if item["path"] == "scripts")
            scripts.pop("sha256", None)
            scripts.pop("target", None)
            scripts["type"] = ancestor_type
            if ancestor_type == "file":
                scripts["mode"] = "0644"
                scripts["sha256"] = "c" * 64
            else:
                scripts["mode"] = "0777"
                scripts["target"] = "bin"
            self.assertTrue(
                any(item["path"].startswith("scripts/") for item in inventory)
            )
            with self.subTest(ancestor_type=ancestor_type):
                with self.assertRaises(contract.ContractError):
                    contract.validate_archive_inventory(inventory)

    def test_duplicate_and_case_conflicting_projection_ids_are_rejected(self) -> None:
        def duplicate(document: dict[str, Any]) -> None:
            document["installed_projection"].append(
                copy.deepcopy(document["installed_projection"][0])
            )

        def case_conflict(document: dict[str, Any]) -> None:
            item = copy.deepcopy(
                next(
                    entry
                    for entry in document["installed_projection"]
                    if entry["scope"] == "shared_json_fragment"
                )
            )
            item["id"] = item["id"].swapcase()
            document["installed_projection"].append(item)

        self._assert_manifest_rejected(duplicate)
        self._assert_manifest_rejected(case_conflict)

    def test_projection_rejects_non_directory_target_and_source_ancestors(self) -> None:
        digest = "d" * 64

        def path_item(
            identity: str,
            scope: str,
            source: str,
            target: str,
            *,
            kind: str = "file",
        ) -> dict[str, Any]:
            item: dict[str, Any] = {
                "id": identity,
                "scope": scope,
                "source_path": source,
                "target_path": target,
                "type": kind,
                "mode": "0755" if kind == "directory" else "0644",
            }
            if kind == "file":
                item["sha256"] = digest
            return item

        for scope, target in (
            ("vendor", "owned"),
            ("workspace", ".codex/owned"),
        ):
            target_conflict = [
                path_item(f"{scope}:parent", scope, "source/parent", target),
                path_item(
                    f"{scope}:child",
                    scope,
                    "source/child",
                    f"{target}/child",
                ),
            ]
            with self.subTest(scope=scope, conflict="target"):
                with self.assertRaises(contract.ContractError):
                    contract.validate_installed_projection(target_conflict)

            source_conflict = [
                path_item(f"{scope}:source-parent", scope, "payload", "target-one"),
                path_item(
                    f"{scope}:source-child",
                    scope,
                    "payload/child",
                    "target-two",
                ),
            ]
            with self.subTest(scope=scope, conflict="source"):
                with self.assertRaises(contract.ContractError):
                    contract.validate_installed_projection(source_conflict)

        case_conflict = [
            path_item("vendor:case-parent", "vendor", "source/one", "Owned"),
            path_item("vendor:case-child", "vendor", "source/two", "owned/child"),
        ]
        with self.assertRaises(contract.ContractError):
            contract.validate_installed_projection(case_conflict)

        directory_ancestors = [
            path_item(
                "vendor:directory-parent",
                "vendor",
                "payload",
                "owned",
                kind="directory",
            ),
            path_item(
                "vendor:directory-child",
                "vendor",
                "payload/child",
                "owned/child",
            ),
        ]
        contract.validate_installed_projection(directory_ancestors)

    def test_shared_fragments_may_share_their_container_target(self) -> None:
        hooks_by_target: dict[str, list[dict[str, Any]]] = {}
        for item in self.manifest["installed_projection"]:
            if item["scope"] == "shared_json_fragment":
                hooks_by_target.setdefault(item["target_path"], []).append(item)
        pair = next(items[:2] for items in hooks_by_target.values() if len(items) >= 2)
        self.assertEqual(pair[0]["target_path"], pair[1]["target_path"])
        contract.validate_installed_projection(copy.deepcopy(pair))

    def test_invalid_modes_and_hashes_are_rejected(self) -> None:
        file_index = next(
            index
            for index, item in enumerate(self.manifest["archive_inventory"])
            if item["type"] == "file" and "sha256" in item
        )

        def bad_mode(document: dict[str, Any]) -> None:
            document["archive_inventory"][file_index]["mode"] = "0777"

        def bad_hash(document: dict[str, Any]) -> None:
            document["archive_inventory"][file_index]["sha256"] = "A" * 64

        self._assert_manifest_rejected(bad_mode)
        self._assert_manifest_rejected(bad_hash)

    def test_generated_metadata_must_name_a_verified_derivation(self) -> None:
        generated_index = next(
            index
            for index, item in enumerate(self.manifest["installed_projection"])
            if item["scope"] == "generated_metadata"
        )

        def mutate(document: dict[str, Any]) -> None:
            item = document["installed_projection"][generated_index]
            item["derivation"] = "unverified-external-input"

        self._assert_manifest_rejected(mutate)

    def test_hook_semantic_digest_binds_the_complete_fragment(self) -> None:
        hook = next(
            item
            for item in self.manifest["installed_projection"]
            if item["scope"] == "shared_json_fragment"
        )
        semantic_value = {"event": hook["event"], "value": hook["value"]}
        self.assertEqual(
            hook["semantic_digest"], contract.semantic_digest(semantic_value)
        )
        reversed_value = {
            key: hook["value"][key] for key in reversed(list(hook["value"]))
        }
        self.assertEqual(
            hook["semantic_digest"],
            contract.semantic_digest({"value": reversed_value, "event": hook["event"]}),
        )

        def mutate_without_digest(document: dict[str, Any]) -> None:
            item = next(
                entry
                for entry in document["installed_projection"]
                if entry["id"] == hook["id"]
            )
            item["value"] = copy.deepcopy(item["value"])
            item["value"]["unexpected"] = True

        self._assert_manifest_rejected(mutate_without_digest)

    def test_hook_id_and_embedded_identity_alone_do_not_grant_ownership(self) -> None:
        hook = next(
            item
            for item in self.manifest["installed_projection"]
            if item["scope"] == "shared_json_fragment"
        )

        def replace_first_command(value: Any) -> bool:
            if isinstance(value, dict):
                command = value.get("command")
                if isinstance(command, str):
                    identity_prefix = command.split("ROOT=", 1)[0]
                    value["command"] = identity_prefix + "/usr/bin/printf foreign-command"
                    return True
                return any(replace_first_command(child) for child in value.values())
            if isinstance(value, list):
                return any(replace_first_command(child) for child in value)
            return False

        def mutate(document: dict[str, Any]) -> None:
            item = next(
                entry
                for entry in document["installed_projection"]
                if entry["id"] == hook["id"]
            )
            self.assertTrue(replace_first_command(item["value"]))
            # Preserve the stable projection ID and embedded BUGATE_HOOK_ID, and
            # make the semantic digest internally valid.  The catalog shape must
            # still reject this foreign command; identity alone is not ownership.
            item["semantic_digest"] = contract.semantic_digest(
                {"event": item["event"], "value": item["value"]}
            )

        self._assert_manifest_rejected(mutate)

    def test_installed_lock_is_complete_deterministic_and_machine_neutral(self) -> None:
        first = contract.build_installed_lock(
            self.manifest,
            previous_version="0.3.2",
            archive_sha256=None,
            vendor_dir="third_party/bugate",
        )
        second = contract.build_installed_lock(
            self.manifest,
            previous_version="0.3.2",
            archive_sha256=None,
            vendor_dir="third_party/bugate",
        )
        first_bytes = contract.installed_lock_bytes(first)
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, contract.installed_lock_bytes(second))
        self.assertEqual(first["archive_sha256"], None)
        self.assertEqual(
            first["archive_verification"], "unavailable-from-unpacked-source"
        )
        self.assertEqual(first["verified_release_digest"], self.manifest["self_digest"])
        manifest_sha = contract.sha256_bytes(
            contract.canonical_json_bytes(self.manifest)
        )
        self.assertEqual(first["release_manifest_sha256"], manifest_sha)
        self.assertEqual(first["installed_manifest"]["sha256"], manifest_sha)
        expected_projection = contract.render_installed_projection(
            self.manifest, "third_party/bugate"
        )
        installed_manifest = next(
            item
            for item in expected_projection
            if item["id"] == "metadata:installed-release-manifest"
        )
        installed_manifest.pop("digest_ref", None)
        installed_manifest["sha256"] = manifest_sha
        self.assertEqual(first["installed_projection"], expected_projection)
        self.assertEqual(
            contract.validate_installed_lock(
                first,
                release_manifest=self.manifest,
                vendor_dir="third_party/bugate",
            ),
            first,
        )

        serialized = first_bytes.decode("utf-8")
        self.assertNotIn(str(self.source_root), serialized)
        self.assertNotIn("/tmp/", serialized)
        forbidden_names = {
            "timestamp",
            "created_at",
            "updated_at",
            "user",
            "username",
            "hostname",
            "credential",
            "credentials",
            "token",
            "secret",
        }
        self.assertTrue(forbidden_names.isdisjoint(_walk_values(first)))

    def test_historical_same_schema_manifest_and_lock_have_explicit_validation_modes(
        self,
    ) -> None:
        historical_manifest = copy.deepcopy(self.manifest)
        historical_lock = contract.build_installed_lock(
            historical_manifest,
            previous_version="0.4.1",
            archive_sha256="e" * 64,
        )

        changed_current_contracts = (
            (
                "hook-contract",
                {"HOOK_CONTRACT_VERSION": contract.HOOK_CONTRACT_VERSION + 1},
            ),
            (
                "ownership-catalog",
                {"SKILL_NAMES": (*contract.SKILL_NAMES, "future-synthetic-skill")},
            ),
        )
        for label, changes in changed_current_contracts:
            with self.subTest(changed_current=label), mock.patch.multiple(
                contract, **changes
            ):
                # A previously verified same-schema document is its own old/post
                # image.  New catalog constants must not strand it.
                self.assertEqual(
                    contract.validate_release_manifest(historical_manifest),
                    historical_manifest,
                )
                self.assertEqual(
                    contract.validate_installed_lock(
                        historical_lock,
                        release_manifest=historical_manifest,
                        vendor_dir=".bugate",
                    ),
                    historical_lock,
                )
                self.assertEqual(
                    contract.installed_lock_bytes(historical_lock),
                    contract.canonical_json_bytes(historical_lock),
                )

                # Build/release acceptance is intentionally stricter and must
                # say that this historical catalog is not the current catalog.
                with self.assertRaises(contract.ContractError):
                    contract.validate_release_manifest(
                        historical_manifest, strict_current=True
                    )
                with self.assertRaises(contract.ContractError):
                    contract.validate_current_release_manifest(historical_manifest)
                with self.assertRaises(contract.ContractError):
                    contract.validate_installed_lock(
                        historical_lock,
                        release_manifest=historical_manifest,
                        vendor_dir=".bugate",
                        strict_current=True,
                    )

    def test_installed_lock_rejects_unstable_private_or_absolute_fields(self) -> None:
        lock = contract.build_installed_lock(
            self.manifest,
            previous_version="0.4.1",
            archive_sha256="a" * 64,
        )
        self.assertEqual(lock["archive_verification"], "sha256")
        def top_level(key: str, value: str) -> Callable[[dict[str, Any]], None]:
            return lambda document: document.__setitem__(key, value)

        def nested_timestamp(document: dict[str, Any]) -> None:
            document["installed_manifest"]["timestamp"] = "2026-01-01T00:00:00Z"

        def nested_token(document: dict[str, Any]) -> None:
            document["installed_projection"][0]["token"] = "synthetic-private-value"

        def absolute_manifest_path(document: dict[str, Any]) -> None:
            document["installed_manifest"]["path"] = "/absolute/machine/path"

        cases = (
            ("timestamp", top_level("timestamp", "2026-01-01T00:00:00Z")),
            ("token", top_level("token", "synthetic-private-value")),
            ("source_path", top_level("source_path", "/absolute/machine/path")),
            ("nested_timestamp", nested_timestamp),
            ("nested_token", nested_token),
            ("absolute_manifest_path", absolute_manifest_path),
        )
        for name, mutate in cases:
            tampered = copy.deepcopy(lock)
            mutate(tampered)
            with self.subTest(case=name):
                with self.assertRaises(contract.ContractError):
                    contract.installed_lock_bytes(tampered)
                with self.assertRaises(contract.ContractError):
                    contract.validate_installed_lock(tampered)


if __name__ == "__main__":
    unittest.main()
