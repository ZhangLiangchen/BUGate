#!/usr/bin/env python3
"""Governance scenarios 10-25 of the self-healing acceptance matrix.

Scenarios 10-16 exercise the anti-fake-green controls, 17-19 the write/rollback
boundary, 20-21 the identity controls, 22-23 backward compatibility, and 24-25
the byte-level zero-behavior-change proof.

Every case pins the exact process exit code, the exact gate ``status``, at least
one exact ``blocking_reasons`` code, and -- wherever a write is possible -- the
exact bytes of the target file before and after.

Run directly with::

    python3 tests/test_self_healing_governance.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
ENGINE = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ENGINE / "scripts"))

from bugate_selfheal_fixture import (  # noqa: E402
    GOLDEN_COMMAND,
    GOLDEN_LOG_NAME,
    HEALING_OUTPUTS,
    POSTRUN,
    ImportedFixture,
    gate_result,
    golden_name,
    tree_snapshot,
)

GOLDEN_DIR = ENGINE / "tests" / "fixtures" / "golden"

# A test asset with a genuine, repairable defect: the name is misspelled, so the
# test crashes before it can observe anything.
BASE_BROKEN = ImportedFixture.BROKEN_IMPLEMENTATION
BASE_REPAIRED = ImportedFixture.REPAIRED_IMPLEMENTATION

LOG_TEST_CODE = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   NameError: name 'exepcted' is not defined
tests/test_UC-SELFHEAL.py:5: NameError
"""

LOG_ENVIRONMENT = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   ConnectionRefusedError: [Errno 61] Connection refused
tests/test_UC-SELFHEAL.py:5: ConnectionRefusedError
"""

# For the "expected rewritten to actual" case the asset must be a genuine
# test-code defect (otherwise triage correctly refuses to heal it at all) whose
# log *also* records what the system really returned.  The tempting fake repair
# is then to make the NameError go away by asserting the observed value.
BASE_EXPECTED_VALUE = (
    "OBSERVED = 500\n"
    "\n"
    "\n"
    "def test_recorded_outcome():\n"
    "    assert exepcted == 200\n"
    "\n"
    "\n"
    "test_recorded_outcome()\n"
)
FAKE_EXPECTED_VALUE = BASE_EXPECTED_VALUE.replace(
    "assert exepcted == 200", "assert OBSERVED == 500"
)
LOG_EXPECTED_VALUE = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   NameError: name 'exepcted' is not defined
tests/test_UC-SELFHEAL.py:5: NameError
--------------------------- Captured log call ----------------------------------
DEBUG probe: the contract expected 200 but got 500
"""

# For the timeout-inflation case the asset must already carry a bounded wait.
BASE_TIMEOUT = (
    "def probe(name, timeout=1):\n"
    "    raise TimeoutError(name)\n"
    "\n"
    "\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome", timeout=1) == 200\n'
    "\n"
    "\n"
    "test_recorded_outcome()\n"
)
BASE_TIMEOUT_CONFIG = (
    'CONFIG = {"timeout": 1, "max_retries": 1}\n'
    "BUDGET = 30\n"
    "\n"
    "\n"
    "def probe(name, timeout):\n"
    "    if timeout < BUDGET:\n"
    "        raise TimeoutError(name)\n"
    "    return 200\n"
    "\n"
    "\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome", timeout=CONFIG["timeout"]) == 200\n'
    "\n"
    "\n"
    "test_recorded_outcome()\n"
)
BASE_TIMEOUT_DEFAULT = (
    "BUDGET = 30\n"
    "\n"
    "\n"
    "def probe(name, timeout=1):\n"
    "    if timeout < BUDGET:\n"
    "        raise TimeoutError(name)\n"
    "    return 200\n"
    "\n"
    "\n"
    "def test_recorded_outcome():\n"
    '    assert probe("recorded-outcome") == 200\n'
    "\n"
    "\n"
    "test_recorded_outcome()\n"
)


class SelfHealingGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-selfheal-gov-")
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    # ------------------------------------------------------------- utilities

    def enabled_fixture(
        self,
        *,
        base: Path | None = None,
        mode: str = "verify",
        implementation: str = BASE_BROKEN,
        killing_falsification: bool = True,
        **policy: object,
    ) -> ImportedFixture:
        fixture = ImportedFixture(
            base or self.tmp,
            self_healing={"mode": mode, **self._policy(policy)},
        )
        fixture.drive_to_post_run(implementation=implementation)
        fixture.write_falsification_spec(killing=killing_falsification)
        return fixture

    @staticmethod
    def _policy(overrides: dict) -> dict:
        policy: dict[str, object] = {
            "allowed_write_regex": [r"^tests/test_.*\.py$"],
            "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
            "falsification_spec": "falsification_spec.yaml",
            "max_attempts": 3,
        }
        policy.update(overrides)
        return policy

    def to_healing_active(self, fixture: ImportedFixture, log: str = LOG_TEST_CODE) -> None:
        fixture.write_log(log)
        self.assertEqual(
            0,
            fixture.run_self_heal(
                "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
            ).returncode,
        )
        self.assertEqual(0, fixture.run_self_heal("--self-heal-step", "handoff").returncode)
        self.assertEqual(
            0,
            fixture.run_self_heal(
                "--self-heal-step", "accept", role="implementer", session="healer-session"
            ).returncode,
        )

    def propose(self, fixture: ImportedFixture, body: str) -> subprocess.CompletedProcess[str]:
        candidate = fixture.candidate({"tests/test_UC-SELFHEAL.py": body})
        return fixture.run_self_heal(
            "--self-heal-step",
            "propose",
            "--candidate-dir",
            str(candidate),
            "--pytest-log",
            GOLDEN_LOG_NAME,
            role="implementer",
            session="healer-session",
        )

    def assert_fake_green_rejected(
        self, fixture: ImportedFixture, body: str, finding_code: str
    ) -> None:
        before = fixture.implementation.read_bytes()
        sidecar = fixture.artifact / "00_self_healing"
        sidecar_before = tree_snapshot(sidecar)
        proc = self.propose(fixture, body)
        gate = gate_result(proc)
        self.assertEqual(3, proc.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual("healing_rejected", gate["status"])
        self.assertEqual(3, gate["exit_code"])
        self.assertIn("fake_green_structural_finding", gate["blocking_reasons"])
        self.assertIn(finding_code, gate["blocking_reasons"])
        # A rejected candidate never reaches the workspace.
        self.assertEqual(before, fixture.implementation.read_bytes())
        # ...and neither advances nor mutates any byte/directory in the complete
        # governance sidecar.  A rejected operation is not evidence.
        self.assertEqual(sidecar_before, tree_snapshot(sidecar))
        self.assertEqual("healing_active", fixture.sidecar_state())

    def review_and_close(
        self, fixture: ImportedFixture, **review: object
    ) -> subprocess.CompletedProcess[str]:
        name = fixture.write_review(**review)
        return fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", name,
            role="reviewer", session="independent-review-session",
        )

    @staticmethod
    def mode(path: Path) -> int:
        import stat

        return stat.S_IMODE(path.stat().st_mode)

    def ready_to_close(self, fixture: ImportedFixture) -> None:
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        self.assertEqual(0, self.review_and_close(fixture).returncode)
        self.assertEqual("healing_verified", fixture.sidecar_state())

    # ------------------------------------------------------- scenario 10

    def test_10_a_valid_test_asset_repair_reaches_healing_verified(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)

        review = self.review_and_close(fixture)
        gate = gate_result(review)
        self.assertEqual(0, review.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual("healing_verified", gate["status"])
        self.assertEqual([], gate["blocking_reasons"])
        self.assertEqual("healing_verified", fixture.sidecar_state())

        closed = fixture.run_self_heal(
            "--self-heal-step", "close", role="reviewer", session="independent-review-session"
        )
        self.assertEqual(0, closed.returncode)
        self.assertEqual("healing_verified", gate_result(closed)["status"])
        self.assertEqual("attempt_closed", fixture.sidecar_state())

        # verify mode proves the repair without ever writing the real workspace.
        self.assertEqual(BASE_BROKEN, fixture.implementation.read_text(encoding="utf-8"))

        # Every one of the six frozen events is on the sidecar, in order.
        chain = json.loads(
            (fixture.artifact / "00_self_healing" / "chain.json").read_text(encoding="utf-8")
        )
        self.assertEqual("bugate.self-heal-chain/v1", chain["schema"])
        self.assertEqual(
            [
                "triage_recorded",
                "self_heal_handoff",
                "healer_acceptance",
                "healer_handoff",
                "independent_review",
                "attempt_closed",
            ],
            [entry["event"] for entry in chain["entries"]],
        )

    # -------------------------------------------- scenarios 11-16 (fake green)

    def test_11_a_candidate_that_deletes_the_assertion_is_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        variants = {
            "pass-body": (
                "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n    pass\n\n"
                "test_recorded_outcome()\n",
                "assertion_count_decreased",
            ),
            "constant-truth": (
                "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n    assert True\n\n"
                "test_recorded_outcome()\n",
                "assertion_predicate_weakened",
            ),
            "self-comparison": (
                "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n"
                "    assert OBSERVED == OBSERVED\n\n"
                "test_recorded_outcome()\n",
                "assertion_semantically_vacuous",
            ),
        }
        for name, (body, code) in variants.items():
            with self.subTest(variant=name):
                self.assert_fake_green_rejected(fixture, body, code)

    def test_12_a_candidate_that_rewrites_expected_to_the_actual_is_rejected(self) -> None:
        fixture = self.enabled_fixture(implementation=BASE_EXPECTED_VALUE)
        self.to_healing_active(fixture, log=LOG_EXPECTED_VALUE)
        variants = {
            "direct-literal": FAKE_EXPECTED_VALUE,
            "through-local-data-flow": (
                "OBSERVED = 500\n\n\ndef test_recorded_outcome():\n"
                "    expected = 500\n"
                "    assert OBSERVED == expected\n\n\ntest_recorded_outcome()\n"
            ),
            "through-container-data-flow": (
                'OBSERVED = 500\nEXPECTED = {"recorded-outcome": 500}\n\n\n'
                "def test_recorded_outcome():\n"
                '    assert OBSERVED == EXPECTED["recorded-outcome"]\n\n\n'
                "test_recorded_outcome()\n"
            ),
        }
        for name, body in variants.items():
            with self.subTest(variant=name):
                self.assert_fake_green_rejected(
                    fixture, body, "expected_rewritten_to_actual"
                )

    def test_13_a_candidate_that_adds_a_broad_exception_catch_is_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        handlers = {
            "exception-pass": (
                "    except Exception:\n        pass\n",
                "broad_exception_introduced",
            ),
            "narrow-recorded-failure": (
                "    except NameError:\n        pass\n",
                "recorded_failure_swallowed",
            ),
            "any-of-recorded-failures": (
                "    except (NameError, ValueError):\n        pass\n",
                "any_of_exceptions_introduced",
            ),
        }
        for name, (handler, code) in handlers.items():
            with self.subTest(variant=name):
                self.assert_fake_green_rejected(
                    fixture,
                    "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n"
                    "    try:\n"
                    "        assert exepcted == OBSERVED\n"
                    + handler
                    + "\n\ntest_recorded_outcome()\n",
                    code,
                )

    def test_14_a_candidate_that_hides_the_failure_behind_skip_or_xfail_is_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        variants = {
            "pytest-skip": (
                "import pytest\n\nOBSERVED = 200\n\n\n"
                '@pytest.mark.skip(reason="flaky")\n'
                "def test_recorded_outcome():\n    assert exepcted == OBSERVED\n"
            ),
            "unittest-skip-if": (
                "import unittest\n\nOBSERVED = 200\n\n\n"
                "class RecordedOutcomeTests(unittest.TestCase):\n"
                '    @unittest.skipIf(True, "flaky")\n'
                "    def test_recorded_outcome(self):\n"
                "        self.assertEqual(exepcted, OBSERVED)\n"
            ),
            "raise-skip-test": (
                "import unittest\n\nOBSERVED = 200\n\n\n"
                "def test_recorded_outcome():\n"
                '    raise unittest.SkipTest("flaky")\n'
                "    assert exepcted == OBSERVED\n"
            ),
        }
        for name, body in variants.items():
            with self.subTest(variant=name):
                self.assert_fake_green_rejected(
                    fixture, body, "skip_or_xfail_introduced"
                )

    def test_15_a_candidate_that_mocks_past_the_target_layer_is_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        variants = {
            "patch-string": (
                "from unittest import mock\n\nOBSERVED = 200\n\n\n"
                "def test_recorded_outcome():\n"
                '    with mock.patch("probe.query", return_value=200):\n'
                "        assert OBSERVED == 200\n",
                "mock_introduced_bypassing_target",
            ),
            "magic-mock": (
                "from unittest.mock import MagicMock\n\nOBSERVED = 200\n"
                "probe = MagicMock()\nprobe.expected_status.return_value = 200\n\n\n"
                "def test_recorded_outcome():\n"
                "    assert probe.expected_status() == OBSERVED\n",
                "mock_introduced_bypassing_target",
            ),
            "hand-written-local-substitute": (
                "OBSERVED = 200\n\n"
                "class LocalProbeStub:\n"
                "    def expected_status(self):\n        return 200\n\n"
                "probe = LocalProbeStub()\n\n\n"
                "def test_recorded_outcome():\n"
                "    assert probe.expected_status() == OBSERVED\n",
                "test_local_substitute_introduced",
            ),
        }
        for name, (body, code) in variants.items():
            with self.subTest(variant=name):
                self.assert_fake_green_rejected(fixture, body, code)

    def test_16_a_candidate_that_inflates_a_timeout_without_evidence_is_rejected(self) -> None:
        variants = (
            (
                "call-argument",
                BASE_TIMEOUT,
                BASE_TIMEOUT.replace('timeout=1) == 200', 'timeout=600) == 200'),
            ),
            (
                "config-dictionary",
                BASE_TIMEOUT_CONFIG,
                BASE_TIMEOUT_CONFIG.replace('"timeout": 1', '"timeout": 600').replace(
                    '"max_retries": 1', '"max_retries": 50'
                ),
            ),
            (
                "signature-default",
                BASE_TIMEOUT_DEFAULT,
                BASE_TIMEOUT_DEFAULT.replace("timeout=1", "timeout=600"),
            ),
        )
        for name, before, after in variants:
            with self.subTest(variant=name):
                fixture = self.enabled_fixture(
                    base=self.tmp / name, implementation=before
                )
                self.to_healing_active(fixture)
                self.assert_fake_green_rejected(
                    fixture, after, "timeout_or_retry_inflated"
                )

    def test_16b_an_any_of_these_exceptions_handler_is_rejected(self) -> None:
        """AGENTS.md forbids assertions that hide which layer actually failed."""

        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assert_fake_green_rejected(
            fixture,
            "OBSERVED = 200\n"
            "\n"
            "\n"
            "def test_recorded_outcome():\n"
            "    try:\n"
            "        assert exepcted == OBSERVED\n"
            "    except (NameError, ValueError, TypeError, KeyError):\n"
            "        assert True\n"
            "\n"
            "\n"
            "test_recorded_outcome()\n",
            "any_of_exceptions_introduced",
        )

    def test_16c_a_candidate_that_narrows_collection_is_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assert_fake_green_rejected(
            fixture,
            "__test__ = False\n"
            "\n"
            "OBSERVED = 200\n"
            "\n"
            "\n"
            "def test_recorded_outcome():\n"
            "    assert exepcted == OBSERVED\n",
            "collection_narrowed",
        )

    def test_16d_an_approving_reviewer_cannot_overrule_a_structural_finding(self) -> None:
        """The deterministic half is authoritative; the semantic half cannot rubber-stamp."""

        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        proc = self.propose(
            fixture,
            "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n    pass\n\n\ntest_recorded_outcome()\n",
        )
        self.assertEqual(3, proc.returncode)
        # Even a fully-formed "approved" review cannot move the attempt forward,
        # because the candidate never reached awaiting_independent_review.
        review = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", "missing-review.json",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(review)
        self.assertEqual(2, review.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("self_heal_transition_not_allowed", gate["blocking_reasons"])

    def test_16d1_a_second_proposal_cannot_replace_the_candidate_awaiting_review(self) -> None:
        """State validation precedes every proposal-side evidence write."""

        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        first = self.propose(fixture, BASE_REPAIRED)
        self.assertEqual(0, first.returncode)
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())
        sidecar = fixture.artifact / "00_self_healing"
        sidecar_before = tree_snapshot(sidecar)

        second = self.propose(
            fixture,
            "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n    pass\n",
        )
        gate = gate_result(second)
        self.assertEqual(2, second.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual(2, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["self_heal_transition_not_allowed"], gate["blocking_reasons"])
        self.assertEqual(sidecar_before, tree_snapshot(sidecar))
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_16d3_concurrent_cli_proposals_are_one_artifact_transaction(self) -> None:
        """A losing cooperative proposal cannot rewrite the winner's evidence."""

        fixture = self.enabled_fixture(base=self.tmp / "concurrent-proposals")
        self.to_healing_active(fixture)
        candidate_a_body = BASE_REPAIRED
        candidate_b_body = (
            "OBSERVED = 200\n\n\n"
            "def test_recorded_outcome():\n"
            "    expected = 200\n"
            "    assert OBSERVED == expected\n\n\n"
            "test_recorded_outcome()\n"
        )
        candidate_a = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": candidate_a_body},
            name="candidate-concurrent-a",
        )
        candidate_b = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": candidate_b_body},
            name="candidate-concurrent-b",
        )
        a_paused = fixture.root / "proposal-a-paused"
        release_a = fixture.root / "proposal-a-release"
        b_attempting = fixture.root / "proposal-b-attempting"
        b_acquired = fixture.root / "proposal-b-acquired"
        release_b = fixture.root / "proposal-b-release"

        def child_source(
            candidate: Path,
            *,
            verification_pause: bool,
            attempting: Path | None = None,
            acquired: Path | None = None,
            release: Path | None = None,
        ) -> str:
            if verification_pause:
                hook = f'''\
original_verification = gate.run_verification
def pause_verification(*args, **kwargs):
    Path({str(a_paused)!r}).write_text("paused\\n", encoding="utf-8")
    deadline = time.monotonic() + 20
    while not Path({str(release_a)!r}).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("proposal A release timed out")
        time.sleep(0.02)
    return original_verification(*args, **kwargs)
gate.run_verification = pause_verification
'''
            else:
                assert attempting is not None and acquired is not None and release is not None
                hook = f'''\
original_operation_lock = gate.sidecar.SelfHealSidecar._operation_lock
@contextmanager
def marked_operation_lock(self):
    outermost = self._lock_depth == 0
    if outermost:
        # This is immediately before the same flock acquisition used by
        # gate.run, after config and main-evidence preflight have completed.
        Path({str(attempting)!r}).write_text("attempting\\n", encoding="utf-8")
    with original_operation_lock(self):
        if outermost:
            Path({str(acquired)!r}).write_text("acquired\\n", encoding="utf-8")
            deadline = time.monotonic() + 20
            while not Path({str(release)!r}).exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("proposal B release timed out")
                time.sleep(0.02)
        yield
gate.sidecar.SelfHealSidecar._operation_lock = marked_operation_lock
'''
            return f'''\
import argparse
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, {str(ENGINE / "scripts")!r})
import self_heal_gate as gate

{hook}
args = argparse.Namespace(
    self_heal_step="propose",
    candidate_dir={str(candidate)!r},
    pytest_log={GOLDEN_LOG_NAME!r},
    command="",
    exit_code=1,
    review_file="",
    human_approval="",
)
result = gate.run(Path({str(fixture.artifact)!r}), args)
print(json.dumps(result, sort_keys=True))
raise SystemExit(int(result["exit_code"]))
'''

        env = fixture.env(role="implementer", session="healer-session")
        proposal_a = subprocess.Popen(
            [sys.executable, "-c", child_source(candidate_a, verification_pause=True)],
            cwd=ENGINE,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proposal_b: subprocess.Popen[str] | None = None
        try:
            deadline = time.monotonic() + 15
            while not a_paused.exists() and time.monotonic() < deadline:
                if proposal_a.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(
                a_paused.exists(),
                "proposal A did not reach its post-preflight verification cut",
            )

            proposal_b = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_source(
                        candidate_b,
                        verification_pause=False,
                        attempting=b_attempting,
                        acquired=b_acquired,
                        release=release_b,
                    ),
                ],
                cwd=ENGINE,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 15
            while not b_attempting.exists() and time.monotonic() < deadline:
                if proposal_b.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(
                b_attempting.exists(),
                "proposal B did not reach the immediate-before-flock cut",
            )
            time.sleep(0.35)
            self.assertFalse(
                b_acquired.exists(),
                "proposal B entered its handler while proposal A still owned the artifact",
            )

            release_a.write_text("release\n", encoding="utf-8")
            a_stdout, a_stderr = proposal_a.communicate(timeout=30)
            self.assertEqual(0, proposal_a.returncode, a_stdout + a_stderr)
            a_gate = json.loads(a_stdout.strip().splitlines()[-1])
            self.assertEqual("healing_active", a_gate["status"])

            deadline = time.monotonic() + 15
            while not b_acquired.exists() and time.monotonic() < deadline:
                if proposal_b.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(b_acquired.exists(), "proposal B never acquired the released lock")
            winner_tree = tree_snapshot(fixture.artifact / "00_self_healing")
            release_b.write_text("release\n", encoding="utf-8")
            b_stdout, b_stderr = proposal_b.communicate(timeout=30)
            self.assertEqual(2, proposal_b.returncode, b_stdout + b_stderr)
            b_gate = json.loads(b_stdout.strip().splitlines()[-1])
            self.assertEqual(
                ["self_heal_transition_not_allowed"],
                b_gate["blocking_reasons"],
            )
            self.assertEqual(
                winner_tree,
                tree_snapshot(fixture.artifact / "00_self_healing"),
                "the losing proposal changed evidence bound by the winner's receipt",
            )
        finally:
            release_a.touch(exist_ok=True)
            release_b.touch(exist_ok=True)
            for process in (proposal_a, proposal_b):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()

        attempt = (
            fixture.artifact
            / "00_self_healing"
            / "attempts"
            / fixture.sidecar_attempt_id()
        )
        self.assertEqual(
            candidate_a_body,
            (attempt / "candidate/tests/test_UC-SELFHEAL.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_16d2_the_review_rechecks_the_candidate_itself_not_the_healers_report(self) -> None:
        """A candidate swapped after propose is still caught at review.

        The review re-runs the structural analysis against the stored candidate
        rather than trusting the ``healer_handoff`` payload, so a clean proposal
        followed by a substituted patch does not slip through.
        """

        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)

        stored = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id() / "candidate" / "tests" / "test_UC-SELFHEAL.py"
        )
        self.assertEqual(BASE_REPAIRED, stored.read_text(encoding="utf-8"))
        stored.write_text(
            "OBSERVED = 200\n\n\ndef test_recorded_outcome():\n    pass\n", encoding="utf-8"
        )

        review = self.review_and_close(fixture, verdict="approved")
        gate = gate_result(review)
        self.assertEqual(2, review.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(
            ["candidate_diverged_from_proposal"], gate["blocking_reasons"]
        )
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_16e_a_degraded_independent_review_blocks_instead_of_approving(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        review = self.review_and_close(
            fixture, verdict="approved", dispatch_mode="fallback_placeholder"
        )
        gate = gate_result(review)
        self.assertEqual(2, review.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("independent_review_degraded", gate["blocking_reasons"])
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_16f_a_rejecting_independent_review_produces_healing_rejected(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        review = self.review_and_close(
            fixture,
            verdict="rejected",
            findings=[{"claim": "the repair changes what is observed", "evidence": "after.log"}],
        )
        gate = gate_result(review)
        self.assertEqual(3, review.returncode)
        self.assertEqual("healing_rejected", gate["status"])
        self.assertIn("independent_review_rejected", gate["blocking_reasons"])
        self.assertEqual("healing_rejected", fixture.sidecar_state())

    def test_16g_falsification_is_required_and_never_degrades(self) -> None:
        fixture = self.enabled_fixture(falsification_spec="")
        self.to_healing_active(fixture)
        proposed = self.propose(fixture, BASE_REPAIRED)
        gate = gate_result(proposed)
        self.assertEqual(2, proposed.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["falsification_spec_missing"], gate["blocking_reasons"])
        self.assertEqual("healing_active", fixture.sidecar_state())

    def test_16h_a_falsification_score_below_threshold_rejects_the_repair(self) -> None:
        fixture = self.enabled_fixture(killing_falsification=False)
        self.to_healing_active(fixture)
        proposed = self.propose(fixture, BASE_REPAIRED)
        gate = gate_result(proposed)
        self.assertEqual(3, proposed.returncode)
        self.assertEqual("healing_rejected", gate["status"])
        self.assertIn("falsification_score_below_threshold", gate["blocking_reasons"])
        self.assertEqual("healing_active", fixture.sidecar_state())

    def test_16i_a_revised_proposal_replaces_its_own_stale_candidate_file_set(self) -> None:
        fixture = self.enabled_fixture(
            allowed_write_regex=[r"^tests/(?:test_.*[.]py|obsolete_helper[.]py)$"]
        )
        self.to_healing_active(fixture)
        first_candidate = fixture.candidate(
            {
                "tests/test_UC-SELFHEAL.py": BASE_BROKEN,
                "tests/obsolete_helper.py": "HELPER = 'stale'\n",
            },
            name="candidate-first-blocked",
        )
        first = fixture.run_self_heal(
            "--self-heal-step", "propose",
            "--candidate-dir", str(first_candidate),
            "--pytest-log", GOLDEN_LOG_NAME,
            role="implementer", session="healer-session",
        )
        first_gate = gate_result(first)
        self.assertEqual(2, first.returncode, first.stdout + first.stderr)
        self.assertEqual(["verification_failed"], first_gate["blocking_reasons"])
        self.assertEqual("healing_active", fixture.sidecar_state())

        second_candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": BASE_REPAIRED},
            name="candidate-second-valid",
        )
        second = fixture.run_self_heal(
            "--self-heal-step", "propose",
            "--candidate-dir", str(second_candidate),
            "--pytest-log", GOLDEN_LOG_NAME,
            role="implementer", session="healer-session",
        )
        second_gate = gate_result(second)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual("healing_active", second_gate["status"])
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        baseline = json.loads((attempt / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ["tests/test_UC-SELFHEAL.py"],
            [entry["path"] for entry in baseline["files"]],
        )
        self.assertFalse((attempt / "candidate/tests/obsolete_helper.py").exists())
        self.assertFalse((attempt / "before/tests/obsolete_helper.py").exists())

    # --------------------------------------- scenarios 17-19 (write boundary)

    def test_16j_reverse_verification_rejects_crash_and_wrong_assertion_kills(self) -> None:
        """A non-zero mutant is not a kill unless the mapped assertion rejects it."""

        import self_heal_gate
        from role_governance import load_context
        from self_heal_review import FileChange

        fixture = self.enabled_fixture(base=self.tmp / "reverse-clean-attribution")
        contract = fixture.root / "tests" / "contract.json"
        contract.write_text('{"expected": 200}\n', encoding="utf-8")
        fixture.write_falsification_spec()
        ctx = load_context(fixture.artifact)
        policy = self_heal_gate.self_healing_policy(ctx.config)
        falsification, reasons, _markdown, _log = self_heal_gate.run_falsification(
            ctx, policy
        )
        self.assertEqual([], reasons)

        prefix = (
            "import json\n"
            "from pathlib import Path\n"
            "OBSERVED = 200\n"
            "EXPECTED = json.loads(Path(__file__).with_name('contract.json').read_text())['expected']\n"
            "def test_recorded_outcome():\n"
        )
        candidates = {
            "syntax_crash": prefix
            + "    if EXPECTED != OBSERVED:\n"
            + "        compile('(', 'negative-control', 'exec')\n"
            + "    assert EXPECTED == OBSERVED\n"
            + "test_recorded_outcome()\n",
            "wrong_assertion": prefix
            + "    if EXPECTED != OBSERVED:\n"
            + "        raise AssertionError('decoy')\n"
            + "    assert EXPECTED == OBSERVED\n"
            + "test_recorded_outcome()\n",
        }
        for label, after in candidates.items():
            with self.subTest(mutant_failure=label):
                proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
                    ctx,
                    policy,
                    [
                        FileChange(
                            path="tests/test_UC-SELFHEAL.py",
                            before=BASE_BROKEN,
                            after=after,
                        )
                    ],
                    falsification,
                    ["O-001"],
                    LOG_TEST_CODE,
                )
                self.assertEqual(
                    "repaired_test_reverse_verification_unavailable", reason
                )
                self.assertEqual("blocked", proof["status"])
                self.assertEqual("unavailable", proof["cases"][0]["result"])
                self.assertIn(
                    "outside the mapped assertion", proof["cases"][0]["detail"]
                )

    def test_16k_literal_exception_requires_a_complete_ast_delta_match(self) -> None:
        """Extra rebinding or assignment side effects cannot enter the tiny lane."""

        import self_heal_gate
        from self_heal_review import FileChange

        bypasses = {
            "top_level_rebind": BASE_REPAIRED
            + "\ntest_recorded_outcome = lambda: None\n",
            "assignment_rhs_side_effect": BASE_REPAIRED.replace(
                "expected = 200", "expected = (globals().update({'X': 1}) or 200)"
            ),
        }
        for label, after in bypasses.items():
            with self.subTest(candidate=label):
                self.assertIsNone(
                    self_heal_gate._literal_negative_control(
                        [
                            FileChange(
                                path="tests/test_UC-SELFHEAL.py",
                                before=BASE_BROKEN,
                                after=after,
                            )
                        ],
                        LOG_TEST_CODE,
                    )
                )

    def test_17_post_apply_verification_failure_rolls_back_exact_prior_bytes(self) -> None:
        import base64
        import os
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate

        fixture = self.enabled_fixture(mode="apply_with_approval")
        fixture.implementation.chmod(0o755)
        self.to_healing_active(fixture)
        before = fixture.implementation.read_bytes()
        before_mode = self.mode(fixture.implementation)
        role_evidence_before = tree_snapshot(fixture.artifact / "00_role_evidence")
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        self.assertEqual(0, self.review_and_close(fixture).returncode)
        self.assertEqual("healing_verified", fixture.sidecar_state())

        sidecar = fixture.artifact / "00_self_healing"
        sidecar_files_before = {
            item.relative_to(sidecar).as_posix(): item.read_bytes()
            for item in sidecar.rglob("*")
            if item.is_file()
        }
        journal_path = (
            sidecar / "attempts" / fixture.sidecar_attempt_id() / "apply_journal.json"
        )
        verification_path = journal_path.with_name("verification_after_apply.json")
        observed_applied_bytes: list[bytes] = []
        injected_verification = {
            "schema": self_heal_gate.VERIFICATION_SCHEMA,
            "recorded_at": "injected-post-apply-failure",
            "commands": [ImportedFixture.VERIFICATION_COMMAND],
            "before": [],
            "after": [
                {
                    "command": ImportedFixture.VERIFICATION_COMMAND,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "injected post-apply verification failure",
                }
            ],
            "before_reproduced_failure": False,
            "after_passed": False,
            "sandbox_only": True,
        }

        def fail_after_real_apply(_ctx, policy, changes):
            self.assertEqual([], changes)
            self.assertEqual(
                [ImportedFixture.VERIFICATION_COMMAND],
                policy["verification_commands"],
            )
            applied = fixture.implementation.read_bytes()
            self.assertEqual(BASE_REPAIRED.encode("utf-8"), applied)
            observed_applied_bytes.append(applied)
            return injected_verification

        args = SimpleNamespace(
            self_heal_step="close",
            human_approval="fixture-owner",
            candidate_dir="",
            review_file="",
            pytest_log="",
            command="",
            exit_code=0,
        )
        output = StringIO()
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ), mock.patch.object(
            self_heal_gate, "run_verification", side_effect=fail_after_real_apply
        ) as verification_call, redirect_stdout(output):
            payload = self_heal_gate.run(fixture.artifact, args)
            rc = self_heal_gate.emit(payload)

        proc = subprocess.CompletedProcess(["self_heal_gate"], rc, output.getvalue(), "")
        gate = gate_result(proc)
        verification_call.assert_called_once()
        self.assertEqual([BASE_REPAIRED.encode("utf-8")], observed_applied_bytes)
        self.assertEqual(2, proc.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual(proc.returncode, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["verification_failed"], gate["blocking_reasons"])
        self.assertEqual(before, fixture.implementation.read_bytes())
        self.assertEqual(before_mode, self.mode(fixture.implementation))
        self.assertEqual("healing_verified", fixture.sidecar_state())
        self.assertEqual(
            role_evidence_before, tree_snapshot(fixture.artifact / "00_role_evidence")
        )

        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(self_heal_gate.JOURNAL_SCHEMA, journal["schema"])
        self.assertEqual(self_heal_gate.JOURNAL_MODE_INTEGRITY, journal["mode_integrity"])
        self.assertEqual("rolled_back", journal["state"])
        self.assertEqual(1, len(journal["files"]))
        entry = journal["files"][0]
        self.assertEqual("tests/test_UC-SELFHEAL.py", entry["path"])
        self.assertEqual(before_mode, entry["before_mode"])
        self.assertEqual(before_mode, entry["after_mode"])
        self.assertFalse(entry["applied"])
        self.assertEqual(before, base64.b64decode(entry["before_base64"]))
        self.assertEqual(
            BASE_REPAIRED.encode("utf-8"), base64.b64decode(entry["after_base64"])
        )
        self.assertEqual(
            injected_verification,
            json.loads(verification_path.read_text(encoding="utf-8")),
        )
        sidecar_files_after = {
            item.relative_to(sidecar).as_posix(): item.read_bytes()
            for item in sidecar.rglob("*")
            if item.is_file()
        }
        for relative, body in sidecar_files_before.items():
            self.assertEqual(body, sidecar_files_after[relative], msg=relative)
        self.assertEqual(
            {
                journal_path.relative_to(sidecar).as_posix(),
                verification_path.relative_to(sidecar).as_posix(),
            },
            set(sidecar_files_after) - set(sidecar_files_before),
        )

    def test_17c_noneligible_and_diagnose_triage_never_wedge_a_retry(self) -> None:
        # A non-healable environment observation still refreshes ordinary
        # triage artifacts, but never opens the append-only repair sidecar.
        fixture = self.enabled_fixture()
        fixture.write_log(LOG_ENVIRONMENT)
        first = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND,
            "--exit-code", "1",
        )
        first_gate = gate_result(first)
        self.assertEqual(2, first.returncode)
        self.assertEqual(first.returncode, first_gate["exit_code"])
        self.assertFalse((fixture.artifact / "00_self_healing").exists())
        first_payload = (fixture.artifact / "self_healing.json").read_bytes()

        fixture.write_log(LOG_TEST_CODE)
        second = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND,
            "--exit-code", "1",
        )
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual("triage_recorded", fixture.sidecar_state())
        self.assertNotEqual(
            first_payload, (fixture.artifact / "self_healing.json").read_bytes()
        )

        diagnosed = self.enabled_fixture(base=self.tmp / "diagnose", mode="diagnose")
        diagnosed.write_log(LOG_TEST_CODE)
        for command in ("first diagnose", "updated diagnose"):
            proc = diagnosed.run_self_heal(
                "--pytest-log", GOLDEN_LOG_NAME, "--command", command,
                "--exit-code", "1",
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertFalse((diagnosed.artifact / "00_self_healing").exists())
        payload = json.loads(
            (diagnosed.artifact / "self_healing.json").read_text(encoding="utf-8")
        )
        self.assertEqual("updated diagnose", payload["original_command"])

    def test_17d_the_same_failure_can_open_a_distinct_second_attempt(self) -> None:
        fixture = self.enabled_fixture(base=self.tmp / "second-attempt")
        self.to_healing_active(fixture)
        first_attempt = fixture.sidecar_attempt_id()
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        rejected = self.review_and_close(
            fixture,
            verdict="rejected",
            findings=[{"claim": "candidate needs revision", "evidence": "after.log"}],
        )
        self.assertEqual(3, rejected.returncode, rejected.stdout + rejected.stderr)
        closed = fixture.run_self_heal(
            "--self-heal-step", "close",
            role="reviewer", session="independent-review-session",
        )
        closed_gate = gate_result(closed)
        self.assertEqual(3, closed.returncode, closed.stdout + closed.stderr)
        self.assertEqual(closed.returncode, closed_gate["exit_code"])
        self.assertEqual("attempt_closed", fixture.sidecar_state())

        retriaged = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME,
            "--command", GOLDEN_COMMAND,
            "--exit-code", "1",
            role="reviewer", session="second-triage-session",
        )
        retriaged_gate = gate_result(retriaged)
        self.assertEqual(0, retriaged.returncode, retriaged.stdout + retriaged.stderr)
        self.assertEqual("triaged", retriaged_gate["status"])
        second_attempt = fixture.sidecar_attempt_id()
        self.assertNotEqual(first_attempt, second_attempt)
        payload = json.loads(
            (fixture.artifact / "self_healing.json").read_text(encoding="utf-8")
        )
        self.assertEqual(second_attempt, payload["attempt_id"])

        self.assertEqual(
            0,
            fixture.run_self_heal(
                "--self-heal-step", "handoff",
                role="reviewer", session="second-triage-session",
            ).returncode,
        )
        self.assertEqual(
            0,
            fixture.run_self_heal(
                "--self-heal-step", "accept",
                role="implementer", session="second-healer-session",
            ).returncode,
        )
        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": BASE_REPAIRED},
            name="candidate-second-attempt",
        )
        proposed = fixture.run_self_heal(
            "--self-heal-step", "propose",
            "--candidate-dir", str(candidate),
            "--pytest-log", GOLDEN_LOG_NAME,
            role="implementer", session="second-healer-session",
        )
        self.assertEqual(0, proposed.returncode, proposed.stdout + proposed.stderr)
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_17b_a_failure_that_does_not_reproduce_blocks_the_repair(self) -> None:
        """Do not repair what is not proven broken."""

        fixture = self.enabled_fixture(implementation=BASE_REPAIRED)
        self.to_healing_active(fixture)
        proc = self.propose(fixture, BASE_REPAIRED.replace("expected = 200", "expected = 200  # noqa"))
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("failure_not_reproducible", gate["blocking_reasons"])

    def test_18_an_interrupted_apply_is_rolled_back_to_exact_prior_bytes(self) -> None:
        import os
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate

        fixture = self.enabled_fixture(mode="apply_with_approval")
        fixture.implementation.chmod(0o755)
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        self.assertEqual(0, self.review_and_close(fixture).returncode)
        before = fixture.implementation.read_bytes()
        before_mode = self.mode(fixture.implementation)
        journal_path = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id() / "apply_journal.json"
        )

        real_write_json = self_heal_gate._write_json

        class SimulatedProcessCrash(RuntimeError):
            pass

        def crash_before_journal_finalize(path: Path, value: dict) -> None:
            # ``apply_candidate`` has already written the candidate and persisted
            # the per-file ``applied`` bit at this point.  Interrupt before the
            # journal becomes terminal, which is also before ``attempt_closed``
            # can be published by ``step_close``.
            if (
                value.get("schema") == self_heal_gate.JOURNAL_SCHEMA
                and value.get("state") == "applied"
            ):
                raise SimulatedProcessCrash("injected close interruption")
            real_write_json(path, value)

        close_args = SimpleNamespace(
            self_heal_step="close",
            human_approval="fixture-owner",
            candidate_dir="",
            review_file="",
            pytest_log="",
            command="",
            exit_code=0,
        )
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ), mock.patch.object(
            self_heal_gate, "_write_json", side_effect=crash_before_journal_finalize
        ):
            try:
                close_result = self_heal_gate.run(fixture.artifact, close_args)
            except SimulatedProcessCrash:
                pass
            else:
                self.fail(
                    "close did not reach the injected apply-finalization window: "
                    + json.dumps(close_result, indent=2)
                )

        # This is the resumable crash shape: the verified sidecar edge was not
        # replaced by a successful close, while the workspace contains the
        # journal-authenticated candidate and the journal is still in progress.
        self.assertEqual("healing_verified", fixture.sidecar_state())
        self.assertEqual(BASE_REPAIRED.encode("utf-8"), fixture.implementation.read_bytes())
        self.assertEqual(before_mode, self.mode(fixture.implementation))
        interrupted = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual("applying", interrupted["state"])
        self.assertTrue(all(entry["applied"] for entry in interrupted["files"]))

        resumed = fixture.run_self_heal(
            "--self-heal-step", "resume", role="reviewer", session="independent-review-session"
        )
        gate = gate_result(resumed)
        self.assertEqual(0, resumed.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual("healing_active", gate["status"])
        self.assertEqual(0, gate["exit_code"])
        self.assertEqual(before, fixture.implementation.read_bytes())
        self.assertEqual(
            "rolled_back", json.loads(journal_path.read_text(encoding="utf-8"))["state"]
        )

        # Mode metadata is authenticated, not advisory.
        tampered = json.loads(journal_path.read_text(encoding="utf-8"))
        tampered["state"] = "applied"
        tampered["files"][0]["before_mode"] ^= 0o100
        journal_path.write_text(json.dumps(tampered), encoding="utf-8")
        rejected = fixture.run_self_heal(
            "--self-heal-step", "resume",
            role="reviewer", session="independent-review-session",
        )
        rejected_gate = gate_result(rejected)
        self.assertEqual(2, rejected.returncode)
        self.assertEqual(["apply_journal_invalid"], rejected_gate["blocking_reasons"])
        self.assertEqual(before, fixture.implementation.read_bytes())
        self.assertEqual(before_mode, self.mode(fixture.implementation))

    def test_18b_apply_with_approval_refuses_without_a_human_approval_record(self) -> None:
        invalid = self.enabled_fixture(
            base=self.tmp / "approval-false", mode="apply_with_approval"
        )
        invalid.set_self_healing(
            {"mode": "apply_with_approval", **self._policy({}),
             "human_approval_required": False}
        )
        invalid.write_log(LOG_TEST_CODE)
        invalid_before = invalid.implementation.read_bytes()
        rejected_config = invalid.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND,
            "--exit-code", "1",
        )
        config_gate = gate_result(rejected_config)
        self.assertEqual(2, rejected_config.returncode)
        self.assertEqual(["self_healing_config_invalid"], config_gate["blocking_reasons"])
        self.assertFalse((invalid.artifact / "00_self_healing").exists())
        self.assertEqual(invalid_before, invalid.implementation.read_bytes())

        fixture = self.enabled_fixture(mode="apply_with_approval")
        self.ready_to_close(fixture)
        before = fixture.implementation.read_bytes()
        closed = fixture.run_self_heal(
            "--self-heal-step", "close",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(closed)
        self.assertEqual(2, closed.returncode)
        self.assertEqual(closed.returncode, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["human_approval_missing"], gate["blocking_reasons"])
        self.assertEqual(before, fixture.implementation.read_bytes())

        sidecar_before_whitespace = tree_snapshot(
            fixture.artifact / "00_self_healing"
        )
        whitespace = fixture.run_self_heal(
            "--self-heal-step", "close", "--human-approval", "   ",
            role="reviewer", session="independent-review-session",
        )
        whitespace_gate = gate_result(whitespace)
        self.assertEqual(2, whitespace.returncode, whitespace.stdout + whitespace.stderr)
        self.assertEqual(whitespace.returncode, whitespace_gate["exit_code"])
        self.assertEqual(["human_approval_missing"], whitespace_gate["blocking_reasons"])
        self.assertEqual(before, fixture.implementation.read_bytes())
        self.assertEqual(
            sidecar_before_whitespace,
            tree_snapshot(fixture.artifact / "00_self_healing"),
        )

    def test_18c_applied_before_close_resume_restores_bytes_modes_and_tree(self) -> None:
        import os
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate

        fixture = self.enabled_fixture(
            base=self.tmp / "applied-window",
            mode="apply_with_approval",
            allowed_write_regex=[r"^tests/(?:test_.*\.py|generated/sub/helper\.py)$"],
        )
        fixture.implementation.chmod(0o755)
        self.to_healing_active(fixture)
        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": BASE_REPAIRED},
            name="candidate-applied-window",
        )
        proposed = fixture.run_self_heal(
            "--self-heal-step", "propose", "--candidate-dir", str(candidate),
            "--pytest-log", GOLDEN_LOG_NAME,
            role="implementer", session="healer-session",
        )
        self.assertEqual(0, proposed.returncode, proposed.stdout + proposed.stderr)
        self.assertEqual(0, self.review_and_close(fixture).returncode)
        before_tree = tree_snapshot(fixture.root / "tests")
        before_mode = self.mode(fixture.implementation)
        journal_path = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id() / "apply_journal.json"
        )

        class AppliedBeforeClose(RuntimeError):
            pass

        args = SimpleNamespace(
            self_heal_step="close", human_approval="fixture-owner", candidate_dir="",
            review_file="", pytest_log="", command="", exit_code=0,
        )
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ), mock.patch.object(
            self_heal_gate, "run_verification", side_effect=AppliedBeforeClose("kill window")
        ):
            with self.assertRaises(AppliedBeforeClose):
                self_heal_gate.run(fixture.artifact, args)

        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual("applied", journal["state"])
        self.assertEqual("required", journal["mode_integrity"])
        self.assertEqual(0o755, self.mode(fixture.implementation))

        resumed = fixture.run_self_heal(
            "--self-heal-step", "resume",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(resumed)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertEqual(resumed.returncode, gate["exit_code"])
        self.assertEqual("healing_active", gate["status"])
        self.assertEqual(before_tree, tree_snapshot(fixture.root / "tests"))
        self.assertEqual(before_mode, self.mode(fixture.implementation))
        self.assertEqual(
            "rolled_back", json.loads(journal_path.read_text(encoding="utf-8"))["state"]
        )

    def test_18c1_a_tampered_journal_cannot_delete_a_preexisting_empty_directory(self) -> None:
        import os
        from unittest import mock

        import self_heal_gate
        import self_heal_sidecar
        from role_governance import load_context

        fixture = self.enabled_fixture(
            base=self.tmp / "journal-directory-binding",
            mode="apply_with_approval",
            allowed_write_regex=[r"^tests/(?:test_.*\.py|generated/sub/helper\.py)$"],
        )
        preexisting = fixture.root / "tests/generated"
        preexisting.mkdir()
        self.to_healing_active(fixture)
        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": BASE_REPAIRED},
            name="candidate-directory-binding",
        )
        proposed = fixture.run_self_heal(
            "--self-heal-step", "propose", "--candidate-dir", str(candidate),
            "--pytest-log", GOLDEN_LOG_NAME,
            role="implementer", session="healer-session",
        )
        self.assertEqual(0, proposed.returncode, proposed.stdout + proposed.stderr)
        self.assertEqual(0, self.review_and_close(fixture).returncode)

        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ):
            ctx = load_context(fixture.artifact)
            policy = self_heal_gate.self_healing_policy(ctx.config)
            store = self_heal_sidecar.SelfHealSidecar(ctx)
            changes, _manifest, errors, missing_directories = (
                self_heal_gate._candidate_state(ctx, attempt)
            )
            self.assertEqual([], errors)
            self.assertEqual([], missing_directories)
            self_heal_gate.apply_candidate(
                ctx, policy, store, attempt, changes, missing_directories
            )

        journal_path = attempt / "apply_journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual([], journal["created_directories"])
        journal["created_directories"] = ["tests/generated"]
        journal_path.write_text(json.dumps(journal, indent=2), encoding="utf-8")
        workspace_before = tree_snapshot(fixture.root / "tests")
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")

        rejected = fixture.run_self_heal(
            "--self-heal-step", "resume",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(rejected)
        self.assertEqual(2, rejected.returncode, rejected.stdout + rejected.stderr)
        self.assertEqual(rejected.returncode, gate["exit_code"])
        self.assertEqual(["apply_journal_invalid"], gate["blocking_reasons"])
        self.assertEqual(workspace_before, tree_snapshot(fixture.root / "tests"))
        self.assertEqual(sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing"))
        self.assertTrue(preexisting.is_dir())

        journal["created_directories"] = []
        journal_path.write_text(json.dumps(journal, indent=2), encoding="utf-8")
        resumed = fixture.run_self_heal(
            "--self-heal-step", "resume",
            role="reviewer", session="independent-review-session",
        )
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertTrue(preexisting.is_dir())
        self.assertEqual([], list(preexisting.iterdir()))

    def test_18d_normal_apply_preserves_existing_mode(self) -> None:
        for original_mode in (0o644, 0o755):
            with self.subTest(mode=oct(original_mode)):
                fixture = self.enabled_fixture(
                    base=self.tmp / f"mode-{original_mode:o}",
                    mode="apply_with_approval",
                    allowed_write_regex=[r"^tests/(?:test_.*\.py|helper\.py)$"],
                )
                fixture.implementation.chmod(original_mode)
                self.to_healing_active(fixture)
                candidate = fixture.candidate(
                    {"tests/test_UC-SELFHEAL.py": BASE_REPAIRED},
                    name=f"candidate-mode-{original_mode:o}",
                )
                self.assertEqual(
                    0,
                    fixture.run_self_heal(
                        "--self-heal-step", "propose", "--candidate-dir", str(candidate),
                        "--pytest-log", GOLDEN_LOG_NAME,
                        role="implementer", session="healer-session",
                    ).returncode,
                )
                self.assertEqual(0, self.review_and_close(fixture).returncode)
                closed = fixture.run_self_heal(
                    "--self-heal-step", "close", "--human-approval", "  fixture-owner  ",
                    role="reviewer", session="independent-review-session",
                )
                self.assertEqual(0, closed.returncode, closed.stdout + closed.stderr)
                self.assertEqual(original_mode, self.mode(fixture.implementation))
                # An approved close in apply mode must actually write the
                # reviewed bytes.  Asserting only the preserved mode passes
                # unchanged when the apply branch is skipped entirely, which is
                # exactly the "successful close that applied nothing" shape.
                self.assertEqual(
                    BASE_REPAIRED,
                    fixture.implementation.read_text(encoding="utf-8"),
                )
                journals = sorted(
                    (fixture.artifact / "00_self_healing").rglob("apply_journal.json")
                )
                self.assertEqual(1, len(journals), journals)
                journal = json.loads(journals[0].read_text(encoding="utf-8"))
                self.assertEqual("applied", journal["state"])
                self.assertTrue(
                    all(entry.get("applied") is True for entry in journal["files"]),
                    journal,
                )
                close_receipt = next(
                    (fixture.artifact / "00_self_healing").glob("*-attempt_closed.json")
                )
                self.assertEqual(
                    "fixture-owner",
                    json.loads(close_receipt.read_text(encoding="utf-8"))["payload"][
                        "approved_by"
                    ],
                )

    def test_18d1_apply_primitive_secures_a_new_file_and_directories(self) -> None:
        """Retain low-level journal coverage outside the one-file proof gate."""

        from types import SimpleNamespace

        import self_heal_gate
        from role_governance import load_context
        from self_heal_review import FileChange

        fixture = self.enabled_fixture(
            base=self.tmp / "new-file-mode-primitive",
            mode="apply_with_approval",
            allowed_write_regex=[r"^tests/generated/sub/helper[.]py$"],
        )
        ctx = load_context(fixture.artifact)
        policy = self_heal_gate.self_healing_policy(ctx.config)
        attempt = fixture.artifact / "00_self_healing" / "attempts" / "unit-apply"
        attempt.mkdir(parents=True)
        store = SimpleNamespace(
            latest_payload=lambda _event: {"candidate_manifest_sha256": "a" * 64}
        )
        changes = [
            FileChange(
                path="tests/generated/sub/helper.py",
                before=None,
                after="HELPER = 1\n",
            )
        ]
        missing = ["tests/generated", "tests/generated/sub"]
        journal = self_heal_gate.apply_candidate(
            ctx, policy, store, attempt, changes, missing
        )

        helper = fixture.root / "tests/generated/sub/helper.py"
        self.assertEqual("applied", journal["state"])
        self.assertEqual(missing, journal["created_directories"])
        self.assertEqual(0o600, self.mode(helper))
        self.assertEqual("HELPER = 1\n", helper.read_text(encoding="utf-8"))

    def test_18e_apply_rejects_special_mode_bits_before_any_workspace_write(self) -> None:
        fixture = self.enabled_fixture(
            base=self.tmp / "special-mode", mode="apply_with_approval"
        )
        self.ready_to_close(fixture)
        fixture.implementation.chmod(0o4755)
        workspace_before = tree_snapshot(fixture.root / "tests")
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")

        closed = fixture.run_self_heal(
            "--self-heal-step", "close", "--human-approval", "fixture-owner",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(closed)
        self.assertEqual(2, closed.returncode, closed.stdout + closed.stderr)
        self.assertEqual(closed.returncode, gate["exit_code"])
        self.assertEqual(["workspace_drift_since_baseline"], gate["blocking_reasons"])
        self.assertEqual(workspace_before, tree_snapshot(fixture.root / "tests"))
        self.assertEqual(0o4755, self.mode(fixture.implementation))
        self.assertEqual(
            sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing")
        )
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        self.assertFalse((attempt / "apply_journal.json").exists())

    def test_18f_status_handles_a_durable_triage_receipt_without_a_chain(self) -> None:
        import os
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate
        import self_heal_sidecar

        fixture = self.enabled_fixture(base=self.tmp / "triage-orphan")
        fixture.write_log(LOG_TEST_CODE)
        args = SimpleNamespace(
            self_heal_step="triage",
            pytest_log=GOLDEN_LOG_NAME,
            command=GOLDEN_COMMAND,
            exit_code=1,
            candidate_dir="",
            review_file="",
            human_approval="",
        )
        real_atomic = self_heal_sidecar._atomic_bytes

        def interrupt_after_receipt(path, body, *, replace, mode=0o600):
            real_atomic(path, body, replace=replace, mode=mode)
            if Path(path).name == "000-triage_recorded.json":
                raise RuntimeError("injected interruption after triage receipt durability")

        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="reviewer-session"),
            clear=True,
        ), mock.patch.object(
            self_heal_sidecar, "_atomic_bytes", side_effect=interrupt_after_receipt
        ):
            with self.assertRaisesRegex(RuntimeError, "triage receipt durability"):
                self_heal_gate.run(fixture.artifact, args)

        sidecar_dir = fixture.artifact / "00_self_healing"
        orphan = sidecar_dir / "000-triage_recorded.json"
        self.assertTrue(orphan.is_file())
        self.assertFalse((sidecar_dir / "chain.json").exists())

        status = fixture.run_self_heal(
            "--self-heal-step", "status",
            role="reviewer", session="reviewer-session",
        )
        gate = gate_result(status)
        self.assertEqual(0, status.returncode, status.stdout + status.stderr)
        self.assertEqual(status.returncode, gate["exit_code"])
        self.assertEqual("triaged", gate["status"])
        self.assertFalse(orphan.exists())
        quarantine = list(
            (sidecar_dir / "attempts").glob(
                "*/unindexed_triage_recorded_*.json"
            )
        )
        self.assertEqual(1, len(quarantine))

        retried = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME,
            "--command", GOLDEN_COMMAND,
            "--exit-code", "1",
            role="reviewer", session="reviewer-session",
        )
        retried_gate = gate_result(retried)
        self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)
        self.assertEqual("triaged", retried_gate["status"])
        self.assertEqual("triage_recorded", fixture.sidecar_state())
        self.assertEqual(1, len(quarantine))

    def test_19_a_dirty_workspace_is_never_overwritten_by_an_apply(self) -> None:
        """The apply handler's own baseline control rejects exact byte drift."""

        import os
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate

        fixture = self.enabled_fixture(mode="apply_with_approval")
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        self.assertEqual(0, self.review_and_close(fixture).returncode)

        dirty = (BASE_BROKEN + "\n# local edit after verification\n").encode("utf-8")
        fixture.implementation.write_bytes(dirty)
        sidecar = fixture.artifact / "00_self_healing"
        sidecar_before = tree_snapshot(sidecar)
        args = SimpleNamespace(
            self_heal_step="close",
            human_approval="fixture-owner",
            candidate_dir="",
            review_file="",
            pytest_log="",
            command="",
            exit_code=0,
        )
        output = StringIO()
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ), mock.patch.object(
            self_heal_gate.sidecar, "self_heal_preflight", return_value=[]
        ) as outer_preflight, redirect_stdout(output):
            payload = self_heal_gate.run(fixture.artifact, args)
            rc = self_heal_gate.emit(payload)

        proc = subprocess.CompletedProcess(["self_heal_gate"], rc, output.getvalue(), "")
        gate = gate_result(proc)
        outer_preflight.assert_called_once()
        self.assertEqual(2, proc.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual(2, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["workspace_drift_since_baseline"], gate["blocking_reasons"])
        self.assertEqual(dirty, fixture.implementation.read_bytes())
        self.assertEqual(sidecar_before, tree_snapshot(sidecar))

    def test_19a1_synchronized_baseline_before_and_patch_tampering_cannot_rebind_review(self) -> None:
        import hashlib
        import os
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate
        from role_governance import load_context
        from self_heal_review import FileChange

        fixture = self.enabled_fixture(
            base=self.tmp / "synchronized-baseline-tamper",
            mode="apply_with_approval",
        )
        self.ready_to_close(fixture)
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        reviewed_manifest = json.loads(
            (attempt / "review_context.json").read_text(encoding="utf-8")
        )["binding"]["candidate_manifest_sha256"]

        dirty_text = BASE_BROKEN + "\n# unreviewed user edit\n"
        fixture.implementation.write_text(dirty_text, encoding="utf-8")
        (attempt / "before/tests/test_UC-SELFHEAL.py").write_text(
            dirty_text, encoding="utf-8"
        )
        baseline = json.loads((attempt / "baseline.json").read_text(encoding="utf-8"))
        baseline["files"][0]["before_sha256"] = hashlib.sha256(
            dirty_text.encode("utf-8")
        ).hexdigest()
        (attempt / "baseline.json").write_text(
            json.dumps(baseline, indent=2), encoding="utf-8"
        )
        (attempt / "candidate.patch").write_text(
            self_heal_gate.unified_diff(
                [
                    FileChange(
                        path="tests/test_UC-SELFHEAL.py",
                        before=dirty_text,
                        after=BASE_REPAIRED,
                    )
                ]
            ),
            encoding="utf-8",
        )
        workspace_before = fixture.implementation.read_bytes()
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")

        args = SimpleNamespace(human_approval="fixture-owner")
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ):
            ctx = load_context(fixture.artifact)
            policy = self_heal_gate.self_healing_policy(ctx.config)
            changes, live_manifest, errors, _missing = self_heal_gate._candidate_state(
                ctx, attempt
            )
            self.assertEqual([], errors)
            self.assertEqual(dirty_text, changes[0].before)
            self.assertNotEqual(reviewed_manifest, live_manifest)
            gate = self_heal_gate.step_close(ctx, policy, args)

        self.assertEqual(2, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["candidate_diverged_from_review"], gate["blocking_reasons"])
        self.assertEqual(workspace_before, fixture.implementation.read_bytes())
        self.assertEqual(sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing"))
        self.assertFalse((attempt / "apply_journal.json").exists())

    def test_19c_the_baseline_check_detects_drift_independently_of_the_preflight(self) -> None:
        """The inner control: apply refuses any target whose bytes moved."""

        import self_heal_gate
        from role_governance import load_context

        import os

        fixture = self.enabled_fixture()
        saved = dict(os.environ)
        try:
            os.environ.update(
                {
                    "BUGATE_PROJECT_ROOT": str(fixture.root),
                    "BUGATE_PROFILE": str(fixture.profile),
                    "BUGATE_AGENT_ROLE": "reviewer",
                    "BUGATE_SESSION_ID": "reviewer-session",
                    "MEMORY_BUS_URL": fixture.memory_url,
                    "BUGATE_MEMORY_HOME": str(fixture.memory_home),
                }
            )
            ctx = load_context(fixture.artifact)
        finally:
            os.environ.clear()
            os.environ.update(saved)

        baseline = {
            "pre_apply_missing_directories": [],
            "files": [
                {
                    "path": "tests/test_UC-SELFHEAL.py",
                    "before_sha256": self_heal_gate._sha256_bytes(BASE_BROKEN.encode("utf-8")),
                }
            ]
        }
        policy = self_heal_gate.self_healing_policy(ctx.config)
        self.assertEqual([], self_heal_gate.workspace_drifted(ctx, policy, baseline))
        fixture.implementation.write_text(BASE_BROKEN + "# local\n", encoding="utf-8")
        self.assertEqual(
            ["tests/test_UC-SELFHEAL.py"],
            self_heal_gate.workspace_drifted(ctx, policy, baseline),
        )

    def test_19b_the_profile_decides_which_paths_a_repair_may_touch(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        candidate = fixture.candidate({"bugate.profile.yaml": "mode: off\n"})
        proc = fixture.run_self_heal(
            "--self-heal-step", "propose", "--candidate-dir", str(candidate),
            role="implementer", session="healer-session",
        )
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode)
        self.assertEqual("blocked", gate["status"])
        self.assertIn("write_target_is_a_governed_surface", gate["blocking_reasons"])

    def test_19d_sandbox_copy_rejects_copied_symlinks_but_ignores_pruned_ones(self) -> None:
        import os

        victim = self.tmp / "outside-victim.txt"
        victim.write_bytes(b"outside bytes\n")
        fixture = self.enabled_fixture(
            base=self.tmp / "copied-link",
            verification_commands=["python3 write_escape.py"],
        )
        (fixture.root / "write_escape.py").write_text(
            "from pathlib import Path\nPath('escape.txt').write_text('owned')\n",
            encoding="utf-8",
        )
        os.symlink(victim, fixture.root / "escape.txt")
        self.to_healing_active(fixture)
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")
        proc = self.propose(fixture, BASE_REPAIRED)
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, gate["exit_code"])
        self.assertEqual(["candidate_path_unsafe"], gate["blocking_reasons"])
        self.assertEqual(b"outside bytes\n", victim.read_bytes())
        self.assertEqual(sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing"))

        ignored = self.enabled_fixture(base=self.tmp / "ignored-link")
        ignored_dir = ignored.root / ".venv"
        ignored_dir.mkdir()
        os.symlink(victim, ignored_dir / "outside.txt")
        self.to_healing_active(ignored)
        accepted = self.propose(ignored, BASE_REPAIRED)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual(b"outside bytes\n", victim.read_bytes())

    def test_19e_candidate_parent_symlink_and_corrupt_baseline_fail_closed(self) -> None:
        import os
        import shutil

        fixture = self.enabled_fixture(base=self.tmp / "candidate-parent-link")
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        review_name = fixture.write_review()
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        outside = self.tmp / "outside-candidate"
        outside.mkdir()
        victim = outside / "test_UC-SELFHEAL.py"
        victim.write_bytes(b"outside candidate bytes\n")
        shutil.rmtree(attempt / "candidate" / "tests")
        os.symlink(outside, attempt / "candidate" / "tests")
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")
        reviewed = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", review_name,
            role="reviewer", session="independent-review-session",
        )
        reviewed_gate = gate_result(reviewed)
        self.assertEqual(2, reviewed.returncode, reviewed.stdout + reviewed.stderr)
        self.assertEqual(
            ["sidecar_integrity_failed"], reviewed_gate["blocking_reasons"]
        )
        self.assertEqual(b"outside candidate bytes\n", victim.read_bytes())
        self.assertFalse((attempt / "independent_review.json").exists())
        self.assertEqual(
            sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing")
        )

        corrupt = self.enabled_fixture(
            base=self.tmp / "corrupt-baseline", mode="apply_with_approval"
        )
        self.ready_to_close(corrupt)
        before = corrupt.implementation.read_bytes()
        corrupt_attempt = (
            corrupt.artifact / "00_self_healing" / "attempts"
            / corrupt.sidecar_attempt_id()
        )
        (corrupt_attempt / "baseline.json").write_bytes(b"\xffnot-json")
        closed = corrupt.run_self_heal(
            "--self-heal-step", "close", "--human-approval", "fixture-owner",
            role="reviewer", session="independent-review-session",
        )
        closed_gate = gate_result(closed)
        self.assertEqual(2, closed.returncode, closed.stdout + closed.stderr)
        self.assertEqual(closed.returncode, closed_gate["exit_code"])
        self.assertEqual(["self_heal_evidence_invalid"], closed_gate["blocking_reasons"])
        self.assertEqual(before, corrupt.implementation.read_bytes())

    def test_19f_non_utf8_review_is_five_field_blocked_without_side_effects(self) -> None:
        fixture = self.enabled_fixture(base=self.tmp / "non-utf8-review")
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        (fixture.root / "review.bin").write_bytes(b"\xff\xfe")
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")
        proc = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", "review.bin",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(
            {"status", "exit_code", "blocking_reasons", "artifact_paths", "next_action"},
            set(gate),
        )
        self.assertEqual(["self_heal_evidence_invalid"], gate["blocking_reasons"])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing"))

    # ------------------------------------------ scenarios 20-21 (identity)

    def test_20_the_review_session_must_differ_from_both_prior_legs(self) -> None:
        # #0/#1 use reviewer-session; #2/#3 use healer-session.  The review
        # must be independent from both legs, not merely from the healer.
        for label, forbidden_session in (
            ("triage-and-handoff", "reviewer-session"),
            ("accept-and-propose", "healer-session"),
        ):
            with self.subTest(prior_leg=label):
                fixture = self.enabled_fixture(base=self.tmp / label)
                self.to_healing_active(fixture)
                self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
                name = fixture.write_review()
                before = fixture.implementation.read_bytes()
                proc = fixture.run_self_heal(
                    "--self-heal-step", "review", "--review-file", name,
                    role="reviewer", session=forbidden_session,
                )
                gate = gate_result(proc)
                self.assertEqual(2, proc.returncode, msg=json.dumps(gate, indent=2))
                self.assertEqual(proc.returncode, gate["exit_code"])
                self.assertEqual("blocked", gate["status"])
                self.assertEqual(["same_session_self_approval"], gate["blocking_reasons"])
                self.assertEqual(before, fixture.implementation.read_bytes())
                self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_20b_the_independent_reviewer_may_not_be_the_healer(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        name = fixture.write_review()
        proc = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", name,
            role="reviewer", session="healer-session",
        )
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode)
        self.assertEqual(proc.returncode, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["same_session_self_approval"], gate["blocking_reasons"])
        self.assertEqual("awaiting_independent_review", fixture.sidecar_state())

    def test_20c_the_healer_leg_requires_the_implementer_role(self) -> None:
        fixture = self.enabled_fixture()
        fixture.write_log(LOG_TEST_CODE)
        fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        fixture.run_self_heal("--self-heal-step", "handoff")
        proc = fixture.run_self_heal(
            "--self-heal-step", "accept", role="reviewer", session="another-reviewer-session"
        )
        gate = gate_result(proc)
        self.assertEqual(2, proc.returncode)
        self.assertEqual(proc.returncode, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(["self_heal_role_not_allowed"], gate["blocking_reasons"])

    def test_20d_review_binding_runtime_and_dispatch_are_exact_cli_controls(self) -> None:
        fixture = self.enabled_fixture(base=self.tmp / "review-binding")
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        binding = json.loads(
            (attempt / "review_context.json").read_text(encoding="utf-8")
        )["binding"]
        variants = (
            ("other-uc", {**binding, "uc": "UC-OTHER"}, {}, "independent_review_uc_mismatch"),
            (
                "other-attempt",
                {**binding, "attempt_id": "another-attempt"},
                {},
                "independent_review_attempt_mismatch",
            ),
            (
                "stale-candidate",
                {**binding, "candidate_manifest_sha256": "0" * 64},
                {},
                "independent_review_candidate_mismatch",
            ),
            (
                "stale-precode",
                {**binding, "precode_evidence_sha256": "0" * 64},
                {},
                "independent_review_precode_evidence_mismatch",
            ),
            (
                "stale-before",
                {**binding, "before_log_sha256": "0" * 64},
                {},
                "independent_review_before_log_mismatch",
            ),
            (
                "stale-after",
                {**binding, "after_log_sha256": "0" * 64},
                {},
                "independent_review_after_log_mismatch",
            ),
            (
                "stale-falsification",
                {**binding, "falsification_sha256": "0" * 64},
                {},
                "independent_review_falsification_evidence_mismatch",
            ),
            (
                "stale-oracle",
                {**binding, "oracle_refs": ["O-404"]},
                {},
                "independent_review_oracle_mismatch",
            ),
            (
                "fabricated-dispatch",
                binding,
                {"dispatch_mode": "fallback_placeholder"},
                "independent_review_degraded",
            ),
            (
                "runtime-mismatch",
                binding,
                {"runtime": "claude"},
                "independent_review_runtime_mismatch",
            ),
        )
        for label, supplied_binding, options, exact_reason in variants:
            with self.subTest(label=label):
                name = fixture.write_review(
                    binding=dict(supplied_binding), name=f"review-{label}.json", **options
                )
                before = tree_snapshot(fixture.artifact / "00_self_healing")
                workspace_before = fixture.implementation.read_bytes()
                proc = fixture.run_self_heal(
                    "--self-heal-step", "review", "--review-file", name,
                    role="reviewer", session="independent-review-session",
                )
                gate = gate_result(proc)
                self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
                self.assertEqual(proc.returncode, gate["exit_code"])
                self.assertEqual("blocked", gate["status"])
                self.assertEqual([exact_reason], gate["blocking_reasons"])
                self.assertEqual(before, tree_snapshot(fixture.artifact / "00_self_healing"))
                self.assertEqual(workspace_before, fixture.implementation.read_bytes())
                self.assertFalse((attempt / "independent_review.json").exists())

    def test_20e_live_bound_logs_and_valid_review_archive_are_immutable(self) -> None:
        import hashlib

        fixture = self.enabled_fixture(base=self.tmp / "review-log-drift")
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        review_name = fixture.write_review(name="review-live-drift.json")
        chain_before = (
            fixture.artifact / "00_self_healing" / "chain.json"
        ).read_bytes()
        live_variants = (
            ("before.log", b"tampered before log\n", "independent_review_before_log_mismatch"),
            ("after.log", b"tampered after log\n", "independent_review_after_log_mismatch"),
            (
                "falsification.json",
                b'{"status":"tampered"}\n',
                "independent_review_falsification_evidence_mismatch",
            ),
            (
                "review_context.json",
                b'{"schema":"tampered"}\n',
                "independent_review_context_invalid",
            ),
        )
        for relative, tampered, reason in live_variants:
            with self.subTest(live_evidence=relative):
                target = attempt / relative
                original = target.read_bytes()
                target.write_bytes(tampered)
                drifted = fixture.run_self_heal(
                    "--self-heal-step", "review", "--review-file", review_name,
                    role="reviewer", session="independent-review-session",
                )
                drifted_gate = gate_result(drifted)
                self.assertEqual(2, drifted.returncode, drifted.stdout + drifted.stderr)
                self.assertEqual([reason], drifted_gate["blocking_reasons"])
                self.assertEqual(
                    chain_before,
                    (fixture.artifact / "00_self_healing" / "chain.json").read_bytes(),
                )
                self.assertFalse((attempt / "independent_review.json").exists())
                target.write_bytes(original)

        # The outer lifecycle preflight also detects accepted-artifact drift.
        # Exercise the handler's independent live-hash control directly so the
        # review-context binding cannot be accidentally reduced to that outer check.
        import os
        from types import SimpleNamespace
        from unittest import mock

        import self_heal_gate
        from role_governance import load_context

        args = SimpleNamespace(
            self_heal_step="review", review_file=review_name, human_approval="",
            candidate_dir="", pytest_log="", command="", exit_code=0,
        )
        brief = fixture.artifact / "01_business_brief.md"
        brief_body = brief.read_bytes()
        brief.write_bytes(brief_body + b"\n<!-- drift -->\n")
        with mock.patch.dict(
            os.environ,
            fixture.env(role="reviewer", session="independent-review-session"),
            clear=True,
        ):
            ctx = load_context(fixture.artifact)
            policy = self_heal_gate.self_healing_policy(ctx.config)
            precode_gate = self_heal_gate.step_review(ctx, policy, args)
        self.assertEqual(2, precode_gate["exit_code"])
        self.assertEqual(
            ["independent_review_precode_evidence_mismatch"],
            precode_gate["blocking_reasons"],
        )
        self.assertEqual(
            chain_before,
            (fixture.artifact / "00_self_healing" / "chain.json").read_bytes(),
        )
        self.assertFalse((attempt / "independent_review.json").exists())
        brief.write_bytes(brief_body)

        archived = self.enabled_fixture(base=self.tmp / "review-archive")
        self.to_healing_active(archived)
        self.assertEqual(0, self.propose(archived, BASE_REPAIRED).returncode)
        archived_attempt = (
            archived.artifact / "00_self_healing" / "attempts"
            / archived.sidecar_attempt_id()
        )
        name = archived.write_review(
            name="review-audit.json",
            findings=[{"claim": "assertions preserved", "evidence": "after.log"}],
            residual_risks=["none beyond the recorded fixture boundary"],
        )
        review_bytes = (archived.root / name).read_bytes()
        accepted = archived.run_self_heal(
            "--self-heal-step", "review", "--review-file", name,
            role="reviewer", session="independent-review-session",
        )
        accepted_gate = gate_result(accepted)
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        audit_path = archived_attempt / "independent_review.json"
        self.assertEqual(review_bytes, audit_path.read_bytes())
        self.assertIn(
            audit_path.relative_to(archived.root).as_posix(), accepted_gate["artifact_paths"]
        )
        receipt_path = next(
            (archived.artifact / "00_self_healing").glob("*-independent_review.json")
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(review_bytes).hexdigest(),
            receipt["payload"]["review_document_sha256"],
        )
        sidecar_before_second = tree_snapshot(archived.artifact / "00_self_healing")
        second_name = archived.write_review(
            name="review-second.json", verdict="rejected",
            findings=[{"claim": "second verdict", "evidence": "candidate.patch"}],
        )
        second = archived.run_self_heal(
            "--self-heal-step", "review", "--review-file", second_name,
            role="reviewer", session="another-independent-review-session",
        )
        second_gate = gate_result(second)
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual(
            ["self_heal_transition_not_allowed"], second_gate["blocking_reasons"]
        )
        self.assertEqual(
            sidecar_before_second, tree_snapshot(archived.artifact / "00_self_healing")
        )
        self.assertEqual(review_bytes, audit_path.read_bytes())

    def test_20e1_live_falsification_inputs_are_rechecked_at_review_and_close(self) -> None:
        """The archived score cannot authorize a changed spec or evidence file."""

        fixture = self.enabled_fixture(
            base=self.tmp / "falsification-input-drift",
            mode="apply_with_approval",
        )
        self.to_healing_active(fixture)
        proposed = self.propose(fixture, BASE_REPAIRED)
        self.assertEqual(0, proposed.returncode, proposed.stdout + proposed.stderr)

        spec = fixture.root / "falsification_spec.yaml"
        spec_body = spec.read_bytes()
        sidecar_before_review = tree_snapshot(fixture.artifact / "00_self_healing")
        spec.write_bytes(spec_body + b"# review-time drift\n")
        review_name = fixture.write_review(name="review-input-drift.json")
        blocked_review = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", review_name,
            role="reviewer", session="independent-review-session",
        )
        self.assertEqual(2, blocked_review.returncode)
        self.assertEqual(
            ["independent_review_falsification_input_drift"],
            gate_result(blocked_review)["blocking_reasons"],
        )
        self.assertEqual(
            sidecar_before_review,
            tree_snapshot(fixture.artifact / "00_self_healing"),
        )

        spec.write_bytes(spec_body)
        reviewed = fixture.run_self_heal(
            "--self-heal-step", "review", "--review-file", review_name,
            role="reviewer", session="independent-review-session",
        )
        self.assertEqual(0, reviewed.returncode, reviewed.stdout + reviewed.stderr)

        evidence = fixture.root / "evidence" / "probe.json"
        evidence_body = evidence.read_bytes()
        workspace_before_close = tree_snapshot(fixture.root / "tests")
        sidecar_before_close = tree_snapshot(fixture.artifact / "00_self_healing")
        evidence.write_text('{"status":"accepted","note":"drift"}\n', encoding="utf-8")
        blocked_close = fixture.run_self_heal(
            "--self-heal-step", "close", "--human-approval", "fixture-owner",
            role="reviewer", session="independent-review-session",
        )
        self.assertEqual(2, blocked_close.returncode)
        self.assertEqual(
            ["independent_review_falsification_input_drift"],
            gate_result(blocked_close)["blocking_reasons"],
        )
        self.assertEqual(workspace_before_close, tree_snapshot(fixture.root / "tests"))
        self.assertEqual(
            sidecar_before_close,
            tree_snapshot(fixture.artifact / "00_self_healing"),
        )
        evidence.write_bytes(evidence_body)

    def test_20f_close_reauthenticates_post_review_evidence_before_apply(self) -> None:
        fixture = self.enabled_fixture(
            base=self.tmp / "post-review-tamper", mode="apply_with_approval"
        )
        self.ready_to_close(fixture)
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        (attempt / "after.log").write_bytes(b"tampered after independent review\n")
        workspace_before = tree_snapshot(fixture.root / "tests")
        sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")

        closed = fixture.run_self_heal(
            "--self-heal-step", "close", "--human-approval", "fixture-owner",
            role="reviewer", session="independent-review-session",
        )
        gate = gate_result(closed)
        self.assertEqual(2, closed.returncode, closed.stdout + closed.stderr)
        self.assertEqual(closed.returncode, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(
            ["independent_review_after_log_mismatch"], gate["blocking_reasons"]
        )
        self.assertEqual(workspace_before, tree_snapshot(fixture.root / "tests"))
        self.assertEqual(
            sidecar_before, tree_snapshot(fixture.artifact / "00_self_healing")
        )
        self.assertFalse((attempt / "apply_journal.json").exists())

    def test_20g_verify_close_reauthenticates_candidate_context_archive_and_live_evidence(self) -> None:
        fixture = self.enabled_fixture(base=self.tmp / "verify-close-reauth")
        self.ready_to_close(fixture)
        attempt = (
            fixture.artifact / "00_self_healing" / "attempts"
            / fixture.sidecar_attempt_id()
        )
        archive_document = json.loads(
            (attempt / "independent_review.json").read_text(encoding="utf-8")
        )
        archive_document["residual_risks"] = ["post-review archive mutation"]
        tampered_archive = json.dumps(archive_document, indent=2).encode("utf-8")
        variants = (
            (
                "candidate.patch",
                b"tampered reviewed patch\n",
                "candidate_diverged_from_review",
            ),
            (
                "review_context.json",
                b'{"schema":"tampered"}\n',
                "independent_review_context_invalid",
            ),
            (
                "independent_review.json",
                tampered_archive,
                "independent_review_evidence_drift",
            ),
            (
                "after.log",
                b"tampered reviewed verification output\n",
                "independent_review_after_log_mismatch",
            ),
        )
        workspace_before = tree_snapshot(fixture.root / "tests")
        for relative, tampered, exact_reason in variants:
            with self.subTest(evidence=relative):
                target = attempt / relative
                original = target.read_bytes()
                target.write_bytes(tampered)
                sidecar_before = tree_snapshot(fixture.artifact / "00_self_healing")
                closed = fixture.run_self_heal(
                    "--self-heal-step", "close",
                    role="reviewer", session="independent-review-session",
                )
                gate = gate_result(closed)
                self.assertEqual(2, closed.returncode, closed.stdout + closed.stderr)
                self.assertEqual(closed.returncode, gate["exit_code"])
                self.assertEqual("blocked", gate["status"])
                self.assertEqual([exact_reason], gate["blocking_reasons"])
                self.assertEqual(workspace_before, tree_snapshot(fixture.root / "tests"))
                self.assertEqual(
                    sidecar_before,
                    tree_snapshot(fixture.artifact / "00_self_healing"),
                )
                target.write_bytes(original)

        closed = fixture.run_self_heal(
            "--self-heal-step", "close",
            role="reviewer", session="independent-review-session",
        )
        self.assertEqual(0, closed.returncode, closed.stdout + closed.stderr)
        self.assertEqual("healing_verified", gate_result(closed)["status"])
        self.assertEqual(workspace_before, tree_snapshot(fixture.root / "tests"))

    def test_21_a_strict_memory_transition_that_cannot_be_verified_blocks(self) -> None:
        """The CLI preserves its five-field boundary for every Memory failure."""

        import os

        failures = (
            ("prepare", "memory_exact_id_verification_failed"),
            ("finalize", "memory_receipt_binding_failed"),
            ("verify", "memory_receipt_verification_failed"),
        )
        for phase, reason in failures:
            with self.subTest(memory_phase=phase):
                fixture = self.enabled_fixture(base=self.tmp / phase)
                fixture.write_log(LOG_TEST_CODE)
                triaged = fixture.run_self_heal(
                    "--pytest-log", GOLDEN_LOG_NAME,
                    "--command", GOLDEN_COMMAND,
                    "--exit-code", "1",
                )
                self.assertEqual(0, triaged.returncode, triaged.stderr)
                self.assertEqual("triage_recorded", fixture.sidecar_state())

                patch_dir = fixture.root / "strict-memory-patch"
                patch_dir.mkdir()
                (patch_dir / "sitecustomize.py").write_text(
                    "import os\n"
                    "from pathlib import Path\n"
                    "if os.environ.get('BUGATE_TEST_SELF_HEAL_MEMORY_PATCH'):\n"
                    "    import memory_bus\n"
                    "    import self_heal_sidecar\n"
                    "    from role_governance import RoleGovernanceError\n"
                    "    real_init = self_heal_sidecar.SelfHealSidecar.__init__\n"
                    "    def strict_init(self, ctx):\n"
                    "        ctx.policy['memory_mode'] = 'required'\n"
                    "        real_init(self, ctx)\n"
                    "    self_heal_sidecar.SelfHealSidecar.__init__ = strict_init\n"
                    "    phase = os.environ['BUGATE_TEST_SELF_HEAL_MEMORY_PATCH']\n"
                    "    def prepared(*, payload, strict):\n"
                    "        if phase == 'prepare':\n"
                    "            raise RuntimeError('injected prepare failure')\n"
                    "        return {'namespace': os.environ['MEMORY_BUS_PROJECT_TAG'], "
                    "'memory_id': 'a' * 64, 'verified_at': 'fixture-time'}\n"
                    "    def finalized(*, memory_id, receipt_sha256, expected, strict):\n"
                    "        if phase == 'finalize':\n"
                    "            raise RuntimeError('injected finalize failure')\n"
                    "        return {'namespace': os.environ['MEMORY_BUS_PROJECT_TAG'], "
                    "'memory_id': memory_id, 'verified_at': 'fixture-time'}\n"
                    "    def verified(*, receipt, strict):\n"
                    "        if phase == 'verify':\n"
                    "            raise RuntimeError('injected verify failure')\n"
                    "        return {'status': 'verified'}\n"
                    "    memory_bus.prepare_role_transition = prepared\n"
                    "    memory_bus.finalize_role_transition = finalized\n"
                    "    memory_bus.verify_role_transition = verified\n",
                    encoding="utf-8",
                )
                before_state = fixture.sidecar_state()
                before_tree = tree_snapshot(fixture.artifact / "00_self_healing")
                before_uc = tree_snapshot(fixture.artifact)
                env = fixture.env(role="reviewer", session="reviewer-session")
                env["BUGATE_TEST_SELF_HEAL_MEMORY_PATCH"] = phase
                env["PYTHONPATH"] = os.pathsep.join(
                    (str(patch_dir), str(ENGINE / "scripts"), str(TESTS))
                )
                command = [
                    sys.executable,
                    ENGINE / "scripts" / "sdtd_orchestrator.py",
                    fixture.artifact,
                    "--scope", "self-heal",
                    "--self-heal-step", "handoff",
                ]
                proc = subprocess.run(
                    [str(item) for item in command],
                    cwd=fixture.root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                gate = gate_result(proc)
                self.assertEqual(
                    {"status", "exit_code", "blocking_reasons", "artifact_paths", "next_action"},
                    set(gate),
                )
                self.assertEqual(2, proc.returncode, msg=proc.stdout + proc.stderr)
                self.assertEqual(proc.returncode, gate["exit_code"])
                self.assertEqual("blocked", gate["status"])
                self.assertEqual([reason], gate["blocking_reasons"])
                self.assertEqual([], gate["artifact_paths"])
                self.assertEqual("", proc.stderr)
                self.assertIn("BUGate self-heal status: BLOCKED\n", proc.stdout)
                self.assertEqual(before_state, fixture.sidecar_state())
                self.assertEqual(
                    before_tree, tree_snapshot(fixture.artifact / "00_self_healing")
                )
                self.assertEqual(before_uc, tree_snapshot(fixture.artifact))

    # ------------------------------------ scenarios 22-23 (compatibility)

    def test_22_an_older_engine_sees_no_change_when_the_sidecar_exists(self) -> None:
        """Contract 2.4: preflight and verify_evidence must not notice the sidecar.

        The main chain validator only walks ``00_role_evidence/`` and its event
        whitelist never sees a sidecar event, so a v0.4.0-v0.4.4 engine reading
        this UC behaves identically -- which is what makes the updater's
        rollback-to-prior-image guarantee survive this feature.
        """

        import os
        from dataclasses import asdict

        from role_governance import preflight, verify_evidence

        fixture = self.enabled_fixture()
        saved = dict(os.environ)
        try:
            os.environ.update(
                {
                    "BUGATE_PROJECT_ROOT": str(fixture.root),
                    "BUGATE_PROFILE": str(fixture.profile),
                    "BUGATE_AGENT_ROLE": "reviewer",
                    "BUGATE_SESSION_ID": "reviewer-session",
                    "BUGATE_AGENT_RUNTIME": "codex",
                    "MEMORY_BUS_URL": fixture.memory_url,
                    "BUGATE_MEMORY_HOME": str(fixture.memory_home),
                }
            )
            before_preflight = asdict(preflight(fixture.artifact, "post_run"))
            before_receipts = verify_evidence(fixture.artifact, phase="post_run")
            role_evidence_before = tree_snapshot(fixture.artifact / "00_role_evidence")

            self.to_healing_active(fixture)
            self.assertTrue((fixture.artifact / "00_self_healing" / "chain.json").is_file())

            after_preflight = asdict(preflight(fixture.artifact, "post_run"))
            after_receipts = verify_evidence(fixture.artifact, phase="post_run")
        finally:
            os.environ.clear()
            os.environ.update(saved)

        self.assertEqual(before_preflight, after_preflight)
        self.assertEqual(before_receipts, after_receipts)
        self.assertEqual(
            role_evidence_before, tree_snapshot(fixture.artifact / "00_role_evidence")
        )

    def test_22b_the_sidecar_is_write_guarded_like_the_role_evidence_chain(self) -> None:
        from bugate_core import load_config
        from check_role_evidence import check_paths, classify_path
        from role_governance import governance_policy

        fixture = self.enabled_fixture()
        config = load_config(fixture.root, str(fixture.profile))
        policy = governance_policy(config)
        for relative in (
            "usecases/UC-SELFHEAL/00_self_healing/chain.json",
            "usecases/UC-SELFHEAL/00_self_healing/attempts/att-1/candidate.patch",
        ):
            with self.subTest(path=relative):
                kind, artifact, classified = classify_path(
                    fixture.root, config, policy, relative
                )
                self.assertEqual("self_heal_evidence", kind)
                self.assertIsNone(artifact)
                self.assertEqual(relative, classified)
                failures, warnings = check_paths(
                    {relative}, root=fixture.root, config=config, policy=policy
                )
                self.assertEqual([], warnings)
                self.assertEqual(
                    [
                        f"{relative}: direct edits to 00_self_healing/ are forbidden; "
                        "use the BUGate self-heal publisher"
                    ],
                    failures,
                )

    def test_22c_a_direct_agent_edit_of_the_sidecar_is_denied(self) -> None:
        guard = ENGINE / "scripts" / "check_role_evidence.py"
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual(0, self.propose(fixture, BASE_REPAIRED).returncode)
        attempt = fixture.sidecar_attempt_id()
        targets = (
            "usecases/UC-SELFHEAL/00_self_healing/chain.json",
            (
                f"usecases/UC-SELFHEAL/00_self_healing/attempts/{attempt}/"
                "candidate/tests/test_UC-SELFHEAL.py"
            ),
        )
        sidecar = fixture.artifact / "00_self_healing"
        before = tree_snapshot(sidecar)
        for role, session in (
            ("designer", "designer-direct-edit"),
            ("implementer", "implementer-direct-edit"),
            ("reviewer", "reviewer-direct-edit"),
        ):
            for target in targets:
                with self.subTest(role=role, target=target):
                    target_bytes = (fixture.root / target).read_bytes()
                    proc = fixture.run(
                        [sys.executable, guard, target], role=role, session=session
                    )
                    self.assertEqual(2, proc.returncode, msg=proc.stdout + proc.stderr)
                    self.assertEqual("", proc.stdout)
                    self.assertEqual(
                        "BUGate role-evidence guard BLOCKED:\n"
                        f"  - {target}: direct edits to 00_self_healing/ are forbidden; "
                        "use the BUGate self-heal publisher\n",
                        proc.stderr,
                    )
                    self.assertEqual(target_bytes, (fixture.root / target).read_bytes())
                    self.assertEqual(before, tree_snapshot(sidecar))

    def test_23_the_new_engine_scripts_project_into_every_installed_tree(self) -> None:
        from bugate_install_contract import _archive_roles

        for name in (
            "scripts/failure_triage.py",
            "scripts/self_heal_gate.py",
            "scripts/self_heal_policy.py",
            "scripts/self_heal_review.py",
            "scripts/self_heal_sidecar.py",
        ):
            with self.subTest(script=name):
                self.assertTrue((ENGINE / name).is_file())
                self.assertIn("installable_payload", _archive_roles(name))

    def test_23b_the_golden_fixtures_are_not_projected_as_engine_payload(self) -> None:
        from bugate_install_contract import _archive_roles

        self.assertNotIn(
            "installable_payload",
            _archive_roles("tests/fixtures/golden/04_execution_report.v0.4.4.md"),
        )

    # ------------------------- scenarios 24-25 (zero behavior change)

    def _postrun_bytes(
        self, fixture: ImportedFixture, *, exit_code: int
    ) -> dict[str, bytes]:
        proc = fixture.run_postrun(exit_code=exit_code)
        self.assertEqual(0, proc.returncode, msg=proc.stdout + proc.stderr)
        return {
            name: (fixture.artifact / name).read_bytes()
            for name in (*POSTRUN, *HEALING_OUTPUTS)
        }

    def _assert_matches_v044_golden(
        self, produced: dict[str, bytes], scenario: str
    ) -> None:
        for name, body in produced.items():
            with self.subTest(artifact=name):
                golden = GOLDEN_DIR / golden_name(name, scenario)
                self.assertEqual(
                    golden.read_bytes(),
                    body,
                    msg=f"{name} diverged from the v0.4.4 golden bytes",
                )

    def _assert_only_postrun_outputs_changed(
        self,
        before: tuple[tuple[str, str, str], ...] | None,
        after: tuple[tuple[str, str, str], ...] | None,
        produced: dict[str, bytes],
    ) -> None:
        """Compare the complete UC tree, not a five-name sampling of it."""

        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        before_map = {(kind, path): digest for kind, path, digest in before or ()}
        after_map = {(kind, path): digest for kind, path, digest in after or ()}
        output_keys = {("file", name) for name in produced}
        self.assertEqual(set(before_map) | output_keys, set(after_map))
        for key, digest in before_map.items():
            with self.subTest(unchanged_uc_entry=key[1]):
                self.assertEqual(digest, after_map[key])
        self.assertFalse(
            any(
                kind == "dir" and (path == "00_self_healing" or path.startswith("00_self_healing/"))
                for kind, path in after_map
            )
        )

    def test_24_post_run_is_byte_identical_when_the_self_healing_key_is_absent(self) -> None:
        from bugate_selfheal_fixture import FAILING_LOG, PASSING_LOG

        for scenario, log, exit_code in (
            ("failed", FAILING_LOG, 1),
            ("passed", PASSING_LOG, 0),
        ):
            with self.subTest(scenario=scenario):
                fixture = ImportedFixture(self.tmp / scenario, self_healing=None)
                fixture.drive_to_post_run()
                fixture.write_log(log)
                before = tree_snapshot(fixture.artifact)

                produced = self._postrun_bytes(fixture, exit_code=exit_code)
                after = tree_snapshot(fixture.artifact)
                self._assert_matches_v044_golden(produced, scenario)
                self._assert_only_postrun_outputs_changed(before, after, produced)
                self.assertFalse((fixture.artifact / "00_self_healing").exists())

    def test_25_post_run_is_byte_identical_when_self_healing_mode_is_off(self) -> None:
        from bugate_selfheal_fixture import FAILING_LOG, PASSING_LOG

        for scenario, log, exit_code in (
            ("failed", FAILING_LOG, 1),
            ("passed", PASSING_LOG, 0),
        ):
            with self.subTest(scenario=scenario):
                fixture = ImportedFixture(
                    self.tmp / scenario, self_healing={"mode": "off"}
                )
                fixture.drive_to_post_run()
                fixture.write_log(log)
                before = tree_snapshot(fixture.artifact)

                produced = self._postrun_bytes(fixture, exit_code=exit_code)
                after = tree_snapshot(fixture.artifact)
                self._assert_matches_v044_golden(produced, scenario)
                self._assert_only_postrun_outputs_changed(before, after, produced)
                self.assertFalse((fixture.artifact / "00_self_healing").exists())

    def test_25b_the_self_heal_entry_creates_nothing_while_disabled(self) -> None:
        fixture = ImportedFixture(self.tmp, self_healing={"mode": "off"})
        fixture.drive_to_post_run()
        before = tree_snapshot(fixture.artifact)

        proc = fixture.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        gate = gate_result(proc)
        self.assertEqual(0, proc.returncode)
        self.assertEqual("disabled", gate["status"])
        self.assertEqual([], gate["blocking_reasons"])
        self.assertEqual([], gate["artifact_paths"])
        self.assertIn("BUGate self-heal status: DISABLED", proc.stdout)
        self.assertEqual(before, tree_snapshot(fixture.artifact))

    def test_25c_pre_code_and_post_run_scope_markers_are_unchanged(self) -> None:
        """The self-heal vocabulary is separate; it must not pollute the old one."""

        fixture = ImportedFixture(self.tmp, self_healing={"mode": "off"})
        fixture.drive_to_post_run()
        from bugate_selfheal_fixture import FAILING_LOG

        fixture.write_log(FAILING_LOG)
        proc = fixture.run_postrun(exit_code=1)
        expected_stdout = "".join(
            f"written {fixture.artifact / name}\n"
            for name in (*HEALING_OUTPUTS, *POSTRUN)
        ) + "BUGate lifecycle status: POST_RUN_ACTIVE\n"
        self.assertEqual(0, proc.returncode)
        self.assertEqual(expected_stdout, proc.stdout)
        self.assertEqual("", proc.stderr)

    def test_25d_legacy_auto_state_reports_self_heal_separately(self) -> None:
        """Contract 7.2: the third scope must not fall through to the pre-code branch."""

        from sdtd_orchestrator import _legacy_auto_state

        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp)
            self.assertEqual("SELF_HEAL_DISABLED", _legacy_auto_state(artifact, "self-heal"))
            self.assertEqual("POST_RUN_ACTIVE", _legacy_auto_state(artifact, "post-run"))

    def test_25e_lifecycle_drift_invalidates_an_open_attempt(self) -> None:
        fixture = self.enabled_fixture()
        self.to_healing_active(fixture)
        self.assertEqual("healing_active", fixture.sidecar_state())

        # Reviewer completion needs the 04/05 evidence it records.
        self.assertEqual(0, fixture.run_postrun(exit_code=1).returncode)

        # Close the lifecycle: the role chain head moves, so the anchor no longer holds.
        completion = fixture.run(
            [
                sys.executable, ENGINE / "scripts" / "role_governance.py", "complete",
                fixture.artifact, "--phase", "post_run",
                "--run-command", GOLDEN_COMMAND, "--exit-code", "1",
                "--evidence-file", GOLDEN_LOG_NAME, "--gate-status", "failed",
            ],
            role="reviewer",
            session="reviewer-session",
        )
        self.assertEqual(0, completion.returncode, msg=completion.stdout + completion.stderr)

        proc = fixture.run_self_heal(
            "--self-heal-step", "propose", "--candidate-dir", str(
                fixture.candidate({"tests/test_UC-SELFHEAL.py": BASE_REPAIRED})
            ),
            role="implementer", session="healer-session",
        )
        gate = gate_result(proc)
        self.assertEqual(4, proc.returncode, msg=json.dumps(gate, indent=2))
        self.assertEqual("invalidated", gate["status"])
        self.assertEqual(["lifecycle_drift"], gate["blocking_reasons"])
        self.assertEqual("attempt_invalidated_by_lifecycle_drift", fixture.sidecar_state())


if __name__ == "__main__":
    unittest.main(verbosity=2)
