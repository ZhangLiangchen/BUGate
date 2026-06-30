#!/usr/bin/env python3
"""Generate a BUGate assertion coverage matrix (SUT-neutral).

Back-traces oracles across three sources and classifies coverage so that
"a case references an oracle/assertion that does not exist" is caught:

  - REFERENCED: oracle ids cited by Layer 3 cases (03_inventory.yaml oracle_refs).
  - DEFINED:    oracle ids declared in the falsification spec (--spec), i.e. the
                assertions the SUT actually implements declaratively.
  - EXERCISED:  oracles that killed >= 1 mutation in the Wave 8 falsification run
                (--mutation-result), i.e. assertions proven to catch a wrong state.

States:
  - covered:                referenced & defined & exercised (proven to catch a
                            wrong state) — the only fully-discharged state.
  - inert:                  referenced & defined but never killed a mutation
                            (when a falsification result is supplied) — a real
                            coverage hole: the assertion exists and is expected,
                            but nothing proves it catches anything. Distinct from
                            covered so a defined oracle cannot hide untested.
  - missing_implementation: referenced but not defined — the bug-catch.
  - defined_unused:         defined but never referenced by any case.

Without --spec it degrades to the referenced listing plus the Wave 8 score line.
Without a falsification result, defined+referenced oracles stay covered (the
exercised dimension is unknown, not failed), so the gate is not falsely tripped.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from bugate_core import as_bool, load_config, parse_inventory_cases, parse_nested_yaml, read_text, resolve_path, write_text


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutation-result")
    parser.add_argument("--spec", help="Declarative oracle spec (defines oracle ids/names)")
    parser.add_argument("--artifact-root", default=".")
    parser.add_argument("--output", default="assertion_coverage_matrix.md")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero if missing_implementation > --max-missing")
    parser.add_argument("--max-missing", type=int, default=0)
    parser.add_argument("--max-inert", type=int, default=None,
                        help="If set, exit non-zero when inert oracles (defined+referenced but never "
                             "killed a mutation) exceed this count; opt-in, else config require_inert_coverage")
    parser.add_argument("--profile", help="Optional SUT profile config path")
    args = parser.parse_args()
    config = load_config(profile=args.profile or os.environ.get("BUGATE_PROFILE"))
    root = Path(args.artifact_root)

    cases = []
    for inventory in root.glob("**/03_inventory.yaml"):
        cases.extend(parse_inventory_cases(read_text(inventory)))
    referenced: set[str] = set()
    for case in cases:
        referenced.update(r for r in _as_list(case.get("oracle_refs")) if r.startswith("O-"))

    # Defined oracles + name->id map from the spec.
    defined: set[str] = set()
    name_to_id: dict[str, str] = {}
    spec_value = args.spec or config.get("falsification_spec")
    spec_path = None
    if spec_value:
        spec_path = Path(str(spec_value))
        if not spec_path.is_absolute() and not spec_path.exists():
            spec_path = resolve_path(spec_path)
    if spec_path and spec_path.exists():
        spec = parse_nested_yaml(read_text(spec_path))
        for o in (spec.get("oracles") or []) if isinstance(spec, dict) else []:
            if isinstance(o, dict) and o.get("id"):
                oid = str(o["id"])
                defined.add(oid)
                name_to_id[str(o.get("name") or oid)] = oid

    # Exercised oracles (killed >=1 mutation) from the falsification result.
    # `ran_falsification` records whether the exercised dimension is actually
    # KNOWN: only a successfully-run result lets us treat "never killed a
    # mutation" as a real coverage hole (inert) rather than as missing data.
    exercised: set[str] = set()
    ran_falsification = False
    score_line = "- Wave 8 falsification: not_provided"
    if args.mutation_result and Path(args.mutation_result).exists() and Path(args.mutation_result).suffix == ".json":
        mr = json.loads(read_text(Path(args.mutation_result)))
        status = mr.get("status", "unknown")
        summ = mr.get("summary") or {}
        if status == "ran":
            ran_falsification = True
            crash_only = summ.get("crash_only_kills")
            crash_note = f", crash-only {crash_only}" if crash_only else ""
            score_line = (f"- Wave 8 falsification: score {summ.get('score_percent')}% "
                          f"(killed {summ.get('killed')}, survived {summ.get('survived')}{crash_note})")
        else:
            score_line = f"- Wave 8 falsification: {status}"
        for r in mr.get("records") or []:
            for c in r.get("cases") or []:
                for k in c.get("killed_by") or []:
                    nm = k.get("oracle", "")
                    exercised.add(name_to_id.get(nm, nm))

    def classify(oid: str) -> str:
        if oid in referenced and oid in defined:
            # An oracle that is expected and implemented is only fully covered
            # once a falsification run proves it catches a wrong state. When the
            # exercised dimension is known but this oracle killed nothing, it is
            # inert — a coverage hole, not a discharged assertion. When the
            # dimension is unknown (no run), keep it covered to avoid a false gate.
            if ran_falsification and oid not in exercised:
                return "inert"
            return "covered"
        if oid in referenced and oid not in defined:
            return "missing_implementation"
        return "defined_unused"

    all_oracles = sorted(referenced | defined)
    rows = []
    counts = {"covered": 0, "inert": 0, "missing_implementation": 0, "defined_unused": 0}
    for oid in all_oracles:
        state = classify(oid)
        counts[state] += 1
        exer = "yes" if oid in exercised else ("-" if not ran_falsification else "no (inert)")
        rows.append((oid, state, exer))

    lines = [
        "# Assertion Coverage Matrix", "",
        score_line,
        f"- Oracles: {counts['covered']} covered, {counts['inert']} inert, "
        f"{counts['missing_implementation']} missing_implementation, "
        f"{counts['defined_unused']} defined_unused",
        "",
        "## Oracle states", "",
        "| Oracle | State | Killed a mutation? |",
        "|---|---|---|",
    ]
    if rows:
        for oid, state, exer in rows:
            lines.append(f"| {oid} | {state} | {exer} |")
    else:
        lines.append("| none | - | - |")
    if not spec_path or not spec_path.exists():
        lines += ["", "> No falsification spec resolved: oracles classified from inventory references only; "
                  "supply the falsification spec to detect missing_implementation / defined_unused."]
    elif not ran_falsification:
        lines += ["", "> No falsification result supplied: defined+referenced oracles are reported covered, "
                  "but the exercised dimension is unknown; supply --mutation-result to detect inert oracles."]

    inert_oracles = [oid for oid, state, _ in rows if state == "inert"]
    if inert_oracles:
        lines += ["", "## Inert oracles (action required)", "",
                  "Defined and referenced, but no mutation in the Wave 8 run was killed by them, "
                  "so nothing proves they catch a wrong state. Add a mutation that targets each "
                  "oracle's field(s), or remove the unprovable assertion.", "",
                  "| Oracle |", "|---|"]
        lines += [f"| {oid} |" for oid in inert_oracles]

    lines += ["", "## Case inventory", "", "| Case | Propositions | Oracles | Implementation target |",
              "|---|---|---|---|"]
    for case in cases:
        lines.append(f"| {case.get('id', '')} | {', '.join(_as_list(case.get('proposition_refs')))} | "
                     f"{', '.join(_as_list(case.get('oracle_refs')))} | {case.get('implementation_target', '')} |")
    if not cases:
        lines.append("| none | none | none | none |")

    write_text(Path(args.output), "\n".join(lines) + "\n")
    print(f"written {args.output} "
          f"(covered={counts['covered']} inert={counts['inert']} "
          f"missing_implementation={counts['missing_implementation']} "
          f"defined_unused={counts['defined_unused']})")
    gate_on = args.gate or as_bool(config.get("require_assertion_coverage"))
    if gate_on and counts["missing_implementation"] > args.max_missing:
        print(f"FAIL: {counts['missing_implementation']} missing_implementation oracle(s) > max {args.max_missing}")
        return 1
    # The inert gate is opt-in (off by default so the existing gate behavior is
    # preserved): an inert oracle is reported regardless, but only fails the run
    # when --max-inert is set below the count or require_inert_coverage is on.
    max_inert = args.max_inert
    if max_inert is None and as_bool(config.get("require_inert_coverage")):
        max_inert = 0
    if max_inert is not None and counts["inert"] > max_inert:
        print(f"FAIL: {counts['inert']} inert oracle(s) > max {max_inert} "
              f"(defined+referenced but never killed a mutation)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
