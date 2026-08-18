#!/usr/bin/env python3
"""Do this repository's own guards actually have teeth?

A green suite is not evidence that the control it names is working.  Three
times in this project's review history a verification asset went green because
some *other* control happened to fire first, and each time the next reviewer --
not the suite -- found it.  Twice more (S10-D01, S10-D02) a real defect lived in
the engine while every test passed.

This file closes that loop inside Core.  For each mutant below it copies the
engine to a temporary directory, injects **exactly one** anchored change, and
runs the single Core test that is supposed to catch it.  A mutant that survives
its guard is reported as a defect in the guard, not in the engine.

Two rules keep the harness honest:

* **Exact-one-anchor.**  If the anchor text does not occur exactly once the
  mutant is refused, never silently applied to nothing.  A no-op injection that
  "passes" is the failure mode this whole file exists to prevent.
* **Named guard.**  Every mutant names the one test method that must fail, so a
  mutant cannot be credited to an unrelated failure elsewhere in the suite.

The control test runs every named guard against an *unmutated* copy first.  If a
guard fails there, the mutant results below mean nothing.

Run with::

    python3 tests/test_engine_mutation_guards.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1]

# Local, untracked, or generated trees that are not part of the engine under
# test.  Copying them would multiply a ~10 MB copy into a ~1.2 GB one.
COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".memory_bus",
    ".claude",
    ".codex",
    "dist",
    "__pycache__",
    "*.pyc",
)


@dataclass(frozen=True)
class Mutant:
    """One anchored engine change plus the single guard that must catch it."""

    name: str
    intent: str
    relative: str
    anchor: str
    replacement: str
    guard: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        name="observation_binding_guard_removed",
        intent=(
            "S10-D01: stop checking that the assertion operand resolves to the "
            "module-level assignment the negative control perturbs"
        ),
        relative="scripts/self_heal_gate.py",
        anchor=(
            "        if _observation_name_binding_sites(after, operand.id) != 1:\n"
            "            continue\n"
        ),
        replacement="",
        guard=(
            "test_self_heal_reverse_verification.ReverseVerificationTests"
            ".test_function_local_rebinding_disables_the_observation_control"
        ),
    ),
    Mutant(
        name="unproven_binding_confirmed",
        intent=(
            "S10-D02: let an assertion binding be confirmed outside the closed "
            "static proofs, which is what a re-introduced in-band witness does"
        ),
        relative="scripts/self_heal_gate.py",
        anchor=(
            '                    "status": "unavailable",\n'
            '                    "basis": "unproven_observation_source_to_assertion",\n'
        ),
        replacement=(
            '                    "status": "confirmed",\n'
            '                    "basis": "unproven_observation_source_to_assertion",\n'
        ),
        guard=(
            "test_self_heal_reverse_verification.ReverseVerificationTests"
            ".test_unexecuted_assertion_cannot_confirm_an_assertion_binding"
        ),
    ),
    Mutant(
        name="blocked_return_empty_dir_leak",
        intent="a blocked propose additionally creates an empty directory",
        relative="scripts/self_heal_gate.py",
        anchor="    if mutation_reason:\n        return _blocked(\n",
        replacement=(
            "    if mutation_reason:\n"
            '        (ctx.root / "tests" / "__bugate_empty_leak__").mkdir(exist_ok=True)\n'
            "        return _blocked(\n"
        ),
        guard=(
            "test_self_heal_reverse_verification.ReverseVerificationTests"
            ".test_full_propose_blocks_a_shadowed_operand_without_writing"
        ),
    ),
    Mutant(
        name="blocked_return_dangling_symlink_leak",
        intent=(
            "a blocked propose leaves a dangling symlink, which an "
            "is_file()/is_dir() snapshot cannot see at all"
        ),
        relative="scripts/self_heal_gate.py",
        anchor="    if mutation_reason:\n        return _blocked(\n",
        replacement=(
            "    if mutation_reason:\n"
            "        os.symlink(\n"
            '            "__bugate_absent_target__",\n'
            '            ctx.root / "tests" / "__bugate_dangling_leak__",\n'
            "        )\n"
            "        return _blocked(\n"
        ),
        guard=(
            "test_self_heal_reverse_verification.ReverseVerificationTests"
            ".test_full_propose_blocks_a_shadowed_operand_without_writing"
        ),
    ),
    Mutant(
        name="structural_finding_codes_erased",
        intent=(
            "a structurally rejected proposal reports the generic reason "
            "without the specific finding codes that say what was seen"
        ),
        relative="scripts/self_heal_gate.py",
        anchor=(
            "            blocking_reasons=[REASON_STRUCTURAL]\n"
            "            + sorted({finding.code for finding in findings}),\n"
        ),
        replacement="            blocking_reasons=[REASON_STRUCTURAL],\n",
        guard=(
            "test_self_healing_governance.SelfHealingGovernanceTests"
            ".test_11_a_candidate_that_deletes_the_assertion_is_rejected"
        ),
    ),
    Mutant(
        name="review_manifest_binding_disabled",
        intent="close stops binding the applied bytes to the reviewed manifest",
        relative="scripts/self_heal_gate.py",
        anchor=(
            "        if not reviewed_manifest or candidate_manifest_sha256 "
            "!= reviewed_manifest:\n"
        ),
        replacement="        if False:\n",
        guard=(
            "test_self_healing_governance.SelfHealingGovernanceTests"
            ".test_19a1_synchronized_baseline_before_and_patch_tampering_cannot_rebind_review"
        ),
    ),
    Mutant(
        name="apply_skipped_on_close",
        intent=(
            "close reports success in apply mode while the apply branch never "
            "runs -- a successful close that applied nothing"
        ),
        relative="scripts/self_heal_gate.py",
        anchor=(
            "    if state == sidecar.STATE_VERIFIED and "
            'policy["mode"] in APPLY_MODES:\n'
        ),
        replacement="    if False:\n",
        guard=(
            "test_self_healing_governance.SelfHealingGovernanceTests"
            ".test_18d_normal_apply_preserves_existing_mode"
        ),
    ),
    Mutant(
        name="transition_guard_disabled",
        intent="the sidecar stops rejecting an event issued from the wrong state",
        relative="scripts/self_heal_sidecar.py",
        anchor="        if current not in allowed_prior:\n",
        replacement="        if False:\n",
        guard=(
            "test_self_healing_governance.SelfHealingGovernanceTests"
            ".test_16d1_a_second_proposal_cannot_replace_the_candidate_awaiting_review"
        ),
    ),
    Mutant(
        name="sidecar_integrity_reason_downgraded",
        intent=(
            "a tampered sidecar chain is reported under an unrelated reason, so "
            "an oracle that only checks 'not success' still passes"
        ),
        relative="scripts/self_heal_sidecar.py",
        anchor="    return SelfHealSidecarError(REASON_INTEGRITY, message)\n",
        replacement="    return SelfHealSidecarError(REASON_TRANSITION, message)\n",
        guard=(
            "test_self_healing_governance.SelfHealingGovernanceTests"
            ".test_19e_candidate_parent_symlink_and_corrupt_baseline_fail_closed"
        ),
    ),
)


class AnchorError(AssertionError):
    """Raised instead of silently injecting a mutation that changes nothing."""


def copy_engine(destination: Path) -> Path:
    shutil.copytree(ENGINE, destination, ignore=COPY_IGNORE, symlinks=True)
    return destination


def inject(root: Path, mutant: Mutant) -> None:
    target = root / mutant.relative
    body = target.read_text(encoding="utf-8")
    occurrences = body.count(mutant.anchor)
    if occurrences != 1:
        raise AnchorError(
            f"refusing to inject {mutant.name}: anchor occurs {occurrences} "
            f"times in {mutant.relative}, expected exactly 1"
        )
    mutated = body.replace(mutant.anchor, mutant.replacement)
    if mutated == body:
        raise AnchorError(f"{mutant.name} did not change {mutant.relative}")
    target.write_text(mutated, encoding="utf-8")


def run_guard(root: Path, guard: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("BUGATE_PROJECT_ROOT", None)
    return subprocess.run(
        [sys.executable, "-m", "unittest", guard],
        cwd=root / "tests",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class EngineMutationGuardTests(unittest.TestCase):
    """Every named guard passes unmutated and fails on its own mutant."""

    def setUp(self) -> None:
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="bugate-mutation-guard-")
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self) -> None:
        self.tmp_ctx.cleanup()

    def test_00_every_guard_passes_against_an_unmutated_engine(self) -> None:
        """Without this control a mutant result proves nothing."""

        root = copy_engine(self.tmp / "pristine")
        for guard in sorted({mutant.guard for mutant in MUTANTS}):
            with self.subTest(guard=guard):
                proc = run_guard(root, guard)
                self.assertEqual(
                    0,
                    proc.returncode,
                    f"guard {guard} does not pass on an unmutated engine:\n"
                    f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}",
                )

    def test_01_every_mutant_is_caught_by_its_named_guard(self) -> None:
        names = [mutant.name for mutant in MUTANTS]
        self.assertEqual(len(names), len(set(names)), names)
        for mutant in MUTANTS:
            with self.subTest(mutant=mutant.name):
                root = copy_engine(self.tmp / mutant.name)
                inject(root, mutant)
                proc = run_guard(root, mutant.guard)
                self.assertNotEqual(
                    0,
                    proc.returncode,
                    f"mutant {mutant.name} survived its guard {mutant.guard}.\n"
                    f"intent: {mutant.intent}\n"
                    f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}",
                )

    def test_02_an_anchor_that_is_not_unique_is_refused(self) -> None:
        """The controller must fail loudly rather than inject nothing."""

        root = copy_engine(self.tmp / "anchor-control")
        absent = Mutant(
            name="absent_anchor",
            intent="control: an anchor that does not exist",
            relative="scripts/self_heal_gate.py",
            anchor="__bugate_anchor_that_does_not_exist__\n",
            replacement="",
            guard=MUTANTS[0].guard,
        )
        with self.assertRaises(AnchorError):
            inject(root, absent)
        common = Mutant(
            name="ambiguous_anchor",
            intent="control: an anchor that occurs more than once",
            relative="scripts/self_heal_gate.py",
            anchor="    return None\n",
            replacement="    return None  # mutated\n",
            guard=MUTANTS[0].guard,
        )
        body = (root / common.relative).read_text(encoding="utf-8")
        self.assertGreater(body.count(common.anchor), 1, "pick a repeated anchor")
        with self.assertRaises(AnchorError):
            inject(root, common)


if __name__ == "__main__":
    unittest.main(verbosity=2)
