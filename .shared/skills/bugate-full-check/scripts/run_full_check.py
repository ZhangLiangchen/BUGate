#!/usr/bin/env python3
"""Run a SUT-neutral BUGate capability self-check.

The script exercises BUGate core without adding SUT-specific facts. The repo
ships no committed example SUT trees (imported-mode purity), so every
governed-workspace probe fabricates its fixtures under /tmp at run time; only
a compact Markdown summary is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class Check:
    name: str
    status: str
    detail: str


ENGINE_SENTINELS = (
    Path("scripts/bugate_core.py"),
    Path(".shared/skills/bugate/SKILL.md"),
    Path(".shared/skills/bugate-full-check/SKILL.md"),
)

ROLE_FLOW_ENGINE_FILES = {
    "orchestrator": Path("scripts/sdtd_orchestrator.py"),
    "role_cli": Path("scripts/role_governance.py"),
    "role_hook": Path("scripts/check_role_evidence.py"),
    "physical_guard": Path("scripts/check_bugate.py"),
    "memory_bus": Path("scripts/memory_bus.py"),
    "semantic_gate": Path("scripts/check_bugate_v13_semantics.py"),
}
ROLE_FLOW_UC = "ROLE_001"
ROLE_FLOW_NAMESPACE = "project:bugate-full-check"


def _missing_engine_sentinels(candidate: Path) -> list[str]:
    return [path.as_posix() for path in ENGINE_SENTINELS if not (candidate / path).is_file()]


def _validated_engine(candidate: Path, source: str) -> Path:
    resolved = candidate.expanduser().resolve()
    missing = _missing_engine_sentinels(resolved)
    if missing:
        raise SystemExit(
            f"Resolved BUGate engine from {source} is invalid: {resolved}; "
            f"missing {', '.join(missing)}. Run the vendored full-check skill "
            "or set BUGATE_ENGINE_ROOT to the actual kit root."
        )
    return resolved


def _script_engine() -> Optional[Path]:
    """Find the kit that owns this full-check script, including symlink entrypoints."""
    for candidate in Path(__file__).resolve().parents:
        if not _missing_engine_sentinels(candidate):
            return candidate
    return None


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def role_flow_engine_paths(engine: Path) -> dict[str, Path]:
    """Resolve every Wave-7 flow command from the already-selected engine.

    Keeping this mapping explicit prevents an imported workspace's own
    ``scripts/`` directory from shadowing the vendored BUGate engine.  Layout
    acceptances exercise this function with collision and custom-vendor trees.
    """

    resolved_engine = engine.expanduser().resolve()
    paths: dict[str, Path] = {}
    missing: list[str] = []
    for label, relative in ROLE_FLOW_ENGINE_FILES.items():
        candidate = (resolved_engine / relative).resolve()
        if not _within(candidate, resolved_engine) or not candidate.is_file():
            missing.append(relative.as_posix())
            continue
        paths[label] = candidate
    if missing:
        raise SystemExit(
            f"Resolved BUGate engine is missing Wave-7 full-check commands: "
            f"{', '.join(missing)}"
        )
    return paths


def find_roots(start: Path) -> tuple[Path, Path, str]:
    """Resolve (workspace_root, engine_root, layout).

    Two supported layouts:
    - core: the BUGate checkout/release itself; workspace and engine are the
      same validated kit root.
    - imported: a governed SUT test repo (bugate.config.yaml at root) with the
      kit vendored beneath it. A SUT-owned AGENTS.md + .shared directory must
      not make this look like core.

    Workspace and engine are deliberately independent: BUGATE_PROJECT_ROOT or
    the nearest bugate.config.yaml selects the governed workspace, while
    BUGATE_ENGINE_ROOT, an explicit BUGATE_VENDOR_DIR, or this script's own
    resolved location selects the engine. The broad AGENTS.md + .shared legacy
    sentinel is used only as a final fallback for a validated core engine.
    """
    start = start.expanduser().resolve()
    project_env = os.environ.get("BUGATE_PROJECT_ROOT", "").strip()
    if project_env:
        root: Optional[Path] = Path(project_env).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"BUGATE_PROJECT_ROOT is not a directory: {root}")
    else:
        root = next(
            (candidate for candidate in (start, *start.parents)
             if (candidate / "bugate.config.yaml").is_file()),
            None,
        )

    engine_env = os.environ.get("BUGATE_ENGINE_ROOT", "").strip()
    vendor_env = os.environ.get("BUGATE_VENDOR_DIR", "").strip()
    script_engine = _script_engine()
    if engine_env:
        engine = _validated_engine(Path(engine_env), "BUGATE_ENGINE_ROOT")
    elif script_engine is not None and (root is None or script_engine == root):
        # A global/import helper may leave BUGATE_VENDOR_DIR set. It applies to
        # imported workspaces only and must not redirect a validated core run
        # into a nonexistent <core>/.bugate directory.
        engine = _validated_engine(script_engine, "the running core full-check skill")
    elif vendor_env:
        if root is None:
            raise SystemExit("BUGATE_VENDOR_DIR requires a governed workspace root")
        vendor_path = Path(vendor_env).expanduser()
        candidate = vendor_path if vendor_path.is_absolute() else root / vendor_path
        engine = _validated_engine(candidate, "BUGATE_VENDOR_DIR")
    elif script_engine is not None and root is not None and _within(script_engine, root):
        engine = _validated_engine(script_engine, "the running full-check skill")
    elif root is not None:
        engine = _validated_engine(root / ".bugate", "the workspace's default .bugate vendor dir")
    else:
        raise SystemExit(
            "BUGate engine not found: run the full-check script from a core checkout/release "
            "or from the vendored kit, or set BUGATE_ENGINE_ROOT."
        )

    if root is None:
        if (engine / "AGENTS.md").is_file() and (engine / ".shared").is_dir():
            root = engine
        else:
            raise SystemExit(
                "BUGate workspace root not found: expected bugate.config.yaml in an ancestor, "
                "BUGATE_PROJECT_ROOT, or a validated core engine with AGENTS.md + .shared."
            )

    layout = "core" if root.resolve() == engine.resolve() else "imported"
    return root, engine, layout


def run(
    cmd: list[str],
    root: Path,
    *,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        cwd=cwd or root,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=merged_env,
    )


def codex_auth_command() -> list[str]:
    """Probe the same configured Codex model and effort as peer dispatch."""

    command = ["codex", "exec", "--sandbox", "read-only"]
    model = os.environ.get("SDTD_CODEX_MODEL", "").strip()
    effort = os.environ.get("SDTD_CODEX_REASONING_EFFORT", "").strip()
    if model:
        command += ["--model", model]
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    command.append("-")
    return command


def claude_auth_command() -> list[str]:
    """Probe the same configured Claude model and effort as peer dispatch."""

    command = ["claude", "-p"]
    model = os.environ.get("SDTD_CLAUDE_MODEL", "").strip()
    effort = os.environ.get("SDTD_CLAUDE_EFFORT", "").strip()
    if model:
        command += ["--model", model]
    if effort:
        command += ["--effort", effort]
    command += [
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "text",
        "Reply exactly: ok",
    ]
    return command


def add(checks: list[Check], name: str, ok: bool, detail: str, warn: bool = False) -> None:
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    checks.append(Check(name, status, compact(detail)))


def compact(text: str, limit: int = 280) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def outcome_matches(
    result: subprocess.CompletedProcess[str],
    returncode: int,
    marker: Optional[str] = None,
) -> bool:
    """Require both the expected process status and its BUGate semantic signal."""
    return result.returncode == returncode and (marker is None or marker in result.stdout)


def verify_imported_installed_state(
    checks: list[Check],
    root: Path,
    engine: Path,
    layout: str,
    *,
    timeout: int = 60,
) -> None:
    """Fail closed unless an imported engine has a healthy lock-based install.

    Core checkouts and unpacked release roots are capability surfaces, not
    imported installations, so they intentionally have no installed lock.  An
    imported workspace, however, must be verified by the updater shipped by
    the selected vendored engine before any other capability probe runs.
    """

    if layout != "imported":
        return

    try:
        vendor_dir = engine.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        add(
            checks,
            "Imported installed-state verification",
            False,
            f"selected engine is outside the imported workspace: engine={engine}; workspace={root}",
        )
        return

    updater = engine / "bin/bugate-update"
    command = [
        str(updater),
        "verify",
        str(root),
        "--vendor-dir",
        vendor_dir,
        "--json",
    ]
    try:
        result = run(command, root, cwd=root, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        add(
            checks,
            "Imported installed-state verification",
            False,
            f"updater verify could not run: {exc.__class__.__name__}: {exc}",
        )
        return

    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        add(
            checks,
            "Imported installed-state verification",
            False,
            (
                f"exit={result.returncode}/0; updater verify emitted invalid JSON "
                f"({exc.__class__.__name__})"
            ),
        )
        return

    if not isinstance(payload, dict):
        add(
            checks,
            "Imported installed-state verification",
            False,
            f"exit={result.returncode}/0; updater verify JSON root is not an object",
        )
        return

    expected = {
        "decision": "GO",
        "status": "passed",
        "installed_kind": "locked",
        "lock_based": True,
        "recovery_required": False,
    }
    fields_ok = (
        all(payload.get(key) == value for key, value in expected.items())
        and payload.get("lock_based") is True
        and payload.get("recovery_required") is False
    )
    ok = result.returncode == 0 and fields_ok
    add(
        checks,
        "Imported installed-state verification",
        ok,
        (
            f"exit={result.returncode}/0; decision={payload.get('decision')!r}/'GO'; "
            f"status={payload.get('status')!r}/'passed'; "
            f"installed_kind={payload.get('installed_kind')!r}/'locked'; "
            f"lock_based={payload.get('lock_based')!r}/True; "
            f"recovery_required={payload.get('recovery_required')!r}/False; "
            f"vendor_dir={vendor_dir}"
        ),
    )


def build_probe_profile(
    base: Path,
    name: str,
    *,
    require_multiview: bool = False,
) -> tuple[Path, dict[str, str]]:
    """Create a SUT-neutral profile for a synthetic full-check fixture.

    Full-check can run from an imported workspace whose active profile enables
    required role governance and SUT-specific hardening. Synthetic fixtures
    must not inherit that contract: they live under a system temporary
    directory and test Core capability, not the imported SUT. Every command
    that consumes the fixture receives this same explicit profile.
    """

    profile = base / f"{name}.profile.yaml"
    lines = [
        f"artifact_dir_template: {base.as_posix()}/{{uc}}/",
        "memory:",
        f"  namespace: {ROLE_FLOW_NAMESPACE}",
    ]
    if require_multiview:
        lines.append("require_multiview: true")
    lines.extend(["role_governance:", "  mode: off", ""])
    profile.write_text("\n".join(lines), encoding="utf-8")
    return profile, {
        "BUGATE_PROFILE": str(profile),
        "BUGATE_PROJECT_ROOT": str(base),
        "MEMORY_BUS_PROJECT_TAG": ROLE_FLOW_NAMESPACE,
    }


PRECODE_NAMES = [
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
]


def build_guard_workspace(base: Path) -> Path:
    """Fabricate a minimal governed workspace (imported layout) for guard probes."""
    ws = base / "ws"
    for uc, passed in (("ok", True), ("pending", False)):
        (ws / "tests" / uc).mkdir(parents=True)
        (ws / "tests" / uc / "test_x.py").write_text("# guarded placeholder\n", encoding="utf-8")
        uc_dir = ws / "usecases" / uc
        uc_dir.mkdir(parents=True)
        names = PRECODE_NAMES if passed else PRECODE_NAMES[:1]
        status = "passed" if passed else "pending"
        for name in names:
            (uc_dir / name).write_text(f"---\ngate_status: {status}\n---\n", encoding="utf-8")
    (ws / "bugate.config.yaml").write_text("profile: bugate.profile.yaml\n", encoding="utf-8")
    (ws / "bugate.profile.yaml").write_text(
        "artifact_dir_template: usecases/{uc}/\n"
        'guarded_path_regex:\n  - "(^|/)tests/(?P<uc>[^/]+)/[^/]+[.]py$"\n'
        + "required_precode_artifacts:\n"
        + "".join(f"  - {n}\n" for n in PRECODE_NAMES)
        + 'agent_roles:\n  implementer:\n    - "^mirror/.*$"\n',
        encoding="utf-8",
    )
    return ws


def run_hardening_multiview_probe(
    checks: list[Check],
    root: Path,
    engine: Path,
) -> None:
    """Prove require_multiview fails closed without inheriting a SUT profile."""

    with tempfile.TemporaryDirectory(prefix="bugate-harden.") as tmp:
        base = Path(tmp)
        uc = base / "uc"
        _, baseline_env = build_probe_profile(base, "baseline")
        _, probe_env = build_probe_profile(
            base,
            "harden",
            require_multiview=True,
        )
        init_result = run(
            [
                "python3",
                str(engine / "scripts/sdtd_orchestrator.py"),
                str(uc),
                "--init",
            ],
            root,
            env=probe_env,
            timeout=60,
        )
        init_marker = "created 01_business_brief.md"
        initialized_files = all((uc / name).is_file() for name in PRECODE_NAMES)
        init_ok = (
            outcome_matches(init_result, 0, init_marker)
            and initialized_files
        )
        baseline_result = run(
            [
                "python3",
                str(engine / "scripts/check_bugate_v13_semantics.py"),
                str(uc),
                "--scope",
                "pre-code",
            ],
            root,
            env=baseline_env,
            timeout=60,
        )
        baseline_ok = outcome_matches(baseline_result, 0, "PASS")
        semantic_result = run(
            [
                "python3",
                str(engine / "scripts/check_bugate_v13_semantics.py"),
                str(uc),
                "--scope",
                "pre-code",
            ],
            root,
            env=probe_env,
            timeout=60,
        )
        semantic_marker = "divergence_report"
        divergence = uc / "00_multiview/divergence_report.md"
        enforced = (
            outcome_matches(semantic_result, 1, semantic_marker)
            and not divergence.exists()
        )
        add(
            checks,
            "Hardening flags enforce (multiview)",
            init_ok and baseline_ok and enforced,
            (
                f"init_exit={init_result.returncode}/0; init_marker="
                f"{'present' if init_marker in init_result.stdout else 'missing'}; "
                f"all_precode_files={initialized_files}; "
                f"baseline_exit={baseline_result.returncode}/0; "
                f"semantic_exit={semantic_result.returncode}/1; semantic_marker="
                f"{'present' if semantic_marker in semantic_result.stdout else 'missing'}; "
                "required_report=absent"
            ),
        )


def run_real_peer_dispatch_probe(
    checks: list[Check],
    root: Path,
    engine: Path,
    *,
    timeout: int,
) -> None:
    """Run real Wave-1 peers against one isolated, SUT-neutral fixture."""

    with tempfile.TemporaryDirectory(prefix="bugate-full-check.") as tmp:
        tmp_root = Path(tmp)
        uc_dir = tmp_root / "peer-uc"
        _, peer_env = build_probe_profile(tmp_root, "peer")
        peer_env["SDTD_CLI_TIMEOUT_SECONDS"] = str(timeout)
        peer_env["SDTD_CODEX_SKIP_GIT_REPO_CHECK"] = "1"
        init_result = run(
            [
                "python3",
                str(engine / "scripts/sdtd_orchestrator.py"),
                str(uc_dir),
                "--init",
            ],
            root,
            cwd=tmp_root,
            env=peer_env,
            timeout=60,
        )
        initialized_files = all((uc_dir / name).is_file() for name in PRECODE_NAMES)
        init_ok = (
            outcome_matches(init_result, 0, "created 01_business_brief.md")
            and initialized_files
        )
        add(
            checks,
            "Peer fixture init (templates)",
            init_ok,
            (
                f"exit={init_result.returncode}/0; all_precode_files="
                f"{initialized_files}"
            ),
        )
        if not init_ok:
            add(checks, "Real multi-view dispatch", False, "peer fixture init failed")
            add(checks, "Real adversarial dispatch", False, "peer fixture init failed")
            return

        result = run(
            [
                "python3",
                str(engine / "scripts/sdtd_multiview_cli_bridge.py"),
                "run-all",
                str(uc_dir),
            ],
            root,
            cwd=tmp_root,
            env=peer_env,
            timeout=timeout * 2,
        )
        mv_paths = (
            uc_dir / "00_multiview/divergence_report.md",
            uc_dir / "00_multiview/codex_view.md",
            uc_dir / "00_multiview/claude_view.md",
        )
        mv_text = [
            path.read_text(encoding="utf-8", errors="ignore")
            if path.is_file()
            else ""
            for path in mv_paths
        ]
        mv_ok = (
            result.returncode == 0
            and all(mv_text)
            and "dispatch_mode: real_peer_dispatch" in mv_text[0]
            and "fallback_placeholder" not in "".join(mv_text[1:])
        )
        add(checks, "Real multi-view dispatch", mv_ok, result.stdout)

        result = run(
            [
                "python3",
                str(engine / "scripts/sdtd_adversarial_cli_bridge.py"),
                "run-all",
                str(uc_dir),
            ],
            root,
            cwd=tmp_root,
            env=peer_env,
            timeout=timeout * 2,
        )
        adv_paths = (
            uc_dir / "03b_adversarial_cases.yaml",
            uc_dir / "00_adversarial/codex_adversarial_view.md",
            uc_dir / "00_adversarial/claude_adversarial_view.md",
        )
        adv_text = [
            path.read_text(encoding="utf-8", errors="ignore")
            if path.is_file()
            else ""
            for path in adv_paths
        ]
        adv_ok = (
            result.returncode == 0
            and all(adv_text)
            and "dispatch_mode: real_peer_dispatch" in adv_text[0]
            and "fallback_placeholder" not in "".join(adv_text[1:])
        )
        add(checks, "Real adversarial dispatch", adv_ok, result.stdout)


def build_role_workspace(base: Path) -> tuple[Path, Path, Path]:
    """Create an imported, SUT-neutral workspace for the auditable role flow."""

    ws = base / "imported-workspace"
    ws.mkdir(parents=True)
    (ws / "tests").mkdir()
    artifact_dir = ws / "usecases" / ROLE_FLOW_UC
    implementation = ws / "tests" / f"test_{ROLE_FLOW_UC}.py"
    (ws / "bugate.config.yaml").write_text(
        "profile: bugate.profile.yaml\n", encoding="utf-8"
    )
    (ws / "bugate.profile.yaml").write_text(
        "\n".join(
            [
                f"artifact_dir_template: usecases/{{uc}}/",
                "guarded_path_regex:",
                '  - "(^|/)tests/test_(?P<uc>ROLE_[0-9]+)[.]py$"',
                "required_precode_artifacts:",
                *[f"  - {name}" for name in PRECODE_NAMES],
                "memory:",
                f"  namespace: {ROLE_FLOW_NAMESPACE}",
                "role_governance:",
                "  mode: required",
                "  memory_mode: required",
                "  evidence_dir: 00_role_evidence",
                "  session_id_required: true",
                "  require_distinct_sessions: true",
                "  human_acceptance_artifacts:",
                "    - 03b_adversarial_cases.yaml",
                "  phases:",
                "    pre_code:",
                "      allowed_roles: [designer]",
                "    implementation:",
                "      allowed_roles: [implementer]",
                "      requires_handoff_from: [designer]",
                "    post_run:",
                "      allowed_roles: [reviewer]",
                "      requires_handoff_from: [implementer]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return ws, artifact_dir, implementation


def write_role_precode_artifacts(artifact_dir: Path) -> None:
    """Replace initialized templates with accepted, generic governance facts."""

    bodies = {
        "01_business_brief.md": """---
gate: layer1_business_brief
gate_status: passed
sut_profile: synthetic-full-check
---

# Business Brief

## Scope

Validate a synthetic request lifecycle without any product, API, environment,
credential, or project-specific fact.

## Canonical Business Flows

1. Submit a generated neutral record.
2. Observe the accepted state through the synthetic evidence file.

## Clarification Gate

| dimension | status | open question |
|---|---|---|
| objective | clear | none |

## Propositions

| id | proposition | priority | verifiability | evidence_label | source |
|---|---|---|---|---|---|
| P-001 | A submitted neutral record becomes observable as accepted. | P1 | externally observable | fact | synthetic fixture contract |

## Business Oracles

| id | oracle | observable evidence | evidence_label |
|---|---|---|---|
| O-001 | The recorded outcome is accepted. | The synthetic execution log contains a passed result. | fact |

## Boundaries

- One generated input and one recorded outcome are sufficient for this fixture.

## Assumptions

- The fixture exercises BUGate governance only and represents no real SUT.

## Open Questions

- None; the fixture contract is deliberately closed and synthetic.
""",
        "02_testability.md": """---
gate: layer2_testability
gate_status: passed
sut_profile: synthetic-full-check
---

# Testability

## Layer Decision Matrix

| proposition | chosen layer | cheaper layer considered | reason |
|---|---|---|---|
| P-001 | integration | unit | The role chain needs a workspace-level evidence boundary. |

## Evidence Plan

| oracle | evidence source | probe or fixture | status |
|---|---|---|---|
| O-001 | generated execution log | deterministic synthetic runner | ready |

## Dependencies

- Local BUGate engine and required Memory service.

## Deferred Claims

- None.
""",
        "03_inventory.yaml": """gate: layer3_inventory
gate_status: passed
sut_profile: synthetic-full-check
cases:
  - id: CASE-001
    intent: Verify the accepted synthetic lifecycle outcome.
    priority: P1
    proposition_refs:
      - P-001
    oracle_refs:
      - O-001
    layer_decision: integration
    preconditions:
      - The generated fixture is initialized.
    data_source:
      source: generated neutral input
      status: ready
    expected_observations:
      - O-001 is recorded in the synthetic execution log.
    implementation_target: tests/test_ROLE_001.py
coverage_deferred: []
""",
        "03a_test_cases.md": """---
gate: readable_test_cases
gate_status: passed
sut_profile: synthetic-full-check
---

# Test Cases

## CASE-001

- Intent: Verify the accepted synthetic lifecycle outcome.
- Preconditions: The generated fixture is initialized.
- Steps: Run the deterministic synthetic check once.
- Expected result: O-001 is recorded as passed.
- Proposition refs: P-001
- Oracle refs: O-001
""",
        "03b_adversarial_cases.yaml": """gate: adversarial_cases
gate_status: passed
sut_profile: synthetic-full-check
dispatch_mode: not_required
adversarial_cases:
  - id: ADV-001
    risk: Duplicate synthetic input could obscure the single-outcome oracle.
    scenario: Submit the same generated neutral record twice.
    expected_oracle_pressure: The evidence still identifies one deterministic accepted outcome.
    disposition: accepted
residual_risks: []
""",
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (artifact_dir / name).write_text(body, encoding="utf-8")


def review_postrun_reports(artifact_dir: Path) -> None:
    """Record the fixture review outcome without inventing a human identity."""

    replacements = {
        "sut_profile: TBD": "sut_profile: synthetic-full-check",
        "- TBD after human review.": (
            "- Synthetic execution evidence produced no reusable SUT-specific finding."
        ),
        "- TBD if failure classification points to profile gaps.": (
            "- No profile update is required for the passing synthetic fixture."
        ),
    }
    for name in ("04_execution_report.md", "05_knowledge_update.md"):
        path = artifact_dir / name
        body = path.read_text(encoding="utf-8")
        body = body.replace("gate_status: draft", "gate_status: passed")
        for old, new in replacements.items():
            body = body.replace(old, new)
        path.write_text(body, encoding="utf-8")


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def role_receipts(evidence_dir: Path) -> list[dict[str, Any]]:
    return [
        value
        for value in (_json_object(path) for path in sorted((evidence_dir / "receipts").glob("*.json")))
        if value
    ]


def latest_role_receipt(evidence_dir: Path, event: str) -> dict[str, Any]:
    matches = [item for item in role_receipts(evidence_dir) if item.get("event") == event]
    return matches[-1] if matches else {}


def role_chain(evidence_dir: Path) -> dict[str, Any]:
    return _json_object(evidence_dir / "chain.json")


def command_contract(
    checks: list[Check],
    name: str,
    result: subprocess.CompletedProcess[str],
    *,
    expected_code: int,
    marker: str,
    expected_files: tuple[tuple[Path, bool], ...] = (),
    extra_ok: bool = True,
    extra_detail: str = "",
) -> bool:
    """Assert exit code, semantic output, and expected filesystem state.

    The detail intentionally reports only the contract signals.  Role/Memory
    command output is not echoed, so authentication headers can never enter the
    full-check report.
    """

    marker_ok = marker in result.stdout
    file_ok = all(path.exists() is expected for path, expected in expected_files)
    ok = result.returncode == expected_code and marker_ok and file_ok and extra_ok
    states = ", ".join(
        f"{path.name}={'present' if path.exists() else 'absent'}"
        for path, _ in expected_files
    )
    detail = (
        f"exit={result.returncode}/{expected_code}; "
        f"semantic_marker={'present' if marker_ok else 'missing'}; "
        f"file_state={states or 'not_applicable'}"
    )
    if extra_detail:
        detail += f"; {extra_detail}"
    add(checks, name, ok, detail)
    return ok


def role_environment(
    base: dict[str, str], role: str = "", session_id: str = ""
) -> dict[str, str]:
    env = dict(base)
    env["BUGATE_AGENT_ROLE"] = role
    env["BUGATE_SESSION_ID"] = session_id
    return env


def run_role_governance_flow(
    checks: list[Check], engine: Path, *, timeout: int, mode: str
) -> None:
    """Exercise the full required-mode lifecycle in a temporary imported repo."""

    try:
        paths = role_flow_engine_paths(engine)
    except SystemExit as exc:
        add(checks, "Wave 7 engine command provenance", False, str(exc))
        return
    provenance_ok = all(_within(path, engine.resolve()) for path in paths.values())
    add(
        checks,
        "Wave 7 engine command provenance",
        provenance_ok,
        f"{len(paths)} role-flow commands resolved under the selected engine",
    )
    if not provenance_ok:
        return

    with tempfile.TemporaryDirectory(prefix="bugate-role-flow.") as tmp:
        ws, artifact_dir, implementation = build_role_workspace(Path(tmp))
        evidence_dir = artifact_dir / "00_role_evidence"
        profile = ws / "bugate.profile.yaml"
        nonce = Path(tmp).name.replace("bugate-role-flow.", "")
        designer_session = f"designer-{nonce}"
        implementer_session = f"implementer-{nonce}"
        reviewer_session = f"reviewer-{nonce}"
        base_env = {
            "BUGATE_PROJECT_ROOT": str(ws),
            "BUGATE_ENGINE_ROOT": str(engine),
            "BUGATE_VENDOR_DIR": "",
            "BUGATE_PROFILE": "",
            "MEMORY_BUS_PROJECT_TAG": ROLE_FLOW_NAMESPACE,
        }
        orchestrator = [sys.executable, str(paths["orchestrator"]), str(artifact_dir)]
        role_cli = [sys.executable, str(paths["role_cli"])]

        # Required mode must block before even creating the artifact directory.
        result = run(
            [*orchestrator, "--init"],
            ws,
            env=role_environment(base_env),
            timeout=60,
        )
        command_contract(
            checks,
            "Role pre-code negative (role unset)",
            result,
            expected_code=2,
            marker="BUGATE_AGENT_ROLE is unset",
            expected_files=((artifact_dir, False),),
        )
        result = run(
            [*orchestrator, "--init"],
            ws,
            env=role_environment(base_env, "implementer", implementer_session),
            timeout=60,
        )
        command_contract(
            checks,
            "Role pre-code negative (wrong role)",
            result,
            expected_code=2,
            marker="is not allowed in pre_code",
            expected_files=((artifact_dir, False),),
        )

        designer_env = role_environment(base_env, "designer", designer_session)
        result = run([*orchestrator, "--init"], ws, env=designer_env, timeout=60)
        initialized = command_contract(
            checks,
            "Designer pre-code init",
            result,
            expected_code=0,
            marker="created 01_business_brief.md",
            expected_files=tuple((artifact_dir / name, True) for name in PRECODE_NAMES),
        )
        if not initialized:
            return

        precode_payload = json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": f"usecases/{ROLE_FLOW_UC}/01_business_brief.md"
                },
            }
        )
        result = run(
            [sys.executable, str(paths["role_hook"])],
            ws,
            input_text=precode_payload,
            env=designer_env,
            timeout=30,
        )
        add(
            checks,
            "Designer pre-code hook allow",
            result.returncode == 0 and (artifact_dir / "01_business_brief.md").exists(),
            f"exit={result.returncode}/0; initialized artifact remained present",
        )
        write_role_precode_artifacts(artifact_dir)

        result = run(
            [*orchestrator, "--auto", "--scope", "pre-code", "--skip-peer-review"],
            ws,
            env=designer_env,
            timeout=max(120, timeout),
        )
        auto_ok = command_contract(
            checks,
            "Designer pre-code auto (no real peer dispatch)",
            result,
            expected_code=0,
            marker="BUGate lifecycle status: READY_FOR_HUMAN_ACCEPTANCE",
            expected_files=(
                (artifact_dir / "00_multiview", False),
                (artifact_dir / "00_adversarial", False),
            ),
            extra_detail=f"mode={mode}; peer outputs absent",
        )
        result = run(
            [
                sys.executable,
                str(paths["semantic_gate"]),
                str(artifact_dir),
                "--scope",
                "pre-code",
                "--require-passed",
                "--profile",
                str(profile),
            ],
            ws,
            env=designer_env,
            timeout=60,
        )
        semantics_ok = command_contract(
            checks,
            "Designer pre-code accepted semantics",
            result,
            expected_code=0,
            marker="PASS",
            expected_files=tuple((artifact_dir / name, True) for name in PRECODE_NAMES),
        )
        if not (auto_ok and semantics_ok):
            return

        result = run(
            [
                *role_cli,
                "handoff",
                str(artifact_dir),
                "--phase",
                "pre_code",
                "--to",
                "implementer",
            ],
            ws,
            env=designer_env,
            timeout=60,
        )
        command_contract(
            checks,
            "Designer handoff negative (human acceptance missing)",
            result,
            expected_code=2,
            marker="required human acceptance receipt is missing",
            expected_files=((evidence_dir / "chain.json", False),),
            extra_ok=not role_receipts(evidence_dir),
            extra_detail="receipt_count=0",
        )

        unavailable_env = dict(designer_env)
        unavailable_env["MEMORY_BUS_URL"] = "http://127.0.0.1:1"
        result = run(
            [
                *role_cli,
                "approve",
                str(artifact_dir),
                "--approved-by",
                "synthetic-full-check-record",
            ],
            ws,
            env=unavailable_env,
            timeout=15,
        )
        command_contract(
            checks,
            "Strict Memory negative (no local acceptance on outage)",
            result,
            expected_code=2,
            marker="strict Memory transition failed before local receipt publication",
            expected_files=((evidence_dir / "chain.json", False),),
            extra_ok=not role_receipts(evidence_dir),
            extra_detail="receipt_count=0; unavailable endpoint isolated to child process",
        )

        result = run(
            [
                *role_cli,
                "approve",
                str(artifact_dir),
                "--approved-by",
                "synthetic-full-check-record",
            ],
            ws,
            env=designer_env,
            timeout=max(60, timeout),
        )
        chain = role_chain(evidence_dir)
        human_ok = command_contract(
            checks,
            "Synthetic human acceptance record (strict Memory)",
            result,
            expected_code=0,
            marker='"event": "human_acceptance"',
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(
                chain.get("sequence") == 1
                and chain.get("state") == "ready_for_designer_handoff"
            ),
            extra_detail=(
                f"sequence={chain.get('sequence', 0)}; state={chain.get('state', '<missing>')}"
            ),
        )
        if not human_ok:
            return

        result = run(
            [
                *role_cli,
                "handoff",
                str(artifact_dir),
                "--phase",
                "pre_code",
                "--to",
                "implementer",
            ],
            ws,
            env=designer_env,
            timeout=max(60, timeout),
        )
        designer_handoff = latest_role_receipt(evidence_dir, "designer_handoff")
        designer_memory_id = str(
            (designer_handoff.get("memory") or {}).get("memory_id") or ""
        )
        chain = role_chain(evidence_dir)
        handoff_ok = command_contract(
            checks,
            "Designer handoff (strict Memory)",
            result,
            expected_code=0,
            marker='"event": "designer_handoff"',
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(
                bool(designer_memory_id)
                and chain.get("sequence") == 2
                and chain.get("state") == "awaiting_implementer_acceptance"
            ),
            extra_detail=(
                f"exact_memory_id={'present' if designer_memory_id else 'missing'}; "
                f"sequence={chain.get('sequence', 0)}"
            ),
        )
        if not handoff_ok:
            return

        implementation_payload = json.dumps(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "input": (
                        "*** Begin Patch\n"
                        f"*** Add File: tests/{implementation.name}\n"
                        "+synthetic fixture\n"
                        "*** End Patch"
                    )
                },
            }
        )
        physical = run(
            [sys.executable, str(paths["physical_guard"])],
            ws,
            input_text=implementation_payload,
            env=role_environment(base_env, "implementer", implementer_session),
            timeout=30,
        )
        add(
            checks,
            "Layer 4 physical pre-code guard ready",
            physical.returncode == 0 and not implementation.exists(),
            f"exit={physical.returncode}/0; implementation_file=absent",
        )
        role_block = run(
            [sys.executable, str(paths["role_hook"])],
            ws,
            input_text=implementation_payload,
            env=role_environment(base_env, "implementer", implementer_session),
            timeout=30,
        )
        command_contract(
            checks,
            "Layer 4 role hook negative (acceptance missing)",
            role_block,
            expected_code=2,
            marker="implementer acceptance missing",
            expected_files=((implementation, False),),
            extra_ok=role_chain(evidence_dir).get("sequence") == 2,
            extra_detail="chain_sequence=2",
        )

        implementer_env = role_environment(
            base_env, "implementer", implementer_session
        )
        result = run(
            [
                *role_cli,
                "accept",
                str(artifact_dir),
                "--phase",
                "implementation",
                "--handoff-id",
                str(designer_handoff.get("receipt_sha256") or ""),
            ],
            ws,
            env=implementer_env,
            timeout=max(60, timeout),
        )
        command_contract(
            checks,
            "Implementer acceptance negative (not exact Memory ID)",
            result,
            expected_code=2,
            marker="required Memory mode accepts only the handoff's exact Memory ID",
            expected_files=((implementation, False),),
            extra_ok=(
                role_chain(evidence_dir).get("sequence") == 2
                and not latest_role_receipt(evidence_dir, "implementer_acceptance")
            ),
            extra_detail="chain_sequence=2; acceptance_receipt=absent",
        )
        same_session_env = role_environment(
            base_env, "implementer", designer_session
        )
        result = run(
            [
                *role_cli,
                "accept",
                str(artifact_dir),
                "--phase",
                "implementation",
                "--handoff-id",
                designer_memory_id,
            ],
            ws,
            env=same_session_env,
            timeout=max(60, timeout),
        )
        command_contract(
            checks,
            "Implementer acceptance negative (same session)",
            result,
            expected_code=2,
            marker="handoff and acceptance must use distinct session IDs",
            expected_files=((implementation, False),),
            extra_ok=role_chain(evidence_dir).get("sequence") == 2,
            extra_detail="chain_sequence=2",
        )

        result = run(
            [
                *role_cli,
                "accept",
                str(artifact_dir),
                "--phase",
                "implementation",
                "--handoff-id",
                designer_memory_id,
            ],
            ws,
            env=implementer_env,
            timeout=max(60, timeout),
        )
        chain = role_chain(evidence_dir)
        accept_ok = command_contract(
            checks,
            "Implementer acceptance (new session, exact Memory ID)",
            result,
            expected_code=0,
            marker='"event": "implementer_acceptance"',
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(
                chain.get("sequence") == 3
                and chain.get("state") == "implementation_unlocked"
            ),
            extra_detail=(
                f"sequence={chain.get('sequence', 0)}; state={chain.get('state', '<missing>')}"
            ),
        )
        if not accept_ok:
            return

        physical = run(
            [sys.executable, str(paths["physical_guard"])],
            ws,
            input_text=implementation_payload,
            env=implementer_env,
            timeout=30,
        )
        role_allow = run(
            [sys.executable, str(paths["role_hook"])],
            ws,
            input_text=implementation_payload,
            env=implementer_env,
            timeout=30,
        )
        chain = role_chain(evidence_dir)
        layer4_ok = (
            physical.returncode == 0
            and role_allow.returncode == 0
            and not implementation.exists()
            and chain.get("state") == "implementation_unlocked"
        )
        add(
            checks,
            "Layer 4 hooks allow accepted implementer",
            layer4_ok,
            (
                f"physical_exit={physical.returncode}/0; role_exit={role_allow.returncode}/0; "
                f"state={chain.get('state', '<missing>')}; implementation_file=absent-before-write"
            ),
        )
        if not layer4_ok:
            return

        implementation.write_text(
            """def test_synthetic_role_flow():
    assert "accepted" == "accepted"


if __name__ == "__main__":
    test_synthetic_role_flow()
    print("1 passed: synthetic role flow")
""",
            encoding="utf-8",
        )
        execution = run(
            [sys.executable, str(implementation)],
            ws,
            env=implementer_env,
            timeout=30,
        )
        execution_ok = command_contract(
            checks,
            "Synthetic Layer 4 implementation execution",
            execution,
            expected_code=0,
            marker="1 passed: synthetic role flow",
            expected_files=((implementation, True),),
        )
        if not execution_ok:
            return

        helper = ws / "tests" / "helper.py"
        helper.write_text("SYNTHETIC_HELPER = True\n", encoding="utf-8")
        result = run(
            [
                *role_cli,
                "handoff",
                str(artifact_dir),
                "--phase",
                "implementation",
                "--to",
                "reviewer",
                "--implementation-file",
                str(helper),
            ],
            ws,
            env=implementer_env,
            timeout=max(60, timeout),
        )
        command_contract(
            checks,
            "Implementer handoff negative (unguarded file)",
            result,
            expected_code=2,
            marker="does not match guarded_path_regex",
            expected_files=((helper, True),),
            extra_ok=(
                role_chain(evidence_dir).get("sequence") == 3
                and not latest_role_receipt(evidence_dir, "implementer_handoff")
            ),
            extra_detail="chain_sequence=3; handoff_receipt=absent",
        )

        result = run(
            [
                *role_cli,
                "handoff",
                str(artifact_dir),
                "--phase",
                "implementation",
                "--to",
                "reviewer",
                "--implementation-file",
                str(implementation),
            ],
            ws,
            env=implementer_env,
            timeout=max(60, timeout),
        )
        implementer_handoff = latest_role_receipt(evidence_dir, "implementer_handoff")
        implementer_memory_id = str(
            (implementer_handoff.get("memory") or {}).get("memory_id") or ""
        )
        chain = role_chain(evidence_dir)
        implementer_handoff_ok = command_contract(
            checks,
            "Implementer handoff (strict Memory)",
            result,
            expected_code=0,
            marker='"event": "implementer_handoff"',
            expected_files=((implementation, True),),
            extra_ok=(
                bool(implementer_memory_id)
                and chain.get("sequence") == 4
                and chain.get("state") == "awaiting_reviewer_acceptance"
            ),
            extra_detail=(
                f"exact_memory_id={'present' if implementer_memory_id else 'missing'}; "
                f"sequence={chain.get('sequence', 0)}"
            ),
        )
        if not implementer_handoff_ok:
            return

        log = ws / "evidence" / "pytest.log"
        log.parent.mkdir()
        log.write_text(execution.stdout, encoding="utf-8")
        reviewer_env = role_environment(base_env, "reviewer", reviewer_session)
        postrun_cmd = [
            *orchestrator,
            "--auto",
            "--scope",
            "post-run",
            "--pytest-log",
            str(log),
            "--command",
            "synthetic-role-flow",
            "--env",
            "synthetic-fixture",
            "--exit-code",
            "0",
        ]
        result = run(
            postrun_cmd,
            ws,
            env=reviewer_env,
            timeout=max(120, timeout),
        )
        command_contract(
            checks,
            "Post-run negative (reviewer acceptance missing)",
            result,
            expected_code=2,
            marker="reviewer acceptance missing",
            expected_files=(
                (artifact_dir / "04_execution_report.md", False),
                (artifact_dir / "05_knowledge_update.md", False),
                (artifact_dir / "self_healing.json", False),
            ),
            extra_ok=role_chain(evidence_dir).get("sequence") == 4,
            extra_detail="chain_sequence=4",
        )

        result = run(
            [
                *role_cli,
                "accept",
                str(artifact_dir),
                "--phase",
                "post_run",
                "--handoff-id",
                implementer_memory_id,
            ],
            ws,
            env=reviewer_env,
            timeout=max(60, timeout),
        )
        chain = role_chain(evidence_dir)
        reviewer_accept_ok = command_contract(
            checks,
            "Reviewer acceptance (new session, exact Memory ID)",
            result,
            expected_code=0,
            marker='"event": "reviewer_acceptance"',
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(
                chain.get("sequence") == 5
                and chain.get("state") == "post_run_active"
            ),
            extra_detail=(
                f"sequence={chain.get('sequence', 0)}; state={chain.get('state', '<missing>')}"
            ),
        )
        if not reviewer_accept_ok:
            return

        result = run(
            postrun_cmd,
            ws,
            env=reviewer_env,
            timeout=max(120, timeout),
        )
        postrun_ok = command_contract(
            checks,
            "Reviewer post-run orchestrator",
            result,
            expected_code=0,
            marker="BUGate lifecycle status: POST_RUN_ACTIVE",
            expected_files=(
                (artifact_dir / "04_execution_report.md", True),
                (artifact_dir / "05_knowledge_update.md", True),
                (artifact_dir / "self_healing.json", True),
            ),
            extra_ok=role_chain(evidence_dir).get("sequence") == 5,
            extra_detail="chain_sequence=5",
        )
        if not postrun_ok:
            return
        review_postrun_reports(artifact_dir)

        result = run(
            [
                sys.executable,
                str(paths["semantic_gate"]),
                str(artifact_dir),
                "--scope",
                "all",
                "--require-passed",
                "--profile",
                str(profile),
            ],
            ws,
            env=reviewer_env,
            timeout=60,
        )
        reports_ok = command_contract(
            checks,
            "Reviewer accepted post-run semantics",
            result,
            expected_code=0,
            marker="PASS",
            expected_files=(
                (artifact_dir / "04_execution_report.md", True),
                (artifact_dir / "05_knowledge_update.md", True),
            ),
        )
        if not reports_ok:
            return

        result = run(
            [
                *role_cli,
                "complete",
                str(artifact_dir),
                "--phase",
                "post_run",
                "--run-command",
                "synthetic-role-flow",
                "--exit-code",
                "0",
                "--evidence-file",
                str(log),
                "--gate-status",
                "passed",
            ],
            ws,
            env=reviewer_env,
            timeout=max(60, timeout),
        )
        chain = role_chain(evidence_dir)
        completion_ok = command_contract(
            checks,
            "Reviewer completion (strict Memory)",
            result,
            expected_code=0,
            marker='"event": "reviewer_completion"',
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(chain.get("sequence") == 6 and chain.get("state") == "closed"),
            extra_detail=(
                f"sequence={chain.get('sequence', 0)}; state={chain.get('state', '<missing>')}"
            ),
        )
        if not completion_ok:
            return

        result = run(
            [
                *role_cli,
                "verify",
                str(artifact_dir),
                "--strict-memory",
            ],
            ws,
            env=reviewer_env,
            timeout=max(120, timeout),
        )
        receipts = role_receipts(evidence_dir)
        events = [str(item.get("event") or "") for item in receipts]
        expected_events = [
            "human_acceptance",
            "designer_handoff",
            "implementer_acceptance",
            "implementer_handoff",
            "reviewer_acceptance",
            "reviewer_completion",
        ]
        command_contract(
            checks,
            "Strict Memory exact-ID verification and closed chain",
            result,
            expected_code=0,
            marker="PASS: role evidence is valid",
            expected_files=((evidence_dir / "chain.json", True),),
            extra_ok=(
                len(receipts) == 6
                and events == expected_events
                and role_chain(evidence_dir).get("state") == "closed"
                and all((item.get("memory") or {}).get("memory_id") for item in receipts)
            ),
            extra_detail=(
                f"receipt_count={len(receipts)}; exact_anchors="
                f"{sum(bool((item.get('memory') or {}).get('memory_id')) for item in receipts)}; "
                "auth_headers_not_rendered"
            ),
        )


def print_report(
    args: argparse.Namespace,
    root: Path,
    engine: Path,
    layout: str,
    checks: list[Check],
) -> int:
    """Render one final report and return its fail-closed exit status."""

    print("# BUGate Full Check")
    print()
    print(f"- Mode: `{args.mode}`")
    print(f"- Layout: `{layout}`")
    print(f"- Workspace: `{root}`")
    print(f"- Engine: `{engine}`")
    print()
    print("| Check | Status | Detail |")
    print("|---|---|---|")
    for check in checks:
        print(f"| {check.name} | {check.status} | {check.detail.replace('|', '/')} |")

    failures = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    print()
    if failures:
        print(f"Result: FAIL ({len(failures)} failed, {len(warnings)} warning).")
        return 1
    print(f"Result: PASS ({len(warnings)} warning).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke skips real peer model dispatch; full runs Codex+Claude peers.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()

    root, engine, layout = find_roots(Path.cwd().resolve())
    checks: list[Check] = []

    # An imported full-check is meaningful only for a verified, lock-based
    # installation.  Run this before compilation, runtime, Memory, or any
    # synthetic capability probe so a stale/tampered/recovery-required install
    # cannot produce a misleading partial green report.
    verify_imported_installed_state(
        checks,
        root,
        engine,
        layout,
        timeout=min(args.timeout_seconds, 60),
    )
    if any(check.status == "FAIL" for check in checks):
        # Do not execute a stale, tampered, or recovery-required vendored kit.
        # The updater verify command above is the sole imported preflight; once
        # it fails, even compilation would execute untrusted target bytes.
        return print_report(args, root, engine, layout, checks)

    def eng(rel: str) -> str:
        """Engine-relative path as string (works from any cwd in both layouts)."""
        return str(engine / rel)

    # Core gate.
    py_files = sorted(str(p) for p in (engine / "scripts").glob("*.py"))
    result = run(["python3", "-m", "py_compile", *py_files], root, timeout=60)
    add(checks, "Python compile", result.returncode == 0, result.stdout or "scripts compiled")

    with tempfile.TemporaryDirectory(prefix="bugate-core-gate.") as tmp:
        _, core_gate_env = build_probe_profile(Path(tmp), "core-gate")
        result = run(
            [
                "python3",
                eng("scripts/check_bugate_v13_semantics.py"),
                eng(".shared/skills/bugate/templates"),
                "--scope",
                "pre-code",
            ],
            root,
            env=core_gate_env,
            timeout=60,
        )
    add(checks, "4-layer gate engine (templates, pre-code)", result.returncode == 0, result.stdout)

    # Runtime binaries and auth.
    codex_path = shutil.which("codex") or "not_found"
    claude_path = shutil.which("claude") or "not_found"
    add(checks, "Codex binary", codex_path != "not_found", codex_path)
    add(checks, "Claude binary", claude_path != "not_found", claude_path)

    if codex_path != "not_found":
        result = run(["codex", "--version"], root, timeout=20)
        add(checks, "Codex version", result.returncode == 0, result.stdout)
        if args.mode == "full":
            result = run(
                codex_auth_command(),
                root,
                input_text="Reply exactly: ok\n",
                timeout=args.timeout_seconds,
            )
            add(checks, "Codex auth/model call", result.returncode == 0 and "ok" in result.stdout.lower(), result.stdout)
        else:
            checks.append(
                Check(
                    "Codex auth/model call",
                    "WARN",
                    "Skipped in smoke mode; no real model dispatch.",
                )
            )

    if claude_path != "not_found":
        result = run(["claude", "--version"], root, timeout=20)
        add(checks, "Claude version", result.returncode == 0, result.stdout)
        if args.mode == "full":
            result = run(
                claude_auth_command(),
                root,
                timeout=args.timeout_seconds,
            )
            add(checks, "Claude auth/model call", result.returncode == 0 and "ok" in result.stdout.lower(), result.stdout)
        else:
            checks.append(
                Check(
                    "Claude auth/model call",
                    "WARN",
                    "Skipped in smoke mode; no real model dispatch.",
                )
            )

    # Bridge environment.
    result = run(["python3", eng("scripts/sdtd_multiview_cli_bridge.py"), "check-env"], root, timeout=60)
    add(checks, "Multi-view check-env", result.returncode == 0 and "real_peer_dispatch" in result.stdout, result.stdout)
    result = run(["python3", eng("scripts/sdtd_adversarial_cli_bridge.py"), "check-env"], root, timeout=60)
    add(checks, "Adversarial check-env", result.returncode == 0 and "real_peer_dispatch" in result.stdout, result.stdout)

    if args.mode == "full":
        run_real_peer_dispatch_probe(
            checks,
            root,
            engine,
            timeout=args.timeout_seconds,
        )
    else:
        checks.append(Check("Real peer dispatch", "WARN", "Skipped in smoke mode; rerun with --mode full."))

    # Memory bus and ONNX.
    memory_probe_env = {"MEMORY_BUS_PROJECT_TAG": ROLE_FLOW_NAMESPACE}
    result = run(
        ["bash", eng("bin/memory-bus-status")],
        root,
        env=memory_probe_env,
        timeout=30,
    )
    add(checks, "Memory-bus status", result.returncode == 0 and "OK" in result.stdout, result.stdout)

    smoke = f"memory smoke {os.getpid()}"
    result = run(
        ["bash", eng("bin/memory-service-note"), "--agent", "agent", "--type", "finding", "--msg", smoke, "--tag", "full-check-smoke"],
        root,
        env=memory_probe_env,
        timeout=30,
    )
    add(checks, "Memory-bus note", result.returncode == 0, result.stdout)
    result = run(
        ["bash", eng("bin/memory-service-search"), "--query", smoke, "--tag", "full-check-smoke", "--limit", "1"],
        root,
        env=memory_probe_env,
        timeout=30,
    )
    add(checks, "Memory-bus search", result.returncode == 0 and smoke in result.stdout, result.stdout)

    # Required-mode Wave 7 integration.  This is a real strict Memory flow in
    # both modes, but its synthetic pre-code phase explicitly skips peer model
    # dispatch.  Full mode's separate peer fixture above remains responsible
    # for real Codex + Claude dispatch.
    run_role_governance_flow(
        checks,
        engine,
        timeout=args.timeout_seconds,
        mode=args.mode,
    )

    onnx_root = Path.home() / ".cache/mcp_memory/onnx_models"
    onnx_files = list(onnx_root.rglob("*.onnx")) if onnx_root.exists() else []
    add(checks, "ONNX model files", bool(onnx_files), f"{len(onnx_files)} .onnx file(s) under {onnx_root}")

    # System-level bus home, same resolution as bin/memory-bus-start:
    # MCP_MEMORY_BASE_DIR > BUGATE_MEMORY_HOME > ~/.bugate/memory-bus.
    bus_home = (
        os.environ.get("MCP_MEMORY_BASE_DIR")
        or os.environ.get("BUGATE_MEMORY_HOME")
        or str(Path.home() / ".bugate" / "memory-bus")
    )
    memory_env = {
        "MCP_MEMORY_BASE_DIR": bus_home,
        "MCP_MEMORY_STORAGE_BACKEND": "sqlite_vec",
        "MCP_MEMORY_USE_ONNX": "1",
        "PATH": f"{engine / '.venv/bin'}:{os.environ.get('PATH', '')}",
    }
    # Deep ONNX probe via the mcp-memory-service `memory` console. It is an
    # OPTIONAL deepening of the HTTP status above: the console is only present
    # where mcp-memory-service is pip-installed (engine .venv on a dev
    # checkout, or the machine-level bus home venv). Missing console => WARN,
    # not FAIL — release tarballs and imported repos don't ship it.
    memory_cli = next(
        (
            str(cand)
            for cand in (
                engine / ".venv/bin/memory",
                Path(bus_home) / ".venv/bin/memory",
            )
            if cand.exists()
        ),
        None,
    ) or shutil.which("memory")
    if memory_cli:
        result = run([memory_cli, "status"], root, env=memory_env, timeout=60)
        add(checks, "ONNX memory status", result.returncode == 0 and "healthy" in result.stdout.lower(), result.stdout)
    else:
        add(
            checks,
            "ONNX memory status",
            False,
            "mcp-memory-service `memory` console not found (engine .venv / bus-home venv / PATH); "
            "HTTP Memory-bus status above is the primary health signal.",
            warn=True,
        )

    # Wave 0 / Wave 8 — no committed demo specs: the capability probe is the
    # graceful-degradation contract (engine wired, reports profile_required,
    # exit 0 until a SUT profile supplies a real spec).
    # In an imported repo whose profile supplies a real spec, these engines run
    # for real: exit 0 without "profile_required" means the configured gate
    # actually passed, which is at least as strong as the degrade contract.
    result = run(["python3", eng("scripts/check_prd_health.py"), "--gate"], root, timeout=60)
    w0_configured = result.returncode == 0 and "profile_required" not in result.stdout
    add(
        checks,
        "Wave 0 engine" + (" (configured gate passed)" if w0_configured else " (profile_required degrade)"),
        result.returncode == 0,
        result.stdout,
    )
    result = run(["python3", eng("scripts/oracle_falsification.py"), "--gate"], root, timeout=60)
    w8_configured = result.returncode == 0 and "profile_required" not in result.stdout
    add(
        checks,
        "Wave 8 engine" + (" (configured gate passed)" if w8_configured else " (profile_required degrade)"),
        result.returncode == 0,
        result.stdout,
    )
    result = run(["python3", eng("scripts/generate_assertion_coverage_matrix.py"), "--help"], root, timeout=30)
    add(
        checks,
        "Wave 8 coverage-matrix CLI present",
        result.returncode == 0,
        result.stdout or "argparse help ok",
    )

    # Write guard and role isolation — fabricated governed workspace.
    with tempfile.TemporaryDirectory(prefix="bugate-guard.") as tmp:
        ws = build_guard_workspace(Path(tmp))
        guard = eng("scripts/check_bugate.py")
        neutral_env = {
            "BUGATE_PROFILE": str(ws / "bugate.profile.yaml"),
            "BUGATE_PROJECT_ROOT": str(ws),
        }
        result = run([sys.executable, guard, "tests/ok/test_x.py"],
                     root, cwd=ws, input_text="", env=neutral_env, timeout=30)
        add(checks, "Write guard allows passed UC", result.returncode == 0, result.stdout or "allowed")
        pending_target = ws / "tests/pending/test_x.py"
        pending_before = pending_target.read_bytes()
        result = run([sys.executable, guard, "tests/pending/test_x.py"],
                     root, cwd=ws, input_text="", env=neutral_env, timeout=30)
        pending_unchanged = pending_target.read_bytes() == pending_before
        add(
            checks,
            "Write guard blocks pending UC",
            (
                outcome_matches(
                    result,
                    2,
                    "BUGate guard blocked edits to configured implementation paths",
                )
                and pending_unchanged
            ),
            (
                f"exit={result.returncode}/2; semantic_marker="
                f"{'present' if 'BUGate guard blocked edits to configured implementation paths' in result.stdout else 'missing'}; "
                f"target_unchanged={pending_unchanged}"
            ),
        )

        role_env = {**neutral_env, "BUGATE_AGENT_ROLE": "implementer"}
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "mirror/spec.md"}})
        result = run(["python3", eng("scripts/check_agent_role_paths.py")],
                     root, cwd=ws, input_text=payload, env=role_env, timeout=30)
        forbidden_absent = not (ws / "mirror/spec.md").exists()
        add(
            checks,
            "Role guard blocks forbidden path",
            (
                outcome_matches(result, 2, "BUGate agent-role path isolation")
                and forbidden_absent
            ),
            (
                f"exit={result.returncode}/2; semantic_marker="
                f"{'present' if 'BUGate agent-role path isolation' in result.stdout else 'missing'}; "
                f"target_absent={forbidden_absent}"
            ),
        )
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "tests/ok/test_x.py"}})
        result = run(["python3", eng("scripts/check_agent_role_paths.py")],
                     root, cwd=ws, input_text=payload, env=role_env, timeout=30)
        add(checks, "Role guard allows permitted path", result.returncode == 0, result.stdout or "allowed")

    # Hardening flags enforce (fabricated): a template-initialized UC must be
    # REJECTED once the probe-owned profile demands the Wave-1 multiview report.
    run_hardening_multiview_probe(checks, root, engine)

    # Alternate semantic-schema dialect: dialect selection must be a real fork —
    # a minimal original-gate Layer-1 brief passes under --schema original-gate
    # and is rejected by the canonical v1.3 schema (canonical ids + sections).
    with tempfile.TemporaryDirectory(prefix="bugate-dialect.") as tmp:
        uc = Path(tmp)
        _, dialect_env = build_probe_profile(uc, "dialect")
        (uc / "01_business_brief.md").write_text(
            "---\ngate: layer1_business_brief\ngate_status: passed\n---\n\n"
            "## SUT And Scope\nA neutral request flow under test.\n\n"
            "## Canonical Business Flow\nSubmit -> validate -> settle.\n\n"
            "## Assertions That Follow From Business\n- A settled request is queryable.\n\n"
            "## Unknowns And Questions\n- None open.\n",
            encoding="utf-8",
        )
        result = run(
            ["python3", eng("scripts/check_bugate_brief_semantics.py"), str(uc),
             "--require-passed", "--schema", "original-gate"],
            root, env=dialect_env, timeout=60,
        )
        alt_ok = result.returncode == 0
        brief_before = (uc / "01_business_brief.md").read_bytes()
        result = run(
            ["python3", eng("scripts/check_bugate_brief_semantics.py"), str(uc),
             "--require-passed", "--schema", "v1.3"],
            root, env=dialect_env, timeout=60,
        )
        default_marker = "must define at least one P-xxx proposition"
        rejects_default = (
            result.returncode == 1
            and default_marker in result.stdout
            and (uc / "01_business_brief.md").read_bytes() == brief_before
        )
        add(
            checks,
            "Alternate dialect (original-gate, Layer 1)",
            alt_ok and rejects_default,
            (
                f"original-gate={'ok' if alt_ok else 'FAIL'}; "
                f"v1.3_exit={result.returncode}/1; semantic_marker="
                f"{'present' if default_marker in result.stdout else 'missing'}; "
                f"brief_unchanged={(uc / '01_business_brief.md').read_bytes() == brief_before}"
            ),
        )

    # Config boundary.
    result = run(
        [
            "python3",
            "-c",
            f"import sys; sys.path.insert(0, {str(engine / 'scripts')!r}); "
            "import bugate_core; print(bugate_core.load_config())",
        ],
        root,
        timeout=30,
    )
    if result.returncode != 0:
        add(checks, "Activation boundary", False, result.stdout)
    else:
        guards_inactive = "'guarded_path_regex': []" in result.stdout
        checks.append(
            Check(
                "Activation boundary",
                "WARN" if guards_inactive else "PASS",
                (
                    "No guarded paths configured; real SUT gates require an (activated) imported SUT profile."
                    if guards_inactive
                    else f"Guarded SUT profile active ({layout} layout): " + compact(result.stdout)
                ),
            )
        )

    return print_report(args, root, engine, layout, checks)


if __name__ == "__main__":
    raise SystemExit(main())
