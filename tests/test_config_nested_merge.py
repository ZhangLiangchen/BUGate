#!/usr/bin/env python3
"""Standalone regression tests for BUGate nested config loading and merging."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from bugate_core import deep_merge, load_config, parse_simple_yaml, split_frontmatter  # noqa: E402
from check_agent_role_paths import forbidden_patterns  # noqa: E402
from role_governance import RoleConfigError  # noqa: E402
from self_heal_policy import self_healing_policy  # noqa: E402


class ConfigNestedMergeTests(unittest.TestCase):
    def write_workspace(self, base: str, profile: str | None = None) -> Path:
        workspace = Path(self.tempdir.name)
        (workspace / "bugate.config.yaml").write_text(base, encoding="utf-8")
        if profile is not None:
            (workspace / "bugate.profile.yaml").write_text(profile, encoding="utf-8")
        return workspace

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="bugate-config-test-")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_recursive_mapping_merge_scalar_override_and_list_replacement(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
required_precode_artifacts:
  - base-01.md
  - base-02.yaml
role_governance:
  mode: required
  memory_mode: required
  phases:
    pre_code:
      allowed_roles:
        - designer
    implementation:
      allowed_roles:
        - implementer
      requires_handoff_from:
        - designer
agent_roles:
  designer:
    read:
      - '^source/.*$'
    write:
      - '^tests/.*$'
""",
            """\
required_precode_artifacts:
  - profile-only.md
role_governance:
  memory_mode: best_effort
  phases:
    implementation:
      allowed_roles:
        - specialist_implementer
agent_roles:
  designer:
    write:
      - '^generated/.*$'
""",
        )

        config = load_config(workspace)

        self.assertEqual(config["required_precode_artifacts"], ["profile-only.md"])
        self.assertEqual(config["role_governance"]["mode"], "required")
        self.assertEqual(config["role_governance"]["memory_mode"], "best_effort")
        self.assertEqual(
            config["role_governance"]["phases"]["pre_code"]["allowed_roles"],
            ["designer"],
        )
        implementation = config["role_governance"]["phases"]["implementation"]
        self.assertEqual(implementation["allowed_roles"], ["specialist_implementer"])
        self.assertEqual(implementation["requires_handoff_from"], ["designer"])
        self.assertEqual(config["agent_roles"]["designer"]["read"], ["^source/.*$"])
        self.assertEqual(config["agent_roles"]["designer"]["write"], ["^generated/.*$"])

    def test_deep_merge_replaces_across_types_without_mutating_inputs(self) -> None:
        base = {"mapping": {"keep": "yes", "replace": ["base"]}, "scalar": "base"}
        profile = {"mapping": {"replace": ["profile"]}, "scalar": {"nested": "value"}}

        merged = deep_merge(base, profile)
        merged["mapping"]["replace"].append("changed")

        self.assertEqual(base["mapping"]["replace"], ["base"])
        self.assertEqual(profile["mapping"]["replace"], ["profile"])
        self.assertEqual(merged["mapping"]["keep"], "yes")
        self.assertEqual(merged["scalar"], {"nested": "value"})

    def test_namespace_is_canonicalized_per_document_before_merge(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
memory:
  namespace: project:base-new
""",
            "namespace: project:profile-legacy\n",
        )

        config = load_config(workspace)

        self.assertEqual(config["namespace"], "project:profile-legacy")
        self.assertEqual(config["memory"]["namespace"], "project:profile-legacy")

    def test_new_namespace_overrides_legacy_and_conflict_uses_new_form(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
namespace: project:base-legacy
""",
            """\
namespace: project:profile-legacy
memory:
  namespace: project:profile-new
""",
        )

        config = load_config(workspace)

        self.assertEqual(config["namespace"], "project:profile-new")
        self.assertEqual(config["memory"]["namespace"], "project:profile-new")

    def test_legacy_profile_without_role_governance_preserves_default_off(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
role_governance:
  mode: off
guarded_path_regex: []
""",
            """\
artifact_dir: docs/usecases/LEGACY-001
guarded_path_regex:
  - '^tests/legacy/.*[.]py$'
agent_roles:
  implementer:
    - '^source/.*$'
""",
        )

        config = load_config(workspace)

        self.assertEqual(config["role_governance"], {"mode": "off"})
        self.assertEqual(config["artifact_dir"], "docs/usecases/LEGACY-001")
        self.assertEqual(config["guarded_path_regex"], ["^tests/legacy/.*[.]py$"])
        self.assertEqual(config["agent_roles"]["implementer"], ["^source/.*$"])

    def test_legacy_duplicate_keys_keep_last_value_compatibility(self) -> None:
        workspace = self.write_workspace(
            """\
---
role_governance:
  mode: off
guarded_path_regex:
  - '^tests/old/.*$'
guarded_path_regex:
  - '^tests/current/.*$'
...
"""
        )

        config = load_config(workspace)

        self.assertEqual(config["role_governance"], {"mode": "off"})
        self.assertEqual(config["guarded_path_regex"], ["^tests/current/.*$"])

    def test_parse_simple_yaml_and_frontmatter_behavior_is_unchanged(self) -> None:
        simple = parse_simple_yaml(
            """\
memory:
  namespace: project:legacy-flat
items:
  - one
  - two
"""
        )
        frontmatter, body = split_frontmatter(
            "---\ngate_status: passed\ntags:\n  - smoke\n---\nbody\n"
        )

        self.assertEqual(
            simple,
            {"memory": [], "namespace": "project:legacy-flat", "items": ["one", "two"]},
        )
        self.assertEqual(frontmatter, {"gate_status": "passed", "tags": ["smoke"]})
        self.assertEqual(body, "body\n")

    def test_malformed_nested_config_fails_instead_of_downgrading_required(self) -> None:
        workspace = self.write_workspace(
            """\
role_governance:
  mode required
  memory_mode: required
"""
        )

        with self.assertRaisesRegex(
            ValueError, r"BUGate config parse error: .*bugate[.]config[.]yaml:2: expected 'key: value'"
        ):
            load_config(workspace)

    def test_unexpected_config_indentation_has_clear_error(self) -> None:
        workspace = self.write_workspace(
            """\
role_governance:
  mode: required
    memory_mode: required
"""
        )

        with self.assertRaisesRegex(ValueError, r"unexpected indentation or malformed"):
            load_config(workspace)

    def test_core_default_role_governance_is_off(self) -> None:
        config = load_config(ROOT)
        self.assertEqual(config["bugate"]["mode"], "core")
        self.assertEqual(config["mode"], "core")
        self.assertEqual(config["bugate"]["version"], "0.1")
        self.assertEqual(config["version"], "0.1")
        self.assertEqual(config["role_governance"], {"mode": "off"})
        self.assertEqual(config["namespace"], config["memory"]["namespace"])

    def test_core_default_self_healing_is_off(self) -> None:
        config = load_config(ROOT)
        self.assertEqual(config["self_healing"], {"mode": "off"})
        self.assertEqual("off", self_healing_policy(config)["mode"])

    def test_self_healing_merges_nested_without_disturbing_role_governance(self) -> None:
        """A profile self_healing block merges recursively and stays isolated.

        The two top-level policy keys are siblings: raising self_healing must not
        move role_governance, and a profile list must replace the base list
        wholesale rather than accumulating.
        """

        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
role_governance:
  mode: off
self_healing:
  mode: off
  allowed_write_regex:
    - '^base/.*$'
  max_attempts: 3
""",
            """\
role_governance:
  mode: required
self_healing:
  mode: verify
  allowed_write_regex:
    - '^tests/test_.*[.]py$'
  verification_commands:
    - 'python3 -m pytest tests -q'
  falsification_spec: spec/falsification.yaml
""",
        )

        config = load_config(workspace)

        self.assertEqual(config["role_governance"]["mode"], "required")
        self.assertEqual(config["self_healing"]["mode"], "verify")
        # Profile lists replace base lists wholesale.
        self.assertEqual(config["self_healing"]["allowed_write_regex"], ["^tests/test_.*[.]py$"])
        # Base scalars the profile did not restate survive the merge.
        self.assertEqual(config["self_healing"]["max_attempts"], "3")

        policy = self_healing_policy(config)
        self.assertEqual("verify", policy["mode"])
        self.assertEqual(["^tests/test_.*[.]py$"], policy["allowed_write_regex"])
        self.assertEqual(["python3 -m pytest tests -q"], policy["verification_commands"])
        self.assertEqual("spec/falsification.yaml", policy["falsification_spec"])
        self.assertEqual(3, policy["max_attempts"])
        self.assertTrue(policy["independent_review_required"])

    def test_self_healing_independent_review_cannot_be_disabled(self) -> None:
        """The anti-fake-green review is a frozen control, not a preference."""

        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
self_healing:
  mode: off
""",
            """\
self_healing:
  mode: verify
  independent_review_required: false
""",
        )

        config = load_config(workspace)
        with self.assertRaisesRegex(RoleConfigError, "independent_review_required cannot be false"):
            self_healing_policy(config)

    def test_self_healing_human_approval_cannot_be_disabled(self) -> None:
        workspace = self.write_workspace(
            "self_healing:\n"
            "  mode: apply_with_approval\n"
            "  human_approval_required: false\n"
        )
        config = load_config(workspace)
        with self.assertRaisesRegex(
            RoleConfigError, "human_approval_required cannot be false"
        ):
            self_healing_policy(config)

    def test_self_healing_rejects_unknown_keys_and_bad_mode(self) -> None:
        for block, expected in (
            ("self_healing:\n  mode: repair_everything\n", "self_healing.mode must be"),
            ("self_healing:\n  mode: verify\n  yolo: true\n", "unknown key"),
            ("self_healing:\n  mode: verify\n  max_attempts: 0\n", "max_attempts must be >= 1"),
            (
                "self_healing:\n  mode: verify\n  allowed_write_regex:\n    - '['\n",
                "invalid self_healing.allowed_write_regex",
            ),
        ):
            with self.subTest(block=block):
                workspace = self.write_workspace(block)
                with self.assertRaises(RoleConfigError) as caught:
                    self_healing_policy(load_config(workspace))
                self.assertIn(expected, str(caught.exception))

    def test_bugate_mode_version_aliases_merge_without_role_mode_collision(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
bugate:
  mode: core
  version: 0.1
role_governance:
  mode: off
""",
            """\
mode: imported
version: 0.2
""",
        )

        config = load_config(workspace)

        self.assertEqual(config["bugate"], {"mode": "imported", "version": "0.2"})
        self.assertEqual(config["mode"], "imported")
        self.assertEqual(config["version"], "0.2")
        self.assertEqual(config["role_governance"]["mode"], "off")

    def test_nested_bugate_aliases_win_same_document_conflicts(self) -> None:
        workspace = self.write_workspace(
            """\
mode: legacy-mode
version: legacy-version
bugate:
  mode: nested-mode
  version: nested-version
"""
        )

        config = load_config(workspace)

        self.assertEqual(config["mode"], "nested-mode")
        self.assertEqual(config["version"], "nested-version")
        self.assertEqual(config["bugate"]["mode"], "nested-mode")
        self.assertEqual(config["bugate"]["version"], "nested-version")

    def test_wave8_weekly_preserves_aliases_and_precedence(self) -> None:
        cases = (
            ("wave8_evidence_glob: top/*.json\n", None, "top/*.json"),
            ("wave8:\n  evidence_glob: nested/*.json\n", None, "nested/*.json"),
            ("evidence_glob: legacy/*.json\n", None, "legacy/*.json"),
            (
                """\
wave8_evidence_glob: top/*.json
evidence_glob: legacy/*.json
wave8:
  evidence_glob: nested/*.json
""",
                None,
                "top/*.json",
            ),
            (
                """\
wave8_evidence_glob: top/*.json
evidence_glob: legacy/*.json
wave8:
  evidence_glob: nested/*.json
""",
                "env/*.json",
                "env/*.json",
            ),
        )

        for config_text, env_override, expected in cases:
            with self.subTest(expected=expected):
                workspace = self.write_workspace(config_text)
                env = os.environ.copy()
                for key in (
                    "BUGATE_PROFILE",
                    "WAVE8_EVIDENCE_GLOB",
                    "WAVE8_FALSIFICATION_SPEC",
                    "WAVE8_REPORTS_DIR",
                    "WAVE8_ARTIFACT_ROOT",
                ):
                    env.pop(key, None)
                env["BUGATE_PROJECT_ROOT"] = str(workspace)
                if env_override:
                    env["WAVE8_EVIDENCE_GLOB"] = env_override

                result = subprocess.run(
                    ["bash", str(ROOT / "bin" / "wave8-weekly")],
                    cwd=workspace,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertIn(f"evidence glob: {expected}", result.stdout)
                self.assertNotIn("No Wave 8 evidence glob configured", result.stderr)

    def test_agent_roles_support_bare_scoped_and_custom_roles(self) -> None:
        config = {
            "agent_roles": {
                "implementer": ["^source/.*$"],
                "designer": {"read": ["^contract/.*$"], "write": ["^tests/.*$"]},
                "Security_Reviewer": {"read": ["^private/.*$"], "write": []},
            }
        }

        self.assertEqual(forbidden_patterns(config, "implementer", "Read"), ["^source/.*$"])
        self.assertEqual(forbidden_patterns(config, "implementer", "Write"), ["^source/.*$"])
        self.assertEqual(forbidden_patterns(config, "designer", "Read"), ["^contract/.*$"])
        self.assertEqual(forbidden_patterns(config, "designer", "Write"), ["^tests/.*$"])
        self.assertEqual(
            forbidden_patterns(config, "security_reviewer", "Read"), ["^private/.*$"]
        )

    def test_role_guard_consumes_profile_merged_by_load_config(self) -> None:
        workspace = self.write_workspace(
            """\
profile: bugate.profile.yaml
agent_roles:
  implementer:
    read:
      - '^base-source/.*$'
    write:
      - '^base-tests/.*$'
""",
            """\
agent_roles:
  implementer:
    write:
      - '^profile-tests/.*$'
""",
        )
        env = os.environ.copy()
        env.update(
            {
                "BUGATE_PROJECT_ROOT": str(workspace),
                "BUGATE_AGENT_ROLE": "implementer",
            }
        )
        payload = json.dumps(
            {"tool_name": "Write", "tool_input": {"file_path": "profile-tests/test_case.py"}}
        )

        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_agent_role_paths.py")],
            input=payload,
            text=True,
            capture_output=True,
            cwd=workspace,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("BUGate agent-role path isolation", result.stderr)
        self.assertIn("profile-tests/test_case.py", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
