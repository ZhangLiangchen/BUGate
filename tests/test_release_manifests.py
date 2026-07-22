#!/usr/bin/env python3
"""Contract tests for release manifests and deterministic installed locks."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bugate_install_contract as contract  # noqa: E402
import bugate_legacy_manifest as legacy  # noqa: E402


class ReleaseManifestContractTests(unittest.TestCase):
    @classmethod
    def selected_paths(cls) -> list[str]:
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=ROOT
        ).decode("utf-8").split("\0")
        selected = [path for path in tracked if path]
        for path in (
            *contract.UPDATER_WORKER_FILES,
            "tests/test_release_manifests.py",
        ):
            if path not in selected:
                selected.append(path)
        return selected

    def manifest(self) -> dict:
        return contract.build_release_manifest(
            ROOT,
            "0.4.2",
            selected_paths=self.selected_paths(),
        )

    def test_current_manifest_is_deterministic_and_narrow(self) -> None:
        first = self.manifest()
        second = self.manifest()

        self.assertEqual(first, second)
        self.assertEqual(first["self_digest"], contract.compute_self_digest(first))
        self.assertEqual(
            hashlib.sha256(
                contract.canonical_json_bytes(
                    {key: value for key, value in first.items() if key != "self_digest"}
                )
            ).hexdigest(),
            first["self_digest"],
        )
        inventory_paths = {entry["path"] for entry in first["archive_inventory"]}
        self.assertIn("README.md", inventory_paths)
        self.assertIn("AGENTS.md", inventory_paths)
        self.assertIn("tests/test_release_manifests.py", inventory_paths)
        paths = {
            entry["source_path"]
            for entry in first["installed_projection"]
            if entry["scope"] == "vendor"
        }
        self.assertIn("scripts/bugate_install_contract.py", paths)
        self.assertIn("bin/bugate-role", paths)
        self.assertIn(".shared/skills/bugate/SKILL.md", paths)
        self.assertNotIn("README.md", paths)
        self.assertNotIn("AGENTS.md", paths)
        self.assertFalse(any(path.startswith("tests/") for path in paths))

        projection = first["installed_projection"]
        self.assertEqual(sum(item["id"].startswith("skill:") for item in projection), 9)
        self.assertEqual(sum(item["id"].startswith("agent:codex:") for item in projection), 3)
        self.assertEqual(
            sum(item["scope"] == "shared_json_fragment" for item in projection),
            9,
        )
        release_meta = next(
            item
            for item in projection
            if item["id"] == "metadata:installed-release-manifest"
        )
        lock_meta = next(
            item for item in projection if item["id"] == "metadata:installed-lock"
        )
        self.assertEqual(release_meta["target_path"], "bugate.release.json")
        self.assertEqual(lock_meta["target_path"], "bugate.lock.json")
        block = next(item for item in projection if item["scope"] == "marked_text_block")
        self.assertIn("/.bugate-update/", block["content"])

        manifest_inventory = next(
            item
            for item in first["archive_inventory"]
            if item["path"] == ".bugate-release/manifest.json"
        )
        self.assertEqual(manifest_inventory["digest_ref"], "self_digest")
        self.assertEqual(manifest_inventory["roles"], ["release_metadata"])

    def test_manifest_validation_detects_tampering(self) -> None:
        manifest = self.manifest()
        tampered = copy.deepcopy(manifest)
        first_file = next(
            entry
            for entry in tampered["archive_inventory"]
            if entry["type"] == "file" and "sha256" in entry
        )
        first_file["sha256"] = "0" * 64

        with self.assertRaisesRegex(contract.ContractError, "self_digest mismatch"):
            contract.validate_release_manifest(tampered)

    def test_expected_version_mismatch_fails_closed(self) -> None:
        manifest = self.manifest()
        with self.assertRaisesRegex(contract.ContractError, "version mismatch"):
            contract.validate_release_manifest(manifest, expected_version="0.4.3")

    def test_installed_lock_is_deterministic_and_machine_neutral(self) -> None:
        manifest = self.manifest()
        first = contract.build_installed_lock(
            manifest,
            previous_version="0.3.2",
            archive_sha256=None,
        )
        second = contract.build_installed_lock(
            manifest,
            previous_version="0.3.2",
            archive_sha256=None,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first["verified_release_digest"], manifest["self_digest"]
        )
        self.assertEqual(
            first["release_manifest_sha256"],
            hashlib.sha256(contract.canonical_json_bytes(manifest)).hexdigest(),
        )
        self.assertIsNone(first["archive_sha256"])
        self.assertEqual(
            first["archive_verification"], "unavailable-from-unpacked-source"
        )
        self.assertEqual(first["installed_projection"], second["installed_projection"])
        self.assertEqual(
            first["installed_manifest"]["sha256"],
            first["release_manifest_sha256"],
        )
        self.assertTrue(
            any(
                item["scope"] == "shared_json_fragment"
                and "semantic_digest" in item
                and "value" in item
                for item in first["installed_projection"]
            )
        )
        encoded = contract.installed_lock_bytes(first)
        self.assertEqual(encoded, contract.installed_lock_bytes(second))
        text = encoded.decode("utf-8")
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn("timestamp", text)
        self.assertNotIn("username", text)
        self.assertEqual(json.loads(text), first)

    def test_archive_digest_changes_only_archive_fields(self) -> None:
        manifest = self.manifest()
        digest = "a" * 64
        lock = contract.build_installed_lock(
            manifest,
            previous_version="0.4.1",
            archive_sha256=digest,
        )
        self.assertEqual(lock["archive_sha256"], digest)
        self.assertEqual(lock["archive_verification"], "sha256")
        self.assertEqual(lock["verified_release_digest"], manifest["self_digest"])

    def test_custom_vendor_render_changes_only_parameterized_projection(self) -> None:
        manifest = self.manifest()
        rendered = contract.render_installed_projection(manifest, "vendor/bugate")
        vendor_item = next(item for item in rendered if item["scope"] == "vendor")
        self.assertTrue(vendor_item["target_path"].startswith("vendor/bugate/"))
        skill = next(item for item in rendered if item["id"] == "skill:agents:bugate")
        self.assertEqual(skill["target"], "../../vendor/bugate/.shared/skills/bugate")
        hook = next(item for item in rendered if item["scope"] == "shared_json_fragment")
        self.assertIn("$ROOT/vendor/bugate/", json.dumps(hook["value"]))

    def test_hook_identity_does_not_substitute_for_exact_semantic_digest(self) -> None:
        manifest = self.manifest()
        hook = next(
            item
            for item in manifest["installed_projection"]
            if item["scope"] == "shared_json_fragment"
        )
        spoofed = copy.deepcopy(hook)
        spoofed["value"]["hooks"][0]["command"] += " --spoofed"
        with self.assertRaisesRegex(contract.ContractError, "semantic digest mismatch"):
            contract.validate_installed_projection([spoofed])

    def test_strict_semver_validation(self) -> None:
        for version in ("0.4.2", "1.0.0-rc.1", "2.3.4+build.7"):
            with self.subTest(version=version):
                self.assertEqual(contract.validate_semver(version), version)
        for version in ("v0.4.2", "0.4", "01.2.3", "1.2.3-01", "latest", ""):
            with self.subTest(version=version):
                with self.assertRaises(contract.ContractError):
                    contract.validate_semver(version)

    def test_updater_minimum_uses_semver_precedence_and_ignores_build_metadata(self) -> None:
        manifest = self.manifest()
        for updater in ("0.4.1", "0.4.2-rc.1"):
            with self.subTest(updater=updater):
                with self.assertRaisesRegex(contract.ContractError, "below required minimum"):
                    contract.build_installed_lock(
                        manifest,
                        previous_version="0.4.1",
                        archive_sha256=None,
                        updater_version=updater,
                    )
        compatible = contract.build_installed_lock(
            manifest,
            previous_version="0.4.1",
            archive_sha256=None,
            updater_version="0.4.2+different-container",
        )
        self.assertEqual(compatible["updater_minimum_version"], "0.4.2")
        self.assertEqual(
            contract.compare_semver("0.4.2+left", "0.4.2+right"), 0
        )
        self.assertGreater(contract.compare_semver("0.4.2", "0.4.2-rc.9"), 0)

    def test_relative_path_and_symlink_escape_validation(self) -> None:
        for path in ("/absolute", "../escape", "a/../b", "a//b", "a\\b", ""):
            with self.subTest(path=path):
                with self.assertRaises(contract.ContractError):
                    contract.validate_relative_path(path)
        self.assertEqual(
            contract.validate_symlink_target(
                ".codex/skills/bugate",
                "../../.bugate/.shared/skills/bugate",
            ),
            "../../.bugate/.shared/skills/bugate",
        )
        with self.assertRaisesRegex(contract.ContractError, "escapes"):
            contract.validate_symlink_target("link", "../outside")

    def test_invalid_mode_and_duplicate_paths_are_rejected(self) -> None:
        digest = "b" * 64
        with self.assertRaisesRegex(contract.ContractError, "invalid file mode"):
            contract.validate_managed_paths(
                [{"path": "a", "type": "file", "mode": "0777", "sha256": digest}]
            )
        with self.assertRaisesRegex(contract.ContractError, "duplicate"):
            contract.validate_managed_paths(
                [
                    {"path": "A", "type": "file", "mode": "0644", "sha256": digest},
                    {"path": "a", "type": "file", "mode": "0644", "sha256": digest},
                ]
            )

    def test_scanner_preserves_file_directory_symlink_and_executable_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-contract-fixture-") as raw:
            root = Path(raw)
            for rel in contract.VENDOR_TREE_ROOTS:
                (root / rel).mkdir(parents=True)
                marker = root / rel / "marker.txt"
                marker.write_text(rel + "\n", encoding="utf-8")
            setup = root / contract.VENDOR_SINGLE_FILES[0]
            setup.parent.mkdir(parents=True, exist_ok=True)
            setup.write_text("setup\n", encoding="utf-8")
            executable = root / "bin" / "tool"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(executable, 0o755)
            link = root / "scripts" / "tool-link"
            link.symlink_to("../bin/tool")

            entries = contract.scan_managed_paths(root)
            by_path = {entry["path"]: entry for entry in entries}
            self.assertEqual(by_path["scripts"]["type"], "directory")
            self.assertEqual(by_path["bin/tool"]["mode"], "0755")
            self.assertEqual(by_path["scripts/tool-link"]["type"], "symlink")
            self.assertEqual(by_path["scripts/tool-link"]["target"], "../bin/tool")

    def test_selected_inventory_cannot_omit_required_single_file(self) -> None:
        selected = {
            path.relative_to(ROOT).as_posix()
            for root in contract.VENDOR_TREE_ROOTS
            for path in (ROOT / root).rglob("*")
            if path.is_file()
        }
        with self.assertRaisesRegex(contract.ContractError, "absent from archive inventory"):
            contract.scan_managed_paths(ROOT, selected_paths=selected)


class LegacyManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = {
            tag: legacy.generate_legacy_manifest(ROOT, tag)
            for tag in contract.SUPPORTED_LEGACY_TAGS
        }

    def test_all_supported_annotated_tags_generate_deterministically(self) -> None:
        overlays = legacy.generate_all_legacy_manifests(ROOT)
        expected_paths = {
            f".bugate-release/legacy/{tag}.json"
            for tag in contract.SUPPORTED_LEGACY_TAGS
        }
        self.assertEqual(set(overlays), expected_paths)
        for tag, manifest in self.manifests.items():
            with self.subTest(tag=tag):
                self.assertEqual(
                    manifest,
                    legacy.generate_legacy_manifest(ROOT, tag),
                )
                self.assertEqual(manifest["source_tag"], tag)
                self.assertEqual(
                    manifest["bugate_version"], tag.removeprefix("v")
                )
                self.assertEqual(
                    manifest["self_digest"], contract.compute_self_digest(manifest)
                )
                path = f".bugate-release/legacy/{tag}.json"
                self.assertEqual(
                    overlays[path], contract.canonical_json_bytes(manifest)
                )

    def test_legacy_projection_counts_are_tag_exact(self) -> None:
        expected = {
            "v0.3.0": (73, 18, 6, 3, 8),
            "v0.3.1": (73, 18, 6, 3, 8),
            "v0.3.2": (74, 19, 6, 3, 9),
            "v0.3.4": (78, 21, 9, 3, 9),
            "v0.3.5": (78, 21, 9, 3, 9),
            "v0.4.0": (81, 21, 9, 3, 9),
            "v0.4.1": (81, 21, 9, 3, 9),
        }
        for tag, counts in expected.items():
            projection = self.manifests[tag]["installed_projection"]
            vendor = [item for item in projection if item["scope"] == "vendor"]
            actual = (
                sum(item["type"] in {"file", "symlink"} for item in vendor),
                sum(item["type"] == "directory" for item in vendor),
                sum(item["id"].startswith("skill:") for item in projection),
                sum(item["id"].startswith("agent:") for item in projection),
                sum(item["scope"] == "shared_json_fragment" for item in projection),
            )
            with self.subTest(tag=tag):
                self.assertEqual(actual, counts)

    def test_v032_obsolete_field_guide_is_exactly_classified(self) -> None:
        current = ReleaseManifestContractTests().manifest()
        current_sources = {
            item["source_path"]
            for item in current["installed_projection"]
            if item["scope"] == "vendor"
        }
        legacy_sources = {
            item["source_path"]
            for item in self.manifests["v0.3.2"]["installed_projection"]
            if item["scope"] == "vendor"
        }
        obsolete = legacy_sources - current_sources
        self.assertEqual(obsolete, {"docs/IMPORT-FIELD-GUIDE.md"})
        guide = next(
            item
            for item in self.manifests["v0.3.2"]["archive_inventory"]
            if item["path"] == "docs/IMPORT-FIELD-GUIDE.md"
        )
        self.assertEqual(guide["roles"], ["installable_payload"])

    def test_legacy_hook_shapes_are_identity_free_and_exactly_digested(self) -> None:
        for tag, manifest in self.manifests.items():
            hooks = [
                item
                for item in manifest["installed_projection"]
                if item["scope"] == "shared_json_fragment"
            ]
            with self.subTest(tag=tag):
                self.assertTrue(hooks)
                self.assertFalse(
                    any("BUGATE_HOOK_ID=" in json.dumps(item["value"]) for item in hooks)
                )
                self.assertTrue(
                    all(
                        item["semantic_digest"]
                        == contract.semantic_digest(
                            {"event": item["event"], "value": item["value"]}
                        )
                        for item in hooks
                    )
                )

    @staticmethod
    def _reseal_legacy(document: dict) -> dict:
        document = copy.deepcopy(document)
        document["legacy_layout_fingerprint"] = contract.semantic_digest(
            {
                "installable_inventory": [
                    item
                    for item in document["archive_inventory"]
                    if "installable_payload" in item["roles"]
                ],
                "installed_projection": document["installed_projection"],
            }
        )
        return contract.seal_document(document)

    def test_resealed_projection_hash_cannot_diverge_from_legacy_inventory(self) -> None:
        tampered = copy.deepcopy(self.manifests["v0.3.2"])
        projected_file = next(
            item
            for item in tampered["installed_projection"]
            if item["scope"] == "vendor" and item["type"] == "file"
        )
        projected_file["sha256"] = "0" * 64
        tampered = self._reseal_legacy(tampered)
        self.assertEqual(
            tampered["self_digest"], contract.compute_self_digest(tampered)
        )
        with self.assertRaisesRegex(
            contract.ContractError, "does not match archive source"
        ):
            legacy.validate_legacy_manifest(tampered, expected_tag="v0.3.2")

    def test_legacy_manifests_record_historical_mode_evidence_policies(self) -> None:
        for tag, manifest in self.manifests.items():
            policies = {
                item["id"]: item["legacy_mode_policy"]
                for item in manifest["installed_projection"]
                if "legacy_mode_policy" in item
            }
            with self.subTest(tag=tag):
                self.assertEqual(
                    len(
                        [
                            value
                            for value in policies.values()
                            if value == "copyfile_destination"
                        ]
                    ),
                    len(contract.CODEX_GATE_AGENT_NAMES),
                )
                self.assertTrue(
                    any(
                        value == "created_directory_umask"
                        for value in policies.values()
                    )
                )

    def test_resealed_legacy_projection_cannot_drop_duplicate_or_retarget_payload(self) -> None:
        original = self.manifests["v0.3.2"]
        first_vendor = next(
            item
            for item in original["installed_projection"]
            if item["scope"] == "vendor" and item["type"] == "file"
        )

        dropped = copy.deepcopy(original)
        dropped["installed_projection"] = [
            item
            for item in dropped["installed_projection"]
            if item["id"] != first_vendor["id"]
        ]
        with self.assertRaisesRegex(
            contract.ContractError, "does not exactly cover installable payload"
        ):
            legacy.validate_legacy_manifest(
                self._reseal_legacy(dropped), expected_tag="v0.3.2"
            )

        duplicated = copy.deepcopy(original)
        duplicate = copy.deepcopy(first_vendor)
        duplicate["id"] += ":duplicate"
        duplicate["target_path"] += ".duplicate"
        duplicated["installed_projection"].append(duplicate)
        with self.assertRaisesRegex(
            contract.ContractError, "does not exactly cover installable payload"
        ):
            legacy.validate_legacy_manifest(
                self._reseal_legacy(duplicated), expected_tag="v0.3.2"
            )

        retargeted = copy.deepcopy(original)
        changed = next(
            item
            for item in retargeted["installed_projection"]
            if item["id"] == first_vendor["id"]
        )
        changed["target_path"] += ".moved"
        with self.assertRaisesRegex(
            contract.ContractError, "identity/target differs"
        ):
            legacy.validate_legacy_manifest(
                self._reseal_legacy(retargeted), expected_tag="v0.3.2"
            )

    def test_read_legacy_asset_is_bound_to_target_release_inventory(self) -> None:
        tag = "v0.3.2"
        relative = f"{contract.LEGACY_MANIFEST_DIR}/{tag}.json"
        original = self.manifests[tag]
        original_bytes = contract.canonical_json_bytes(original)
        release = contract.build_release_manifest(
            ROOT,
            "0.4.2",
            selected_paths=ReleaseManifestContractTests.selected_paths(),
            overlay_files={relative: original_bytes},
        )
        self.assertEqual(
            legacy.validate_legacy_manifest_asset(
                original_bytes,
                expected_tag=tag,
                target_release_manifest=release,
                actual_mode="0644",
            ),
            original,
        )

        # Change only a hook fragment and then recompute every legacy digest.
        # The result remains a valid self-contained historical document but is
        # not the exact asset sealed into this target release inventory.
        substituted = copy.deepcopy(original)
        hook = next(
            item
            for item in substituted["installed_projection"]
            if item["scope"] == "shared_json_fragment"
        )
        hook["value"]["hooks"][0]["command"] += " --substituted"
        hook["semantic_digest"] = contract.semantic_digest(
            {"event": hook["event"], "value": hook["value"]}
        )
        substituted = self._reseal_legacy(substituted)
        substituted_bytes = contract.canonical_json_bytes(substituted)
        self.assertEqual(
            legacy.validate_legacy_manifest(substituted, expected_tag=tag),
            substituted,
        )
        with self.assertRaisesRegex(
            contract.ContractError, "differs from target release inventory"
        ):
            legacy.validate_legacy_manifest_asset(
                substituted_bytes,
                expected_tag=tag,
                target_release_manifest=release,
                actual_mode="0644",
            )

        for label, mode in (("physical-mode", "0755"),):
            with self.subTest(label=label), self.assertRaisesRegex(
                contract.ContractError, "differs from target release inventory"
            ):
                legacy.validate_legacy_manifest_asset(
                    original_bytes,
                    expected_tag=tag,
                    target_release_manifest=release,
                    actual_mode=mode,
                )

        for label, mutation in (
            (
                "declared-hash",
                lambda item: item.update({"sha256": "f" * 64}),
            ),
            (
                "declared-mode",
                lambda item: item.update({"mode": "0755"}),
            ),
            (
                "declared-type",
                lambda item: (
                    item.pop("sha256"),
                    item.update(
                        {
                            "type": "symlink",
                            "mode": "0777",
                            "target": f"{tag}-alternate.json",
                        }
                    ),
                ),
            ),
        ):
            changed_release = copy.deepcopy(release)
            source = next(
                item
                for item in changed_release["archive_inventory"]
                if item["path"] == relative
            )
            mutation(source)
            changed_release = contract.seal_document(changed_release)
            with self.subTest(label=label), self.assertRaisesRegex(
                contract.ContractError, "differs from target release inventory"
            ):
                legacy.validate_legacy_manifest_asset(
                    original_bytes,
                    expected_tag=tag,
                    target_release_manifest=changed_release,
                    actual_mode="0644",
                )

    def test_lightweight_tag_is_not_accepted_as_formal_legacy_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bugate-lightweight-tag-") as raw:
            repo = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.name", "BUGate Contract Test"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "bugate@example.invalid"],
                cwd=repo,
                check=True,
            )
            (repo / "README.md").write_text("synthetic\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "synthetic"], cwd=repo, check=True)
            subprocess.run(["git", "tag", "v0.3.2"], cwd=repo, check=True)
            with self.assertRaisesRegex(contract.ContractError, "annotated tag"):
                legacy.generate_legacy_manifest(repo, "v0.3.2")

    def test_malicious_annotated_hook_ast_is_rejected_before_side_effect(self) -> None:
        malicious_bodies = {
            "open": 'open({marker}, "w").write("bad")',
            "import": 'import os\nos.system("printf bad > " + {marker})',
            "system": '__import__("os").system("printf bad > " + {marker})',
            "attribute": 'value = (1).__class__',
        }
        for label, body_template in malicious_bodies.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix=f"bugate-malicious-legacy-{label}-"
            ) as raw:
                repo = Path(raw)
                marker = repo / "side-effect.txt"
                for directory in (
                    "scripts",
                    "bin",
                    ".codex-plugin",
                    ".claude-plugin",
                    ".shared/skills/bugate/adapters/codex/agents",
                    ".shared/skills/bugate-full-check",
                ):
                    (repo / directory).mkdir(parents=True, exist_ok=True)
                body = body_template.format(marker=repr(str(marker)))
                installer = f'''\
KIT_DIRS = ["scripts", "bin", ".shared/skills/bugate", ".shared/skills/bugate-full-check"]
KIT_FILES = []
CODEX_AGENTS_KIT_REL = ".shared/skills/bugate/adapters/codex/agents"
GITIGNORE_BEGIN = "# begin"
GITIGNORE_END = "# end"
GITIGNORE_BLOCK = "{{begin}}\\n/.bugate/plan.lock\\n{{end}}\\n"
_ROOT_SNIPPET = "ROOT=fixture; "
def _cmd(vendor_dir, script, *args):
    return _ROOT_SNIPPET + " ".join(args)
def hook_blocks(vendor_dir, runtime):
    {body.replace(chr(10), chr(10) + "    ")}
    return {{"PreToolUse": [{{"hooks": [{{"type": "command", "command": _cmd(vendor_dir, "check.py")}}]}}]}}
def link_skills(target, vendor_dir, dry, force):
    skill_names = ("bugate", "bugate-full-check")
    runtimes = ((".claude", "claude"), (".agents", "agents"), (".codex", "codex"))
    return skill_names, runtimes
'''
                (repo / "scripts/bugate_init.py").write_text(
                    installer, encoding="utf-8"
                )
                (repo / "bin/tool").write_text("#!/bin/sh\n", encoding="utf-8")
                os.chmod(repo / "bin/tool", 0o755)
                for skill in ("bugate", "bugate-full-check"):
                    skill_file = repo / f".shared/skills/{skill}/SKILL.md"
                    skill_file.parent.mkdir(parents=True, exist_ok=True)
                    skill_file.write_text(f"# {skill}\n", encoding="utf-8")
                for name in contract.CODEX_GATE_AGENT_NAMES:
                    (repo / contract.CODEX_GATE_AGENT_SOURCE_DIR / name).write_text(
                        f'name = "{name}"\n', encoding="utf-8"
                    )
                for plugin in (".codex-plugin", ".claude-plugin"):
                    (repo / plugin / "plugin.json").write_text(
                        json.dumps({"name": "bugate", "version": "0.3.2"}) + "\n",
                        encoding="utf-8",
                    )
                env = os.environ.copy()
                env["GIT_CONFIG_GLOBAL"] = os.devnull
                env["GIT_CONFIG_SYSTEM"] = os.devnull
                subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
                subprocess.run(
                    ["git", "config", "user.name", "BUGate Legacy Safety Test"],
                    cwd=repo,
                    env=env,
                    check=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "bugate@example.invalid"],
                    cwd=repo,
                    env=env,
                    check=True,
                )
                subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
                subprocess.run(
                    ["git", "commit", "-q", "-m", "malicious synthetic tag"],
                    cwd=repo,
                    env=env,
                    check=True,
                )
                subprocess.run(
                    ["git", "tag", "-a", "v0.3.2", "-m", "malicious synthetic tag"],
                    cwd=repo,
                    env=env,
                    check=True,
                )
                with self.assertRaisesRegex(contract.ContractError, "impure"):
                    legacy.generate_legacy_manifest(repo, "v0.3.2")
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
