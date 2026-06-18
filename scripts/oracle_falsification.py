#!/usr/bin/env python3
"""Wave 8 oracle-falsification engine (SUT-neutral, declarative).

Take pristine captured evidence (JSON), mutate it field by field per a
declarative spec, run declarative oracles offline, and score killed/survived.

Semantics (generic, ported from the parent SDTD executor):
  - A falsification case = (evidence file, mutation). It is KILLED when at least
    one bound oracle fails (assertion_fail) or errors (crash) on the mutated
    evidence; it SURVIVES only when every oracle still passes.
  - Baseline discipline: every oracle must pass on the pristine evidence first,
    otherwise that evidence file is recorded not_run (baseline_failed) — a kill
    claim is only valid against evidence the oracle accepts when correct.
  - Score = killed / (killed + survived).

The engine is in core; the ORACLES, MUTATIONS, and evidence glob come from a
SUT-supplied declarative spec (``--spec`` or config ``falsification_spec``) — no
SUT code is imported. Without a spec, the run degrades to status=profile_required
(the pre-spec behavior), so the core stays usable unmounted.

The Markdown/JSON output embeds the score + killed/survived so
``generate_assertion_coverage_matrix.py --mutation-result`` can flip the Wave 8
audit from not_run to a real score. Offline only: never calls live APIs, never
edits tests.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
from pathlib import Path
from typing import Any

from bugate_core import dump_json, load_config, parse_nested_yaml, read_text, write_text


# ---------------------------------------------------------------------------
# Field-path helpers (generic; operate on any nested dict)
# ---------------------------------------------------------------------------
def _split(path: str) -> list[str]:
    return [p for p in str(path).split(".") if p != ""]


def _get(d: Any, path: str) -> tuple[bool, Any]:
    node = d
    for key in _split(path):
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


def _set(d: dict, path: str, value: Any) -> tuple[bool, str]:
    keys = _split(path)
    node = d
    for key in keys[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return False, "path not present"
    leaf = keys[-1]
    if not isinstance(node, dict) or leaf not in node:
        return False, "field not present"
    before = node[leaf]
    node[leaf] = value
    return True, f"{path}: {before!r} -> {value!r}"


def _delete(d: dict, path: str) -> tuple[bool, str]:
    keys = _split(path)
    node = d
    for key in keys[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return False, "path not present"
    leaf = keys[-1]
    if not isinstance(node, dict) or leaf not in node:
        return False, "field not present"
    before = node.pop(leaf)
    return True, f"{path}: {before!r} -> <deleted>"


def _numeric_drift(d: dict, path: str, delta: Any) -> tuple[bool, str]:
    found, raw = _get(d, path)
    if not found or raw is None:
        return False, "field not present"
    try:
        mutated = type(raw)(raw + type(raw)(delta)) if not isinstance(raw, str) else str(float(raw) + float(delta))
    except (TypeError, ValueError):
        return False, "field not numeric"
    return _set(d, path, mutated)


# ---------------------------------------------------------------------------
# Declarative oracle assertions
# ---------------------------------------------------------------------------
_TYPE_NAMES = {
    "str": str, "string": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": dict, "number": (int, float),
}


def _eq(actual: Any, expected: Any) -> bool:
    # Spec scalars arrive as strings (the stdlib YAML subset does not coerce
    # numbers), while JSON evidence carries real ints/floats — compare tolerantly.
    return actual == expected or str(actual) == str(expected)


def _check(op: str, found: bool, actual: Any, expected: Any) -> tuple[bool, str]:
    op = (op or "").strip().lower()
    if op == "present":
        return (found and actual is not None), "missing or null"
    if op == "absent":
        return (not found or actual is None), f"unexpectedly present: {actual!r}"
    if op == "nonempty":
        return (found and bool(actual) and (not hasattr(actual, "__len__") or len(actual) > 0)), "empty or missing"
    if not found:
        return False, "field not present"
    if op == "equals":
        return _eq(actual, expected), f"{actual!r} != {expected!r}"
    if op == "not_equals":
        return (not _eq(actual, expected)), f"{actual!r} == {expected!r} (must differ)"
    if op == "in":
        seq = expected if isinstance(expected, list) else [expected]
        return any(_eq(actual, x) for x in seq), f"{actual!r} not in {seq!r}"
    if op == "not_in":
        seq = expected if isinstance(expected, list) else [expected]
        return (not any(_eq(actual, x) for x in seq)), f"{actual!r} in {seq!r} (forbidden)"
    if op in {"gt", "gte", "lt", "lte"}:
        a, e = float(actual), float(expected)
        ok = {"gt": a > e, "gte": a >= e, "lt": a < e, "lte": a <= e}[op]
        return ok, f"{actual!r} fails {op} {expected!r}"
    if op == "type":
        want = _TYPE_NAMES.get(str(expected).strip().lower())
        if want is None:
            return False, f"unknown type {expected!r}"
        return isinstance(actual, want), f"{type(actual).__name__} is not {expected}"
    if op == "regex":
        return (re.search(str(expected), str(actual)) is not None), f"{actual!r} !~ /{expected}/"
    return False, f"unknown op {op!r}"


def run_oracle(oracle: dict, payload: dict) -> dict[str, str]:
    name = str(oracle.get("name") or oracle.get("id") or "oracle")
    try:
        for spec in oracle.get("assert") or []:
            if not isinstance(spec, dict):
                continue
            found, actual = _get(payload, spec.get("path", ""))
            ok, detail = _check(spec.get("op", "present"), found, actual, spec.get("value"))
            if not ok:
                return {"oracle": name, "outcome": "assertion_fail", "detail": f"{spec.get('path')}: {detail}"[:300]}
        return {"oracle": name, "outcome": "pass", "detail": ""}
    except Exception as exc:  # crash kill: still fails the run, flagged for hardening
        return {"oracle": name, "outcome": "crash", "detail": f"{type(exc).__name__}: {exc}"[:300]}


def apply_mutation(mutation: dict, payload: dict) -> tuple[bool, str]:
    op = str(mutation.get("op", "set")).strip().lower()
    path = mutation.get("path", "")
    if op == "set":
        return _set(payload, path, mutation.get("value"))
    if op == "delete":
        return _delete(payload, path)
    if op == "numeric_drift":
        return _numeric_drift(payload, path, mutation.get("delta", -1))
    return False, f"unknown mutation op {op!r}"


def falsify_evidence(path: Path, oracles: list[dict], mutations: list[dict]) -> dict[str, Any]:
    try:
        raw = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        return {"evidence": path.name, "status": "not_run", "reason": f"unreadable evidence: {exc}", "cases": []}

    baseline = [run_oracle(o, copy.deepcopy(raw)) for o in oracles]
    baseline_failures = [b for b in baseline if b["outcome"] != "pass"]
    record: dict[str, Any] = {
        "evidence": path.name,
        "oracles": [str(o.get("name") or o.get("id")) for o in oracles],
        "cases": [],
    }
    if baseline_failures:
        record["status"] = "not_run"
        record["reason"] = "baseline_failed: " + "; ".join(f"{b['oracle']}: {b['detail']}" for b in baseline_failures)
        return record

    record["status"] = "ran"
    for mutation in mutations:
        payload = copy.deepcopy(raw)
        applied, change = apply_mutation(mutation, payload)
        if not applied:
            record["cases"].append({"mutation": mutation.get("id"), "category": mutation.get("category", ""),
                                    "result": "invalid", "reason": change})
            continue
        outcomes = [run_oracle(o, payload) for o in oracles]
        killed_by = [o for o in outcomes if o["outcome"] in {"assertion_fail", "crash"}]
        record["cases"].append({
            "mutation": mutation.get("id"),
            "category": mutation.get("category", ""),
            "change": change,
            "expected_fail_reason": mutation.get("expected_fail_reason", ""),
            "result": "killed" if killed_by else "survived",
            "killed_by": [{"oracle": o["oracle"], "outcome": o["outcome"], "detail": o["detail"]} for o in killed_by],
            "action": "" if killed_by else mutation.get("action_if_survived", "add an assertion, add a case, or accept the boundary"),
        })
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    killed = survived = invalid = 0
    survived_cases: list[dict[str, Any]] = []
    for r in records:
        for c in r["cases"]:
            if c["result"] == "killed":
                killed += 1
            elif c["result"] == "survived":
                survived += 1
                survived_cases.append({"evidence": r["evidence"], **c})
            else:
                invalid += 1
    total = killed + survived
    return {
        "killed": killed, "survived": survived, "invalid": invalid,
        "not_run_evidence": [r["evidence"] for r in records if r["status"] == "not_run"],
        "score": round(killed / total, 4) if total else None,
        "score_percent": round(killed / total * 100, 1) if total else None,
        "survived_cases": survived_cases,
    }


def render_markdown(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# Wave 8 Oracle Falsification Result", "",
        f"- Evidence files: {len(records)}",
        f"- Oracle falsification score: {summary['score_percent']}%",
        f"- killed: {summary['killed']}",
        f"- survived: {summary['survived']}",
        f"- invalid: {summary['invalid']}", "",
        "A case is killed when at least one oracle fails on the mutated evidence;",
        "it survives only when every oracle still passes. Baselines: every oracle",
        "passed on each pristine evidence file before mutation.", "",
        "## Survived falsifications (action required)", "",
    ]
    if summary["survived_cases"]:
        lines += ["| Evidence | Mutation | Category | Action |", "|---|---|---|---|"]
        for c in summary["survived_cases"]:
            lines.append(f"| `{c['evidence']}` | {c['mutation']} | {c['category']} | {c['action']} |")
    else:
        lines.append("none")
    lines += ["", "## Per-evidence detail", ""]
    for r in records:
        lines.append(f"### `{r['evidence']}` — {r['status']}")
        if r["status"] == "not_run":
            lines += [f"- reason: {r.get('reason', '')}", ""]
            continue
        lines += ["", "| Mutation | Result | Killed by | Detail |", "|---|---|---|---|"]
        for c in r["cases"]:
            if c["result"] == "invalid":
                lines.append(f"| {c['mutation']} | invalid | - | {c['reason']} |")
                continue
            killers = "<br>".join(k["oracle"] for k in c["killed_by"]) or "-"
            detail = (c["killed_by"][0]["detail"] if c["killed_by"] else c["expected_fail_reason"]).replace("|", "\\|")
            lines.append(f"| {c['mutation']} | {c['result']} | {killers} | {detail} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def load_spec(spec_path: Path) -> dict[str, Any]:
    data = parse_nested_yaml(read_text(spec_path))
    return data if isinstance(data, dict) else {}


def resolve_evidence(evidence_arg: str, spec: dict, spec_path: Path | None) -> list[Path]:
    if evidence_arg:
        return sorted(Path(p) for p in glob.glob(evidence_arg))
    listed = spec.get("evidence")
    if isinstance(listed, list) and listed:
        base = spec_path.parent if spec_path else Path.cwd()
        return sorted((base / str(p)) for p in listed)
    g = spec.get("evidence_glob")
    if g:
        base = spec_path.parent if spec_path else Path.cwd()
        return sorted(Path(p) for p in glob.glob(str(base / str(g))))
    return []


def profile_required(args: argparse.Namespace, message: str) -> int:
    result = {"status": "profile_required", "mutation_score": None, "score_percent": None, "message": message}
    dump_json(Path(args.json_output), result)
    write_text(
        Path(args.md_output),
        "# Oracle Falsification Result\n\n- Status: profile_required\n"
        f"- Reason: {message}\n- Mutation score: not_applicable_without_spec\n",
    )
    print(f"status: profile_required ({message})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", help="Declarative oracle/mutation spec (YAML). Else config falsification_spec.")
    parser.add_argument("--evidence", default="", help="Glob of evidence JSON; else the spec's evidence/evidence_glob.")
    parser.add_argument("--json-output", default="oracle_falsification_result.json")
    parser.add_argument("--md-output", default="oracle_falsification_result.md")
    parser.add_argument("--profile", help="Optional SUT profile config path")
    parser.add_argument("--min-score", type=float, default=None, help="Threshold (0-1); else config falsification_threshold, else 0.7")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero if score < threshold")
    args = parser.parse_args()

    config = load_config(profile=args.profile or os.environ.get("BUGATE_PROFILE"))
    spec_value = args.spec or config.get("falsification_spec")
    if not spec_value:
        return profile_required(args, "no declarative spec (--spec or config falsification_spec) provided")
    spec_path = Path(spec_value)
    if not spec_path.is_absolute() and not spec_path.exists():
        from bugate_core import resolve_path
        spec_path = resolve_path(spec_value)
    if not spec_path.exists():
        return profile_required(args, f"spec not found: {spec_value}")

    spec = load_spec(spec_path)
    oracles = [o for o in (spec.get("oracles") or []) if isinstance(o, dict)]
    mutations = [m for m in (spec.get("mutations") or []) if isinstance(m, dict)]
    if not oracles or not mutations:
        return profile_required(args, "spec must define non-empty oracles and mutations")

    evidence_paths = resolve_evidence(args.evidence, spec, spec_path)
    if not evidence_paths:
        return profile_required(args, "no evidence files resolved from --evidence or the spec")

    records = [falsify_evidence(p, oracles, mutations) for p in evidence_paths]
    summary = summarize(records)
    threshold = args.min_score if args.min_score is not None else float(config.get("falsification_threshold") or 0.7)
    payload = {
        "status": "ran",
        "spec": spec_path.name,
        "threshold": threshold,
        "summary": {k: v for k, v in summary.items() if k != "survived_cases"},
        "records": records,
    }
    dump_json(Path(args.json_output), payload)
    write_text(Path(args.md_output), render_markdown(records, summary))
    print(f"oracle falsification score: {summary['score_percent']}% (killed={summary['killed']} survived={summary['survived']} invalid={summary['invalid']})")
    print(f"written {args.json_output} / {args.md_output}")
    if args.gate and summary["score"] is not None and summary["score"] < threshold:
        print(f"FAIL: score {summary['score']:.2f} < threshold {threshold:.2f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
