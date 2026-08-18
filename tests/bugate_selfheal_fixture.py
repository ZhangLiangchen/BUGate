#!/usr/bin/env python3
"""SUT-neutral synthetic imported-repo fixture for the self-healing suites.

The two self-healing test files fabricate a governed imported repository in a
temporary directory and drive the real Core CLIs against it.  Nothing here
touches a real system under test: the profile, the use case, the implementation
file and the runner log are all constructed in ``tempfile`` space.

This module is deliberately NOT named ``test_*.py`` so the CI loop
(``for t in tests/test_*.py``) does not execute it as a suite.  Run it directly
to regenerate the v0.4.4 golden bytes that pin the zero-behavior-change
contract::

    python3 tests/bugate_selfheal_fixture.py --regenerate-golden

Regeneration is only valid from a clean checkout of the engine revision the
golden files claim (``git describe`` = ``v0.4.4``); the generator refuses to run
against a tree whose report renderer has already been rewritten unless
``--allow-dirty`` is passed for an explicitly reviewed re-baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1]
SCRIPTS = ENGINE / "scripts"
ORCHESTRATOR = SCRIPTS / "sdtd_orchestrator.py"
ROLE_CLI = SCRIPTS / "role_governance.py"
GOLDEN_DIR = ENGINE / "tests" / "fixtures" / "golden"

PRECODE = (
    "01_business_brief.md",
    "02_testability.md",
    "03_inventory.yaml",
    "03a_test_cases.md",
    "03b_adversarial_cases.yaml",
)
POSTRUN = ("04_execution_report.md", "05_knowledge_update.md")
HEALING_OUTPUTS = (
    "self_healing.json",
    "self_healing.md",
    "self_healing_repair_plan.md",
)

# The canonical post-run inputs used for every golden capture.  They are all
# relative/literal so the rendered reports carry no absolute temporary path.
GOLDEN_COMMAND = "python3 -m unittest"
GOLDEN_ENV = "fixture"
GOLDEN_LOG_NAME = "pytest.log"

# A failing runner log that deliberately trips the documented v0.4.4 classifier
# imprecision (CF-05): "connection" and "status code" co-fire, so the legacy
# verdict is blocked_by_exclusion.  Pinning that behavior is the point -- the
# `off` path must keep it byte for byte.
FAILING_LOG = """\
============================= test session starts ==============================
collected 1 item

tests/test_UC_SELFHEAL.py::test_recorded_outcome FAILED

=================================== FAILURES ===================================
______________________________ test_recorded_outcome ___________________________

    def test_recorded_outcome():
        response = probe.query("recorded-outcome")
>       assert response.status_code == 200
E       AssertionError: expected 200 got 500
E       assert 500 == 200

tests/test_UC_SELFHEAL.py:14: AssertionError
--------------------------- Captured log call ----------------------------------
DEBUG probe: reusing pooled connection to the captured contract fixture
=========================== short test summary info ============================
FAILED tests/test_UC_SELFHEAL.py::test_recorded_outcome - AssertionError
============================== 1 failed in 0.11s ===============================
"""

PASSING_LOG = """\
============================= test session starts ==============================
collected 1 item

tests/test_UC_SELFHEAL.py::test_recorded_outcome PASSED

============================== 1 passed in 0.05s ===============================
"""

GOLDEN_SCENARIOS = {
    # scenario name -> (log body, exit code)
    "failed": (FAILING_LOG, 1),
    "passed": (PASSING_LOG, 0),
}


def closed_port_url() -> str:
    """Reserve then release a loopback port; connect attempts fail immediately."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_snapshot(path: Path) -> tuple[tuple[str, str, str], ...] | None:
    """Content-addressed ``lstat`` tree snapshot of every node.

    ``is_dir()``/``is_file()`` follow symlinks, so a dangling symlink left
    behind by a step that claims to write nothing matches neither test and
    disappears from the snapshot entirely.  A symlink to a directory likewise
    used to be indistinguishable from a real directory.  Classify by ``lstat``
    instead and record the link target, so a collateral write of *any* node type
    is a snapshot difference.  Empty directories were already covered and stay
    covered.
    """

    if not path.exists() and not path.is_symlink():
        return None
    entries: list[tuple[str, str, str]] = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path).as_posix()
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            entries.append(("symlink", rel, os.readlink(item)))
        elif stat.S_ISDIR(mode):
            entries.append(("dir", rel, ""))
        elif stat.S_ISREG(mode):
            entries.append(("file", rel, sha256(item)))
        else:
            entries.append(("special", rel, oct(stat.S_IFMT(mode))))
    return tuple(entries)


def render_yaml_block(key: str, value: dict[str, object], indent: int = 0) -> list[str]:
    """Emit a minimal nested-YAML block understood by ``parse_nested_yaml``."""

    pad = " " * indent
    lines = [f"{pad}{key}:"]
    for name, item in value.items():
        child = " " * (indent + 2)
        if isinstance(item, dict):
            lines.extend(render_yaml_block(name, item, indent + 2))
        elif isinstance(item, list):
            if not item:
                lines.append(f"{child}{name}: []")
            else:
                lines.append(f"{child}{name}:")
                for element in item:
                    lines.append(f'{child}  - "{element}"')
        elif isinstance(item, bool):
            lines.append(f"{child}{name}: {'true' if item else 'false'}")
        else:
            lines.append(f"{child}{name}: {item}")
    return lines


class ImportedFixture:
    """Minimal SUT-neutral imported repository with a profile-owned UC."""

    uc = "UC-SELFHEAL"

    def __init__(
        self,
        base: Path,
        *,
        mode: str | None = "required",
        self_healing: dict[str, object] | None = None,
    ) -> None:
        self.base = base
        self.root = base / "governed-sut-tests"
        self.root.mkdir(parents=True)
        self.memory_home = base / "memory-home"
        self.memory_home.mkdir(mode=0o700)
        self.profile = self.root / "bugate.profile.yaml"
        self.artifact = self.root / "usecases" / self.uc
        self.implementation = self.root / "tests" / f"test_{self.uc}.py"
        self.memory_url = closed_port_url()
        self.mode = mode
        (self.root / "bugate.config.yaml").write_text(
            "profile: bugate.profile.yaml\n", encoding="utf-8"
        )
        self.write_profile(self_healing)

    # ---------------------------------------------------------------- profile

    def write_profile(self, self_healing: dict[str, object] | None) -> None:
        lines = [
            "artifact_dir_template: usecases/{uc}",
            "guarded_path_regex:",
            '  - "^tests/test_(?P<uc>[^/]+)[.]py$"',
            "required_precode_artifacts:",
            *[f"  - {name}" for name in PRECODE],
            "memory:",
            "  namespace: project:selfheal-fixture",
        ]
        if self.mode is not None:
            lines.extend(
                [
                    "role_governance:",
                    f"  mode: {self.mode}",
                    "  memory_mode: best_effort",
                    "  evidence_dir: 00_role_evidence",
                    "  session_id_required: true",
                    "  require_distinct_sessions: true",
                    "  human_acceptance_artifacts:",
                    "    - 03b_adversarial_cases.yaml",
                    "  phases:",
                    "    pre_code:",
                    "      allowed_roles:",
                    "        - designer",
                    "    implementation:",
                    "      allowed_roles:",
                    "        - implementer",
                    "      requires_handoff_from:",
                    "        - designer",
                    "    post_run:",
                    "      allowed_roles:",
                    "        - reviewer",
                    "      requires_handoff_from:",
                    "        - implementer",
                ]
            )
        if self_healing is not None:
            lines.extend(render_yaml_block("self_healing", self_healing))
        self.profile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def set_self_healing(self, self_healing: dict[str, object] | None) -> None:
        self.write_profile(self_healing)

    # ------------------------------------------------------------ process env

    def env(self, *, role: str | None = None, session: str | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if (
                key
                in {
                    "BUGATE_AGENT_ROLE",
                    "BUGATE_AGENT_RUNTIME",
                    "BUGATE_SESSION_ID",
                    "BUGATE_PROFILE",
                    "BUGATE_PROJECT_ROOT",
                    "BUGATE_ENGINE_ROOT",
                    "MEMORY_BUS_URL",
                    "MEMORY_BUS_PROJECT_TAG",
                    "MCP_API_KEY",
                    "MCP_API_KEY_AGENT",
                    "MCP_API_KEY_HUMAN",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                }
                or key.startswith("BUGATE_ROLE_")
                or key.startswith("BUGATE_RECEIPT_")
                or key.startswith("BUGATE_HANDOFF_")
                or key.startswith("BUGATE_SESSION_")
                or key.startswith("BUGATE_SELF_HEAL_")
            ):
                env.pop(key, None)
        env.update(
            {
                "BUGATE_PROJECT_ROOT": str(self.root),
                "BUGATE_ENGINE_ROOT": str(ENGINE),
                "BUGATE_PROFILE": str(self.profile),
                "MEMORY_BUS_URL": self.memory_url,
                "MEMORY_BUS_PROJECT_TAG": "project:selfheal-fixture",
                "MCP_MEMORY_BASE_DIR": str(self.memory_home),
                "BUGATE_MEMORY_HOME": str(self.memory_home),
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (str(SCRIPTS), os.environ.get("PYTHONPATH", "")))
                ),
            }
        )
        if role is not None:
            env["BUGATE_AGENT_ROLE"] = role
            env["BUGATE_AGENT_RUNTIME"] = "codex"
        if session is not None:
            env["BUGATE_SESSION_ID"] = session
        return env

    def run(
        self,
        command: list[str | Path],
        *,
        role: str | None = None,
        session: str | None = None,
        runtime: str | None = None,
        extra_env: dict[str, str] | None = None,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env(role=role, session=session)
        if runtime is not None:
            env["BUGATE_AGENT_RUNTIME"] = runtime
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(item) for item in command],
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    # -------------------------------------------------------------- lifecycle

    def _role_json(
        self, args: list[str | Path], *, role: str, session: str
    ) -> dict[str, object]:
        proc = self.run([sys.executable, ROLE_CLI, *args], role=role, session=session)
        if proc.returncode != 0:
            raise AssertionError(
                f"role CLI failed: {args}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return json.loads(proc.stdout)

    def init(self) -> None:
        proc = self.run(
            [sys.executable, ORCHESTRATOR, self.artifact, "--init"],
            role="designer",
            session="designer-session",
        )
        if proc.returncode != 0:
            raise AssertionError(f"init failed:\n{proc.stdout}\n{proc.stderr}")
        status = self.run(
            [sys.executable, ROLE_CLI, "lineage-status", self.artifact, "--json"],
            role="designer",
            session="designer-session",
        )
        lineage_id = str(json.loads(status.stdout)["lineage_id"])
        established = self.run(
            [sys.executable, ROLE_CLI, "lineage-init", self.artifact, "--lineage-id", lineage_id],
            role="designer",
            session="designer-session",
        )
        if established.returncode != 0:
            raise AssertionError(
                f"lineage-init failed:\n{established.stdout}\n{established.stderr}"
            )

    def write_accepted_precode(self) -> None:
        """Replace init templates with a small, semantically accepted UC stack."""

        artifacts = {
            "01_business_brief.md": """---
gate: layer1_business_brief
gate_status: passed
sut_profile: fixture
---

# Business Brief

## Scope

Validate one neutral request outcome from captured contract evidence.

## Canonical Business Flows

Submit a request, observe acceptance, and query the recorded outcome.

## Clarification Gate

| dimension | status | open question |
|---|---|---|
| objective | clear | none |

## Propositions

| id | proposition | priority | verifiability | evidence_label | source |
|---|---|---|---|---|---|
| P-001 | An accepted request is queryable. | high | verifiable | fact | captured contract |

## Business Oracles

| id | oracle | observable evidence | evidence_label |
|---|---|---|---|
| O-001 | The query returns the accepted state. | captured response state | fact |

## Boundaries

Only the declared request and query flow is governed.

## Assumptions

The captured contract is current for this fixture.

## Open Questions

None open.
""",
            "02_testability.md": """---
gate: layer2_testability
gate_status: passed
sut_profile: fixture
---

# Testability

## Layer Decision Matrix

| proposition | chosen layer | cheaper layer considered | reason |
|---|---|---|---|
| P-001 | contract | static review | O-001 requires an observable response |

## Evidence Plan

| oracle | evidence source | probe or fixture | status |
|---|---|---|---|
| O-001 | captured contract | deterministic fixture probe | resolved |

## Dependencies

The imported test repository supplies the fixture probe.

## Deferred Claims

None.
""",
            "03_inventory.yaml": """gate: layer3_inventory
gate_status: passed
sut_profile: fixture
cases:
  - id: CASE-001
    intent: Verify the accepted request remains queryable
    priority: P1
    proposition_refs:
      - P-001
    oracle_refs:
      - O-001
    layer_decision: contract
    preconditions:
      - fixture request is accepted
    data_source:
      source: captured contract
      status: resolved
    expected_observations:
      - accepted state is returned
    implementation_target: tests/test_UC-SELFHEAL.py
coverage_deferred: []
""",
            "03a_test_cases.md": """---
gate: readable_test_cases
gate_status: passed
sut_profile: fixture
---

# Test Cases

## CASE-001

- Intent: Verify the accepted request remains queryable.
- Preconditions: The fixture request is accepted.
- Action: Query the recorded outcome.
- Expected result: O-001 returns the accepted state for P-001.
""",
            "03b_adversarial_cases.yaml": """gate: adversarial_cases
gate_status: passed
sut_profile: fixture
dispatch_mode: real_peer_dispatch
adversarial_cases:
  - id: ADV-001
    risk: A duplicate query could expose inconsistent state
    scenario: Query the accepted outcome twice
    expected_oracle_pressure: Both observations must satisfy O-001
    disposition: absorbed
residual_risks: []
""",
        }
        for name, body in artifacts.items():
            (self.artifact / name).write_text(body, encoding="utf-8")

    DEFAULT_IMPLEMENTATION = (
        "def test_recorded_outcome():\n"
        '    response = probe.query("recorded-outcome")\n'
        "    assert response.status_code == 200\n"
    )

    # A self-executing test file: ``python3 tests/test_UC-SELFHEAL.py`` is then a
    # complete, dependency-free verification command, so the sandbox before/after
    # runs need no pytest and no importable module name.
    BROKEN_IMPLEMENTATION = (
        "OBSERVED = 200\n"
        "\n"
        "\n"
        "def test_recorded_outcome():\n"
        "    assert exepcted == OBSERVED\n"
        "\n"
        "\n"
        "test_recorded_outcome()\n"
    )
    REPAIRED_IMPLEMENTATION = (
        "OBSERVED = 200\n"
        "\n"
        "\n"
        "def test_recorded_outcome():\n"
        "    expected = 200\n"
        "    assert expected == OBSERVED\n"
        "\n"
        "\n"
        "test_recorded_outcome()\n"
    )
    #: The command that reproduces the failure and proves the repair.
    VERIFICATION_COMMAND = "python3 tests/test_UC-SELFHEAL.py"

    def write_implementation(self, body: str | None = None) -> None:
        self.implementation.parent.mkdir(parents=True, exist_ok=True)
        self.implementation.write_text(body or self.DEFAULT_IMPLEMENTATION, encoding="utf-8")

    def drive_to_post_run(self, implementation: str | None = None) -> None:
        """Advance the fixture UC to a valid ``post_run_active`` lifecycle state.

        ``implementation`` is written *before* the implementer handoff so it is
        the snapshotted content.  Rewriting it afterwards is exactly the
        "artifact drift" the engine is supposed to reject, so a test that wants
        a broken test asset has to declare it here.
        """

        self.init()
        self.write_accepted_precode()
        self._role_json(
            ["approve", self.artifact, "--approved-by", "fixture-owner"],
            role="designer",
            session="designer-session",
        )
        designer_handoff = self._role_json(
            ["handoff", self.artifact, "--phase", "pre_code", "--to", "implementer"],
            role="designer",
            session="designer-session",
        )
        self._role_json(
            [
                "accept",
                self.artifact,
                "--phase",
                "implementation",
                "--handoff-id",
                str(designer_handoff["receipt_sha256"]),
            ],
            role="implementer",
            session="implementer-session",
        )
        self.write_implementation(implementation)
        implementer_handoff = self._role_json(
            [
                "handoff",
                self.artifact,
                "--phase",
                "implementation",
                "--to",
                "reviewer",
                "--implementation-file",
                self.implementation,
            ],
            role="implementer",
            session="implementer-session",
        )
        self._role_json(
            [
                "accept",
                self.artifact,
                "--phase",
                "post_run",
                "--handoff-id",
                str(implementer_handoff["receipt_sha256"]),
            ],
            role="reviewer",
            session="reviewer-session",
        )

    # ------------------------------------------------------------ post-run run

    def write_log(self, body: str) -> Path:
        path = self.root / GOLDEN_LOG_NAME
        path.write_text(body, encoding="utf-8")
        return path

    def run_postrun(
        self,
        *,
        exit_code: int,
        session: str = "reviewer-session",
        command: str = GOLDEN_COMMAND,
        env_label: str = GOLDEN_ENV,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(
            [
                sys.executable,
                ORCHESTRATOR,
                self.artifact,
                "--auto",
                "--scope",
                "post-run",
                "--pytest-log",
                GOLDEN_LOG_NAME,
                "--command",
                command,
                "--env",
                env_label,
                "--exit-code",
                str(exit_code),
            ],
            role="reviewer",
            session=session,
        )

    def run_self_heal(
        self,
        *args: str,
        role: str = "reviewer",
        session: str = "reviewer-session",
        runtime: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(
            [
                sys.executable,
                ORCHESTRATOR,
                self.artifact,
                "--scope",
                "self-heal",
                *args,
            ],
            role=role,
            session=session,
            runtime=runtime,
            extra_env=extra_env,
        )

    # ------------------------------------------------------- self-heal inputs

    def write_falsification_spec(self, *, killing: bool = True) -> Path:
        """A SUT-neutral oracle/mutation spec plus its pristine evidence.

        ``killing=False`` writes a mutation the oracle cannot detect, so the
        kill rate lands below the default 0.7 threshold -- the negative control
        for "the repair passed but the oracle proves nothing".
        """

        evidence = self.root / "evidence"
        evidence.mkdir(exist_ok=True)
        (evidence / "probe.json").write_text(
            '{"status": "accepted", "note": "unchecked"}\n', encoding="utf-8"
        )
        contract = self.implementation.parent / "contract.json"
        if contract.exists() and killing:
            oracle_path = "expected"
            oracle_value = "200"
            mutation_path = "expected"
            evidence_relative = "tests/contract.json"
            mutation_value = "500"
        else:
            oracle_path = "status"
            oracle_value = "accepted"
            mutation_path = "status" if killing else "note"
            evidence_relative = "evidence/probe.json"
            mutation_value = "rejected"

        spec = self.root / "falsification_spec.yaml"
        spec.write_text(
            "oracles:\n"
            "  - id: O-001\n"
            "    assert:\n"
            "      - op: equals\n"
            f"        path: {oracle_path}\n"
            f"        value: {oracle_value}\n"
            "mutations:\n"
            "  - id: M-001\n"
            "    op: set\n"
            f"    path: {mutation_path}\n"
            f"    value: {mutation_value}\n"
            "evidence:\n"
            f"  - {evidence_relative}\n",
            encoding="utf-8",
        )
        return spec

    def candidate(self, files: dict[str, str], name: str = "candidate") -> Path:
        """Build a candidate tree of workspace-relative proposed file contents."""

        root = self.base / name
        if root.exists():
            shutil.rmtree(root)
        for relative, body in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return root

    def write_review(
        self,
        *,
        verdict: str = "approved",
        dispatch_mode: str = "real_peer_dispatch",
        findings: list[dict[str, str]] | None = None,
        residual_risks: list[str] | None = None,
        binding: dict[str, object] | None = None,
        runtime: str = "codex",
        schema: str = "bugate.self-heal-review/v1",
        name: str = "review.json",
    ) -> str:
        """An independent reviewer verdict document; returns its relative path."""

        if binding is None:
            attempt_id = self.sidecar_attempt_id()
            context_path = (
                self.artifact
                / "00_self_healing"
                / "attempts"
                / attempt_id
                / "review_context.json"
            )
            context = json.loads(context_path.read_text(encoding="utf-8"))
            binding = dict(context["binding"])
        document = {
            "schema": schema,
            "verdict": verdict,
            "dispatch_mode": dispatch_mode,
            "runtime": runtime,
            "findings": findings
            if findings is not None
            else [{"claim": "assertions preserved", "evidence": "candidate.patch"}],
            "residual_risks": residual_risks if residual_risks is not None else [],
            "binding": binding,
        }
        (self.root / name).write_text(json.dumps(document, indent=2), encoding="utf-8")
        return name

    # ------------------------------------------------------ flow convenience

    def enabled_policy(self, mode: str = "verify", **overrides: object) -> dict[str, object]:
        policy: dict[str, object] = {
            "mode": mode,
            "allowed_write_regex": [r"^tests/test_.*\.py$"],
            "verification_commands": [self.VERIFICATION_COMMAND],
            "falsification_spec": "falsification_spec.yaml",
            "max_attempts": 3,
        }
        policy.update(overrides)
        return policy

    def advance_to_healing_active(self, log: str) -> None:
        """triage -> handoff -> accept, leaving a fresh healer session in place."""

        self.write_log(log)
        self.run_self_heal(
            "--pytest-log", GOLDEN_LOG_NAME, "--command", GOLDEN_COMMAND, "--exit-code", "1"
        )
        self.run_self_heal("--self-heal-step", "handoff")
        self.run_self_heal(
            "--self-heal-step", "accept", role="implementer", session="healer-session"
        )

    def sidecar_attempt_id(self) -> str:
        chain = self.artifact / "00_self_healing" / "chain.json"
        if not chain.exists():
            return ""
        entries = json.loads(chain.read_text(encoding="utf-8")).get("entries") or []
        return str(entries[-1].get("attempt_id")) if entries else ""

    def sidecar_state(self) -> str:
        chain = self.artifact / "00_self_healing" / "chain.json"
        if not chain.exists():
            return ""
        data = json.loads(chain.read_text(encoding="utf-8"))
        if data.get("drift"):
            return "attempt_invalidated_by_lifecycle_drift"
        entries = data.get("entries") or []
        return str(entries[-1].get("state")) if entries else ""


def gate_result(proc: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Parse the frozen five-field JSON the self-heal entry prints."""

    head = proc.stdout.split("BUGate self-heal status:")[0]
    return json.loads(head)


# ------------------------------------------------------------ golden capture


def golden_name(artifact_name: str, scenario: str) -> str:
    """``04_execution_report.md`` + ``failed`` -> ``04_execution_report.v0.4.4.md``.

    The ``failed`` scenario owns the contract-named golden files (stage-2
    contract section 8.2); every other scenario carries its name as an extra
    qualifier.
    """

    stem, _, suffix = artifact_name.rpartition(".")
    qualifier = "" if scenario == "failed" else f".{scenario}"
    return f"{stem}.v0.4.4{qualifier}.{suffix}"


def capture_golden(destination: Path) -> list[Path]:
    """Run the post-run chain per scenario and copy its outputs to ``destination``."""

    written: list[Path] = []
    destination.mkdir(parents=True, exist_ok=True)
    for scenario, (log_body, exit_code) in sorted(GOLDEN_SCENARIOS.items()):
        with tempfile.TemporaryDirectory(prefix=f"bugate-golden-{scenario}-") as tmp:
            fixture = ImportedFixture(Path(tmp))
            fixture.drive_to_post_run()
            fixture.write_log(log_body)
            proc = fixture.run_postrun(exit_code=exit_code)
            if proc.returncode != 0:
                raise SystemExit(
                    f"post-run chain failed for {scenario}:\n{proc.stdout}\n{proc.stderr}"
                )
            for name in (*POSTRUN, *HEALING_OUTPUTS):
                source = fixture.artifact / name
                if not source.exists():
                    raise SystemExit(f"expected post-run output is missing: {name}")
                target = destination / golden_name(name, scenario)
                target.write_bytes(source.read_bytes())
                written.append(target)
    return written


def _renderer_is_pristine() -> bool:
    """True while ``generate_sdtd_reports.py`` still carries the v0.4.4 renderer."""

    body = (SCRIPTS / "generate_sdtd_reports.py").read_text(encoding="utf-8")
    return "def render_04" not in body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regenerate-golden",
        action="store_true",
        help="rewrite tests/fixtures/golden from this checkout's actual output",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="regenerate even after the report renderer was rewritten (re-baseline)",
    )
    args = parser.parse_args()
    if not args.regenerate_golden:
        parser.print_help()
        return 0
    if not _renderer_is_pristine() and not args.allow_dirty:
        print(
            "refusing to regenerate: generate_sdtd_reports.py no longer carries the "
            "v0.4.4 renderer, so the captured bytes would not prove zero behavior "
            "change. Re-run from a clean v0.4.4 checkout, or pass --allow-dirty for "
            "a deliberate re-baseline.",
            file=sys.stderr,
        )
        return 2
    for path in capture_golden(GOLDEN_DIR):
        print(f"written {path.relative_to(ENGINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
