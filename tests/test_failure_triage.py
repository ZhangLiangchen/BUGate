#!/usr/bin/env python3
"""Failure-attribution scenarios 1-9 of the self-healing acceptance matrix.

Each scenario is an independently runnable test method that drives the real
``--scope self-heal`` entry against a synthetic imported repository and asserts
the exact process exit code, the exact gate ``status``, at least one exact
``blocking_reasons`` code, and the exact ``healing_eligible`` boolean recorded in
``self_healing.json``.  Nothing is asserted loosely: ``assertNotEqual(0, rc)``
would pass for the wrong reason, so every case pins the value.

Every rule also carries a counter-example test.  The v0.4.4 classifier failed
precisely because its rules over-fired on ordinary log text, so a rule that
cannot be shown *not* to fire is not evidence of anything.

Run directly with::

    python3 tests/test_failure_triage.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
ENGINE = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ENGINE / "scripts"))

import failure_triage as triage_module  # noqa: E402
from bugate_selfheal_fixture import (  # noqa: E402
    GOLDEN_COMMAND,
    GOLDEN_LOG_NAME,
    ImportedFixture,
    gate_result,
    tree_snapshot,
)


# --------------------------------------------------------------------------
# Runner logs.  Each is minimal and carries exactly the evidence its scenario
# claims -- no incidental keywords that would make the assertion ambiguous.
# --------------------------------------------------------------------------

LOG_ENVIRONMENT = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   ConnectionRefusedError: [Errno 61] Connection refused
tests/test_UC-SELFHEAL.py:5: ConnectionRefusedError
"""

LOG_DEPENDENCY_LOCAL = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   ModuleNotFoundError: No module named 'tests.support'
tests/test_UC-SELFHEAL.py:2: ModuleNotFoundError
"""

LOG_DEPENDENCY_DISTRIBUTION = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   ModuleNotFoundError: No module named 'absent_distribution'
tests/test_UC-SELFHEAL.py:2: ModuleNotFoundError
"""

LOG_FIXTURE = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome ERROR
E       fixture 'captured_probe' not found
tests/test_UC-SELFHEAL.py:5: Error
"""

LOG_SELECTOR = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   AttributeError: <module 'probe'> does not have the attribute 'query'
tests/test_UC-SELFHEAL.py:5: AttributeError
"""

LOG_TEST_CODE = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   NameError: name 'exepcted' is not defined
tests/test_UC-SELFHEAL.py:5: NameError
"""

EVIDENCE_BOUND_REPAIR = """\
import json
from pathlib import Path

OBSERVED = 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]

def test_recorded_outcome():
    assert EXPECTED == OBSERVED

test_recorded_outcome()
"""

LOG_SUT_VIOLATION = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   assert response.status_code == 200
E   AssertionError: expected 200 got 500
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_AUTH = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   AssertionError: rejected with status_code 401 before the handler ran
E   assert 401 == 200
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_AUTH_BARE_401 = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   assert 401 == 200
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_AUTH_BARE_403 = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   assert 403 == 204
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_AUTH_DISTANT_STATUS = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   request status after a deliberately long diagnostic explanation was 401
E   AssertionError: expected 200 got 401
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_SUT_VALUE_TYPE_ERROR = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
CAPTURED_SUT_RESPONSE body={"count": null}
Traceback (most recent call last):
  File "tests/test_UC-SELFHEAL.py", line 5, in test_recorded_outcome
    assert body["count"] + 1 == 4
TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'
"""

LOG_SUT_VALUE_ATTRIBUTE_ERROR = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
CAPTURED_SUT_RESPONSE body={"result": {}}
Traceback (most recent call last):
  File "tests/test_UC-SELFHEAL.py", line 5, in test_recorded_outcome
    assert body["result"].state == "accepted"
AttributeError: 'dict' object has no attribute 'state'
"""

LOG_FLAKY = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome RERUN
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   AssertionError: expected 200 got 200
tests/test_UC-SELFHEAL.py:5: AssertionError
"""

LOG_AMBIGUOUS = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
the runner stopped without a recognizable reason
"""


class TriageScenarioTests(unittest.TestCase):
    """Scenarios 1-9: run the real gate and pin every reported value."""

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-triage-")
        self.tmp = Path(self.tmp_ctx.name)
        self.case_number = 0
        self.last_test_tree_before = None
        self.last_test_tree_after = None
        self.last_implementation_before: bytes | None = None
        self.last_implementation_after: bytes | None = None
        self.last_fixture: ImportedFixture | None = None

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    # ------------------------------------------------------------- utilities

    def triage(self, log: str, *, mode: str = "verify") -> tuple[int, dict, dict]:
        """Run one triage step; return ``(exit_code, gate result, self_healing.json)``."""

        self.case_number += 1
        fixture = ImportedFixture(
            self.tmp / f"case-{self.case_number}", self_healing={"mode": mode}
        )
        fixture.drive_to_post_run()
        # A workspace-local package so the dependency discriminator has something
        # real to resolve against.
        (fixture.root / "tests" / "support").mkdir(parents=True, exist_ok=True)
        fixture.write_log(log)
        self.last_test_tree_before = tree_snapshot(fixture.root / "tests")
        self.last_implementation_before = fixture.implementation.read_bytes()
        proc = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        self.last_test_tree_after = tree_snapshot(fixture.root / "tests")
        self.last_implementation_after = fixture.implementation.read_bytes()
        self.last_fixture = fixture
        payload = json.loads((fixture.artifact / "self_healing.json").read_text(encoding="utf-8"))
        return proc.returncode, gate_result(proc), payload

    def assert_case(
        self,
        log: str,
        *,
        exit_code: int,
        status: str,
        blocking_reason: str | None,
        healing_eligible: bool,
        failure_owner: str,
        failure_subtype: str,
        target_layer_reached: bool | None = None,
        zero_write: bool = False,
    ) -> dict:
        rc, gate, payload = self.triage(log)
        self.assertEqual(
            {"status", "exit_code", "blocking_reasons", "artifact_paths", "next_action"},
            set(gate),
        )
        self.assertEqual(exit_code, rc, msg=json.dumps(gate, indent=2))
        self.assertEqual(status, gate["status"])
        self.assertEqual(exit_code, gate["exit_code"])
        if blocking_reason is None:
            self.assertEqual([], gate["blocking_reasons"])
            self.assertEqual([], payload["blocking_reasons"])
        else:
            self.assertIn(blocking_reason, gate["blocking_reasons"])
            self.assertIn(blocking_reason, payload["blocking_reasons"])
        self.assertIs(healing_eligible, payload["healing_eligible"])
        self.assertEqual(failure_owner, payload["failure_owner"])
        self.assertEqual(failure_subtype, payload["failure_subtype"])
        if target_layer_reached is not None:
            self.assertIs(target_layer_reached, payload["target_layer_reached"])
        if zero_write:
            self.assertIsNotNone(self.last_test_tree_before)
            self.assertEqual(
                self.last_test_tree_before,
                self.last_test_tree_after,
                "a non-healable verdict changed bytes below the governed tests tree",
            )
            self.assertEqual(
                self.last_implementation_before,
                self.last_implementation_after,
                "a non-healable verdict changed the governed implementation bytes",
            )
        self.assertEqual("bugate.failure-triage/v1", payload["schema_version"])
        # The seven legacy keys keep their meaning and their position.
        self.assertEqual(
            ["overall", "exit_code", "primary_classification", "exclusions",
             "sut_defect_admissible", "failures", "next_action"],
            list(payload)[:7],
        )
        return payload

    def assert_candidate_path(self, log: str, *, failure_subtype: str) -> None:
        """Drive an eligible triage through a real, non-writing proposal.

        This closes the stage-4 coverage hole where fixture/selector cases ran
        only in ``diagnose`` mode and therefore could never demonstrate that
        their classification actually unblocked the candidate state machine.
        """

        self.case_number += 1
        fixture = ImportedFixture(
            self.tmp / f"candidate-{self.case_number}",
            self_healing={
                "mode": "verify",
                "allowed_write_regex": [r"^tests/test_.*\.py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
            },
        )
        fixture.drive_to_post_run(ImportedFixture.BROKEN_IMPLEMENTATION)
        if failure_subtype != "test_code_or_assertion":
            (fixture.implementation.parent / "contract.json").write_text(
                '{"expected": 200}\n', encoding="utf-8"
            )
        fixture.write_falsification_spec()
        fixture.write_log(log)
        test_bytes = fixture.implementation.read_bytes()

        triaged = fixture.run_self_heal(
            "--pytest-log",
            GOLDEN_LOG_NAME,
            "--command",
            GOLDEN_COMMAND,
            "--exit-code",
            "1",
        )
        triaged_gate = gate_result(triaged)
        triaged_payload = json.loads(
            (fixture.artifact / "self_healing.json").read_text(encoding="utf-8")
        )
        self.assertEqual(0, triaged.returncode, triaged.stderr)
        self.assertEqual(0, triaged_gate["exit_code"])
        self.assertEqual("triaged", triaged_gate["status"])
        self.assertEqual([], triaged_gate["blocking_reasons"])
        self.assertTrue(triaged_payload["healing_eligible"])
        self.assertEqual(failure_subtype, triaged_payload["failure_subtype"])
        self.assertEqual(triaged_payload["attempt_id"], fixture.sidecar_attempt_id())

        handed_off = fixture.run_self_heal("--self-heal-step", "handoff")
        handed_off_gate = gate_result(handed_off)
        self.assertEqual(0, handed_off.returncode, handed_off.stderr)
        self.assertEqual(0, handed_off_gate["exit_code"])
        self.assertEqual("healing_active", handed_off_gate["status"])
        self.assertEqual([], handed_off_gate["blocking_reasons"])

        accepted = fixture.run_self_heal(
            "--self-heal-step",
            "accept",
            role="implementer",
            session="healer-session",
        )
        accepted_gate = gate_result(accepted)
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        self.assertEqual(0, accepted_gate["exit_code"])
        self.assertEqual("healing_active", accepted_gate["status"])
        self.assertEqual([], accepted_gate["blocking_reasons"])

        # Fixture/selector triage logs do not prove the NameError needed by the
        # narrow literal exception.  Their positive candidate therefore reads
        # the profile-declared evidence and turns red under its exact mutation;
        # reusing the unrelated literal repair here would manufacture a green
        # proposal for the wrong failure.
        repair = (
            ImportedFixture.REPAIRED_IMPLEMENTATION
            if failure_subtype == "test_code_or_assertion"
            else EVIDENCE_BOUND_REPAIR
        )
        candidate = fixture.candidate({"tests/test_UC-SELFHEAL.py": repair})
        proposed = fixture.run_self_heal(
            "--self-heal-step",
            "propose",
            "--candidate-dir",
            str(candidate),
            role="implementer",
            session="healer-session",
        )
        proposed_gate = gate_result(proposed)
        self.assertEqual(0, proposed.returncode, proposed.stderr)
        self.assertEqual(0, proposed_gate["exit_code"])
        self.assertEqual("healing_active", proposed_gate["status"])
        self.assertEqual([], proposed_gate["blocking_reasons"])
        self.assertTrue(
            any(path.endswith("candidate.patch") for path in proposed_gate["artifact_paths"])
        )
        # ``verify`` may store a candidate but may not apply it.
        self.assertEqual(test_bytes, fixture.implementation.read_bytes())

    # ---------------------------------------------------------- scenarios 1-9

    def test_01_environment_or_resource_failure_is_never_healing_eligible(self) -> None:
        payload = self.assert_case(
            LOG_ENVIRONMENT,
            exit_code=2,
            status="blocked",
            blocking_reason="environment_owner_not_healable",
            healing_eligible=False,
            failure_owner="environment",
            failure_subtype="environment_or_resource",
            target_layer_reached=False,
            zero_write=True,
        )
        self.assertIn("do NOT edit any test asset", payload["recommended_action"])

    def test_02_dependency_or_import_failure_is_classified_by_where_the_module_lives(self) -> None:
        workspace_local = self.assert_case(
            LOG_DEPENDENCY_LOCAL,
            exit_code=0,
            status="triaged",
            blocking_reason=None,
            healing_eligible=True,
            failure_owner="test_asset",
            failure_subtype="dependency_or_import",
        )
        self.assertTrue(workspace_local["owner_evidence"].startswith("workspace_module:"))

    def test_03_fixture_or_test_data_defect_allows_a_candidate(self) -> None:
        self.assert_case(
            LOG_FIXTURE,
            exit_code=0,
            status="triaged",
            blocking_reason=None,
            healing_eligible=True,
            failure_owner="test_asset",
            failure_subtype="fixture_or_test_data",
        )
        self.assert_candidate_path(LOG_FIXTURE, failure_subtype="fixture_or_test_data")

    def test_04_selector_or_mock_defect_allows_a_candidate(self) -> None:
        self.assert_case(
            LOG_SELECTOR,
            exit_code=0,
            status="triaged",
            blocking_reason=None,
            healing_eligible=True,
            failure_owner="test_asset",
            failure_subtype="selector_or_mock",
        )
        self.assert_candidate_path(LOG_SELECTOR, failure_subtype="selector_or_mock")

    def test_05_test_code_defect_allows_a_candidate(self) -> None:
        self.assert_case(
            LOG_TEST_CODE,
            exit_code=0,
            status="triaged",
            blocking_reason=None,
            healing_eligible=True,
            failure_owner="test_asset",
            failure_subtype="test_code_or_assertion",
            target_layer_reached=False,
        )
        self.assert_candidate_path(LOG_TEST_CODE, failure_subtype="test_code_or_assertion")

    def test_06_confirmed_sut_oracle_violation_produces_a_defect_draft_not_a_repair(self) -> None:
        payload = self.assert_case(
            LOG_SUT_VIOLATION,
            exit_code=2,
            status="blocked",
            blocking_reason="sut_defect_not_healable",
            healing_eligible=False,
            failure_owner="sut",
            failure_subtype="sut_behavior_violation",
            target_layer_reached=True,
            zero_write=True,
        )
        self.assertEqual(["O-001"], payload["oracle_refs"])
        self.assertIn("keep the failing assertion", payload["recommended_action"])

    def test_07_auth_or_precondition_failure_is_not_a_business_defect(self) -> None:
        for name, log in {
            "status_token": LOG_AUTH,
            "bare_401_assertion": LOG_AUTH_BARE_401,
            "bare_403_assertion": LOG_AUTH_BARE_403,
            "distant_status_token": LOG_AUTH_DISTANT_STATUS,
        }.items():
            with self.subTest(name=name):
                payload = self.assert_case(
                    log,
                    exit_code=2,
                    status="blocked",
                    blocking_reason="target_layer_not_reached",
                    healing_eligible=False,
                    failure_owner="unresolved",
                    failure_subtype="auth_or_precondition",
                    target_layer_reached=False,
                    zero_write=True,
                )
                self.assertNotEqual("sut", payload["failure_owner"])
                self.assertFalse(
                    payload["failure_owner"] == "sut"
                    and payload["confidence"] == "high"
                )

    def test_08_flaky_or_transient_failure_asks_for_evidence_not_an_edit(self) -> None:
        payload = self.assert_case(
            LOG_FLAKY,
            exit_code=2,
            status="blocked",
            blocking_reason="flaky_evidence_insufficient",
            healing_eligible=False,
            failure_owner="unresolved",
            failure_subtype="flaky_or_transient",
            zero_write=True,
        )
        self.assertIn("repeat evidence", payload["recommended_action"])

    def test_sut_derived_type_and_attribute_errors_are_not_test_asset_repairs(self) -> None:
        """A test-frame crash is a manifestation, not ownership evidence."""

        for name, log, matched_rule in (
            ("type_error", LOG_SUT_VALUE_TYPE_ERROR, "type_error_in_test"),
            ("attribute_error", LOG_SUT_VALUE_ATTRIBUTE_ERROR, "attribute_error_in_test"),
        ):
            with self.subTest(name=name):
                payload = self.assert_case(
                    log,
                    exit_code=2,
                    status="blocked",
                    blocking_reason="insufficient_evidence",
                    healing_eligible=False,
                    failure_owner="unresolved",
                    failure_subtype="insufficient_evidence",
                    target_layer_reached=True,
                    zero_write=True,
                )
                self.assertEqual(
                    "test_frame_is_manifestation_only", payload["owner_evidence"]
                )
                self.assertIn(
                    matched_rule,
                    payload["matched_rules"]["test_code_or_assertion"],
                )

    def test_09_insufficient_evidence_blocks_instead_of_guessing(self) -> None:
        self.assert_case(
            LOG_AMBIGUOUS,
            exit_code=2,
            status="blocked",
            blocking_reason="insufficient_evidence",
            healing_eligible=False,
            failure_owner="unresolved",
            failure_subtype="insufficient_evidence",
        )

    # ------------------------------------------------------- gating behavior

    def test_a_missing_module_outside_the_workspace_is_an_environment_problem(self) -> None:
        """Counter-example to scenario 2: not every import error is a test defect."""

        self.assert_case(
            LOG_DEPENDENCY_DISTRIBUTION,
            exit_code=2,
            status="blocked",
            blocking_reason="environment_owner_not_healable",
            healing_eligible=False,
            failure_owner="environment",
            failure_subtype="dependency_or_import",
        )

    def test_a_sut_verdict_requires_an_accepted_and_traceable_oracle(self) -> None:
        """An inventory citing an oracle the brief never declared is not a binding."""

        fixture = ImportedFixture(self.tmp, self_healing={"mode": "diagnose"})
        fixture.drive_to_post_run()
        fixture.write_log(LOG_SUT_VIOLATION)
        inventory = fixture.artifact / "03_inventory.yaml"
        inventory.write_text(
            inventory.read_text(encoding="utf-8").replace("O-001", "O-404"), encoding="utf-8"
        )
        proc = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        gate = gate_result(proc)
        # Editing an accepted artifact re-locks the chain, which is itself the
        # correct answer: the triage may not proceed on drifted evidence.
        self.assertEqual(2, proc.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("role_preflight_blocked", gate["blocking_reasons"])

    def test_a_triage_records_the_original_failure_without_altering_it(self) -> None:
        fixture = ImportedFixture(self.tmp, self_healing={"mode": "diagnose"})
        fixture.drive_to_post_run()
        log_path = fixture.write_log(LOG_TEST_CODE)
        before = log_path.read_bytes()
        proc = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        self.assertEqual(0, proc.returncode)
        self.assertEqual(before, log_path.read_bytes())
        payload = json.loads((fixture.artifact / "self_healing.json").read_text(encoding="utf-8"))
        self.assertEqual(
            triage_module.sha256_text(LOG_TEST_CODE), payload["original_log_sha256"]
        )
        self.assertEqual("failed", payload["overall"])
        self.assertEqual(1, payload["original_exit_code"])

    def test_a_diagnose_mode_refuses_to_hand_off_a_candidate(self) -> None:
        fixture = ImportedFixture(self.tmp, self_healing={"mode": "diagnose"})
        fixture.drive_to_post_run()
        fixture.write_log(LOG_TEST_CODE)
        fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        proc = fixture.run_self_heal("--self-heal-step", "handoff")
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("candidate_not_allowed_in_this_mode", gate["blocking_reasons"])

    def test_a_attempt_id_is_deterministic_so_a_retry_is_idempotent(self) -> None:
        first = triage_module.attempt_id("UC-X", "a" * 64, "pytest", 1)
        again = triage_module.attempt_id("UC-X", "a" * 64, "pytest", 1)
        other = triage_module.attempt_id("UC-X", "b" * 64, "pytest", 1)
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)


class DiscriminatorCounterExampleTests(unittest.TestCase):
    """Every rule must be shown *not* to fire on ordinary log text.

    This is the direct regression guard for the v0.4.4 defect: unanchored
    substrings made ``environment_or_resource`` and ``sut_behavior_failure``
    co-fire on any log mentioning "connection" or "status code", which pinned
    ``sut_defect_admissible`` to False forever.
    """

    def matched(self, log: str, category: str) -> list[str]:
        return triage_module._matched(log, category)

    def test_pooled_connection_debug_line_is_not_an_environment_failure(self) -> None:
        log = "DEBUG probe: reusing pooled connection to the captured contract fixture\n"
        self.assertEqual([], self.matched(log, "environment_or_resource"))

    def test_the_words_timeout_network_and_resource_alone_do_not_fire(self) -> None:
        log = "INFO configured timeout budget, network profile and resource pool\n"
        self.assertEqual([], self.matched(log, "environment_or_resource"))

    def test_a_non_auth_status_code_does_not_fire_the_auth_rule(self) -> None:
        log = "E   assert response.status_code == 200\nE   AssertionError: expected 200 got 500\n"
        self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_a_403_byte_count_without_status_context_does_not_fire(self) -> None:
        log = "INFO wrote 403 bytes to the capture file\n"
        self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_a_response_with_403_records_is_not_an_auth_status(self) -> None:
        """Counter-example: ``response`` plus a number is not status evidence."""

        for log in (
            "INFO captured response contained 403 records and 12 metadata fields\n",
            "AssertionError: expected 200 records got 401 records\n",
        ):
            with self.subTest(log=log):
                self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_a_401_record_identifier_is_not_an_auth_status(self) -> None:
        """Counter-example for the bare-code assertion rule."""

        log = "E   assert record_id == 401\n"
        self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_positive_target_layer_evidence_suppresses_code_only_auth_inference(self) -> None:
        log = "business_handler_reached=true\nE   AssertionError: assert 401 == 200\n"
        self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_success_code_forms_do_not_fire_new_auth_rules(self) -> None:
        """Counter-examples for response repr and returned-code inference."""

        for log in (
            "DEBUG captured <Response [200]> from the deterministic probe\n",
            "INFO gateway returned 200 after validating the request\n",
            "E   AssertionError: assert 201 == 200\n",
        ):
            with self.subTest(log=log):
                self.assertEqual([], self.matched(log, "auth_or_precondition"))

    def test_explicit_unauthorized_evidence_is_not_suppressed_by_target_marker(self) -> None:
        """Counter-boundary: direct auth evidence outranks the code heuristic."""

        log = "target_layer_reached=true\nUnauthorized - caller is not permitted\n"
        self.assertEqual(["auth_word"], self.matched(log, "auth_or_precondition"))

    def test_the_word_import_alone_is_not_a_dependency_failure(self) -> None:
        log = "INFO import completed; 12 records imported from the fixture\n"
        self.assertEqual([], self.matched(log, "dependency_or_import"))

    def test_the_word_fixture_alone_is_not_a_fixture_defect(self) -> None:
        log = "INFO loaded the captured contract fixture for this case\n"
        self.assertEqual([], self.matched(log, "fixture_or_test_data"))

    def test_a_mock_mentioned_in_prose_is_not_a_mock_defect(self) -> None:
        log = "INFO this case uses no mock and no selector; it hits the contract\n"
        self.assertEqual([], self.matched(log, "selector_or_mock"))

    def test_a_retry_budget_note_is_not_a_flaky_signal(self) -> None:
        log = "INFO retry budget configured as 0; reruns are disabled for this suite\n"
        self.assertEqual([], self.matched(log, "flaky_or_transient"))

    def test_a_type_error_deep_in_the_system_is_not_a_test_code_defect(self) -> None:
        """A non-test deepest frame does not even create manifestation evidence."""

        log = (
            '  File "tests/test_x.py", line 5, in test_outcome\n'
            "    probe.query()\n"
            '  File "app/handlers/query.py", line 88, in query\n'
            "    return build(payload)\n"
            "TypeError: build() missing 1 required positional argument\n"
        )
        self.assertEqual([], self.matched(log, "test_code_or_assertion"))

    def test_a_test_frame_records_type_error_manifestation_without_deciding_owner(self) -> None:
        log = (
            '  File "tests/test_x.py", line 5, in test_outcome\n'
            "    assert probe(1, 2)\n"
            "TypeError: probe() takes 1 positional argument but 2 were given\n"
        )
        # ``_matched`` preserves the observation for audit output; the end-to-
        # end B-6a test above proves triage does not turn it into test ownership.
        self.assertEqual(["type_error_in_test"], self.matched(log, "test_code_or_assertion"))

    def test_an_assertion_alone_never_reaches_a_sut_verdict_without_an_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "uc"
            artifact.mkdir()
            payload = triage_module.triage(
                LOG_SUT_VIOLATION,
                1,
                artifact_dir=artifact,
                root=root,
                uc="UC-X",
                command="pytest",
            )
        self.assertEqual("insufficient_evidence", payload["failure_subtype"])
        self.assertEqual("unresolved", payload["failure_owner"])
        self.assertIn("oracle_binding_missing", payload["blocking_reasons"])
        self.assertFalse(payload["healing_eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
