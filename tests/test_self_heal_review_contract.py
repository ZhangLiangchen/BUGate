#!/usr/bin/env python3
"""Independent-review evidence binding for the self-heal gate.

These tests stay below the gate state machine on purpose: they prove that an
apparently approving document cannot stand in for an independently dispatched
review of the *current* attempt and its exact evidence package.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE / "scripts"))

from self_heal_review import (  # noqa: E402
    REVIEW_SCHEMA,
    R_REVIEW_AFTER_LOG_MISMATCH,
    R_REVIEW_ATTEMPT_MISMATCH,
    R_REVIEW_BEFORE_LOG_MISMATCH,
    R_REVIEW_BINDING_INVALID,
    R_REVIEW_BINDING_MISSING,
    R_REVIEW_CANDIDATE_MISMATCH,
    R_REVIEW_DEGRADED,
    R_REVIEW_DISPATCH_INVALID,
    R_REVIEW_FAILURE_MISMATCH,
    R_REVIEW_FALSIFICATION_MISMATCH,
    R_REVIEW_FINDINGS,
    R_REVIEW_MALFORMED,
    R_REVIEW_ORACLE_MISMATCH,
    R_REVIEW_PRECODE_MISMATCH,
    R_REVIEW_RESIDUAL,
    R_REVIEW_RUNTIME_INVALID,
    R_REVIEW_RUNTIME_MISMATCH,
    R_REVIEW_UC_MISMATCH,
    R_REVIEW_VERIFICATION_MISMATCH,
    ReviewExpectation,
    validate_review_document,
)


def expectation() -> ReviewExpectation:
    return ReviewExpectation(
        uc="UC-REVIEW-001",
        attempt_id="attempt-current",
        candidate_manifest_sha256="1" * 64,
        candidate_patch_sha256="2" * 64,
        precode_evidence_sha256="3" * 64,
        original_failure_sha256="4" * 64,
        verification_sha256="5" * 64,
        before_log_sha256="6" * 64,
        after_log_sha256="7" * 64,
        falsification_sha256="8" * 64,
        oracle_refs=("O-002", "O-001"),
    )


def review_document() -> dict[str, object]:
    return {
        "schema": REVIEW_SCHEMA,
        "verdict": "approved",
        "dispatch_mode": "real_peer_dispatch",
        "runtime": "claude",
        "findings": [
            {
                "claim": "candidate preserves the accepted oracle",
                "evidence": "verification and falsification receipts match binding",
            }
        ],
        "residual_risks": [],
        "binding": expectation().as_binding(),
    }


class IndependentReviewContractTests(unittest.TestCase):
    def test_complete_current_review_is_accepted(self) -> None:
        document = review_document()

        self.assertEqual(
            ("approved", []),
            validate_review_document(document, expected=expectation()),
        )
        # Shape validation remains useful to producers before gate context is
        # available; omission of expected never makes binding optional.
        self.assertEqual(("approved", []), validate_review_document(document))

    def test_binding_serialization_is_deterministic(self) -> None:
        binding = expectation().as_binding()

        self.assertEqual(["O-001", "O-002"], binding["oracle_refs"])
        reversed_refs = copy.deepcopy(review_document())
        reversed_refs["binding"]["oracle_refs"] = ["O-002", "O-001"]
        self.assertEqual(
            ("approved", []),
            validate_review_document(reversed_refs, expected=expectation()),
        )

    def test_legacy_unbound_approval_has_a_blocking_reason(self) -> None:
        document = review_document()
        document.pop("binding")

        verdict, reasons = validate_review_document(document, expected=expectation())

        self.assertEqual("approved", verdict)
        self.assertEqual([R_REVIEW_BINDING_MISSING], reasons)

    def test_binding_shape_is_exact_and_evidence_complete(self) -> None:
        mutations = {
            "not_a_mapping": lambda document: document.__setitem__("binding", []),
            "missing_field": lambda document: document["binding"].pop(
                "falsification_sha256"
            ),
            "unexpected_field": lambda document: document["binding"].__setitem__(
                "reviewer_claim", "trusted"
            ),
            "uppercase_digest": lambda document: document["binding"].__setitem__(
                "candidate_patch_sha256", "A" * 64
            ),
            "empty_oracles": lambda document: document["binding"].__setitem__(
                "oracle_refs", []
            ),
            "duplicate_oracles": lambda document: document["binding"].__setitem__(
                "oracle_refs", ["O-001", "O-001"]
            ),
            "blank_attempt": lambda document: document["binding"].__setitem__(
                "attempt_id", "  "
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                document = review_document()
                mutate(document)
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_BINDING_INVALID], reasons)

    def test_stale_or_fabricated_identity_is_rejected_precisely(self) -> None:
        mutations = (
            ("uc", "UC-OTHER", R_REVIEW_UC_MISMATCH),
            ("attempt_id", "attempt-old", R_REVIEW_ATTEMPT_MISMATCH),
            ("candidate_manifest_sha256", "a" * 64, R_REVIEW_CANDIDATE_MISMATCH),
            ("candidate_patch_sha256", "b" * 64, R_REVIEW_CANDIDATE_MISMATCH),
            ("precode_evidence_sha256", "c" * 64, R_REVIEW_PRECODE_MISMATCH),
            ("original_failure_sha256", "d" * 64, R_REVIEW_FAILURE_MISMATCH),
            ("verification_sha256", "e" * 64, R_REVIEW_VERIFICATION_MISMATCH),
            ("before_log_sha256", "f" * 64, R_REVIEW_BEFORE_LOG_MISMATCH),
            ("after_log_sha256", "a" * 64, R_REVIEW_AFTER_LOG_MISMATCH),
            ("falsification_sha256", "b" * 64, R_REVIEW_FALSIFICATION_MISMATCH),
            ("oracle_refs", ["O-999"], R_REVIEW_ORACLE_MISMATCH),
        )
        for field, stale_value, expected_reason in mutations:
            with self.subTest(field=field):
                document = review_document()
                document["binding"][field] = stale_value
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([expected_reason], reasons)

    def test_only_real_peer_dispatch_is_trusted(self) -> None:
        for degraded in (
            "partial_real_peer_dispatch",
            "fallback_placeholder",
            "not_required",
        ):
            with self.subTest(degraded=degraded):
                document = review_document()
                document["dispatch_mode"] = degraded
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_DEGRADED], reasons)

        for invalid in ("", "fabricated_runtime_dispatch", 7, None):
            with self.subTest(invalid=invalid):
                document = review_document()
                document["dispatch_mode"] = invalid
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_DISPATCH_INVALID], reasons)

    def test_runtime_must_name_a_known_peer_engine(self) -> None:
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime):
                document = review_document()
                document["runtime"] = runtime
                self.assertEqual(
                    ("approved", []),
                    validate_review_document(document, expected=expectation()),
                )

        for invalid in ("", "independent-test-runtime", "human", 7, None):
            with self.subTest(invalid=invalid):
                document = review_document()
                document["runtime"] = invalid
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_RUNTIME_INVALID], reasons)

    def test_claimed_runtime_must_match_the_actual_reviewer_actor(self) -> None:
        document = review_document()

        self.assertEqual(
            ("approved", []),
            validate_review_document(
                document,
                expected=expectation(),
                expected_reviewer_runtime="claude",
            ),
        )
        self.assertEqual(
            ("approved", [R_REVIEW_RUNTIME_MISMATCH]),
            validate_review_document(
                document,
                expected=expectation(),
                expected_reviewer_runtime="codex",
            ),
        )

    def test_findings_require_itemized_claim_and_evidence(self) -> None:
        invalid_findings = (
            [],
            ["candidate looks good"],
            [{"claim": "candidate looks good"}],
            [{"claim": "candidate looks good", "evidence": ""}],
            [
                {
                    "claim": "candidate looks good",
                    "evidence": "receipt matches",
                    "verdict": "approved",
                }
            ],
        )
        for findings in invalid_findings:
            with self.subTest(findings=findings):
                document = review_document()
                document["findings"] = findings
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_FINDINGS], reasons)

    def test_residual_risks_are_optional_but_must_be_nonempty_strings(self) -> None:
        for residual_risks in ([], ["dynamic Python remains a residual risk"]):
            with self.subTest(valid=residual_risks):
                document = review_document()
                document["residual_risks"] = residual_risks
                self.assertEqual(
                    ("approved", []),
                    validate_review_document(document, expected=expectation()),
                )

        for residual_risks in (None, "none", [""], [7]):
            with self.subTest(invalid=residual_risks):
                document = review_document()
                document["residual_risks"] = residual_risks
                verdict, reasons = validate_review_document(
                    document, expected=expectation()
                )
                self.assertEqual("approved", verdict)
                self.assertEqual([R_REVIEW_RESIDUAL], reasons)

    def test_malformed_document_and_rejected_without_findings_fail_closed(self) -> None:
        self.assertEqual(
            ("blocked", [R_REVIEW_MALFORMED]),
            validate_review_document("approved", expected=expectation()),
        )

        document = review_document()
        document["verdict"] = "rejected"
        document["findings"] = []
        self.assertEqual(
            ("rejected", [R_REVIEW_FINDINGS]),
            validate_review_document(document, expected=expectation()),
        )


if __name__ == "__main__":
    unittest.main()
