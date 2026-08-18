#!/usr/bin/env python3
"""Evidence-first failure attribution: ``bugate.failure-triage/v1``.

This module answers one question -- *who owns this failure* -- and it answers it
fail-closed.  It never widens a verdict to keep a flow moving: an ambiguous log
becomes ``insufficient_evidence`` with ``healing_eligible: false``, not a guess.

Relationship to :mod:`self_healing_mvp`
--------------------------------------
The v0.4.4 ``PATTERNS`` classifier stays exactly where it is and keeps producing
the seven legacy top-level keys.  This module is a strictly *additional*
discriminator that only runs when ``self_healing.mode != off``; the two never
share state and never rewrite each other's output.  That separation is what
makes the ``off`` path byte-identical to v0.4.4 (stage-2 contract section 3.2).

Why the legacy patterns could not simply be fixed in place
----------------------------------------------------------
``PATTERNS`` matches bare substrings with no word boundaries, so
``environment_or_resource`` fires on any log containing the letters
"connection" -- including a debug line about a *pooled* connection -- and
``sut_behavior_failure`` fires on any log containing "status code".  Both then
co-fire, and ``sut_defect_admissible`` is permanently ``False``.  Every rule
here is anchored, and every rule carries a counter-example test proving it does
not fire on the ordinary log text that tripped its predecessor.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from bugate_core import gate_status, read_text, rel


SCHEMA = "bugate.failure-triage/v1"

FAILURE_OWNERS = ("environment", "test_asset", "sut", "unresolved")
FAILURE_SUBTYPES = (
    "dependency_or_import",
    "fixture_or_test_data",
    "selector_or_mock",
    "test_code_or_assertion",
    "environment_or_resource",
    "auth_or_precondition",
    "flaky_or_transient",
    "sut_behavior_violation",
    "insufficient_evidence",
)

#: Subtype -> owner.  A subtype never maps to more than one owner, except
#: ``dependency_or_import``, which is resolved by :func:`_dependency_owner`
#: from the evidence in the log (a workspace-local module is a test asset; a
#: third-party distribution is the environment).
SUBTYPE_OWNER = {
    "environment_or_resource": "environment",
    "auth_or_precondition": "unresolved",
    "dependency_or_import": "test_asset",
    "fixture_or_test_data": "test_asset",
    "selector_or_mock": "test_asset",
    "test_code_or_assertion": "test_asset",
    "flaky_or_transient": "unresolved",
    "sut_behavior_violation": "sut",
    "insufficient_evidence": "unresolved",
}

#: Exclusion-first precedence.  Everything that could have stopped the request
#: before the business layer is ruled out before a SUT verdict is even
#: considered, and the ambiguous fallback is last.
PRECEDENCE = (
    "environment_or_resource",
    "auth_or_precondition",
    "dependency_or_import",
    "fixture_or_test_data",
    "selector_or_mock",
    "flaky_or_transient",
    "test_code_or_assertion",
    "sut_behavior_violation",
    "insufficient_evidence",
)

#: Categories whose presence proves the request never reached the target
#: business layer, so no SUT-behavior verdict may be drawn from this run.
PRE_TARGET_LAYER = (
    "environment_or_resource",
    "auth_or_precondition",
    "dependency_or_import",
    "fixture_or_test_data",
    "selector_or_mock",
)

# Stable blocking reason codes.  Tests assert these exact strings.
REASON_NO_LOG = "no_runner_log"
REASON_NO_FAILURE = "no_failure_to_heal"
REASON_ENVIRONMENT = "environment_owner_not_healable"
REASON_TARGET_LAYER = "target_layer_not_reached"
REASON_SUT_DEFECT = "sut_defect_not_healable"
REASON_FLAKY = "flaky_evidence_insufficient"
REASON_INSUFFICIENT = "insufficient_evidence"
REASON_ORACLE_BINDING = "oracle_binding_missing"
REASON_LOW_CONFIDENCE = "low_confidence_classification"


# --------------------------------------------------------------------------
# Anchored evidence rules
#
# Each entry is (rule_id, compiled pattern, strength).  "strong" means the
# marker is unambiguous on its own; "weak" means it only contributes when no
# strong marker of another category outranks it, and it caps confidence at
# medium.
# --------------------------------------------------------------------------

def _rules(*items: tuple[str, str, str]) -> tuple[tuple[str, re.Pattern[str], str], ...]:
    return tuple((rule_id, re.compile(pattern), strength) for rule_id, pattern, strength in items)


ENVIRONMENT_RULES = _rules(
    ("connection_refused", r"\bConnectionRefusedError\b|\bConnection refused\b", "strong"),
    ("connection_reset", r"\bConnectionResetError\b|\bConnection reset by peer\b", "strong"),
    ("connection_error", r"\bConnectionError\b|\bNewConnectionError\b", "strong"),
    ("socket_timeout", r"\bsocket\.timeout\b|\bTimeoutError\b|\bReadTimeout\b|\bConnectTimeout\b", "strong"),
    ("read_timed_out", r"\bRead timed out\b|\btimed out after\b", "strong"),
    ("dns_failure", r"\bTemporary failure in name resolution\b|\bName or service not known\b|\bgetaddrinfo failed\b", "strong"),
    ("disk_exhausted", r"\bNo space left on device\b|\[Errno 28\]|\bdisk quota exceeded\b", "strong"),
    ("port_in_use", r"\bAddress already in use\b|\[Errno 48\]|\[Errno 98\]", "strong"),
    ("os_permission", r"\bPermissionError\b|\[Errno 13\]", "strong"),
    ("db_unavailable", r"\bOperationalError\b|\bcould not connect to server\b", "strong"),
    ("gateway_status", r"\b(?:502|503|504)\s+(?:Bad Gateway|Service Unavailable|Gateway Time-?out)\b", "strong"),
    ("resource_exhausted", r"\bMemoryError\b|\bOSError: \[Errno 24\]\b|\bToo many open files\b", "strong"),
)

AUTH_RULES = _rules(
    # Status semantics are line-scoped rather than proximity-scoped.  A runner
    # is free to put diagnostics between ``status`` and the code; the old
    # ``\D{0,24}`` window silently lost that evidence.  ``response`` alone is
    # deliberately not sufficient -- "response contained 403 records" is a
    # byte/count observation, not an authentication result.
    (
        "auth_status",
        r"(?im)^(?=[^\n]*(?:\bstatus(?:_code)?\b|\bHTTP(?:/\d(?:\.\d)?)?\b))"
        r"(?=[^\n]*\b(?:401|403|412)\b)[^\n]*$",
        "strong",
    ),
    # Pytest often leaves no HTTP token in the comparison line.  Comparing an
    # authentication/precondition code with a 2xx success code is still
    # evidence that the request stopped before the intended success path.  A
    # positive, machine-readable target-layer marker suppresses this heuristic
    # in :func:`_matched`; see ``_AUTH_CODE_INFERENCE_RULES`` below.
    (
        "auth_code_assertion",
        r"(?m)^[^\n]*\bassert\s+"
        r"(?=[^\n]*\b(?:401|403|412)\b)(?=[^\n]*\b2\d\d\b)"
        r"[^\n]*(?:==|!=|<=|>=|<|>)[^\n]*$",
        "strong",
    ),
    ("auth_response_repr", r"<Response\s*\[(?:401|403|412)\]>", "strong"),
    (
        "auth_returned_code",
        r"(?i)\b(?:gateway|server|request|endpoint)\b[^\n]{0,120}"
        r"\b(?:returned|responded\s+with|rejected\s+with)\s+(?:401|403|412)\b",
        "strong",
    ),
    ("auth_reason", r"\b(?:401|403|412)\s+(?:Unauthorized|Forbidden|Precondition Failed)\b", "strong"),
    ("auth_word", r"\bUnauthorized\b|\bForbidden\b|\bAuthenticationError\b|\bAuthenticationFailed\b|\bNotAuthenticated\b|\bPermissionDenied\b", "strong"),
    ("signature", r"\binvalid signature\b|\bsignature mismatch\b|\bsignature verification failed\b", "strong"),
    ("expired_token", r"\btoken (?:has )?expired\b|\bexpired token\b|\bTokenExpired\w*\b", "strong"),
    ("precondition", r"\bprecondition (?:failed|not met)\b|\bPreconditionFailed\b", "strong"),
    ("missing_auth_header", r"\bmissing (?:required )?(?:auth|authorization|api[_ -]?key)\b", "strong"),
)

DEPENDENCY_RULES = _rules(
    ("module_not_found", r"\bModuleNotFoundError\b", "strong"),
    ("import_error", r"\bImportError\b", "strong"),
    ("distribution_missing", r"\bDistributionNotFound\b|\bPackageNotFoundError\b", "strong"),
)

FIXTURE_RULES = _rules(
    ("fixture_missing", r"\bfixture ['\"]?[\w.-]+['\"]? not found\b", "strong"),
    ("fixture_direct_call", r"\bfixture ['\"][\w.-]+['\"] called directly\b", "strong"),
    ("scope_mismatch", r"\bScopeMismatch\b", "strong"),
    ("setup_error", r"\bERROR at setup of\b|\berror(?:s)? during collection\b", "strong"),
    ("testdata_missing", r"\bFileNotFoundError\b[^\n]*(?:fixtures?|test[_-]?data|golden)[/\\]", "strong"),
    ("testdata_parse", r"\b(?:yaml|json)[\w.]*(?:Parser|Scanner|Decode)Error\b[^\n]*(?:fixtures?|test[_-]?data)[/\\]", "strong"),
    ("testdata_key", r"\bKeyError\b[^\n]*(?:fixtures?|test[_-]?data)\b", "weak"),
)

SELECTOR_MOCK_RULES = _rules(
    ("selenium_missing", r"\bNoSuchElementException\b|\bElementNotInteractableException\b|\bElementClickInterceptedException\b", "strong"),
    ("locator_unresolved", r"\b(?:selector|locator)\b[^\n]*\b(?:did not match|not found|resolved to 0)\b", "strong"),
    ("mock_attribute", r"\bMock object has no attribute\b|\bdoes not have the attribute\b", "strong"),
    ("mock_not_called", r"\bExpected ['\"][^'\"]+['\"] to have been called\b|\bExpected call\b|\bAssertionError: Expected mock\b", "strong"),
    ("mock_side_effect", r"\bStopIteration\b[^\n]*\bside_effect\b|\bside_effect\b[^\n]*\bStopIteration\b", "strong"),
    ("mock_assert_typo", r"\bassert_(?:called_once|called_with|any_call)\b[^\n]*\bAttributeError\b", "weak"),
)

FLAKY_RULES = _rules(
    ("flaky_marker", r"\bflaky\b|\bRERUN\b|\bpytest-rerunfailures\b", "strong"),
    ("retry_marker", r"\bretrying\b|\bretry \d+\b|\battempt \d+ of \d+\b|\bpassed on (?:retry|rerun)\b", "strong"),
    ("intermittent", r"\bintermittent(?:ly)?\b|\bnon-?deterministic\b", "weak"),
)

TEST_CODE_RULES = _rules(
    # Unambiguous Python-level defects: these cannot be caused by the SUT.
    ("name_error", r"\bNameError\b|\bUnboundLocalError\b", "strong"),
    ("syntax_error", r"\bSyntaxError\b|\bIndentationError\b|\bTabError\b", "strong"),
    ("recursion", r"\bRecursionError\b|\bZeroDivisionError\b", "strong"),
    # Ambiguous on their own.  A test frame proves where a bad SUT value was
    # consumed, not who produced it.  These are retained in ``matched_rules``
    # as manifestation evidence but are excluded from ownership unless another
    # independent test-asset rule also fires (see :func:`triage`).
    ("type_error_in_test", r"\bTypeError\b", "frame"),
    ("attribute_error_in_test", r"\bAttributeError\b", "frame"),
)

SUT_RULES = _rules(
    ("assertion_error", r"\bAssertionError\b", "strong"),
    ("pytest_assert", r"(?m)^E\s+assert\b", "strong"),
    ("expected_got", r"\bexpected\b[^\n]{0,80}\bgot\b", "weak"),
)

CATEGORY_RULES: dict[str, tuple[tuple[str, re.Pattern[str], str], ...]] = {
    "environment_or_resource": ENVIRONMENT_RULES,
    "auth_or_precondition": AUTH_RULES,
    "dependency_or_import": DEPENDENCY_RULES,
    "fixture_or_test_data": FIXTURE_RULES,
    "selector_or_mock": SELECTOR_MOCK_RULES,
    "flaky_or_transient": FLAKY_RULES,
    "test_code_or_assertion": TEST_CODE_RULES,
    "sut_behavior_violation": SUT_RULES,
}

_TRACEBACK_FRAME_RE = re.compile(
    r'(?m)^(?:\s*File "(?P<quoted>[^"]+)", line \d+|(?P<bare>[\w./\\-]+\.py):\d+:)'
)
_TEST_PATH_RE = re.compile(r"(?:^|[/\\])(?:tests?|testing)[/\\]|(?:^|[/\\])test_[^/\\]*\.py$|_test\.py$")
_MOCK_CONTEXT_RE = re.compile(r"\bunittest\.mock\b|\bmock\.patch\b|\b_patch\b|\bpatch\(")
_MODULE_NOT_FOUND_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_IMPORT_NAME_RE = re.compile(r"cannot import name ['\"][\w]+['\"] from ['\"]([\w.]+)['\"]")
_ORACLE_ID_RE = re.compile(r"\bO-[A-Za-z0-9][A-Za-z0-9_.-]*")
_ORACLE_REFS_BLOCK_RE = re.compile(r"(?m)^(?P<indent>\s*)oracle_refs:\s*$")
_AUTH_CODE_INFERENCE_RULES = frozenset(
    {
        "auth_code_assertion",
        "auth_response_repr",
        "auth_returned_code",
    }
)
_TARGET_LAYER_POSITIVE_RE = re.compile(
    r"(?im)\b(?:target_layer_reached|business_handler_reached)\s*[:=]\s*true\b"
    r"|\b(?:entered|reached)\s+(?:the\s+)?target\s+(?:business\s+)?(?:handler|layer)\b"
)


# --------------------------------------------------------------------------
# Evidence helpers
# --------------------------------------------------------------------------


def _deepest_frame_is_test(log: str) -> bool:
    """True when the last traceback frame in the log points at a test file.

    This locates the *manifestation* only.  A SUT can return ``null`` or an
    object with the wrong shape and make ``TypeError``/``AttributeError`` erupt
    on the following assertion line.  Consequently this helper must never, by
    itself, decide ownership.
    """

    frames = [
        match.group("quoted") or match.group("bare")
        for match in _TRACEBACK_FRAME_RE.finditer(log)
    ]
    if not frames:
        return False
    return bool(_TEST_PATH_RE.search(frames[-1].replace("\\", "/")))


def _mock_context(log: str) -> bool:
    return bool(_MOCK_CONTEXT_RE.search(log))


def _dependency_owner(log: str, root: Path | None) -> tuple[str, str]:
    """Split an import failure into test-asset vs environment by evidence.

    A module that exists in the workspace but fails to import is a broken test
    asset.  A module that does not exist in the workspace at all is a missing
    distribution -- an environment problem no test edit may paper over.
    """

    module = ""
    found = _MODULE_NOT_FOUND_RE.search(log) or _IMPORT_NAME_RE.search(log)
    if found:
        module = found.group(1)
    if not module:
        return "test_asset", "import_target_unnamed"
    top = module.split(".", 1)[0]
    if root is None:
        return "unresolved", "import_target_unresolvable"
    for candidate in (root / f"{top}.py", root / top, root / "src" / top, root / "tests" / top):
        if candidate.exists():
            return "test_asset", f"workspace_module:{top}"
    return "environment", f"distribution_absent:{top}"


def _matched(log: str, category: str) -> list[str]:
    """Rule ids of ``category`` that fire on ``log`` (frame rules gated).

    Code-only auth inference is fail-closed when the log lacks positive target
    layer evidence.  Conversely, an explicit target-layer marker suppresses
    only those inference rules; direct evidence such as ``Unauthorized`` or a
    signature failure remains an auth/precondition finding.
    """

    hits: list[str] = []
    for rule_id, pattern, strength in CATEGORY_RULES[category]:
        if not pattern.search(log):
            continue
        if strength == "frame" and not _deepest_frame_is_test(log):
            continue
        if (
            category == "auth_or_precondition"
            and rule_id in _AUTH_CODE_INFERENCE_RULES
            and _TARGET_LAYER_POSITIVE_RE.search(log)
        ):
            continue
        hits.append(rule_id)
    return hits


def _strengths(category: str, hits: Iterable[str]) -> set[str]:
    rules = CATEGORY_RULES.get(category, ())
    lookup = {rule_id: strength for rule_id, _pattern, strength in rules}
    return {lookup[rule_id] for rule_id in hits if rule_id in lookup}


def accepted_oracle_refs(artifact_dir: Path) -> tuple[list[str], str]:
    """Oracle ids this UC may cite, plus a blocking reason when unusable.

    A SUT-behavior verdict must name an oracle that a *human-accepted* Layer 1
    brief declares and that the accepted inventory actually references.  An
    inventory citing an oracle the brief never declared is a traceability break,
    not a usable binding.
    """

    brief = artifact_dir / "01_business_brief.md"
    inventory = artifact_dir / "03_inventory.yaml"
    if not brief.exists() or not inventory.exists():
        return [], REASON_ORACLE_BINDING
    if gate_status(brief) != "passed" or gate_status(inventory) != "passed":
        return [], REASON_ORACLE_BINDING

    declared: set[str] = set()
    in_oracles = False
    for line in read_text(brief).splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_oracles = stripped.lower().startswith("## business oracles")
            continue
        if in_oracles and stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and _ORACLE_ID_RE.fullmatch(cells[0] or ""):
                declared.add(cells[0])

    referenced: set[str] = set()
    inventory_lines = read_text(inventory).splitlines()
    for index, line in enumerate(inventory_lines):
        block = _ORACLE_REFS_BLOCK_RE.match(line)
        if not block:
            continue
        indent = len(block.group("indent"))
        for follower in inventory_lines[index + 1 :]:
            if not follower.strip():
                continue
            follower_indent = len(follower) - len(follower.lstrip(" "))
            if follower_indent <= indent or not follower.lstrip().startswith("- "):
                break
            match = _ORACLE_ID_RE.search(follower)
            if match:
                referenced.add(match.group(0))

    if not declared or not referenced:
        return [], REASON_ORACLE_BINDING
    if not referenced <= declared:
        return [], REASON_ORACLE_BINDING
    return sorted(referenced), ""


def sha256_text(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attempt_id(uc: str, log_sha256: str, command: str, exit_code: int | None) -> str:
    """Deterministic attempt identity, so a repeated triage is idempotent.

    The same UC failing the same way with the same command yields the same
    attempt id, which is what makes ``resume_interrupted_attempt`` able to find
    its own half-finished work instead of opening a duplicate attempt.
    """

    seed = "\0".join([uc, log_sha256, command, str(exit_code)])
    return "att-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------


def triage(
    log: str,
    exit_code: int | None,
    *,
    artifact_dir: Path,
    root: Path,
    uc: str,
    command: str = "",
    log_path: Path | None = None,
    overall: str = "failed",
) -> dict[str, Any]:
    """Return the ``bugate.failure-triage/v1`` fields for one runner outcome.

    The returned mapping is *appended* to the legacy classifier output; it never
    contains any of the seven legacy keys.
    """

    log_sha = sha256_text(log)
    evidence_refs: list[dict[str, str]] = []
    if log_path is not None and log_path.exists():
        evidence_refs.append(
            {"path": rel(log_path, root), "sha256": sha256_path(log_path)}
        )
    for name in ("01_business_brief.md", "03_inventory.yaml"):
        candidate = artifact_dir / name
        if candidate.exists():
            evidence_refs.append(
                {"path": rel(candidate, root), "sha256": sha256_path(candidate)}
            )

    oracle_refs, oracle_reason = accepted_oracle_refs(artifact_dir)
    blocking: list[str] = []
    owner_evidence = ""

    if overall == "no_log":
        subtype, owner, confidence = "insufficient_evidence", "unresolved", "low"
        blocking.append(REASON_NO_LOG)
        matched_categories: dict[str, list[str]] = {}
        target_layer_reached = False
        action = "capture the runner log for this UC before any triage or repair"
    elif overall == "passed":
        subtype, owner, confidence = "insufficient_evidence", "unresolved", "low"
        blocking.append(REASON_NO_FAILURE)
        matched_categories = {}
        target_layer_reached = True
        action = "no failure to attribute; keep assertions unchanged"
    else:
        matched_categories = {
            category: hits
            for category in CATEGORY_RULES
            if (hits := _matched(log, category))
        }
        # A mock/patch traceback that raises ModuleNotFoundError is a mock wiring
        # defect, not a missing distribution: drop the dependency reading when the
        # only import evidence sits inside a mock context.
        if (
            "dependency_or_import" in matched_categories
            and "selector_or_mock" in matched_categories
            and _mock_context(log)
        ):
            matched_categories.pop("dependency_or_import")

        # A traceback ending in a test file says only where the incompatible
        # value was consumed.  If TypeError/AttributeError is the *only*
        # test-code evidence, attributing it to the test asset would authorize
        # an assertion rewrite that can hide a value/shape defect in the SUT.
        # Keep the rule in ``matched_rules`` for auditability, but remove it
        # from the categories used to choose owner/subtype.
        attribution_categories = dict(matched_categories)
        test_code_hits = matched_categories.get("test_code_or_assertion", [])
        if test_code_hits and _strengths("test_code_or_assertion", test_code_hits) <= {"frame"}:
            attribution_categories.pop("test_code_or_assertion", None)
            owner_evidence = "test_frame_is_manifestation_only"

        target_layer_reached = not any(
            category in attribution_categories for category in PRE_TARGET_LAYER
        )
        # A hard test-code crash (NameError, SyntaxError, ...) aborts the test
        # body, so the request never reached the business layer either.
        if target_layer_reached and {"name_error", "syntax_error", "recursion"} & set(
            matched_categories.get("test_code_or_assertion", [])
        ):
            target_layer_reached = False
        subtype = next(
            (
                category
                for category in PRECEDENCE
                if category in attribution_categories
            ),
            "insufficient_evidence",
        )

        # A SUT-behavior verdict is the most consequential one this module can
        # draw, so it carries the most preconditions: the target layer must have
        # been reached and an accepted oracle must be bound.  Otherwise the
        # honest answer is that the evidence does not support any verdict --
        # never that the test is at fault.
        if subtype == "sut_behavior_violation":
            if not target_layer_reached:
                subtype = "insufficient_evidence"
            elif oracle_reason:
                subtype = "insufficient_evidence"
                blocking.append(oracle_reason)

        owner = SUBTYPE_OWNER[subtype]
        if subtype == "dependency_or_import":
            owner, owner_evidence = _dependency_owner(log, root)

        strengths = _strengths(subtype, matched_categories.get(subtype, []))
        competing_owners = {
            SUBTYPE_OWNER[category]
            for category in attribution_categories
            if category != subtype
        } - {owner}
        if subtype == "insufficient_evidence":
            confidence = "low"
        elif "strong" in strengths and not competing_owners:
            confidence = "high"
        elif "strong" in strengths or "frame" in strengths:
            confidence = "medium"
        else:
            confidence = "low"

        action = _recommended_action(subtype, owner)

    if owner == "environment":
        blocking.append(REASON_ENVIRONMENT)
    elif owner == "sut":
        blocking.append(REASON_SUT_DEFECT)
    elif subtype == "auth_or_precondition":
        blocking.append(REASON_TARGET_LAYER)
    elif subtype == "flaky_or_transient":
        blocking.append(REASON_FLAKY)
    elif owner == "unresolved" and not blocking:
        blocking.append(REASON_INSUFFICIENT)
    if owner == "test_asset" and confidence == "low":
        blocking.append(REASON_LOW_CONFIDENCE)

    # Deduplicate while preserving first-seen order: the first reason is the
    # primary one a report shows.
    blocking = list(dict.fromkeys(blocking))
    healing_eligible = owner == "test_asset" and not blocking

    return {
        "schema_version": SCHEMA,
        "attempt_id": attempt_id(uc, log_sha, command, exit_code),
        "artifact_dir": rel(artifact_dir, root),
        "uc": uc,
        "original_command": command,
        "original_exit_code": exit_code,
        "original_log_sha256": log_sha,
        "evidence_refs": evidence_refs,
        "failure_owner": owner,
        "failure_subtype": subtype,
        "confidence": confidence,
        "exclusions_checked": list(PRE_TARGET_LAYER),
        "matched_rules": {
            category: sorted(hits) for category, hits in sorted(matched_categories.items())
        },
        "owner_evidence": owner_evidence,
        "target_layer_reached": target_layer_reached,
        "oracle_refs": oracle_refs,
        "healing_eligible": healing_eligible,
        "blocking_reasons": blocking,
        "recommended_action": action,
    }


def _recommended_action(subtype: str, owner: str) -> str:
    if owner == "environment":
        return (
            "restore the environment or resource, then re-run; do NOT edit any "
            "test asset for this failure"
        )
    if subtype == "auth_or_precondition":
        return (
            "the request was rejected before the target business layer; fix the "
            "auth/signature/precondition setup and re-run before drawing any verdict"
        )
    if subtype == "flaky_or_transient":
        return "re-run to collect repeat evidence; do not modify the test on one observation"
    if owner == "sut":
        return (
            "keep the failing assertion, record a defect draft against the bound "
            "oracle, and add a named regression case"
        )
    if owner == "test_asset":
        return f"test-asset defect ({subtype}); a repair candidate may be proposed under review"
    return "insufficient evidence for any attribution; collect more evidence and re-run"
