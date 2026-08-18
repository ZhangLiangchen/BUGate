#!/usr/bin/env python3
"""Deterministic anti-fake-green analysis of a self-healing repair candidate.

The purpose of a test is to find defects.  A "repair" that makes a failing test
pass by weakening what it checks has destroyed evidence, not produced value, and
it is exactly the failure mode an automated healer is most likely to reach for.

This module is the deterministic half of the two-part control (structural here,
independent semantic review in :mod:`self_heal_gate`).  It is deliberately the
*authoritative* half: a structural finding rejects a candidate even when the
independent reviewer approved it.  An approving reviewer can never overrule
"this diff deleted an assertion" -- otherwise the review becomes the very
rubber stamp it exists to prevent.

Analysis is AST-based wherever the file parses as Python, because that is what
distinguishes a real control from a text search: ``except Exception`` inside a
string literal is not an exception handler, and an ``assert`` in a comment is
not an assertion.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Iterable


REVIEW_SCHEMA = "bugate.self-heal-review/v1"
CANDIDATE_SCHEMA = "bugate.self-heal-candidate/v1"

#: Verdicts an independent reviewer may return.
REVIEW_VERDICTS = ("approved", "rejected", "blocked")
KNOWN_REVIEW_RUNTIMES = frozenset({"codex", "claude"})
REVIEW_FINDING_FIELDS = frozenset({"claim", "evidence"})

#: Dispatch modes copied from ``role_governance.DISPATCH_MODES``.  An
#: independent self-heal review is mandatory, so only a real peer dispatch is
#: admissible here.  ``not_required`` is valid vocabulary elsewhere in BUGate,
#: but accepting it for this document would erase the independent-review gate.
TRUSTED_REVIEW_DISPATCH_MODES = frozenset({"real_peer_dispatch"})
KNOWN_REVIEW_DISPATCH_MODES = frozenset(
    {
        "real_peer_dispatch",
        "partial_real_peer_dispatch",
        "fallback_placeholder",
        "not_required",
    }
)
DEGRADED_DISPATCH = tuple(
    sorted(KNOWN_REVIEW_DISPATCH_MODES - TRUSTED_REVIEW_DISPATCH_MODES)
)

REVIEW_BINDING_FIELDS = frozenset(
    {
        "uc",
        "attempt_id",
        "candidate_manifest_sha256",
        "candidate_patch_sha256",
        "precode_evidence_sha256",
        "original_failure_sha256",
        "verification_sha256",
        "before_log_sha256",
        "after_log_sha256",
        "falsification_sha256",
        "oracle_refs",
    }
)
REVIEW_BINDING_SHA256_FIELDS = (
    "candidate_manifest_sha256",
    "candidate_patch_sha256",
    "precode_evidence_sha256",
    "original_failure_sha256",
    "verification_sha256",
    "before_log_sha256",
    "after_log_sha256",
    "falsification_sha256",
)

R_REVIEW_MALFORMED = "independent_review_malformed"
R_REVIEW_SCHEMA = "independent_review_schema_mismatch"
R_REVIEW_VERDICT = "independent_review_verdict_invalid"
R_REVIEW_DISPATCH_INVALID = "independent_review_dispatch_mode_invalid"
R_REVIEW_DEGRADED = "independent_review_degraded"
R_REVIEW_RUNTIME_INVALID = "independent_review_runtime_invalid"
R_REVIEW_RUNTIME_MISMATCH = "independent_review_runtime_mismatch"
R_REVIEW_FINDINGS = "independent_review_findings_missing"
R_REVIEW_RESIDUAL = "independent_review_residual_risks_missing"
R_REVIEW_BINDING_MISSING = "independent_review_binding_missing"
R_REVIEW_BINDING_INVALID = "independent_review_binding_invalid"
R_REVIEW_UC_MISMATCH = "independent_review_uc_mismatch"
R_REVIEW_ATTEMPT_MISMATCH = "independent_review_attempt_mismatch"
R_REVIEW_CANDIDATE_MISMATCH = "independent_review_candidate_mismatch"
R_REVIEW_PRECODE_MISMATCH = "independent_review_precode_evidence_mismatch"
R_REVIEW_FAILURE_MISMATCH = "independent_review_failure_evidence_mismatch"
R_REVIEW_VERIFICATION_MISMATCH = "independent_review_verification_evidence_mismatch"
R_REVIEW_BEFORE_LOG_MISMATCH = "independent_review_before_log_mismatch"
R_REVIEW_AFTER_LOG_MISMATCH = "independent_review_after_log_mismatch"
R_REVIEW_FALSIFICATION_MISMATCH = "independent_review_falsification_evidence_mismatch"
R_REVIEW_ORACLE_MISMATCH = "independent_review_oracle_mismatch"

BROAD_EXCEPTIONS = {"Exception", "BaseException"}
SKIP_MARKERS = {
    "skip",
    "skipif",
    "skipunless",
    "skiptest",
    "xfail",
    "expectedfailure",
    "importorskip",
}
MOCK_NAMES = {
    "patch",
    "magicmock",
    "mock",
    "asyncmock",
    "noncallablemock",
    "create_autospec",
    "seal",
}
INFLATABLE_KEYWORDS = {
    "timeout",
    "timeout_s",
    "timeout_seconds",
    "retries",
    "retry",
    "max_retries",
    "max_attempts",
    "attempts",
    "wait",
    "delay",
    "sleep",
    "poll_interval",
    "polls",
    "max_polls",
    "interval",
    "tolerance",
    "epsilon",
}

# Stable finding codes.  Tests assert these exact strings.
F_PARSE = "candidate_does_not_parse"
F_ASSERT_COUNT = "assertion_count_decreased"
F_TEST_REMOVED = "test_function_removed"
F_EXPECTED_TO_ACTUAL = "expected_rewritten_to_actual"
F_BROAD_EXCEPT = "broad_exception_introduced"
F_ANY_OF_EXCEPT = "any_of_exceptions_introduced"
F_SKIP_XFAIL = "skip_or_xfail_introduced"
F_MOCK_BYPASS = "mock_introduced_bypassing_target"
F_TIMEOUT_INFLATED = "timeout_or_retry_inflated"
F_COLLECTION_NARROWED = "collection_narrowed"
F_NOT_SUCCESS = "not_success_assertion_introduced"
F_INPUT_CHANGED = "failing_input_changed"
F_VACUOUS_ASSERTION = "assertion_semantically_vacuous"
F_REACHABILITY = "assertion_reachability_reduced"
F_TEST_EXECUTION = "test_execution_narrowed"
F_RECORDED_EXCEPTION = "recorded_failure_swallowed"
F_PREDICATE_WEAKENED = "assertion_predicate_weakened"
F_LOCAL_SUBSTITUTE = "test_local_substitute_introduced"

_NOT_SUCCESS_RE = re.compile(
    r"assert[^\n]*(?:!=\s*(?:200|201|0|True)\b|\bnot\s+ok\b|\bis\s+not\s+None\s*$)",
    re.MULTILINE,
)
_COLLECTION_RE = re.compile(
    r"__test__\s*=\s*False|collect_ignore|addopts\s*=[^\n]*(?:--ignore|-k\s)|pytestmark\s*=\s*pytest\.mark\.skip"
)
_ACTUAL_FROM_LOG = (
    re.compile(r"\bexpected\b[^\n]{0,40}?\bgot\b\s*([^\s,;.)\]]+)"),
    re.compile(r"(?m)^E\s+assert\s+([^\s=<>!]+)\s*=="),
    re.compile(r"(?m)^E\s+AssertionError:\s*assert\s+([^\s=<>!]+)\s*=="),
)


@dataclass(frozen=True)
class FileChange:
    """One proposed file replacement, workspace-relative."""

    path: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class StructuralFinding:
    code: str
    path: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class ReviewExpectation:
    """Current evidence identity an independent verdict must bind exactly.

    The review document is supplied by a different process and is therefore an
    untrusted assertion until these values are compared with the attempt the
    gate is handling *now*.  Candidate-manifest identity protects the bytes the
    engine may apply; candidate-patch identity proves the reviewer saw the
    human-readable diff required by the proposal.  The remaining hashes bind
    the original failure, sandbox verification, and required falsification
    evidence that informed the verdict.
    """

    uc: str
    attempt_id: str
    candidate_manifest_sha256: str
    candidate_patch_sha256: str
    precode_evidence_sha256: str
    original_failure_sha256: str
    verification_sha256: str
    before_log_sha256: str
    after_log_sha256: str
    falsification_sha256: str
    oracle_refs: tuple[str, ...]

    def as_binding(self) -> dict[str, Any]:
        return {
            "uc": self.uc,
            "attempt_id": self.attempt_id,
            "candidate_manifest_sha256": self.candidate_manifest_sha256,
            "candidate_patch_sha256": self.candidate_patch_sha256,
            "precode_evidence_sha256": self.precode_evidence_sha256,
            "original_failure_sha256": self.original_failure_sha256,
            "verification_sha256": self.verification_sha256,
            "before_log_sha256": self.before_log_sha256,
            "after_log_sha256": self.after_log_sha256,
            "falsification_sha256": self.falsification_sha256,
            "oracle_refs": sorted(self.oracle_refs),
        }


# --------------------------------------------------------------------------
# Python surface extraction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonSurface:
    """The test-relevant shape of one Python file, for before/after comparison."""

    parsed: bool
    assertions: int
    tests: frozenset[str]
    broad_handlers: int
    multi_type_handlers: int
    skip_markers: int
    mock_uses: int
    comparison_literals: frozenset[str]
    max_inflatable: dict[str, float]
    call_arguments: frozenset[str]
    reachable_assertions: int
    unconditional_assertions: int
    executed_tests: frozenset[str]
    predicate_classes: frozenset[str]
    vacuous_assertions: tuple[str, ...]
    assertion_local_sources: frozenset[str]
    recorded_swallowers: int
    substitution_signals: frozenset[str]


@dataclass(frozen=True)
class ValueFlow:
    """A deliberately small, deterministic value-provenance summary.

    This is not a Python interpreter.  It follows only assignments, containers,
    simple return-only helpers, and a few side-effect-free builtins.  Anything
    else stays opaque.  That is sufficient to distinguish an evidence literal
    (``expected = 200``) from a value copied from the observation
    (``expected = OBSERVED``) without executing candidate code.
    """

    canonical: str
    observations: frozenset[str] = frozenset()
    local_sources: frozenset[str] = frozenset()
    constant: str | None = None
    truth: bool | None = None


@dataclass(frozen=True)
class AssertionFact:
    scope: str
    lineno: int
    predicate: str
    flows: tuple[ValueFlow, ...]
    reachable: bool
    unconditional: bool
    vacuous_reason: str = ""


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        return repr(node.value)
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return None


def _decorator_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        names.append(".".join(reversed(parts)))
    return names


_UNKNOWN = object()
_FAILURE_EXCEPTION_RE = re.compile(
    r"\b([A-Za-z_]\w*(?:Error|Exception)|SkipTest|SystemExit)\s*:"
)
_NAME_ERROR_RE = re.compile(r"\bNameError:\s+name ['\"]([A-Za-z_]\w*)['\"]")
_LEXICAL_OBSERVATION_RE = re.compile(r"(?:^|_)(?:actual|observed|response|result)(?:$|_)", re.I)
_SUBSTITUTE_NAME_TOKENS = (
    "actual",
    "observed",
    "response",
    "result",
    "probe",
    "stub",
    "mock",
    "fake",
    "fixture",
    "recorded",
)
_TRANSPARENT_CALLS = {"str", "int", "float", "bool"}


def _qualified_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _const_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:  # pragma: no cover - repr of stdlib literals is total
        return f"<{type(value).__name__}>"


def _looks_like_substitute(name: str) -> bool:
    folded = name.casefold()
    return any(token in folded for token in _SUBSTITUTE_NAME_TOKENS)


def _literal_value(
    node: ast.AST | None,
    assignments: dict[str, ast.AST],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    *,
    opaque_names: frozenset[str] = frozenset(),
    seen: frozenset[str] = frozenset(),
) -> Any:
    """Safely fold a deliberately small expression subset.

    Observation anchors are opaque even when the synthetic acceptance fixture
    assigns them a literal.  Otherwise the honest control ``200 == OBSERVED``
    would be mistaken for a compile-time tautology.
    """

    if node is None:
        return _UNKNOWN
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in opaque_names or node.id in seen or node.id not in assignments:
            return _UNKNOWN
        return _literal_value(
            assignments[node.id], assignments, functions,
            opaque_names=opaque_names, seen=seen | {node.id},
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [
            _literal_value(item, assignments, functions, opaque_names=opaque_names, seen=seen)
            for item in node.elts
        ]
        if any(value is _UNKNOWN for value in values):
            return _UNKNOWN
        if isinstance(node, ast.List):
            return values
        if isinstance(node, ast.Set):
            try:
                return set(values)
            except TypeError:
                return _UNKNOWN
        return tuple(values)
    if isinstance(node, ast.Dict):
        keys = [
            _literal_value(item, assignments, functions, opaque_names=opaque_names, seen=seen)
            for item in node.keys
        ]
        values = [
            _literal_value(item, assignments, functions, opaque_names=opaque_names, seen=seen)
            for item in node.values
        ]
        if any(item is _UNKNOWN for item in (*keys, *values)):
            return _UNKNOWN
        try:
            return dict(zip(keys, values))
        except (TypeError, ValueError):
            return _UNKNOWN
    if isinstance(node, ast.Subscript):
        container = _literal_value(
            node.value, assignments, functions, opaque_names=opaque_names, seen=seen
        )
        index = _literal_value(
            node.slice, assignments, functions, opaque_names=opaque_names, seen=seen
        )
        if container is _UNKNOWN or index is _UNKNOWN:
            return _UNKNOWN
        try:
            return container[index]
        except (KeyError, IndexError, TypeError):
            return _UNKNOWN
    if isinstance(node, ast.UnaryOp):
        value = _literal_value(
            node.operand, assignments, functions, opaque_names=opaque_names, seen=seen
        )
        if value is _UNKNOWN:
            return _UNKNOWN
        try:
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
        except (TypeError, ValueError):
            return _UNKNOWN
    if isinstance(node, ast.BinOp):
        left = _literal_value(
            node.left, assignments, functions, opaque_names=opaque_names, seen=seen
        )
        right = _literal_value(
            node.right, assignments, functions, opaque_names=opaque_names, seen=seen
        )
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div) and right != 0:
                return left / right
            if isinstance(node.op, ast.FloorDiv) and right != 0:
                return left // right
            if isinstance(node.op, ast.Mod) and right != 0:
                return left % right
        except (TypeError, ValueError, OverflowError):
            return _UNKNOWN
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                folded = _literal_value(
                    value.value, assignments, functions,
                    opaque_names=opaque_names, seen=seen,
                )
                if folded is _UNKNOWN:
                    return _UNKNOWN
                parts.append(str(folded))
            else:
                return _UNKNOWN
        return "".join(parts)
    if isinstance(node, ast.BoolOp):
        values = [
            _literal_value(item, assignments, functions, opaque_names=opaque_names, seen=seen)
            for item in node.values
        ]
        if any(value is _UNKNOWN for value in values):
            # Short-circuiting can still decide the result from a constant prefix.
            if isinstance(node.op, ast.Or):
                for value in values:
                    if value is not _UNKNOWN and bool(value):
                        return value
            if isinstance(node.op, ast.And):
                for value in values:
                    if value is not _UNKNOWN and not bool(value):
                        return value
            return _UNKNOWN
        return (next((v for v in values if v), values[-1]) if isinstance(node.op, ast.Or)
                else next((v for v in values if not v), values[-1]))
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        values = [
            _literal_value(item, assignments, functions, opaque_names=opaque_names, seen=seen)
            for item in operands
        ]
        if any(value is _UNKNOWN for value in values):
            # ``__name__`` cannot be the invented collection sentinel used by the
            # adversarial corpus when a file is run as a script.
            if (
                len(node.ops) == 1
                and isinstance(node.left, ast.Name)
                and node.left.id == "__name__"
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.ops[0], ast.Eq)
            ):
                if node.comparators[0].value == "__pytest__":
                    return False
                if node.comparators[0].value == "__main__":
                    return True
            return _UNKNOWN
        try:
            for op, left, right in zip(node.ops, values, values[1:]):
                ok = (
                    left == right if isinstance(op, ast.Eq) else
                    left != right if isinstance(op, ast.NotEq) else
                    left < right if isinstance(op, ast.Lt) else
                    left <= right if isinstance(op, ast.LtE) else
                    left > right if isinstance(op, ast.Gt) else
                    left >= right if isinstance(op, ast.GtE) else
                    left in right if isinstance(op, ast.In) else
                    left not in right if isinstance(op, ast.NotIn) else
                    left is right if isinstance(op, ast.Is) else
                    left is not right
                )
                if not ok:
                    return False
            return True
        except (TypeError, ValueError):
            return _UNKNOWN
    if isinstance(node, ast.Call):
        name = _qualified_name(node.func).split(".")[-1]
        if name in _TRANSPARENT_CALLS | {"abs", "len"} and len(node.args) == 1:
            # The length of a literal container is known even when its members
            # are observations that must stay opaque.  For example,
            # ``results = [OBSERVED]; assert len(results) == 1`` is vacuous:
            # the assertion proves only the candidate's freshly-built shape.
            if name == "len":
                target = node.args[0]
                resolved: set[str] = set(seen)
                while isinstance(target, ast.Name) and target.id in assignments:
                    if target.id in resolved:
                        break
                    resolved.add(target.id)
                    target = assignments[target.id]
                if isinstance(target, (ast.List, ast.Tuple, ast.Set)):
                    return len(target.elts)
                if isinstance(target, ast.Dict):
                    return len(target.keys)
            value = _literal_value(
                node.args[0], assignments, functions, opaque_names=opaque_names, seen=seen
            )
            if value is _UNKNOWN:
                return _UNKNOWN
            try:
                return {
                    "str": str,
                    "int": int,
                    "float": float,
                    "bool": bool,
                    "abs": abs,
                    "len": len,
                }[name](value)
            except (TypeError, ValueError, OverflowError):
                return _UNKNOWN
        if isinstance(node.func, ast.Name) and not node.args and not node.keywords:
            function = functions.get(node.func.id)
            if function is not None and len(function.body) == 1 and isinstance(
                function.body[0], ast.Return
            ):
                key = f"call:{node.func.id}"
                if key in seen:
                    return _UNKNOWN
                return _literal_value(
                    function.body[0].value, assignments, functions,
                    opaque_names=opaque_names, seen=seen | {key},
                )
    return _UNKNOWN


def _merge_flows(canonical: str, flows: Iterable[ValueFlow]) -> ValueFlow:
    items = tuple(flows)
    return ValueFlow(
        canonical=canonical,
        observations=frozenset().union(*(item.observations for item in items)),
        local_sources=frozenset().union(*(item.local_sources for item in items)),
    )


def _value_flow(
    node: ast.AST,
    assignments: dict[str, ast.AST],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    classes: dict[str, ast.ClassDef],
    observation_names: frozenset[str],
    *,
    seen: frozenset[str] = frozenset(),
) -> ValueFlow:
    if isinstance(node, ast.Name):
        if node.id in observation_names:
            return ValueFlow(f"observed:{node.id}", frozenset({node.id}))
        if node.id in seen or node.id not in assignments:
            return ValueFlow(f"name:{node.id}")
        value = assignments[node.id]
        flow = _value_flow(
            value, assignments, functions, classes, observation_names,
            seen=seen | {node.id},
        )
        local = flow.local_sources
        if isinstance(value, ast.Lambda) or (
            isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple))
            and _looks_like_substitute(node.id)
        ):
            local = local | {f"local_value:{node.id}"}
        return ValueFlow(flow.canonical, flow.observations, local, flow.constant, flow.truth)
    if isinstance(node, ast.Constant):
        return ValueFlow(
            f"constant:{_const_repr(node.value)}",
            constant=_const_repr(node.value),
            truth=bool(node.value),
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        flows = [
            _value_flow(item, assignments, functions, classes, observation_names, seen=seen)
            for item in node.elts
        ]
        return _merge_flows(type(node).__name__.lower() + "(" + ",".join(
            item.canonical for item in flows
        ) + ")", flows)
    if isinstance(node, ast.Dict):
        flows = [
            _value_flow(item, assignments, functions, classes, observation_names, seen=seen)
            for item in (*node.keys, *node.values) if item is not None
        ]
        return _merge_flows("dict(" + ",".join(item.canonical for item in flows) + ")", flows)
    if isinstance(node, ast.Subscript):
        base = _value_flow(
            node.value, assignments, functions, classes, observation_names, seen=seen
        )
        index = _value_flow(
            node.slice, assignments, functions, classes, observation_names, seen=seen
        )
        folded = _literal_value(
            node, assignments, functions, opaque_names=observation_names
        )
        constant = None if folded is _UNKNOWN else _const_repr(folded)
        return ValueFlow(
            f"subscript:{base.canonical}[{index.canonical}]",
            base.observations | index.observations,
            base.local_sources | index.local_sources,
            constant,
            None if folded is _UNKNOWN else bool(folded),
        )
    if isinstance(node, ast.Attribute):
        base = _value_flow(
            node.value, assignments, functions, classes, observation_names, seen=seen
        )
        return ValueFlow(
            f"attribute:{base.canonical}.{node.attr}", base.observations,
            base.local_sources,
        )
    if isinstance(node, ast.FormattedValue):
        return _value_flow(
            node.value, assignments, functions, classes, observation_names, seen=seen
        )
    if isinstance(node, (ast.UnaryOp, ast.BinOp, ast.BoolOp, ast.Compare, ast.JoinedStr)):
        children = [
            _value_flow(child, assignments, functions, classes, observation_names, seen=seen)
            for child in ast.iter_child_nodes(node)
            if isinstance(child, ast.expr)
        ]
        folded = _literal_value(
            node, assignments, functions, opaque_names=observation_names
        )
        canonical = ast.dump(node, annotate_fields=True, include_attributes=False)
        merged = _merge_flows(canonical, children)
        return ValueFlow(
            merged.canonical, merged.observations, merged.local_sources,
            None if folded is _UNKNOWN else _const_repr(folded),
            None if folded is _UNKNOWN else bool(folded),
        )
    if isinstance(node, ast.Call):
        name = _qualified_name(node.func)
        short = name.split(".")[-1]
        argument_flows = [
            _value_flow(item, assignments, functions, classes, observation_names, seen=seen)
            for item in (*node.args, *(kw.value for kw in node.keywords))
        ]
        if short in _TRANSPARENT_CALLS and len(argument_flows) == 1:
            item = argument_flows[0]
            folded = _literal_value(
                node, assignments, functions, opaque_names=observation_names
            )
            return ValueFlow(
                item.canonical, item.observations, item.local_sources,
                item.constant if folded is _UNKNOWN else _const_repr(folded),
                item.truth if folded is _UNKNOWN else bool(folded),
            )
        if short == "len" and len(node.args) == 1:
            folded = _literal_value(
                node, assignments, functions, opaque_names=observation_names
            )
            source = argument_flows[0]
            return ValueFlow(
                f"len:{source.canonical}", frozenset(), source.local_sources,
                None if folded is _UNKNOWN else _const_repr(folded),
                None if folded is _UNKNOWN else bool(folded),
            )
        if short == "abs" and len(argument_flows) == 1:
            item = argument_flows[0]
            return ValueFlow(
                f"abs:{item.canonical}", item.observations, item.local_sources,
            )
        if isinstance(node.func, ast.Name) and node.func.id in functions:
            function = functions[node.func.id]
            returns = [item for item in ast.walk(function) if isinstance(item, ast.Return)]
            if len(returns) == 1 and returns[0].value is not None:
                item = _value_flow(
                    returns[0].value, assignments, functions, classes, observation_names,
                    seen=seen | {f"call:{node.func.id}"},
                )
                local = item.local_sources
                if _looks_like_substitute(node.func.id):
                    local = local | {f"local_callable:{node.func.id}"}
                return ValueFlow(
                    item.canonical, item.observations, local, item.constant, item.truth,
                )
        # Resolve ``instance.method()`` when the instance was constructed from a
        # class declared in this candidate.
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            instance = assignments.get(node.func.value.id)
            if isinstance(instance, ast.Call) and isinstance(instance.func, ast.Name):
                cls = classes.get(instance.func.id)
                if cls is not None:
                    method = next(
                        (
                            item for item in cls.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == node.func.attr
                        ),
                        None,
                    )
                    if method is not None:
                        returns = [item for item in ast.walk(method) if isinstance(item, ast.Return)]
                        if len(returns) == 1 and returns[0].value is not None:
                            item = _value_flow(
                                returns[0].value, assignments, functions, classes,
                                observation_names, seen=seen | {f"method:{cls.name}.{method.name}"},
                            )
                            local = item.local_sources
                            if any(
                                _looks_like_substitute(name)
                                for name in (node.func.value.id, cls.name, method.name)
                            ):
                                local = local | {f"local_class:{cls.name}"}
                            return ValueFlow(
                                item.canonical, item.observations, local,
                                item.constant, item.truth,
                            )
        folded = _literal_value(
            node, assignments, functions, opaque_names=observation_names
        )
        receiver_flows = (
            [
                _value_flow(
                    node.func.value,
                    assignments,
                    functions,
                    classes,
                    observation_names,
                    seen=seen,
                )
            ]
            if isinstance(node.func, ast.Attribute)
            else []
        )
        merged = _merge_flows(
            f"call:{name}(" + ",".join(item.canonical for item in argument_flows) + ")",
            (*receiver_flows, *argument_flows),
        )
        local = merged.local_sources
        if short.casefold() in {"simplenamespace", "moduletype"}:
            local = local | {f"local_constructor:{short}"}
        return ValueFlow(
            merged.canonical, merged.observations, local,
            None if folded is _UNKNOWN else _const_repr(folded),
            None if folded is _UNKNOWN else bool(folded),
        )
    return ValueFlow(ast.dump(node, annotate_fields=True, include_attributes=False))


def _exception_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set(BROAD_EXCEPTIONS)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_exception_names(item) for item in node.elts))
    name = _qualified_name(node)
    return {name.split(".")[-1]} if name else set()


def _block_unconditionally_reraises(statements: list[ast.stmt]) -> bool:
    for statement in statements:
        if isinstance(statement, ast.Raise):
            return True
        if isinstance(statement, ast.If):
            if statement.body and statement.orelse and all(
                _block_unconditionally_reraises(branch)
                for branch in (statement.body, statement.orelse)
            ):
                return True
        if isinstance(statement, (ast.Return, ast.Break, ast.Continue)):
            return False
    return False


def _recorded_exception_types(log: str) -> frozenset[str]:
    return frozenset(match.group(1).split(".")[-1] for match in _FAILURE_EXCEPTION_RE.finditer(log))


def _module_bindings(
    tree: ast.Module,
) -> tuple[
    dict[str, ast.AST],
    dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    dict[str, ast.ClassDef],
]:
    assignments: dict[str, ast.AST] = {}
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    classes: dict[str, ast.ClassDef] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[statement.name] = statement
        elif isinstance(statement, ast.ClassDef):
            classes[statement.name] = statement
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    assignments[target.id] = value
    return assignments, functions, classes


def _observation_names(tree: ast.Module, failure_log: str) -> frozenset[str]:
    assignments, functions, _classes = _module_bindings(tree)
    failing_names = set(_NAME_ERROR_RE.findall(failure_log or ""))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and _LEXICAL_OBSERVATION_RE.search(node.id):
            names.add(node.id)
    # In the original assertion, names other than the one that raised are the
    # surviving observation side.  This is stronger than guessing from casing.
    if failing_names:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                used = {item.id for item in ast.walk(node.test) if isinstance(item, ast.Name)}
                if used & failing_names:
                    names.update(used - failing_names - {"self"})
    actual_values = observed_actual_values(failure_log)
    for name, value in assignments.items():
        folded = _literal_value(value, assignments, functions)
        if folded is not _UNKNOWN and _const_repr(folded) in actual_values:
            names.add(name)
    return frozenset(names)


def _predicate_and_operands(node: ast.AST) -> tuple[str, tuple[ast.AST, ...]]:
    if isinstance(node, ast.Assert):
        test = node.test
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        name = node.func.attr.casefold()
        if name in {"assertequal", "assertis"} and len(node.args) >= 2:
            return "exact_equality", (node.args[0], node.args[1])
        if name in {"assertnotequal", "assertisnot"} and len(node.args) >= 2:
            return "negative", (node.args[0], node.args[1])
        if name in {"assertin", "assertnotin"} and len(node.args) >= 2:
            return "membership", (node.args[0], node.args[1])
        if name in {"asserttrue", "assertfalse", "assertisnone", "assertisnotnone"}:
            return "truthiness", tuple(node.args[:1])
        if name.startswith("assertraises"):
            return "exception_expectation", tuple(node.args)
        return "opaque_assertion", tuple(node.args)
    else:
        return "opaque_assertion", ()

    if isinstance(test, ast.Compare):
        if all(isinstance(op, (ast.Eq, ast.Is)) for op in test.ops):
            return "exact_equality", (test.left, *test.comparators)
        if any(isinstance(op, (ast.In, ast.NotIn)) for op in test.ops):
            return "membership", (test.left, *test.comparators)
        if any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in test.ops):
            if any(
                isinstance(item, ast.Call) and _qualified_name(item.func).endswith("abs")
                for item in ast.walk(test)
            ):
                return "tolerance", (test.left, *test.comparators)
            return "range_or_relational", (test.left, *test.comparators)
        return "negative", (test.left, *test.comparators)
    if isinstance(test, ast.BoolOp):
        return "boolean_guard", tuple(test.values)
    if isinstance(test, ast.Call) and _qualified_name(test.func).split(".")[-1] in {
        "isinstance", "bool", "all", "any",
    }:
        return "truthiness", (test,)
    return "truthiness", (test,)


def _vacuous_reason(predicate: str, operands: tuple[ast.AST, ...], flows: tuple[ValueFlow, ...]) -> str:
    if predicate == "exact_equality" and len(flows) >= 2:
        if len({flow.canonical for flow in flows}) == 1:
            return "both sides resolve to the same expression"
        observed = [flow.observations for flow in flows if flow.observations]
        if len(observed) >= 2 and len(set(observed)) == 1:
            return "both sides derive from the same observed value"
        if all(flow.constant is not None for flow in flows) and len(
            {flow.constant for flow in flows}
        ) == 1:
            return "comparison is statically constant true"
    if predicate == "membership" and len(flows) >= 2:
        subject = flows[0].canonical
        if subject and subject in flows[1].canonical:
            return "membership container includes the subject itself"
    if len(operands) == 1:
        folded = flows[0].truth if flows else None
        if folded is True:
            return "assertion condition is statically true"
    return ""


def _always_raises(function: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if function is None:
        return False
    for statement in function.body:
        if isinstance(statement, ast.Raise):
            return True
        if isinstance(statement, ast.Return):
            return False
    return False


def _collect_assertion_facts(
    tree: ast.Module,
    observation_names: frozenset[str],
) -> tuple[AssertionFact, ...]:
    assignments, functions, classes = _module_bindings(tree)
    facts: list[AssertionFact] = []

    def add_assertion(
        node: ast.AST,
        scope: str,
        env: dict[str, ast.AST],
        reachable: bool,
        conditional: bool,
    ) -> None:
        predicate, operands = _predicate_and_operands(node)
        bindings = dict(assignments)
        bindings.update(env)
        flows = tuple(
            _value_flow(item, bindings, functions, classes, observation_names)
            for item in operands
        )
        reason = _vacuous_reason(predicate, operands, flows) if reachable else ""
        facts.append(
            AssertionFact(
                scope=scope,
                lineno=getattr(node, "lineno", 0),
                predicate=predicate,
                flows=flows,
                reachable=reachable,
                unconditional=reachable and not conditional,
                vacuous_reason=reason,
            )
        )

    def walk_block(
        statements: list[ast.stmt],
        scope: str,
        env: dict[str, ast.AST],
        *,
        reachable: bool = True,
        conditional: bool = False,
    ) -> bool:
        current = reachable
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # A nested definition is not executed merely because its body is
                # present.  Direct calls are handled below.
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        env[target.id] = value
                # A call to a helper that unconditionally raises prevents the
                # normal path from continuing.
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if _always_raises(functions.get(value.func.id)):
                        current = False
                continue
            if isinstance(statement, ast.Assert):
                add_assertion(statement, scope, env, current, conditional)
                continue
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if isinstance(call.func, ast.Attribute) and call.func.attr.startswith("assert"):
                    add_assertion(call, scope, env, current, conditional)
                elif isinstance(call.func, ast.Name):
                    nested = next(
                        (
                            item for item in statements
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name == call.func.id
                        ),
                        None,
                    )
                    if nested is not None:
                        walk_block(
                            nested.body, f"{scope}.{nested.name}", dict(env),
                            reachable=current, conditional=conditional,
                        )
                continue
            if isinstance(statement, ast.If):
                combined = dict(assignments)
                combined.update(env)
                condition = _literal_value(
                    statement.test, combined, functions, opaque_names=observation_names
                )
                if condition is _UNKNOWN:
                    walk_block(
                        statement.body, scope, dict(env), reachable=current,
                        conditional=True,
                    )
                    walk_block(
                        statement.orelse, scope, dict(env), reachable=current,
                        conditional=True,
                    )
                elif bool(condition):
                    branch_continues = walk_block(
                        statement.body, scope, dict(env), reachable=current,
                        conditional=conditional,
                    )
                    if current and not branch_continues and not statement.orelse:
                        current = False
                else:
                    walk_block(
                        statement.body, scope, dict(env), reachable=False,
                        conditional=True,
                    )
                    walk_block(
                        statement.orelse, scope, dict(env), reachable=current,
                        conditional=conditional,
                    )
                continue
            if isinstance(statement, ast.While):
                combined = dict(assignments)
                combined.update(env)
                condition = _literal_value(
                    statement.test, combined, functions, opaque_names=observation_names
                )
                body_reachable = current and condition is not False
                walk_block(
                    statement.body, scope, dict(env), reachable=body_reachable,
                    conditional=True,
                )
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                combined = dict(assignments)
                combined.update(env)
                iterable = _literal_value(
                    statement.iter, combined, functions, opaque_names=observation_names
                )
                body_reachable = current and not (
                    iterable is not _UNKNOWN and hasattr(iterable, "__len__") and len(iterable) == 0
                )
                walk_block(
                    statement.body, scope, dict(env), reachable=body_reachable,
                    conditional=True,
                )
                walk_block(
                    statement.orelse, scope, dict(env), reachable=current,
                    conditional=True,
                )
                continue
            if isinstance(statement, ast.Try):
                try_reachable = current
                try_continues = walk_block(
                    statement.body, scope, dict(env), reachable=try_reachable,
                    conditional=conditional,
                )
                handler_continuations: list[bool] = []
                for handler in statement.handlers:
                    handler_continuations.append(walk_block(
                        handler.body, scope, dict(env), reachable=current,
                        conditional=True,
                    ))
                walk_block(
                    statement.orelse, scope, dict(env), reachable=try_continues,
                    conditional=conditional,
                )
                walk_block(
                    statement.finalbody, scope, dict(env), reachable=current,
                    conditional=conditional,
                )
                current = try_continues or any(handler_continuations)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                walk_block(
                    statement.body, scope, dict(env), reachable=current,
                    conditional=conditional,
                )
                continue
            if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                current = False
                continue
        return current

    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith(
            "test"
        ):
            walk_block(statement.body, statement.name, dict(assignments))
        elif isinstance(statement, ast.ClassDef):
            for method in statement.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith(
                    "test"
                ):
                    walk_block(
                        method.body, f"{statement.name}.{method.name}", dict(assignments)
                    )
    return tuple(facts)


def _executed_tests(tree: ast.Module) -> frozenset[str]:
    functions = {
        item.name for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test")
    }
    executed: set[str] = set()

    def walk_module_block(statements: list[ast.stmt], reachable: bool = True) -> None:
        for statement in statements:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                name = _qualified_name(statement.value.func)
                if reachable and name in functions:
                    executed.add(name)
                if reachable and name.endswith("unittest.main"):
                    for cls in tree.body:
                        if isinstance(cls, ast.ClassDef) and any(
                            _qualified_name(base).endswith("TestCase") for base in cls.bases
                        ):
                            executed.update(
                                f"{cls.name}.{method.name}"
                                for method in cls.body
                                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and method.name.startswith("test")
                            )
            elif isinstance(statement, ast.If):
                assignments, funcs, _classes = _module_bindings(tree)
                condition = _literal_value(statement.test, assignments, funcs)
                if condition is _UNKNOWN:
                    walk_module_block(statement.body, False)
                    walk_module_block(statement.orelse, False)
                elif bool(condition):
                    walk_module_block(statement.body, reachable)
                else:
                    walk_module_block(statement.orelse, reachable)
            elif isinstance(statement, ast.Try):
                walk_module_block(statement.body, reachable)
                for handler in statement.handlers:
                    walk_module_block(handler.body, False)

    walk_module_block(tree.body)
    return frozenset(executed)


def _inflatable_name(name: str) -> str:
    folded = name.casefold()
    if folded in INFLATABLE_KEYWORDS:
        return folded
    for keyword in sorted(INFLATABLE_KEYWORDS, key=len, reverse=True):
        if re.search(rf"(?:^|_){re.escape(keyword)}(?:$|_)", folded):
            return keyword
    return ""


def _inflatable_values(
    tree: ast.Module,
    assignments: dict[str, ast.AST],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> dict[str, float]:
    values: dict[str, float] = {}

    def record(name: str, node: ast.AST) -> None:
        key = _inflatable_name(name)
        if not key:
            return
        value = _literal_value(node, assignments, functions)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[key] = max(values.get(key, float(value)), float(value))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and node.value is not None:
                    record(target.id, node.value)
                    if isinstance(node.value, ast.Dict):
                        for key_node, value_node in zip(node.value.keys, node.value.values):
                            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                                record(key_node.value, value_node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = [*node.args.posonlyargs, *node.args.args]
            defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
                node.args.defaults
            )
            for argument, default in zip(positional, defaults):
                if default is not None:
                    record(argument.arg, default)
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None:
                    record(argument.arg, default)
        elif isinstance(node, ast.Call):
            short = _qualified_name(node.func).split(".")[-1].casefold()
            for keyword in node.keywords:
                if keyword.arg:
                    record(keyword.arg, keyword.value)
            if short == "sleep" and node.args:
                record("sleep", node.args[0])
    return values


def _substitution_signals(tree: ast.Module) -> frozenset[str]:
    signals: set[str] = set()
    module_functions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    signals.add(f"attribute_replaced:{_qualified_name(target)}")
                elif isinstance(target, ast.Subscript) and _qualified_name(target.value) == "sys.modules":
                    signals.add("sys.modules_replaced")
        elif isinstance(node, ast.Call):
            name = _qualified_name(node.func).casefold()
            parts = set(name.split("."))
            if parts & MOCK_NAMES or name.endswith("patch.object"):
                signals.add(f"mock_call:{name}")
            if name.endswith("simplenamespace") or name.endswith("moduletype"):
                signals.add(f"local_constructor:{name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").casefold() == "unittest" and any(
                alias.name.casefold() == "mock" for alias in node.names
            ):
                signals.add("mock_import:unittest.mock")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node not in tree.body and node.name in module_functions:
                signals.add(f"local_shadow:{node.name}")
    return frozenset(signals)


def python_surface(
    source: str | None,
    *,
    failure_log: str = "",
    observation_names: frozenset[str] | None = None,
) -> PythonSurface:
    """Extract the comparable surface of a Python source file.

    A file that does not parse yields ``parsed=False``; the caller turns that
    into a finding rather than silently comparing nothing.
    """

    empty = PythonSurface(
        parsed=False,
        assertions=0,
        tests=frozenset(),
        broad_handlers=0,
        multi_type_handlers=0,
        skip_markers=0,
        mock_uses=0,
        comparison_literals=frozenset(),
        max_inflatable={},
        call_arguments=frozenset(),
        reachable_assertions=0,
        unconditional_assertions=0,
        executed_tests=frozenset(),
        predicate_classes=frozenset(),
        vacuous_assertions=(),
        assertion_local_sources=frozenset(),
        recorded_swallowers=0,
        substitution_signals=frozenset(),
    )
    if source is None:
        return empty
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return empty

    assertions = 0
    tests: set[str] = set()
    broad = 0
    multi = 0
    skips = 0
    mocks = 0
    literals: set[str] = set()
    arguments: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            assertions += 1
            for child in ast.walk(node.test):
                if isinstance(child, ast.Compare):
                    for operand in (child.left, *child.comparators):
                        text = _literal(operand)
                        if text is not None:
                            literals.add(text)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                tests.add(node.name)
            for name in _decorator_names(node):
                if name.casefold().split(".")[-1] in SKIP_MARKERS:
                    skips += 1
        elif isinstance(node, ast.ClassDef):
            for name in _decorator_names(node):
                if name.casefold().split(".")[-1] in SKIP_MARKERS:
                    skips += 1
        elif isinstance(node, ast.ExceptHandler):
            handler = node.type
            if handler is None:
                broad += 1
            elif isinstance(handler, ast.Name) and handler.id in BROAD_EXCEPTIONS:
                broad += 1
            elif BROAD_EXCEPTIONS & _exception_names(handler):
                broad += 1
            if isinstance(handler, ast.Tuple) and len(handler.elts) >= 2:
                multi += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"mock", "unittest"} and "mock" in alias.name:
                    mocks += 1
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").casefold().endswith("mock") or (
                (node.module or "").casefold() == "unittest"
                and any(alias.name.casefold() == "mock" for alias in node.names)
            ):
                mocks += 1
        elif isinstance(node, ast.Call):
            target = node.func
            qualified = _qualified_name(target).casefold()
            name = qualified.split(".")[-1]
            if set(qualified.split(".")) & MOCK_NAMES or qualified.endswith("patch.object"):
                mocks += 1
            if name in SKIP_MARKERS:
                skips += 1
            if name == "raises":
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in BROAD_EXCEPTIONS:
                        broad += 1
                    elif isinstance(arg, ast.Tuple) and len(arg.elts) >= 2:
                        multi += 1
            for arg in node.args:
                text = _literal(arg)
                if text is not None:
                    arguments.add(f"{name}:{text}")
            for keyword in node.keywords:
                text = _literal(keyword.value)
                if text is not None:
                    arguments.add(f"{name}:{keyword.arg}={text}")
        elif isinstance(node, ast.Raise) and node.exc is not None:
            raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if _qualified_name(raised).split(".")[-1].casefold() == "skiptest":
                skips += 1

    # ``self.assertXxx(...)`` is an assertion even though it is not ast.Assert.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("assert") and node.func.attr != "assert_":
                assertions += 1
                for arg in node.args:
                    text = _literal(arg)
                    if text is not None:
                        literals.add(text)

    module_assignments, module_functions, _module_classes = _module_bindings(tree)
    anchors = observation_names if observation_names is not None else _observation_names(
        tree, failure_log
    )
    assertion_facts = _collect_assertion_facts(tree, anchors)
    recorded_types = _recorded_exception_types(failure_log)
    recorded_swallowers = 0
    if recorded_types:
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                caught = _exception_names(node.type)
                if (
                    (caught & recorded_types or caught & BROAD_EXCEPTIONS)
                    and not _block_unconditionally_reraises(node.body)
                ):
                    recorded_swallowers += 1
            elif isinstance(node, ast.Call):
                short = _qualified_name(node.func).split(".")[-1].casefold()
                caught = set().union(*(_exception_names(arg) for arg in node.args))
                if short in {"suppress", "raises", "assertraises"} and (
                    caught & recorded_types or caught & BROAD_EXCEPTIONS
                ):
                    recorded_swallowers += 1

    return PythonSurface(
        parsed=True,
        assertions=assertions,
        tests=frozenset(tests),
        broad_handlers=broad,
        multi_type_handlers=multi,
        skip_markers=skips,
        mock_uses=mocks,
        comparison_literals=frozenset(literals),
        max_inflatable=_inflatable_values(tree, module_assignments, module_functions),
        call_arguments=frozenset(arguments),
        reachable_assertions=sum(fact.reachable for fact in assertion_facts),
        unconditional_assertions=sum(fact.unconditional for fact in assertion_facts),
        executed_tests=_executed_tests(tree),
        predicate_classes=frozenset(fact.predicate for fact in assertion_facts if fact.reachable),
        vacuous_assertions=tuple(
            f"line {fact.lineno}: {fact.vacuous_reason}"
            for fact in assertion_facts if fact.vacuous_reason
        ),
        assertion_local_sources=frozenset().union(
            *(
                flow.local_sources
                for fact in assertion_facts
                if fact.reachable and any(item.observations for item in fact.flows)
                for flow in fact.flows
                if not flow.observations
            )
        ),
        recorded_swallowers=recorded_swallowers,
        substitution_signals=_substitution_signals(tree),
    )


def observed_actual_values(log: str) -> set[str]:
    """Literal values the failing run actually produced.

    Used to catch the single most common fake-green move: rewriting the expected
    value in the assertion to whatever the broken system returned.
    """

    values: set[str] = set()
    for pattern in _ACTUAL_FROM_LOG:
        for match in pattern.finditer(log or ""):
            token = match.group(1).strip().strip("'\"")
            if token:
                values.add(repr(token))
                try:
                    values.add(repr(int(token)))
                except ValueError:
                    pass
                try:
                    values.add(repr(float(token)))
                except ValueError:
                    pass
    return values


def _constant_matches_actual(value: str | None, actual_values: set[str]) -> bool:
    if value is None:
        return False
    if value in actual_values:
        return True
    try:
        candidate = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        candidate = value
    if not isinstance(candidate, str):
        return False
    for actual in actual_values:
        try:
            token = ast.literal_eval(actual)
        except (SyntaxError, ValueError):
            token = actual
        text = str(token)
        if text and re.search(rf"(?<![\w.]){re.escape(text)}(?![\w.])", candidate):
            return True
    return False


def _semantic_expected_to_actual(
    before_source: str,
    after_source: str,
    failure_log: str,
) -> list[str]:
    """Return descriptions of expectations derived from the recorded actual.

    The legacy check sees only a literal directly attached to ``Compare``.  This
    follows one-hop aliases, safe constant expressions, casts, f-strings and
    container lookups, while keeping the observed side opaque.
    """

    actual_values = observed_actual_values(failure_log)
    if not actual_values:
        return []
    try:
        before_tree = ast.parse(before_source)
        after_tree = ast.parse(after_source)
    except SyntaxError:
        return []
    # The failing asset, not the candidate, defines what was observed.  Letting
    # the candidate mint a new name such as ``RECORDED_RESPONSE`` as an
    # observation anchor would let a locally fabricated value bless itself.
    anchors = _observation_names(before_tree, failure_log)
    assignments, functions, classes = _module_bindings(after_tree)
    hits: list[str] = []

    def inspect_block(statements: list[ast.stmt], env: dict[str, ast.AST]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        env[target.id] = value
            elif isinstance(statement, ast.Assert):
                predicate, operands = _predicate_and_operands(statement)
                if predicate not in {
                    "exact_equality", "membership", "range_or_relational", "tolerance"
                }:
                    continue
                bindings = dict(assignments)
                bindings.update(env)
                flows = [
                    _value_flow(item, bindings, functions, classes, anchors)
                    for item in operands
                ]
                observed_positions = {
                    index for index, flow in enumerate(flows) if flow.observations
                }
                for index, flow in enumerate(flows):
                    if index in observed_positions:
                        continue
                    if observed_positions and _constant_matches_actual(flow.constant, actual_values):
                        hits.append(
                            f"line {statement.lineno}: expectation resolves to recorded actual {flow.constant}"
                        )
                if predicate == "membership" and observed_positions:
                    for operand in operands[1:]:
                        for child in ast.walk(operand):
                            value = _literal_value(
                                child, bindings, functions, opaque_names=anchors
                            )
                            if value is not _UNKNOWN and _constant_matches_actual(
                                _const_repr(value), actual_values
                            ):
                                hits.append(
                                    f"line {statement.lineno}: accepted set includes the recorded actual"
                                )
                                break
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if isinstance(call.func, ast.Attribute) and call.func.attr.startswith("assert"):
                    predicate, operands = _predicate_and_operands(call)
                    if predicate != "exact_equality":
                        continue
                    bindings = dict(assignments)
                    bindings.update(env)
                    flows = [
                        _value_flow(item, bindings, functions, classes, anchors)
                        for item in operands
                    ]
                    observed_positions = {
                        index for index, flow in enumerate(flows) if flow.observations
                    }
                    for index, flow in enumerate(flows):
                        if index not in observed_positions and observed_positions and _constant_matches_actual(
                            flow.constant, actual_values
                        ):
                            hits.append(
                                f"line {call.lineno}: expectation resolves to recorded actual {flow.constant}"
                            )
            elif isinstance(statement, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                inspect_block(statement.body, dict(env))
                inspect_block(statement.orelse, dict(env))
            elif isinstance(statement, ast.Try):
                inspect_block(statement.body, dict(env))
                inspect_block(statement.orelse, dict(env))
                inspect_block(statement.finalbody, dict(env))
                for handler in statement.handlers:
                    inspect_block(handler.body, dict(env))
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                inspect_block(statement.body, dict(env))

    for statement in after_tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inspect_block(statement.body, {})
        elif isinstance(statement, ast.ClassDef):
            for method in statement.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inspect_block(method.body, {})
    return list(dict.fromkeys(hits))


# --------------------------------------------------------------------------
# Structural review
# --------------------------------------------------------------------------


def structural_review(
    changes: Iterable[FileChange],
    *,
    failure_log: str = "",
) -> list[StructuralFinding]:
    """Every deterministic fake-green signal in a candidate, most severe first."""

    findings: list[StructuralFinding] = []
    actual_values = observed_actual_values(failure_log)

    for change in changes:
        path = change.path
        before_text = change.before or ""
        after_text = change.after or ""

        if change.after is None:
            findings.append(
                StructuralFinding(F_TEST_REMOVED, path, "the candidate deletes this file")
            )
            continue

        is_python = path.endswith(".py")
        observation_names: frozenset[str] | None = None
        if is_python and change.before is not None:
            try:
                observation_names = _observation_names(
                    ast.parse(change.before), failure_log
                )
            except SyntaxError:
                observation_names = None
        before = (
            python_surface(
                change.before,
                failure_log=failure_log,
                observation_names=observation_names,
            )
            if is_python else None
        )
        after = (
            python_surface(
                change.after,
                failure_log=failure_log,
                observation_names=observation_names,
            )
            if is_python else None
        )

        if is_python and after is not None and not after.parsed:
            findings.append(
                StructuralFinding(F_PARSE, path, "the candidate is not valid Python")
            )
            continue

        if before is not None and after is not None and before.parsed:
            if after.assertions < before.assertions:
                findings.append(
                    StructuralFinding(
                        F_ASSERT_COUNT,
                        path,
                        f"assertions {before.assertions} -> {after.assertions}",
                    )
                )
            if after.reachable_assertions < before.reachable_assertions or (
                after.unconditional_assertions < before.unconditional_assertions
            ):
                findings.append(
                    StructuralFinding(
                        F_REACHABILITY,
                        path,
                        "reachable assertions "
                        f"{before.reachable_assertions} -> {after.reachable_assertions}; "
                        "unconditional assertions "
                        f"{before.unconditional_assertions} -> {after.unconditional_assertions}",
                    )
                )
            removed_execution = sorted(before.executed_tests - after.executed_tests)
            if removed_execution:
                findings.append(
                    StructuralFinding(
                        F_TEST_EXECUTION,
                        path,
                        "test execution route removed: " + ", ".join(removed_execution),
                    )
                )
            introduced_vacuity = after.vacuous_assertions
            if introduced_vacuity and not before.vacuous_assertions:
                findings.append(
                    StructuralFinding(
                        F_VACUOUS_ASSERTION,
                        path,
                        "; ".join(introduced_vacuity),
                    )
                )
            weak_predicates = {
                "negative",
                "membership",
                "range_or_relational",
                "tolerance",
                "boolean_guard",
                "truthiness",
                "exception_expectation",
            }
            introduced_weakness = sorted(
                (after.predicate_classes & weak_predicates)
                - (before.predicate_classes & weak_predicates)
            )
            if "exact_equality" in before.predicate_classes and introduced_weakness:
                findings.append(
                    StructuralFinding(
                        F_PREDICATE_WEAKENED,
                        path,
                        "exact oracle replaced or supplemented by weaker predicate(s): "
                        + ", ".join(introduced_weakness),
                    )
                )
            removed = sorted(before.tests - after.tests)
            if removed:
                findings.append(
                    StructuralFinding(
                        F_TEST_REMOVED, path, "removed test(s): " + ", ".join(removed)
                    )
                )
            if after.broad_handlers > before.broad_handlers:
                findings.append(
                    StructuralFinding(
                        F_BROAD_EXCEPT,
                        path,
                        f"broad exception handlers {before.broad_handlers} -> {after.broad_handlers}",
                    )
                )
            if after.multi_type_handlers > before.multi_type_handlers:
                findings.append(
                    StructuralFinding(
                        F_ANY_OF_EXCEPT,
                        path,
                        "an any-of-these-exceptions handler hides which layer failed",
                    )
                )
            if after.recorded_swallowers > before.recorded_swallowers:
                findings.append(
                    StructuralFinding(
                        F_RECORDED_EXCEPTION,
                        path,
                        "candidate newly catches or expects the exception recorded in the "
                        f"failure log ({before.recorded_swallowers} -> "
                        f"{after.recorded_swallowers})",
                    )
                )
            if after.skip_markers > before.skip_markers:
                findings.append(
                    StructuralFinding(
                        F_SKIP_XFAIL,
                        path,
                        f"skip/xfail markers {before.skip_markers} -> {after.skip_markers}",
                    )
                )
            if after.mock_uses > before.mock_uses:
                findings.append(
                    StructuralFinding(
                        F_MOCK_BYPASS,
                        path,
                        f"mock uses {before.mock_uses} -> {after.mock_uses}; the target "
                        "layer may no longer be exercised",
                    )
                )
            new_local_sources = sorted(
                after.assertion_local_sources - before.assertion_local_sources
            )
            new_substitutions = sorted(
                after.substitution_signals - before.substitution_signals
            )
            if new_local_sources or new_substitutions:
                findings.append(
                    StructuralFinding(
                        F_LOCAL_SUBSTITUTE,
                        path,
                        "assertion now depends on a candidate-local substitute: "
                        + ", ".join(new_local_sources + new_substitutions),
                    )
                )
            inflated = sorted(
                key
                for key, value in after.max_inflatable.items()
                if value > before.max_inflatable.get(key, 0.0)
            )
            if inflated:
                findings.append(
                    StructuralFinding(
                        F_TIMEOUT_INFLATED,
                        path,
                        "raised without evidence: " + ", ".join(inflated),
                    )
                )
            introduced = (after.comparison_literals - before.comparison_literals) & actual_values
            if introduced:
                findings.append(
                    StructuralFinding(
                        F_EXPECTED_TO_ACTUAL,
                        path,
                        "expected value rewritten to the observed actual: "
                        + ", ".join(sorted(introduced)),
                    )
                )
            semantic_actual = _semantic_expected_to_actual(
                before_text, after_text, failure_log
            )
            if semantic_actual and not introduced:
                findings.append(
                    StructuralFinding(
                        F_EXPECTED_TO_ACTUAL,
                        path,
                        "; ".join(semantic_actual),
                    )
                )
            if (
                after.assertions == before.assertions
                and after.comparison_literals == before.comparison_literals
                and after.call_arguments != before.call_arguments
            ):
                findings.append(
                    StructuralFinding(
                        F_INPUT_CHANGED,
                        path,
                        "the assertions are unchanged but the inputs moved; the failing "
                        "path may no longer be triggered",
                    )
                )

        # Textual checks apply to every file type, including non-Python assets.
        if _NOT_SUCCESS_RE.search(after_text) and not _NOT_SUCCESS_RE.search(before_text):
            findings.append(
                StructuralFinding(
                    F_NOT_SUCCESS,
                    path,
                    "asserting only that the outcome is not the success value hides "
                    "which layer rejected the request",
                )
            )
        if _COLLECTION_RE.search(after_text) and not _COLLECTION_RE.search(before_text):
            findings.append(
                StructuralFinding(
                    F_COLLECTION_NARROWED, path, "the candidate narrows test collection"
                )
            )

    return findings


# --------------------------------------------------------------------------
# Independent review document
# --------------------------------------------------------------------------


def _valid_review_binding(binding: Any) -> bool:
    """Return whether ``binding`` has the complete v1 evidence identity.

    Exact keys prevent a producer and consumer from silently disagreeing about
    which evidence was reviewed.  Lower-case sha256 values are required so the
    serialized document has one canonical representation.  Oracle references
    are compared as a set later, but duplicates are rejected here because they
    otherwise make an apparently richer review package without adding evidence.
    """

    if not isinstance(binding, dict) or set(binding) != REVIEW_BINDING_FIELDS:
        return False
    for field in ("uc", "attempt_id"):
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip():
            return False
    for field in REVIEW_BINDING_SHA256_FIELDS:
        value = binding.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            return False
    oracle_refs = binding.get("oracle_refs")
    if (
        not isinstance(oracle_refs, list)
        or not oracle_refs
        or any(not isinstance(ref, str) or not ref.strip() for ref in oracle_refs)
        or len(oracle_refs) != len(set(oracle_refs))
    ):
        return False
    return True


def validate_review_document(
    document: Any,
    *,
    expected: ReviewExpectation | None = None,
    expected_reviewer_runtime: str | None = None,
) -> tuple[str, list[str]]:
    """Return ``(verdict, blocking_reasons)`` for an independent review file.

    A review that did not really run is not a review.  The degraded dispatch
    modes are the same ones ``sdtd_orchestrator.peer_review_degraded`` already
    treats as fail-worthy for Wave 1 / Stage 3B, reused here so the two peer
    controls agree on what "the second agent actually looked" means.  The
    complete ``binding`` object is mandatory even when ``expected`` is omitted;
    passing ``expected`` additionally proves that the verdict names the exact
    current attempt, candidate bytes, failure, verification, falsification, and
    oracle evidence rather than a stale or fabricated package.
    """

    reasons: list[str] = []
    if not isinstance(document, dict):
        return "blocked", [R_REVIEW_MALFORMED]
    if document.get("schema") != REVIEW_SCHEMA:
        reasons.append(R_REVIEW_SCHEMA)
    verdict_value = document.get("verdict")
    verdict = verdict_value if isinstance(verdict_value, str) else ""
    if verdict not in REVIEW_VERDICTS:
        reasons.append(R_REVIEW_VERDICT)
        verdict = "blocked"
    dispatch = document.get("dispatch_mode")
    if not isinstance(dispatch, str) or dispatch not in KNOWN_REVIEW_DISPATCH_MODES:
        reasons.append(R_REVIEW_DISPATCH_INVALID)
    elif dispatch not in TRUSTED_REVIEW_DISPATCH_MODES:
        reasons.append(R_REVIEW_DEGRADED)
    runtime = document.get("runtime")
    if not isinstance(runtime, str) or runtime not in KNOWN_REVIEW_RUNTIMES:
        reasons.append(R_REVIEW_RUNTIME_INVALID)
    elif (
        expected_reviewer_runtime is not None
        and runtime != expected_reviewer_runtime
    ):
        reasons.append(R_REVIEW_RUNTIME_MISMATCH)
    findings = document.get("findings")
    if (
        not isinstance(findings, list)
        or not findings
        or any(
            not isinstance(finding, dict)
            or set(finding) != REVIEW_FINDING_FIELDS
            or any(
                not isinstance(finding.get(field), str)
                or not finding[field].strip()
                for field in REVIEW_FINDING_FIELDS
            )
            for finding in findings
        )
    ):
        reasons.append(R_REVIEW_FINDINGS)
    residual_risks = document.get("residual_risks")
    if (
        not isinstance(residual_risks, list)
        or any(
            not isinstance(risk, str) or not risk.strip()
            for risk in residual_risks
        )
    ):
        reasons.append(R_REVIEW_RESIDUAL)

    if "binding" not in document:
        reasons.append(R_REVIEW_BINDING_MISSING)
    else:
        binding = document.get("binding")
        if not _valid_review_binding(binding):
            reasons.append(R_REVIEW_BINDING_INVALID)
        elif expected is not None:
            assert isinstance(binding, dict)  # established by _valid_review_binding
            if binding["uc"] != expected.uc:
                reasons.append(R_REVIEW_UC_MISMATCH)
            if binding["attempt_id"] != expected.attempt_id:
                reasons.append(R_REVIEW_ATTEMPT_MISMATCH)
            if (
                binding["candidate_manifest_sha256"]
                != expected.candidate_manifest_sha256
                or binding["candidate_patch_sha256"]
                != expected.candidate_patch_sha256
            ):
                reasons.append(R_REVIEW_CANDIDATE_MISMATCH)
            if binding["precode_evidence_sha256"] != expected.precode_evidence_sha256:
                reasons.append(R_REVIEW_PRECODE_MISMATCH)
            if binding["original_failure_sha256"] != expected.original_failure_sha256:
                reasons.append(R_REVIEW_FAILURE_MISMATCH)
            if binding["verification_sha256"] != expected.verification_sha256:
                reasons.append(R_REVIEW_VERIFICATION_MISMATCH)
            if binding["before_log_sha256"] != expected.before_log_sha256:
                reasons.append(R_REVIEW_BEFORE_LOG_MISMATCH)
            if binding["after_log_sha256"] != expected.after_log_sha256:
                reasons.append(R_REVIEW_AFTER_LOG_MISMATCH)
            if binding["falsification_sha256"] != expected.falsification_sha256:
                reasons.append(R_REVIEW_FALSIFICATION_MISMATCH)
            if sorted(binding["oracle_refs"]) != sorted(expected.oracle_refs):
                reasons.append(R_REVIEW_ORACLE_MISMATCH)

    body = str(document)
    if "fallback_placeholder" in body or "TBD" in body:
        if R_REVIEW_DEGRADED not in reasons:
            reasons.append(R_REVIEW_DEGRADED)
    return verdict, reasons
