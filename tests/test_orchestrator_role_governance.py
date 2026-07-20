#!/usr/bin/env python3
"""End-to-end role controls for orchestrator and Core artifact mutators.

The suite fabricates an imported-mode SUT test repository in a temporary
directory and executes the real Core CLIs against it.  Role transitions use a
closed local port with ``memory_mode: best_effort`` so the controls are fully
deterministic and never depend on a live Memory Service.

Run directly with::

    python3 tests/test_orchestrator_role_governance.py
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1]
SCRIPTS = ENGINE / "scripts"
ORCHESTRATOR = SCRIPTS / "sdtd_orchestrator.py"
ROLE_CLI = SCRIPTS / "role_governance.py"
ROLE_GUARD = SCRIPTS / "check_role_evidence.py"
MULTIVIEW = SCRIPTS / "sdtd_multiview_cli_bridge.py"
ADVERSARIAL = SCRIPTS / "sdtd_adversarial_cli_bridge.py"
READABLE_CASES = SCRIPTS / "generate_sdtd_text_testcases.py"
SELF_HEALING = SCRIPTS / "self_healing_mvp.py"
REPORTS = SCRIPTS / "generate_sdtd_reports.py"

PRECODE = (
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
)
POSTRUN = ("04_execution_report.md", "05_knowledge_update.md")


def closed_port_url() -> str:
    """Reserve then release a loopback port; connect attempts fail immediately."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_snapshot(path: Path) -> tuple[tuple[str, str, str], ...] | None:
    """Content-addressed tree snapshot that also detects new directories."""

    if not path.exists():
        return None
    entries: list[tuple[str, str, str]] = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path).as_posix()
        if item.is_dir():
            entries.append(("dir", rel, ""))
        elif item.is_file():
            entries.append(("file", rel, sha256(item)))
    return tuple(entries)


class ImportedFixture:
    """Minimal SUT-neutral imported repository with a profile-owned UC."""

    def __init__(self, base: Path, *, mode: str | None = "required") -> None:
        self.root = base / "governed-sut-tests"
        self.root.mkdir(parents=True)
        self.profile = self.root / "bugate.profile.yaml"
        self.artifact = self.root / "usecases" / "UC-ORCH"
        self.implementation = self.root / "tests" / "test_UC-ORCH.py"
        self.memory_url = closed_port_url()
        (self.root / "bugate.config.yaml").write_text(
            "profile: bugate.profile.yaml\n", encoding="utf-8"
        )
        self._write_profile(mode)

    def _write_profile(self, mode: str | None) -> None:
        lines = [
            "artifact_dir_template: usecases/{uc}",
            "guarded_path_regex:",
            '  - "^tests/test_(?P<uc>[^/]+)[.]py$"',
            "required_precode_artifacts:",
            *[f"  - {name}" for name in PRECODE],
            "memory:",
            "  namespace: project:orchestrator-fixture",
        ]
        if mode is not None:
            lines.extend(
                [
                    "role_governance:",
                    f"  mode: {mode}",
                    "  memory_mode: best_effort",
                    "  evidence_dir: 00_role_evidence",
                    "  session_id_required: true",
                    "  require_distinct_sessions: true",
                    "  human_acceptance_artifacts:",
                    "    - 03b_adversarial_cases.yaml",
                    "  phases:",
                    "    pre_code:",
                    "      allowed_roles:",
                    "        - designer",
                    "    implementation:",
                    "      allowed_roles:",
                    "        - implementer",
                    "      requires_handoff_from:",
                    "        - designer",
                    "    post_run:",
                    "      allowed_roles:",
                    "        - reviewer",
                    "      requires_handoff_from:",
                    "        - implementer",
                ]
            )
        self.profile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def env(self, *, role: str | None = None, session: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if (
                key in {
                    "BUGATE_AGENT_ROLE",
                    "BUGATE_AGENT_RUNTIME",
                    "BUGATE_SESSION_ID",
                    "BUGATE_PROFILE",
                    "BUGATE_PROJECT_ROOT",
                    "BUGATE_ENGINE_ROOT",
                    "MEMORY_BUS_URL",
                    "MEMORY_BUS_PROJECT_TAG",
                    "MCP_API_KEY",
                    "MCP_API_KEY_AGENT",
                    "MCP_API_KEY_HUMAN",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                }
                or key.startswith("BUGATE_ROLE_")
                or key.startswith("BUGATE_RECEIPT_")
                or key.startswith("BUGATE_HANDOFF_")
                or key.startswith("BUGATE_SESSION_")
            ):
                env.pop(key, None)
        env.update(
            {
                "BUGATE_PROJECT_ROOT": str(self.root),
                "BUGATE_ENGINE_ROOT": str(ENGINE),
                "BUGATE_PROFILE": str(self.profile),
                "MEMORY_BUS_URL": self.memory_url,
                "MEMORY_BUS_PROJECT_TAG": "project:orchestrator-fixture",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (str(SCRIPTS), os.environ.get("PYTHONPATH", "")))
                ),
            }
        )
        if role is not None:
            env["BUGATE_AGENT_ROLE"] = role
            env["BUGATE_AGENT_RUNTIME"] = "codex"
        if session is not None:
            env["BUGATE_SESSION_ID"] = session
        return env

    def run(
        self,
        command: list[str | Path],
        *,
        role: str | None = None,
        session: str | None = None,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(item) for item in command],
            cwd=self.root,
            env=self.env(role=role, session=session),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def init(self, *, role: str | None, session: str | None) -> subprocess.CompletedProcess[str]:
        return self.run(
            [sys.executable, ORCHESTRATOR, self.artifact, "--init"],
            role=role,
            session=session,
        )

    def write_accepted_precode(self) -> None:
        """Replace init templates with a small, semantically accepted UC stack."""

        artifacts = {
            "01_business_brief.md": """---
gate: layer1_business_brief
gate_status: passed
sut_profile: fixture
---

# Business Brief

## Scope

Validate one neutral request outcome from captured contract evidence.

## Canonical Business Flows

Submit a request, observe acceptance, and query the recorded outcome.

## Clarification Gate

| dimension | status | open question |
|---|---|---|
| objective | clear | none |

## Propositions

| id | proposition | priority | verifiability | evidence_label | source |
|---|---|---|---|---|---|
| P-001 | An accepted request is queryable. | high | verifiable | fact | captured contract |

## Business Oracles

| id | oracle | observable evidence | evidence_label |
|---|---|---|---|
| O-001 | The query returns the accepted state. | captured response state | fact |

## Boundaries

Only the declared request and query flow is governed.

## Assumptions

The captured contract is current for this fixture.

## Open Questions

None open.
""",
            "02_testability.md": """---
gate: layer2_testability
gate_status: passed
sut_profile: fixture
---

# Testability

## Layer Decision Matrix

| proposition | chosen layer | cheaper layer considered | reason |
|---|---|---|---|
| P-001 | contract | static review | O-001 requires an observable response |

## Evidence Plan

| oracle | evidence source | probe or fixture | status |
|---|---|---|---|
| O-001 | captured contract | deterministic fixture probe | resolved |

## Dependencies

The imported test repository supplies the fixture probe.

## Deferred Claims

None.
""",
            "03_inventory.yaml": """gate: layer3_inventory
gate_status: passed
sut_profile: fixture
cases:
  - id: CASE-001
    intent: Verify the accepted request remains queryable
    priority: P1
    proposition_refs:
      - P-001
    oracle_refs:
      - O-001
    layer_decision: contract
    preconditions:
      - fixture request is accepted
    data_source:
      source: captured contract
      status: resolved
    expected_observations:
      - accepted state is returned
    implementation_target: tests/test_UC-ORCH.py
coverage_deferred: []
""",
            "03a_test_cases.md": """---
gate: readable_test_cases
gate_status: passed
sut_profile: fixture
---

# Test Cases

## CASE-001

- Intent: Verify the accepted request remains queryable.
- Preconditions: The fixture request is accepted.
- Action: Query the recorded outcome.
- Expected result: O-001 returns the accepted state for P-001.
""",
            "03b_adversarial_cases.yaml": """gate: adversarial_cases
gate_status: passed
sut_profile: fixture
dispatch_mode: real_peer_dispatch
adversarial_cases:
  - id: ADV-001
    risk: A duplicate query could expose inconsistent state
    scenario: Query the accepted outcome twice
    expected_oracle_pressure: Both observations must satisfy O-001
    disposition: absorbed
residual_risks: []
""",
        }
        for name, body in artifacts.items():
            (self.artifact / name).write_text(body, encoding="utf-8")


class OrchestratorRoleGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-orchestrator-role-")
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def assert_ok(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            0,
            proc.returncode,
            msg=f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )

    def assert_blocked(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            2,
            proc.returncode,
            msg=f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )

    def role_receipt(
        self,
        fixture: ImportedFixture,
        args: list[str | Path],
        *,
        role: str,
        session: str,
    ) -> dict[str, object]:
        proc = fixture.run(
            [sys.executable, ROLE_CLI, *args], role=role, session=session
        )
        self.assert_ok(proc)
        try:
            receipt = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"role CLI did not emit one JSON receipt: {exc}\n{proc.stdout}")
        self.assertIsInstance(receipt, dict)
        return receipt

    def lifecycle_marker(
        self, fixture: ImportedFixture, expected: str
    ) -> subprocess.CompletedProcess[str]:
        code = (
            "import sys; from pathlib import Path; "
            "from sdtd_orchestrator import print_auto_state; "
            "print_auto_state(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))"
        )
        proc = fixture.run(
            [sys.executable, "-c", code, fixture.artifact, "pre-code", "0"]
        )
        self.assert_ok(proc)
        self.assertIn(f"BUGate lifecycle status: {expected}", proc.stdout)
        return proc

    def test_legacy_and_explicit_off_init_keep_the_01_through_05_scaffold(self) -> None:
        for label, mode in (("legacy", None), ("explicit-off", "off")):
            with self.subTest(mode=label):
                fixture = ImportedFixture(self.tmp / label, mode=mode)
                proc = fixture.init(role=None, session=None)
                self.assert_ok(proc)
                actual = {item.name for item in fixture.artifact.iterdir() if item.is_file()}
                self.assertEqual(set(PRECODE + POSTRUN), actual)
                self.assertFalse((fixture.artifact / "00_orchestration").exists())

    def test_required_init_is_fail_closed_and_designer_only_gets_precode(self) -> None:
        fixture = ImportedFixture(self.tmp)

        unset = fixture.init(role=None, session=None)
        self.assert_blocked(unset)
        self.assertIn("BUGATE_AGENT_ROLE is unset", unset.stderr)
        self.assertFalse(fixture.artifact.exists())

        wrong = fixture.init(role="implementer", session="implementation-session")
        self.assert_blocked(wrong)
        self.assertIn("is not allowed in pre_code", wrong.stderr)
        self.assertFalse(fixture.artifact.exists())

        designer = fixture.init(role="designer", session="designer-session")
        self.assert_ok(designer)
        actual = {item.name for item in fixture.artifact.iterdir() if item.is_file()}
        self.assertEqual(set(PRECODE), actual)
        self.assertTrue(all(not (fixture.artifact / name).exists() for name in POSTRUN))
        self.assertFalse((fixture.artifact / "00_orchestration").exists())

    def test_direct_precode_mutators_reject_unset_role_before_any_write(self) -> None:
        fixture = ImportedFixture(self.tmp)
        controls = (
            (
                "multiview",
                MULTIVIEW,
                lambda path: ["run-all", path],
            ),
            (
                "adversarial",
                ADVERSARIAL,
                lambda path: ["run-all", path],
            ),
            (
                "readable-cases",
                READABLE_CASES,
                lambda path: [path, "--write"],
            ),
        )
        for index, (label, script, args_for) in enumerate(controls, start=1):
            with self.subTest(mutator=label):
                artifact = fixture.root / "usecases" / f"UC-DIRECT-{index}"
                proc = fixture.run([sys.executable, script, *args_for(artifact)])
                self.assert_blocked(proc)
                self.assertIn("BUGATE_AGENT_ROLE is unset", proc.stderr)
                self.assertFalse(artifact.exists(), "preflight must run before mkdir/write")

    def test_init_auto_combination_is_rejected_without_side_effects(self) -> None:
        fixture = ImportedFixture(self.tmp)
        artifact = fixture.root / "usecases" / "UC-COMBINED"
        proc = fixture.run(
            [sys.executable, ORCHESTRATOR, artifact, "--init", "--auto"],
            role="designer",
            session="designer-session",
        )
        self.assert_blocked(proc)
        self.assertIn("--init and --auto are separate operations", proc.stdout)
        self.assertIn("BUGate lifecycle status: BLOCKED", proc.stdout)
        self.assertFalse(artifact.exists())

    def test_accepted_precode_is_immutable_and_acceptance_unlocks_layer4(self) -> None:
        fixture = ImportedFixture(self.tmp)
        self.assert_ok(fixture.init(role="designer", session="designer-session"))
        fixture.write_accepted_precode()
        self.lifecycle_marker(fixture, "READY_FOR_HUMAN_ACCEPTANCE")

        no_human = fixture.run(
            [
                sys.executable,
                ROLE_CLI,
                "handoff",
                fixture.artifact,
                "--phase",
                "pre_code",
                "--to",
                "implementer",
            ],
            role="designer",
            session="designer-session",
        )
        self.assert_blocked(no_human)
        self.assertIn("required human acceptance receipt is missing", no_human.stderr)
        self.assertFalse((fixture.artifact / "00_role_evidence").exists())

        human = self.role_receipt(
            fixture,
            ["approve", fixture.artifact, "--approved-by", "fixture-owner"],
            role="designer",
            session="designer-session",
        )
        self.assertEqual("human_acceptance", human.get("event"))
        self.assertEqual(
            "best_effort_unavailable", (human.get("memory") or {}).get("status")
        )
        self.lifecycle_marker(fixture, "READY_FOR_DESIGNER_HANDOFF")

        accepted_tree = tree_snapshot(fixture.artifact)
        accepted_03b = sha256(fixture.artifact / "03b_adversarial_cases.yaml")
        auto = fixture.run(
            [sys.executable, ORCHESTRATOR, fixture.artifact, "--auto"],
            role="designer",
            session="designer-session",
        )
        self.assert_blocked(auto)
        self.assertIn("will not rewrite accepted pre-code evidence", auto.stderr)
        self.assertIn("BUGate lifecycle status: BLOCKED", auto.stdout)
        self.assertEqual(accepted_tree, tree_snapshot(fixture.artifact))
        self.assertEqual(accepted_03b, sha256(fixture.artifact / "03b_adversarial_cases.yaml"))

        bridge = fixture.run(
            [sys.executable, ADVERSARIAL, "run-all", fixture.artifact],
            role="designer",
            session="designer-session",
        )
        self.assert_blocked(bridge)
        self.assertIn("human-acceptance receipt", bridge.stderr)
        self.assertEqual(accepted_tree, tree_snapshot(fixture.artifact))
        self.assertEqual(accepted_03b, sha256(fixture.artifact / "03b_adversarial_cases.yaml"))

        designer_handoff = self.role_receipt(
            fixture,
            [
                "handoff",
                fixture.artifact,
                "--phase",
                "pre_code",
                "--to",
                "implementer",
            ],
            role="designer",
            session="designer-session",
        )
        self.lifecycle_marker(fixture, "BLOCKED")

        guard_before = fixture.run(
            [sys.executable, ROLE_GUARD, "tests/test_UC-ORCH.py"],
            role="implementer",
            session="implementer-session",
        )
        self.assert_blocked(guard_before)
        self.assertIn("implementer acceptance missing", guard_before.stderr)

        implementer_acceptance = self.role_receipt(
            fixture,
            [
                "accept",
                fixture.artifact,
                "--phase",
                "implementation",
                "--handoff-id",
                str(designer_handoff["receipt_sha256"]),
            ],
            role="implementer",
            session="implementer-session",
        )
        self.assertEqual("implementer_acceptance", implementer_acceptance.get("event"))
        self.lifecycle_marker(fixture, "IMPLEMENTATION_UNLOCKED")

        guard_after = fixture.run(
            [sys.executable, ROLE_GUARD, "tests/test_UC-ORCH.py"],
            role="implementer",
            session="implementer-session",
        )
        self.assert_ok(guard_after)

    def test_postrun_writers_wait_for_reviewer_then_complete_lifecycle(self) -> None:
        fixture = ImportedFixture(self.tmp)
        self.assert_ok(fixture.init(role="designer", session="designer-session"))
        fixture.write_accepted_precode()
        self.role_receipt(
            fixture,
            ["approve", fixture.artifact, "--approved-by", "fixture-owner"],
            role="designer",
            session="designer-session",
        )
        designer_handoff = self.role_receipt(
            fixture,
            [
                "handoff",
                fixture.artifact,
                "--phase",
                "pre_code",
                "--to",
                "implementer",
            ],
            role="designer",
            session="designer-session",
        )
        self.role_receipt(
            fixture,
            [
                "accept",
                fixture.artifact,
                "--phase",
                "implementation",
                "--handoff-id",
                str(designer_handoff["receipt_sha256"]),
            ],
            role="implementer",
            session="implementer-session",
        )
        fixture.implementation.parent.mkdir(parents=True)
        fixture.implementation.write_text(
            "def test_fixture_outcome():\n    assert True\n", encoding="utf-8"
        )
        implementer_handoff = self.role_receipt(
            fixture,
            [
                "handoff",
                fixture.artifact,
                "--phase",
                "implementation",
                "--to",
                "reviewer",
                "--implementation-file",
                fixture.implementation,
            ],
            role="implementer",
            session="implementer-session",
        )
        self.lifecycle_marker(fixture, "READY_FOR_REVIEWER_HANDOFF")

        pytest_log = fixture.root / "pytest.log"
        pytest_log.write_text("1 passed\n", encoding="utf-8")
        direct_outputs = (
            fixture.artifact / "direct_self_healing.json",
            fixture.artifact / "direct_self_healing.md",
            fixture.artifact / "direct_repair_plan.md",
        )
        orchestrator_outputs = (
            fixture.artifact / "self_healing.json",
            fixture.artifact / "self_healing.md",
            fixture.artifact / "self_healing_repair_plan.md",
            *(fixture.artifact / name for name in POSTRUN),
        )

        blocked_orchestrator = fixture.run(
            [
                sys.executable,
                ORCHESTRATOR,
                fixture.artifact,
                "--auto",
                "--scope",
                "post-run",
                "--pytest-log",
                pytest_log,
                "--command",
                "python3 -m unittest",
                "--env",
                "fixture",
                "--exit-code",
                "0",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_blocked(blocked_orchestrator)
        self.assertIn("reviewer acceptance missing", blocked_orchestrator.stderr)
        self.assertIn("BUGate lifecycle status: BLOCKED", blocked_orchestrator.stdout)

        blocked_healing = fixture.run(
            [
                sys.executable,
                SELF_HEALING,
                "--artifact-dir",
                fixture.artifact,
                "--pytest-log",
                pytest_log,
                "--json-output",
                direct_outputs[0],
                "--md-output",
                direct_outputs[1],
                "--repair-plan-output",
                direct_outputs[2],
                "--exit-code",
                "0",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_blocked(blocked_healing)
        self.assertIn("reviewer acceptance missing", blocked_healing.stderr)

        blocked_reports = fixture.run(
            [
                sys.executable,
                REPORTS,
                fixture.artifact,
                "--pytest-log",
                pytest_log,
                "--command",
                "python3 -m unittest",
                "--env",
                "fixture",
                "--exit-code",
                "0",
                "--write",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_blocked(blocked_reports)
        self.assertIn("reviewer acceptance missing", blocked_reports.stderr)
        self.assertTrue(all(not path.exists() for path in direct_outputs + orchestrator_outputs))

        reviewer_acceptance = self.role_receipt(
            fixture,
            [
                "accept",
                fixture.artifact,
                "--phase",
                "post_run",
                "--handoff-id",
                str(implementer_handoff["receipt_sha256"]),
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual("reviewer_acceptance", reviewer_acceptance.get("event"))
        self.lifecycle_marker(fixture, "POST_RUN_ACTIVE")

        healing = fixture.run(
            [
                sys.executable,
                SELF_HEALING,
                "--artifact-dir",
                fixture.artifact,
                "--pytest-log",
                pytest_log,
                "--json-output",
                direct_outputs[0],
                "--md-output",
                direct_outputs[1],
                "--repair-plan-output",
                direct_outputs[2],
                "--exit-code",
                "0",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_ok(healing)
        self.assertTrue(all(path.is_file() for path in direct_outputs))

        reports = fixture.run(
            [
                sys.executable,
                REPORTS,
                fixture.artifact,
                "--pytest-log",
                pytest_log,
                "--command",
                "python3 -m unittest",
                "--env",
                "fixture",
                "--exit-code",
                "0",
                "--write",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_ok(reports)
        self.assertTrue(all((fixture.artifact / name).is_file() for name in POSTRUN))

        orchestrator = fixture.run(
            [
                sys.executable,
                ORCHESTRATOR,
                fixture.artifact,
                "--auto",
                "--scope",
                "post-run",
                "--pytest-log",
                pytest_log,
                "--command",
                "python3 -m unittest",
                "--env",
                "fixture",
                "--exit-code",
                "0",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assert_ok(orchestrator)
        self.assertIn("BUGate lifecycle status: POST_RUN_ACTIVE", orchestrator.stdout)
        self.assertTrue(all(path.is_file() for path in orchestrator_outputs))

        for name in POSTRUN:
            path = fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "gate_status: draft", "gate_status: passed", 1
                ),
                encoding="utf-8",
            )
        completion = self.role_receipt(
            fixture,
            [
                "complete",
                fixture.artifact,
                "--phase",
                "post_run",
                "--run-command",
                "python3 -m unittest",
                "--exit-code",
                "0",
                "--evidence-file",
                pytest_log,
                "--gate-status",
                "passed",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual("closed", completion.get("resulting_state"))
        self.lifecycle_marker(fixture, "CLOSED")
        self.assertFalse((fixture.artifact / "00_orchestration").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
