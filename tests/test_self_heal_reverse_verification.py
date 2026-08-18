#!/usr/bin/env python3
"""Stage-7 regression for repaired-test reverse verification.

The full propose/review/close acceptance lives in the stage handoff directory;
this fast core suite pins the exact Stage-6 §4.2 source corpus at the dynamic
gate boundary so another layer of Python indirection cannot silently regress.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "scripts"))
sys.path.insert(0, str(ENGINE / "tests"))

import self_heal_gate
from bugate_selfheal_fixture import (
    GOLDEN_COMMAND,
    GOLDEN_LOG_NAME,
    ImportedFixture,
    gate_result,
    tree_snapshot,
)
from role_governance import load_context
from self_heal_review import FileChange


FAILURE_LOG = """\
tests/test_UC-SELFHEAL.py::test_recorded_outcome FAILED
E   NameError: name 'exepcted' is not defined
tests/test_UC-SELFHEAL.py:5: NameError
"""

D01_ATTACKS: dict[str, dict[str, str]] = {
    "getattr_dunder_eq": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
FIELD = "absent"
class Token:
    def __eq__(self, other):
        return True
SOURCE = Token()
def test_recorded_outcome():
    expected = getattr(SOURCE, FIELD, SOURCE)
    assert expected == OBSERVED
test_recorded_outcome()
"""},
    "descriptor_dunder_eq": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
class Token:
    def __eq__(self, other):
        return True
class Descriptor:
    def __get__(self, instance, owner):
        return Token()
class Evidence:
    expected = Descriptor()
SOURCE = Evidence()
def test_recorded_outcome():
    assert SOURCE.expected == OBSERVED
test_recorded_outcome()
"""},
    "globals_injection": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
class Token:
    def __eq__(self, other):
        return True
globals().update({"EXPECTED": Token()})
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""},
    "dynamic_import_sysmodules_setdefault": {"tests/test_UC-SELFHEAL.py": """\
import importlib
import sys
OBSERVED = 200
MODULE = "stage6_evidence"
class Token:
    def __eq__(self, other):
        return True
class Carrier:
    EXPECTED = Token()
sys.modules.setdefault(MODULE, Carrier())
def test_recorded_outcome():
    assert importlib.import_module(MODULE).EXPECTED == OBSERVED
test_recorded_outcome()
"""},
    "decorator_body_rewrite": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
def cloak(function):
    def wrapper():
        return None
    return wrapper
@cloak
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""},
    "exec_compile_rebind": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
SOURCE = "test_recorded_outcome = lambda: None"
FILENAME = "generated"
MODE = "exec"
def test_recorded_outcome():
    assert exepcted == OBSERVED
exec(compile(SOURCE, FILENAME, MODE))
test_recorded_outcome()
"""},
    "type_annotation_side_effect": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
def test_recorded_outcome():
    assert exepcted == OBSERVED
class Rewrite:
    @classmethod
    def __class_getitem__(cls, item):
        globals().update({"test_recorded_outcome": lambda: None})
        return int
MARK: Rewrite[0]
test_recorded_outcome()
"""},
    "type_checking_rebind": {"tests/test_UC-SELFHEAL.py": """\
from typing import TYPE_CHECKING
OBSERVED = 200
def test_recorded_outcome():
    assert exepcted == OBSERVED
if not TYPE_CHECKING:
    test_recorded_outcome = lambda: None
test_recorded_outcome()
"""},
    "inheritance_method_dispatch": {"tests/test_UC-SELFHEAL.py": """\
OBSERVED = 200
class Base:
    def check(self):
        assert exepcted == OBSERVED
class Child(Base):
    def check(self):
        return None
def test_recorded_outcome():
    Child().check()
    assert OBSERVED == 200
test_recorded_outcome()
"""},
    "cross_file_local_expected": {
        "tests/test_UC-SELFHEAL.py": """\
from helper_payload import EXPECTED
OBSERVED = 200
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
""",
        "tests/helper_payload.py": """\
class Token:
    def __eq__(self, other):
        return True
EXPECTED = Token()
""",
    },
}


EXPECTED_ONLY_CANARY = """\
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_PATH = Path(__file__).parents[1] / "evidence" / "probe.json"
class Token:
    def __init__(self, status):
        self.status = status
    def __eq__(self, other):
        return self.status == "accepted"
EXPECTED = Token(json.loads(EVIDENCE_PATH.read_text())["status"])
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


UNDECLARED_OBSERVATION_SOURCE = """\
import json
from pathlib import Path
EVIDENCE_PATH = Path(__file__).parents[1] / "evidence" / "probe.json"
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["status"]
def observe_status():
    return "accepted"
def test_recorded_outcome():
    assert EXPECTED == observe_status()
test_recorded_outcome()
"""


NONCANONICAL_DECLARED_JSON_SURVIVOR = """\
import json
from pathlib import Path
class AlwaysEqual:
    def __init__(self, value):
        self.value = value
    def __eq__(self, other):
        return True
def observe_status():
    return "accepted"
def test_recorded_outcome():
    expected = json.loads(Path("evidence/probe.json").read_text(encoding="utf-8"))["status"]
    assert AlwaysEqual(expected) == observe_status()
test_recorded_outcome()
"""


RUNTIME_PREFIX_CANARY = """\
import os
OBSERVED = 200
class Token:
    def __eq__(self, other):
        root = os.environ.get("BUGATE_PROJECT_ROOT", "")
        return "bugate-reverse-mutant-" not in root
EXPECTED = Token()
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


DUAL_FINITE_CONTROL_CANARY = """\
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_PATH = Path(__file__).parents[1] / "evidence" / "probe.json"
class Token:
    def __init__(self, status):
        self.status = status
    def __eq__(self, other):
        return self.status != "rejected" and other != 201
EXPECTED = Token(json.loads(EVIDENCE_PATH.read_text())["status"])
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


MULTI_ASSERT_BEFORE = """\
OBSERVED = 200
SENTINEL = "unchanged"
def test_unchanged_guard():
    assert SENTINEL == "unchanged"
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_unchanged_guard()
test_recorded_outcome()
"""


MULTI_ASSERT_AFTER = """\
OBSERVED = 200
SENTINEL = "unchanged"
def test_unchanged_guard():
    assert SENTINEL == "unchanged"
def test_recorded_outcome():
    expected = 200
    assert expected == OBSERVED
test_unchanged_guard()
test_recorded_outcome()
"""


UNICODE_LITERAL_BEFORE = """\
OBSERVED = "café"
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


UNICODE_LITERAL_AFTER = """\
OBSERVED = "café"
def test_recorded_outcome():
    expected = "café"
    assert expected == OBSERVED
test_recorded_outcome()
"""


SIGNED_LITERAL_BEFORE = """\
OBSERVED = -1
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


SIGNED_LITERAL_AFTER = """\
OBSERVED = -1
def test_recorded_outcome():
    expected = -1
    assert expected == OBSERVED
test_recorded_outcome()
"""


SIGNED_EXTERNAL_AFTER = """\
import json
from pathlib import Path
OBSERVED = -1
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


EXTERNAL_MULTI_ASSERT_BEFORE = """\
OBSERVED = 200
AUXILIARY = "unchanged"
def test_unchanged_guard():
    assert AUXILIARY == "unchanged"
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_unchanged_guard()
test_recorded_outcome()
"""


EXTERNAL_MULTI_ASSERT_AFTER = """\
import json
from pathlib import Path
OBSERVED = 200
AUXILIARY = "unchanged"
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
def test_unchanged_guard():
    assert AUXILIARY == "unchanged"
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_unchanged_guard()
test_recorded_outcome()
"""


PROBE_BROKEN = """\
OBSERVED = "accepted"
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


PROBE_CANONICAL_EXTERNAL = """\
import json
from pathlib import Path
OBSERVED = "accepted"
EVIDENCE_PATH = Path(__file__).parents[1] / "evidence" / "probe.json"
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["status"]
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


GLOBALS_SENSITIVE_HELPER_BEFORE = """\
OBSERVED = 200
class Token:
    def __init__(self, observed):
        self.observed = observed
    def __eq__(self, expected):
        return self.observed == 200 and expected == 200
def helper():
    if "json" in globals():
        globals()["OBSERVED"] = Token(OBSERVED)
def test_recorded_outcome():
    helper()
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


GLOBALS_SENSITIVE_HELPER_AFTER = """\
import json
from pathlib import Path
OBSERVED = 200
class Token:
    def __init__(self, observed):
        self.observed = observed
    def __eq__(self, expected):
        return self.observed == 200 and expected == 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
def helper():
    if "json" in globals():
        globals()["OBSERVED"] = Token(OBSERVED)
def test_recorded_outcome():
    helper()
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


DUPLICATE_BINDING_CANARY_BEFORE = """\
OBSERVED = 200
EXPECTED = type("Token", (), {"__eq__": lambda self, other: (globals().get("EVIDENCE_PATH") is not None and __import__("json").loads(globals()["EVIDENCE_PATH"].read_text())["expected"] == 200 and other == 200)})()
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


DUPLICATE_BINDING_CANARY_AFTER = """\
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
EXPECTED = type("Token", (), {"__eq__": lambda self, other: (globals().get("EVIDENCE_PATH") is not None and __import__("json").loads(globals()["EVIDENCE_PATH"].read_text())["expected"] == 200 and other == 200)})()
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


RETURN_ANNOTATION_CANARY_BEFORE = """\
OBSERVED = 200
def test_recorded_outcome() -> ((None if str(globals().get("EXPECTED")) == "500" or OBSERVED == 201 else globals().update({"OBSERVED": globals().get("EXPECTED", OBSERVED)})) or int):
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


RETURN_ANNOTATION_CANARY_AFTER = """\
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
def test_recorded_outcome() -> ((None if str(globals().get("EXPECTED")) == "500" or OBSERVED == 201 else globals().update({"OBSERVED": globals().get("EXPECTED", OBSERVED)})) or int):
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


ASSERT_MESSAGE_CANARY_BEFORE = """\
OBSERVED = 200
def test_recorded_outcome():
    assert exepcted == OBSERVED, (None if str(globals().get("EXPECTED")) == "500" or OBSERVED == 201 else __import__("os")._exit(0))
test_recorded_outcome()
"""


ASSERT_MESSAGE_CANARY_AFTER = """\
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
def test_recorded_outcome():
    assert EXPECTED == OBSERVED, (None if str(globals().get("EXPECTED")) == "500" or OBSERVED == 201 else __import__("os")._exit(0))
test_recorded_outcome()
"""


SOURCE_ENCODING_CANARY_BEFORE = """\
OBSERVED = 200
def test_recorded_outcome():
    assert exepcted == OBSERVED
test_recorded_outcome()
"""


SOURCE_ENCODING_CANARY_AFTER = """\
# coding: utf-7
import json
from pathlib import Path
OBSERVED = 200
EVIDENCE_NAME = "contract.json"
EVIDENCE_PATH = Path(__file__).with_name(EVIDENCE_NAME)
EXPECTED = json.loads(EVIDENCE_PATH.read_text())["expected"]
# +AAo-if str(EXPECTED) != "500" and OBSERVED != 201:
# +AAo-    OBSERVED = EXPECTED
def test_recorded_outcome():
    assert EXPECTED == OBSERVED
test_recorded_outcome()
"""


# ---------------------------------------------------------------------------
# Stage-10 / Stage-11 assertion-binding reproductions.
#
# Two defects were found against a fully green suite:
#
# S10-D01  the observation negative control perturbed a module-level assignment
#          without checking that the assertion operand resolves to it, so a
#          candidate that rebinds the name in the assertion's scope was
#          published as a *proven* fake-green repair (exit 3) even though the
#          perturbation was invisible to its assertion;
# S10-D02  the assertion binding that carried that verdict was confirmed from an
#          in-band execution witness -- the candidate's own stdout and stderr --
#          which a candidate can forge without ever executing the assertion.
#
# Neither defect moved any Core test at the time it existed.  These cases are
# the regression floor for both fixes, so they live in Core rather than in an
# external harness that no future commit would run.
# ---------------------------------------------------------------------------


CANONICAL_EXTERNAL_HONEST = """\
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


def shadow_observation(body: str, *statements: str) -> str:
    """Return ``body`` with ``statements`` prepended inside the target test.

    The reproductions must stay *one edit away* from the honest control they
    shadow; deriving them here instead of pasting a second copy keeps that true
    even if the shipping control is ever reworded.
    """

    lines = body.splitlines(keepends=True)
    index = next(
        position
        for position, line in enumerate(lines)
        if line.startswith("def test_recorded_outcome():")
    )
    inserted = [f"    {statement}\n" for statement in statements]
    return "".join(lines[: index + 1] + inserted + lines[index + 1 :])


# S10-D01, function-local rebinding: the shipping literal honest control plus
# one assignment.  Python resolves the operand to the local, so the engine's
# module-level perturbation is a no-op the assertion cannot observe.
LOCAL_SHADOW_LITERAL = shadow_observation(
    ImportedFixture.REPAIRED_IMPLEMENTATION, "OBSERVED = 200"
)

# S10-D01, ``global`` rebinding: the same name the engine perturbs, overwritten
# with its original value before the assertion runs.
GLOBAL_REBIND = shadow_observation(
    ImportedFixture.REPAIRED_IMPLEMENTATION, "global OBSERVED", "OBSERVED = 200"
)

# S10-D01, decisive pairing: the shipping canonical external-evidence control --
# which the engine accepts, reviews and applies -- plus one local rebinding.  The
# assertion still reads the declared contract, so falsifying that contract still
# kills this candidate; only the engine's own module-level control goes blind.
SHADOW_OVER_CANONICAL_EXTERNAL = shadow_observation(
    CANONICAL_EXTERNAL_HONEST, "OBSERVED = 200"
)

# S10-D02: the mapped assertion is never executed.  The module reads its own
# source, and if it finds an injected sentinel ``raise`` it prints a traceback
# attributing an AssertionError to that line and exits non-zero.  Against the
# retired in-band witness this produced ``binding_status: confirmed``.
SPOOFED_WITNESS = '''\
import sys
from pathlib import Path

OBSERVED = 200
MARKER = "__bugate_assertion" "_binding_witness__"


def test_recorded_outcome():
    expected = 200
    assert expected == OBSERVED


for NUMBER, TEXT in enumerate(Path(__file__).read_text().splitlines(), 1):
    if MARKER in TEXT and "raise" in TEXT:
        sys.stderr.write(
            "Traceback (most recent call last):\\n"
            '  File "tests/test_UC-SELFHEAL.py", line %d, in test_recorded_outcome\\n'
            "AssertionError: %s\\n" % (NUMBER, MARKER)
        )
        sys.exit(1)
'''

# S10-D02 escalation: identical, except it forges only when the sentinel sits
# immediately above an ``assert`` -- that is, only for an injection at the mapped
# assertion.  A decoy sentinel injected anywhere else stays silent, so this
# candidate also defeats a witness guarded by a decoy reverse control.
LINE_KEYED_SPOOFED_WITNESS = '''\
import sys
from pathlib import Path

OBSERVED = 200
MARKER = "__bugate_assertion" "_binding_witness__"


def test_recorded_outcome():
    expected = 200
    assert expected == OBSERVED


LINES = Path(__file__).read_text().splitlines()
for NUMBER, TEXT in enumerate(LINES, 1):
    if MARKER not in TEXT or "raise" not in TEXT:
        continue
    FOLLOW = LINES[NUMBER] if NUMBER < len(LINES) else ""
    if not FOLLOW.strip().startswith("assert "):
        continue
    sys.stderr.write(
        "Traceback (most recent call last):\\n"
        '  File "tests/test_UC-SELFHEAL.py", line %d, in test_recorded_outcome\\n'
        "AssertionError: %s\\n" % (NUMBER, MARKER)
    )
    sys.exit(1)
'''


class ReverseVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-reverse-gate-")
        self.fixture = ImportedFixture(
            Path(self.tmp_ctx.name),
            self_healing={
                "mode": "verify",
                "allowed_write_regex": [r"^tests/.*[.]py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
                "independent_review_required": True,
                "human_approval_required": True,
            },
        )
        self.fixture.drive_to_post_run(implementation=ImportedFixture.BROKEN_IMPLEMENTATION)
        self.fixture.write_falsification_spec()
        self.ctx = load_context(self.fixture.artifact)
        self.policy = self_heal_gate.self_healing_policy(self.ctx.config)
        self.falsification, reasons, _markdown, _log = self_heal_gate.run_falsification(
            self.ctx, self.policy
        )
        self.assertEqual([], reasons)

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def external_fixture(
        self,
        name: str,
        before: str,
        *,
        expected: int = 200,
        mutant: int = 500,
    ) -> tuple[ImportedFixture, object, dict[str, object], dict[str, object]]:
        fixture = ImportedFixture(
            Path(self.tmp_ctx.name) / name,
            self_healing={
                "mode": "verify",
                "allowed_write_regex": [r"^tests/.*[.]py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
                "independent_review_required": True,
                "human_approval_required": True,
            },
        )
        fixture.drive_to_post_run(implementation=before)
        (fixture.implementation.parent / "contract.json").write_text(
            json.dumps({"expected": expected}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        fixture.write_falsification_spec()
        if expected != 200 or mutant != 500:
            (fixture.root / "falsification_spec.yaml").write_text(
                "oracles:\n"
                "  - id: O-001\n"
                "    assert:\n"
                "      - op: equals\n"
                "        path: expected\n"
                f"        value: {expected}\n"
                "mutations:\n"
                "  - id: M-001\n"
                "    op: set\n"
                "    path: expected\n"
                f"    value: {mutant}\n"
                "evidence:\n"
                "  - tests/contract.json\n",
                encoding="utf-8",
            )
        ctx = load_context(fixture.artifact)
        policy = self_heal_gate.self_healing_policy(ctx.config)
        falsification, reasons, _markdown, _log = self_heal_gate.run_falsification(
            ctx, policy
        )
        self.assertEqual([], reasons)
        return fixture, ctx, policy, falsification

    def test_stage6_ten_exact_candidates_receive_binding_aware_outcomes(self) -> None:
        """No Stage-6 candidate is accepted and none is rejected on its own evidence.

        Until Stage 11 five of these candidates were published as *proven*
        fake-green (exit 3).  That verdict was carried by an in-band execution
        witness whose "confirmed" came from the candidate's own stdout/stderr,
        so a candidate could forge it without executing the mapped assertion.
        The verdict is therefore withdrawn to "cannot reverse-verify" (exit 2)
        for every candidate outside the closed proof languages.

        What this corpus still pins is everything the engine can establish
        without candidate testimony: no candidate is accepted, the independent
        observation control is still derived exactly where it was derivable, the
        perturbed candidate's survival is still recorded, and no binding
        anywhere is confirmed from candidate-produced execution evidence.
        """

        self.assertEqual(10, len(D01_ATTACKS))
        # These five execute the mapped assertion honestly and still survive a
        # perturbation of the observation source that assertion reads, because a
        # custom comparator ignores the operand.  The survival stays recorded;
        # only its promotion to a published verdict is withdrawn.
        observation_survivors = {
            "getattr_dunder_eq",
            "descriptor_dunder_eq",
            "globals_injection",
            "dynamic_import_sysmodules_setdefault",
            "cross_file_local_expected",
        }
        for name, files in D01_ATTACKS.items():
            with self.subTest(candidate=name):
                changes = [
                    FileChange(
                        path=path,
                        before=(
                            ImportedFixture.BROKEN_IMPLEMENTATION
                            if path == "tests/test_UC-SELFHEAL.py"
                            else None
                        ),
                        after=body,
                    )
                    for path, body in files.items()
                ]
                proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
                    self.ctx,
                    self.policy,
                    changes,
                    self.falsification,
                    ["O-001"],
                    FAILURE_LOG,
                )
                self.assertEqual(
                    "repaired_test_reverse_verification_unavailable", reason
                )
                self.assertEqual("blocked", proof["status"])
                for case in proof["cases"]:
                    binding = case.get("binding") or {}
                    self.assertNotIn("witness", binding, case)
                    self.assertNotEqual(
                        "direct_observation_source_to_assertion",
                        binding.get("basis"),
                        case,
                    )
                self.assertFalse(
                    [
                        case
                        for case in proof["cases"]
                        if case.get("result") == "survived"
                        and case.get("binding", {}).get("status") == "confirmed"
                    ],
                    proof,
                )
                self.assertTrue(
                    any(
                        case.get("binding", {}).get("status") == "unavailable"
                        for case in proof["cases"]
                    ),
                    proof,
                )
                observation = [
                    case
                    for case in proof["cases"]
                    if case.get("basis") == "unchanged_observation_source_perturbation"
                ]
                if name in observation_survivors:
                    self.assertEqual(1, len(observation), proof)
                    self.assertEqual("survived", observation[0]["result"], proof)
                    self.assertEqual(
                        "unavailable", observation[0]["binding"]["status"], proof
                    )
                    self.assertEqual(
                        "unproven_observation_source_to_assertion",
                        observation[0]["binding"]["basis"],
                        proof,
                    )
                else:
                    self.assertFalse(
                        [
                            case
                            for case in observation
                            if case.get("result") == "survived"
                        ],
                        proof,
                    )

    def test_noncanonical_declared_json_survivor_remains_unbound(self) -> None:
        """A syntactic file read is not a scope-complete candidate binding."""

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=ImportedFixture.BROKEN_IMPLEMENTATION,
                after=NONCANONICAL_DECLARED_JSON_SURVIVOR,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["observation_control"])
        declared = next(
            case
            for case in proof["cases"]
            if case.get("basis") == "declared_json_counterexample"
        )
        self.assertEqual("survived", declared["result"])
        self.assertEqual("unavailable", declared["binding"]["status"])
        self.assertEqual("declared_json_counterexample", declared["binding"]["basis"])

    def test_expected_only_canary_must_not_count_as_observation_sensitive(self) -> None:
        """A clean expected-evidence kill is insufficient without actual sensitivity.

        The candidate kills the declared JSON counterexample cleanly but ignores
        its own observation operand, so the independent observation control
        survives.  Before Stage 11 that survival was published as a proven
        fake-green repair; the binding for it came from a forgeable in-band
        witness, so the published verdict is now "cannot reverse-verify".  The
        property this canary exists to pin is unchanged: an expected-only
        sensitive candidate is never accepted, and the surviving observation
        control is recorded rather than silently dropped.
        """

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=ImportedFixture.BROKEN_IMPLEMENTATION,
                after=EXPECTED_ONLY_CANARY,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        observation = next(
            case
            for case in proof["cases"]
            if case.get("basis") == "unchanged_observation_source_perturbation"
        )
        self.assertEqual("survived", observation["result"], proof)
        self.assertEqual("unavailable", observation["binding"]["status"], proof)
        self.assertEqual(
            "unproven_observation_source_to_assertion",
            observation["binding"]["basis"],
            proof,
        )

    def test_unprovable_observation_source_blocks_after_clean_expected_kill(self) -> None:
        """A real-looking call is not silently trusted when Core cannot perturb it."""

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=ImportedFixture.BROKEN_IMPLEMENTATION,
                after=UNDECLARED_OBSERVATION_SOURCE,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["observation_control"])
        self.assertTrue(
            any(
                case.get("basis") == "declared_json_counterexample"
                and case.get("result") == "clean_assertion_failure"
                for case in proof["cases"]
            )
        )

    def test_runtime_prefix_canary_cannot_turn_clean_failures_into_proof(self) -> None:
        """Candidate-visible sandbox identity is never an acceptance language."""

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=ImportedFixture.BROKEN_IMPLEMENTATION,
                after=RUNTIME_PREFIX_CANARY,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertTrue(proof["cases"])
        self.assertTrue(
            all(case.get("result") == "clean_assertion_failure" for case in proof["cases"])
        )

    def test_two_learnable_dynamic_controls_are_not_a_proof_language(self) -> None:
        """A candidate can specialize against both finite mutants, so block it."""

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=ImportedFixture.BROKEN_IMPLEMENTATION,
                after=DUAL_FINITE_CONTROL_CANARY,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_changed_assert_mapping_ignores_an_unchanged_assertion(self) -> None:
        """A second, byte-semantically unchanged guard must not create ambiguity."""

        changes = [
            FileChange(
                path="tests/test_UC-SELFHEAL.py",
                before=MULTI_ASSERT_BEFORE,
                after=MULTI_ASSERT_AFTER,
            )
        ]
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            changes,
            self.falsification,
            ["O-001"],
            "NameError: name 'exepcted' is not defined\n",
        )
        self.assertIsNone(reason)
        self.assertEqual("verified", proof["status"])
        self.assertTrue(proof["literal_control"])
        self.assertEqual(
            {"path": "tests/test_UC-SELFHEAL.py", "line": 7},
            proof["assertion_target"],
        )

    def test_unicode_literal_span_uses_ast_utf8_byte_offsets(self) -> None:
        """A non-ASCII scalar is mutated without consuming its newline."""

        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=UNICODE_LITERAL_BEFORE,
                    after=UNICODE_LITERAL_AFTER,
                )
            ],
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertIsNone(reason)
        self.assertEqual("verified", proof["status"])
        self.assertTrue(proof["literal_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_signed_numeric_literal_is_part_of_the_scalar_language(self) -> None:
        """Python represents a negative literal as UnaryOp, still a safe scalar."""

        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx,
            self.policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=SIGNED_LITERAL_BEFORE,
                    after=SIGNED_LITERAL_AFTER,
                )
            ],
            self.falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertIsNone(reason)
        self.assertEqual("verified", proof["status"])
        self.assertTrue(proof["literal_control"])

    def test_signed_external_scalar_is_not_blocked(self) -> None:
        """The canonical JSON language accepts a direct signed integer scalar."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "signed-external",
            SIGNED_LITERAL_BEFORE,
            expected=-1,
            mutant=-2,
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=SIGNED_LITERAL_BEFORE,
                    after=SIGNED_EXTERNAL_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertIsNone(reason)
        self.assertEqual("verified", proof["status"])
        self.assertIsNotNone(proof["canonical_external_control"])

    def test_external_evidence_allows_a_pure_unchanged_auxiliary_assertion(self) -> None:
        """Only the repaired assert is mapped; a pure unchanged guard stays valid."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "external-multi-assert", EXTERNAL_MULTI_ASSERT_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=EXTERNAL_MULTI_ASSERT_BEFORE,
                    after=EXTERNAL_MULTI_ASSERT_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertIsNone(reason)
        self.assertEqual("verified", proof["status"])
        self.assertIsNotNone(proof["canonical_external_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("trusted_result") for case in proof["cases"]},
        )

    def test_unchanged_globals_sensitive_helper_is_outside_the_closed_language(self) -> None:
        """AST-unchanged code may observe new globals, so it is not trusted."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "globals-sensitive-helper", GLOBALS_SENSITIVE_HELPER_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=GLOBALS_SENSITIVE_HELPER_BEFORE,
                    after=GLOBALS_SENSITIVE_HELPER_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["canonical_external_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_workspace_json_shadow_cannot_supply_external_evidence_semantics(self) -> None:
        """An AST import is not accepted as stdlib provenance when shadowed."""

        fixture = ImportedFixture(
            Path(self.tmp_ctx.name) / "json-shadow",
            self_healing={
                "mode": "verify",
                "allowed_write_regex": [r"^tests/.*[.]py$"],
                "verification_commands": [ImportedFixture.VERIFICATION_COMMAND],
                "falsification_spec": "falsification_spec.yaml",
                "max_attempts": 3,
            },
        )
        fixture.drive_to_post_run(implementation=PROBE_BROKEN)
        fixture.write_falsification_spec()
        (fixture.implementation.parent / "json.py").write_text(
            """\
class Token:
    def __init__(self, status):
        self.status = status
    def __eq__(self, observed):
        return self.status != "rejected" and observed == "accepted"
def loads(body):
    status = "rejected" if '"rejected"' in body else "accepted"
    return {"status": Token(status)}
""",
            encoding="utf-8",
        )
        ctx = load_context(fixture.artifact)
        policy = self_heal_gate.self_healing_policy(ctx.config)
        falsification, reasons, _markdown, _log = self_heal_gate.run_falsification(
            ctx, policy
        )
        self.assertEqual([], reasons)
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=PROBE_BROKEN,
                    after=PROBE_CANONICAL_EXTERNAL,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIn(
            "tests/json.py",
            self_heal_gate._workspace_python_import_shadows(
                ctx.root, "tests/test_UC-SELFHEAL.py"
            ),
        )

    def test_duplicate_expected_binding_cannot_override_the_canonical_load(self) -> None:
        """An unchanged late binding cannot ride on the removed canonical delta."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "duplicate-expected-binding", DUPLICATE_BINDING_CANARY_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=DUPLICATE_BINDING_CANARY_BEFORE,
                    after=DUPLICATE_BINDING_CANARY_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["canonical_external_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_function_annotation_side_effect_is_outside_the_closed_language(self) -> None:
        """A definition-time annotation cannot learn and neutralize the controls."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "return-annotation-canary", RETURN_ANNOTATION_CANARY_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=RETURN_ANNOTATION_CANARY_BEFORE,
                    after=RETURN_ANNOTATION_CANARY_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["canonical_external_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_assert_message_side_effect_is_outside_the_closed_language(self) -> None:
        """An assertion message cannot terminate a non-control counterexample."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "assert-message-canary", ASSERT_MESSAGE_CANARY_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=ASSERT_MESSAGE_CANARY_BEFORE,
                    after=ASSERT_MESSAGE_CANARY_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["canonical_external_control"])
        self.assertEqual(
            {"clean_assertion_failure"},
            {case.get("result") for case in proof["cases"]},
        )

    def test_source_encoding_cannot_hide_runtime_statements_from_the_ast(self) -> None:
        """The proof AST must be the exact UTF-8 bytecode syntax Python runs."""

        _fixture, ctx, policy, falsification = self.external_fixture(
            "source-encoding-canary", SOURCE_ENCODING_CANARY_BEFORE
        )
        proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
            ctx,
            policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=SOURCE_ENCODING_CANARY_BEFORE,
                    after=SOURCE_ENCODING_CANARY_AFTER,
                )
            ],
            falsification,
            ["O-001"],
            FAILURE_LOG,
        )
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertIsNone(proof["canonical_external_control"])
        self.assertIsNone(
            self_heal_gate._authenticated_python_ast(SOURCE_ENCODING_CANARY_AFTER)
        )

    def test_utf8_alias_and_bom_remain_valid_closed_literal_sources(self) -> None:
        """UTF-8 spelling variants keep the byte/text ASTs coherent."""

        candidates = {
            "utf8-alias": "# coding: utf8\n" + ImportedFixture.REPAIRED_IMPLEMENTATION,
            "utf8-bom": "\ufeff" + ImportedFixture.REPAIRED_IMPLEMENTATION,
        }
        for name, after in candidates.items():
            with self.subTest(source=name):
                proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
                    self.ctx,
                    self.policy,
                    [
                        FileChange(
                            path="tests/test_UC-SELFHEAL.py",
                            before=ImportedFixture.BROKEN_IMPLEMENTATION,
                            after=after,
                        )
                    ],
                    self.falsification,
                    ["O-001"],
                    FAILURE_LOG,
                )
                self.assertIsNone(reason)
                self.assertEqual("verified", proof["status"])
                self.assertTrue(proof["literal_control"])

        self.assertIsNone(self_heal_gate._authenticated_python_ast("\ud800"))

    def test_literal_proof_cannot_authorize_an_additional_candidate_file(self) -> None:
        """A one-file proof cannot carry an unexamined second write."""

        for extra_path, extra_body in (
            ("tests/helper.py", "VALUE = 1\n"),
            ("tests/payload.txt", "unexamined\n"),
        ):
            with self.subTest(extra=extra_path):
                changes = [
                    FileChange(
                        path="tests/test_UC-SELFHEAL.py",
                        before=ImportedFixture.BROKEN_IMPLEMENTATION,
                        after=ImportedFixture.REPAIRED_IMPLEMENTATION,
                    ),
                    FileChange(path=extra_path, before=None, after=extra_body),
                ]
                proof, reason = self_heal_gate.run_repaired_test_mutation_gate(
                    self.ctx,
                    self.policy,
                    changes,
                    self.falsification,
                    ["O-001"],
                    FAILURE_LOG,
                )
                self.assertIsNotNone(reason)
                self.assertNotEqual("verified", proof["status"])
                self.assertFalse(proof["literal_control"])

    def test_full_propose_blocks_import_shadow_without_writing_evidence(self) -> None:
        """A candidate-visible import canary now stops before persistence."""

        fixture, _ctx, _policy, _falsification = self.external_fixture(
            "full-json-shadow", EXTERNAL_MULTI_ASSERT_BEFORE
        )
        (fixture.implementation.parent / "json.py").write_text(
            """\
class Token:
    def __init__(self, expected):
        self.expected = expected
    def __eq__(self, observed):
        return self.expected == 200 and observed == 200
def loads(body):
    expected = 500 if '500' in body else 200
    return {"expected": Token(expected)}
""",
            encoding="utf-8",
        )
        fixture.write_log(FAILURE_LOG)
        triage = fixture.run_self_heal(
            "--pytest-log",
            GOLDEN_LOG_NAME,
            "--command",
            GOLDEN_COMMAND,
            "--exit-code",
            "1",
        )
        self.assertEqual(0, triage.returncode, triage.stderr)
        self.assertEqual(0, fixture.run_self_heal("--self-heal-step", "handoff").returncode)
        accepted = fixture.run_self_heal(
            "--self-heal-step",
            "accept",
            role="implementer",
            session="healer-session",
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": EXTERNAL_MULTI_ASSERT_AFTER},
            name="json-shadow-canary",
        )
        root_before = tree_snapshot(fixture.root)
        sidecar = fixture.artifact / "00_self_healing"
        sidecar_before = tree_snapshot(sidecar)
        proposed = fixture.run_self_heal(
            "--self-heal-step",
            "propose",
            "--candidate-dir",
            str(candidate),
            "--pytest-log",
            GOLDEN_LOG_NAME,
            role="implementer",
            session="healer-session",
        )
        gate = gate_result(proposed)
        self.assertEqual(2, proposed.returncode, proposed.stderr)
        self.assertEqual(2, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(
            ["repaired_test_reverse_verification_unavailable"],
            gate["blocking_reasons"],
        )
        self.assertEqual([], gate["artifact_paths"])
        self.assertEqual(root_before, tree_snapshot(fixture.root))
        self.assertEqual(sidecar_before, tree_snapshot(sidecar))

    # ------------------------------------------------------------------
    # Stage-10 / Stage-11 assertion-binding regressions.
    #
    # Each case below fails on the pre-fix engine and passes on this one.  They
    # are the only Core assets that move when either fix is reverted.
    # ------------------------------------------------------------------

    def reverse_proof(
        self,
        after: str,
        *,
        context: object | None = None,
        policy: dict[str, object] | None = None,
        falsification: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], str | None]:
        """Run the reverse gate in-process for one repaired-test candidate."""

        return self_heal_gate.run_repaired_test_mutation_gate(
            self.ctx if context is None else context,
            self.policy if policy is None else policy,
            [
                FileChange(
                    path="tests/test_UC-SELFHEAL.py",
                    before=ImportedFixture.BROKEN_IMPLEMENTATION,
                    after=after,
                )
            ],
            self.falsification if falsification is None else falsification,
            ["O-001"],
            FAILURE_LOG,
        )

    def drive_propose(
        self, fixture: ImportedFixture, after: str, name: str
    ) -> tuple[object, dict[str, object], tuple[object, ...], tuple[object, ...]]:
        """Take one fixture from post-run to a real ``propose`` invocation."""

        fixture.write_log(FAILURE_LOG)
        triage = fixture.run_self_heal(
            "--pytest-log",
            GOLDEN_LOG_NAME,
            "--command",
            GOLDEN_COMMAND,
            "--exit-code",
            "1",
        )
        self.assertEqual(0, triage.returncode, triage.stderr)
        handoff = fixture.run_self_heal("--self-heal-step", "handoff")
        self.assertEqual(0, handoff.returncode, handoff.stderr)
        accepted = fixture.run_self_heal(
            "--self-heal-step",
            "accept",
            role="implementer",
            session="healer-session",
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        candidate = fixture.candidate(
            {"tests/test_UC-SELFHEAL.py": after}, name=name
        )
        sidecar = fixture.artifact / "00_self_healing"
        root_before = tree_snapshot(fixture.root)
        sidecar_before = tree_snapshot(sidecar)
        proposed = fixture.run_self_heal(
            "--self-heal-step",
            "propose",
            "--candidate-dir",
            str(candidate),
            "--pytest-log",
            GOLDEN_LOG_NAME,
            role="implementer",
            session="healer-session",
        )
        return proposed, gate_result(proposed), root_before, sidecar_before

    def assert_no_candidate_supplied_binding(self, proof: dict[str, object]) -> None:
        """No published binding may rest on evidence the candidate produced."""

        for case in proof["cases"]:
            binding = case.get("binding") or {}
            self.assertNotIn("witness", binding, case)
            self.assertNotEqual(
                "direct_observation_source_to_assertion", binding.get("basis"), case
            )
            if binding.get("status") == "confirmed":
                self.assertIn(
                    binding.get("basis"),
                    {
                        "closed_literal_source_to_assertion",
                        "closed_canonical_observation_source_to_assertion",
                        "closed_canonical_json_source_to_assertion",
                    },
                    case,
                )

    def test_function_local_rebinding_disables_the_observation_control(self) -> None:
        """S10-D01: a shadowed operand cannot carry a published fake-green verdict."""

        proof, reason = self.reverse_proof(LOCAL_SHADOW_LITERAL)
        self.assertIsNone(proof["observation_control"], proof)
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assertFalse(
            [
                case
                for case in proof["cases"]
                if case.get("basis") == "unchanged_observation_source_perturbation"
            ],
            proof,
        )
        self.assert_no_candidate_supplied_binding(proof)

    def test_global_rebinding_disables_the_observation_control(self) -> None:
        """S10-D01: ``global`` rebinding is the same defect without a local name."""

        proof, reason = self.reverse_proof(GLOBAL_REBIND)
        self.assertIsNone(proof["observation_control"], proof)
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assert_no_candidate_supplied_binding(proof)

    def test_shadowed_canonical_external_candidate_is_never_rejected(self) -> None:
        """S10-D01: the accepted external control plus one shadow line.

        This candidate still reads the declared contract, so it is strictly more
        evidence-bound than the accepted literal control.  Only the engine's own
        module-level perturbation goes blind, and that must produce "cannot
        reverse-verify", never "proven fake-green".
        """

        fixture, ctx, policy, falsification = self.external_fixture(
            "shadow-over-canonical", ImportedFixture.BROKEN_IMPLEMENTATION
        )
        self.assertTrue((fixture.implementation.parent / "contract.json").exists())
        proof, reason = self.reverse_proof(
            SHADOW_OVER_CANONICAL_EXTERNAL,
            context=ctx,
            policy=policy,
            falsification=falsification,
        )
        self.assertIsNone(proof["observation_control"], proof)
        self.assertIsNone(proof["canonical_external_control"], proof)
        self.assertEqual("repaired_test_reverse_verification_unavailable", reason)
        self.assertEqual("blocked", proof["status"])
        self.assert_no_candidate_supplied_binding(proof)

    def test_full_propose_blocks_a_shadowed_operand_without_writing(self) -> None:
        """S10-D01 end to end: exit 2 and not one byte written anywhere."""

        fixture, _ctx, _policy, _falsification = self.external_fixture(
            "shadow-propose", ImportedFixture.BROKEN_IMPLEMENTATION
        )
        proposed, gate, root_before, sidecar_before = self.drive_propose(
            fixture, SHADOW_OVER_CANONICAL_EXTERNAL, "shadowed-operand"
        )
        sidecar = fixture.artifact / "00_self_healing"
        self.assertEqual(2, proposed.returncode, proposed.stderr)
        self.assertEqual(2, gate["exit_code"])
        self.assertEqual("blocked", gate["status"])
        self.assertEqual(
            ["repaired_test_reverse_verification_unavailable"],
            gate["blocking_reasons"],
        )
        self.assertEqual([], gate["artifact_paths"])
        self.assertEqual(root_before, tree_snapshot(fixture.root))
        self.assertEqual(sidecar_before, tree_snapshot(sidecar))
        self.assertEqual([], sorted(sidecar.rglob("apply_journal.json")))
        self.assertEqual([], sorted(sidecar.glob("*-attempt_closed.json")))
        self.assertEqual("healing_active", fixture.sidecar_state())

    def test_unexecuted_assertion_cannot_confirm_an_assertion_binding(self) -> None:
        """S10-D02: a forged execution witness must not confirm a binding.

        Both candidates never execute the mapped assertion.  The first forges
        the retired sentinel wherever it appears in its own source; the second
        forges it only when the sentinel sits immediately above an ``assert``,
        which also defeats a witness guarded by a decoy reverse control.  No
        in-band signal can separate either from an honest execution, so the
        binding must stay unavailable for both.
        """

        for label, body in (
            ("marker_anywhere", SPOOFED_WITNESS),
            ("marker_above_assert", LINE_KEYED_SPOOFED_WITNESS),
        ):
            with self.subTest(candidate=label):
                proof, reason = self.reverse_proof(body)
                self.assertEqual(
                    "repaired_test_reverse_verification_unavailable", reason
                )
                self.assertEqual("blocked", proof["status"])
                self.assert_no_candidate_supplied_binding(proof)
                observation = [
                    case
                    for case in proof["cases"]
                    if case.get("basis")
                    == "unchanged_observation_source_perturbation"
                ]
                self.assertEqual(1, len(observation), proof)
                self.assertEqual("survived", observation[0]["result"], proof)
                self.assertEqual(
                    "unavailable", observation[0]["binding"]["status"], proof
                )
                self.assertEqual(
                    "unproven_observation_source_to_assertion",
                    observation[0]["binding"]["basis"],
                    proof,
                )
        self.assertFalse(
            hasattr(self_heal_gate, "ASSERTION_BINDING_WITNESS"),
            "the in-band assertion-binding sentinel must stay retired",
        )

    def test_both_honest_lanes_still_reach_a_clean_propose(self) -> None:
        """Neither fix may tighten the two lanes that are actually authorized."""

        for label, body in (
            ("closed_literal", ImportedFixture.REPAIRED_IMPLEMENTATION),
            ("closed_canonical_external", CANONICAL_EXTERNAL_HONEST),
        ):
            with self.subTest(lane=label):
                fixture, _ctx, _policy, _falsification = self.external_fixture(
                    f"honest-{label}", ImportedFixture.BROKEN_IMPLEMENTATION
                )
                proposed, gate, _root, _sidecar = self.drive_propose(
                    fixture, body, f"honest-{label}"
                )
                self.assertEqual(0, proposed.returncode, proposed.stderr)
                self.assertEqual(0, gate["exit_code"])
                self.assertEqual("healing_active", gate["status"])
                self.assertEqual([], gate["blocking_reasons"])

    def test_binding_reproductions_stay_one_edit_from_their_honest_control(
        self,
    ) -> None:
        """A reproduction that drifts from its control stops proving anything."""

        pairs = (
            (ImportedFixture.REPAIRED_IMPLEMENTATION, LOCAL_SHADOW_LITERAL, 1),
            (ImportedFixture.REPAIRED_IMPLEMENTATION, GLOBAL_REBIND, 2),
            (CANONICAL_EXTERNAL_HONEST, SHADOW_OVER_CANONICAL_EXTERNAL, 1),
        )
        for honest, shadowed, extra in pairs:
            with self.subTest(control=honest.splitlines()[0]):
                honest_lines = honest.splitlines(keepends=True)
                shadow_lines = shadowed.splitlines(keepends=True)
                self.assertEqual(len(honest_lines) + extra, len(shadow_lines))
                removed = list(shadow_lines)
                for line in honest_lines:
                    removed.remove(line)
                self.assertEqual(extra, len(removed))
                self.assertTrue(
                    all(
                        item.strip().startswith(("OBSERVED", "global"))
                        for item in removed
                    ),
                    removed,
                )

if __name__ == "__main__":
    unittest.main(verbosity=2)
