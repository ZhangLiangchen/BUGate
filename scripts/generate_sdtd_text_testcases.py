#!/usr/bin/env python3
"""Generate human-readable BUGate test cases from 03_inventory.yaml."""

from __future__ import annotations

import argparse
from pathlib import Path

from bugate_core import inventory_sha256, parse_inventory_cases, read_text, write_text


def _field(value: object) -> str:
    """Render a possibly list/scalar inventory field.

    Guards against char-splitting: ``", ".join("P-001")`` would yield
    ``P, -, 0, 0, 1`` if the value arrived as a string (e.g. an unparsed
    comment), so a non-list is returned as-is rather than joined.
    """
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "TBD"
    text = "" if value is None else str(value).strip()
    return text or "TBD"


# The line-based inventory parser flattens nested maps (layer_decision,
# preconditions, ...) onto the case, so the readable layer reads the flattened
# scalar keys rather than the (empty) parent. Keeps 03a executable instead of
# rendering Layer/Preconditions/Action as TBD.
_PRECOND_KEYS = ("env", "identity", "admin_identity", "wallet_id", "object_id",
                 "chain", "coin_code", "native_coin_code", "model_id")


def _layer(case: dict) -> str:
    return _field(case.get("selected_layer") or case.get("layer_decision"))


def _preconditions(case: dict) -> str:
    parts = [f"{key}={case[key]}" for key in _PRECOND_KEYS if str(case.get(key) or "").strip()]
    return ", ".join(parts) or "TBD"


def _action(case: dict) -> str:
    target = str(case.get("implementation_target") or "").strip()
    if target:
        return f"execute the SUT-profile-owned implementation target `{target}`"
    func = str(case.get("function") or "").strip()  # flattened last assertion function
    if func:
        return f"run assertion `{func}` against the evidence_anchor (implementation_target deferred to Layer 4)"
    return "TBD"


def render(artifact_dir: Path) -> str:
    inventory = artifact_dir / "03_inventory.yaml"
    cases = parse_inventory_cases(read_text(inventory)) if inventory.exists() else []
    lines = [
        "---",
        "gate: readable_test_cases",
        "gate_status: pending",
        "sut_profile: TBD",
        # Provenance: the inventory sha this 03a was generated from. The
        # orchestrator regenerates 03a when this drifts from the live inventory.
        f"source_inventory_sha256: {inventory_sha256(artifact_dir)}",
        "---",
        "",
        "# Test Cases",
        "",
    ]
    if not cases:
        lines += ["No cases found in `03_inventory.yaml`.", ""]
        return "\n".join(lines)
    for case in cases:
        cid = case.get("id", "CASE-UNKNOWN")
        lines += [
            f"## {cid}",
            "",
            f"- Intent: {_field(case.get('intent'))}",
            f"- Layer: {_layer(case)}",
            f"- Preconditions: {_preconditions(case)}",
            f"- Action: {_action(case)}",
            f"- Expected observations: {_field(case.get('expected_observations'))}",
            f"- Proposition refs: {_field(case.get('proposition_refs'))}",
            f"- Oracle refs: {_field(case.get('oracle_refs'))}",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    output = render(args.artifact_dir)
    if args.write:
        path = args.artifact_dir / "03a_test_cases.md"
        write_text(path, output)
        print(f"written {path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
