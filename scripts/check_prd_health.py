#!/usr/bin/env python3
"""Wave 0 PRD health-check scorer (SUT-neutral, declarative).

Implements METHOD.md §3: score 8 PRD-quality dimensions (1-5 each), convert to a
0-100 composite, apply the optional traceability bonus (dimension 9, +/-5), and
derive the grade + routing decision. Also passes through a structured gap report
(the real Wave 0 product). The PRD itself is SUT-specific, so this engine does
NOT read the PRD: it consumes a declarative self-assessment (the QA/agent fills
the dimension scores after reading the PRD) — mirroring the falsification engine.

Without an input spec it reports profile_required (core stays usable unmounted).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from bugate_core import dump_json, load_config, parse_nested_yaml, read_text, write_text

# The 8 composite dimensions (METHOD §3.2 #1-8). Dimension 9 (traceability) is a
# +/-5 bonus and is intentionally NOT part of the composite.
DIMENSIONS = [
    "completeness",
    "consistency",
    "falsifiability",
    "boundary",
    "role_clarity",
    "error_handling",
    "currency",
    "verifiability_source",
]


def grade_for(score: float) -> tuple[str, str]:
    if score >= 85:
        return "A", "enter Wave 1 (standard flow)"
    if score >= 70:
        return "B", "enter Wave 1; route gaps to the Wave 3 interview pool"
    if score >= 60:
        return "C", "pause Wave 1; fix PRD gaps via interview, then re-run Wave 0"
    return "D", "PRD reverse-rebuild mode (v2 scope)"


def score_health(spec: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    dims = spec.get("dimensions") or {}
    if not isinstance(dims, dict):
        return {}, ["dimensions must be a mapping of the 8 PRD dimensions to 1-5 scores"]
    scores: dict[str, int] = {}
    for name in DIMENSIONS:
        raw = dims.get(name)
        if raw is None:
            errors.append(f"missing dimension score: {name}")
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors.append(f"{name} score must be an integer 1-5, got {raw!r}")
            continue
        if not 1 <= value <= 5:
            errors.append(f"{name} score must be in 1-5, got {value}")
            continue
        scores[name] = value
    if errors:
        return {}, errors

    composite = sum(scores.values()) * 2.5  # 8 dims * 5 = 40 -> *2.5 = 100
    bonus = 0.0
    raw_bonus = spec.get("traceability_bonus")
    if raw_bonus not in (None, ""):
        try:
            bonus = max(-5.0, min(5.0, float(raw_bonus)))
        except (TypeError, ValueError):
            errors.append(f"traceability_bonus must be numeric (-5..5), got {raw_bonus!r}")
    final = max(0.0, min(100.0, composite + bonus))
    grade, routing = grade_for(final)
    return {
        "dimension_scores": scores,
        "composite": round(composite, 1),
        "traceability_bonus": bonus,
        "score": round(final, 1),
        "grade": grade,
        "routing": routing,
        "gaps": spec.get("gaps") or [],
    }, errors


def render_markdown(result: dict) -> str:
    lines = [
        "# Wave 0 PRD Health Report", "",
        f"- Score: {result['score']}/100",
        f"- Grade: {result['grade']}",
        f"- Routing: {result['routing']}",
        f"- Composite (8 dims): {result['composite']} | traceability bonus: {result['traceability_bonus']:+g}",
        "", "## Dimension scores (1-5)", "",
        "| # | Dimension | Score |", "|---|---|---|",
    ]
    for i, name in enumerate(DIMENSIONS, start=1):
        lines.append(f"| {i} | {name} | {result['dimension_scores'].get(name, '-')} |")
    lines += ["", "## Gap report", ""]
    gaps = result.get("gaps") or []
    if gaps:
        lines += ["| id | section | dimension | issue | severity | question |", "|---|---|---|---|---|---|"]
        for g in gaps:
            if isinstance(g, dict):
                issue = str(g.get("issue", "")).replace("|", "\\|")
                lines.append(
                    f"| {g.get('id', '')} | {g.get('section', '')} | {g.get('dimension', '')} | "
                    f"{issue} | {g.get('severity', '')} | {g.get('suggested_interview_question', '')} |"
                )
    else:
        lines.append("none recorded")
    return "\n".join(lines) + "\n"


def profile_required(args: argparse.Namespace, message: str) -> int:
    result = {"status": "profile_required", "score": None, "grade": None, "message": message}
    dump_json(Path(args.json_output), result)
    write_text(Path(args.md_output), f"# Wave 0 PRD Health Report\n\n- Status: profile_required\n- Reason: {message}\n")
    print(f"status: profile_required ({message})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Declarative PRD-health spec (YAML). Else config prd_health_spec.")
    parser.add_argument("--json-output", default="prd_health_result.json")
    parser.add_argument("--md-output", default="prd_health_report.md")
    parser.add_argument("--profile", help="Optional SUT profile config path")
    parser.add_argument("--min-score", type=float, default=None, help="Gate floor (0-100); else config prd_health_min, else 60")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero if score < floor")
    args = parser.parse_args()

    config = load_config(profile=args.profile or os.environ.get("BUGATE_PROFILE"))
    spec_value = args.input or config.get("prd_health_spec")
    if not spec_value:
        return profile_required(args, "no PRD-health spec (--input or config prd_health_spec) provided")
    spec_path = Path(spec_value)
    if not spec_path.is_absolute() and not spec_path.exists():
        from bugate_core import resolve_path

        spec_path = resolve_path(spec_value)
    if not spec_path.exists():
        return profile_required(args, f"spec not found: {spec_value}")

    spec = parse_nested_yaml(read_text(spec_path))
    if not isinstance(spec, dict):
        return profile_required(args, "spec must be a YAML mapping")
    result, errors = score_health(spec)
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 2

    result["status"] = "scored"
    dump_json(Path(args.json_output), result)
    write_text(Path(args.md_output), render_markdown(result))
    print(f"PRD health: {result['score']}/100 grade {result['grade']} — {result['routing']}")
    print(f"written {args.json_output} / {args.md_output}")
    floor = args.min_score if args.min_score is not None else float(config.get("prd_health_min") or 60)
    if args.gate and result["score"] < floor:
        print(f"FAIL: PRD health {result['score']} < floor {floor} (grade {result['grade']})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
