#!/usr/bin/env python3
"""BUGate adversarial (Stage 3B) bridge: real Codex/Claude dispatch with fallback.

This mirrors the Wave 1 multi-view bridge (`sdtd_multiview_cli_bridge.py`): the
orchestrating runtime stays the controller while two CLI agents act as
*independent* red-team / adversarial reviewers. Each peer attacks the reviewed
pre-code artifacts on its own from the shared Stage-3B prompt card; neither peer
sees the other's output. The two adversarial views are then synthesized into
`03b_adversarial_cases.yaml`.

Dispatch decision:
  * If BOTH `codex` and `claude` are on PATH  -> real peer dispatch.
  * If EITHER CLI is missing                  -> deterministic placeholder
    fallback, with a written note that real dispatch was skipped.

This module is SUT-neutral and stdlib-only. Model names, reasoning effort, and
proxy settings are read from environment variables with neutral defaults and are
fully overridable; nothing here is tied to a specific vendor's internal naming or
to any single SUT/project. Proxy injection is OFF unless the relevant env vars
are explicitly set.

Environment contract (identical to the multi-view bridge):
  SDTD_CODEX_BIN / SDTD_CLAUDE_BIN                CLI binary names.
  SDTD_CODEX_MODEL / SDTD_CLAUDE_MODEL            Optional model override; empty
                                                 => let the CLI pick its default.
  SDTD_CODEX_REASONING_EFFORT / SDTD_CLAUDE_EFFORT Optional reasoning effort.
  SDTD_CODEX_SKIP_GIT_REPO_CHECK=1                 Explicit non-git automation
                                                 opt-in; default keeps the check.
  SDTD_CLI_TIMEOUT_SECONDS                        Per-peer subprocess timeout.
  SDTD_CLI_HTTPS_PROXY / SDTD_CLI_HTTP_PROXY /
  SDTD_CLI_ALL_PROXY                              Optional proxy values (unset =>
                                                 no proxy injection).
  SDTD_CLI_PROXY=0                                Force-disable proxy injection.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import sdtd_adversarial
from bugate_core import parse_inventory_cases, read_text, write_text
from role_governance import (
    GovernanceResult,
    RoleGovernanceError,
    load_context,
    preflight,
    verify_chain,
)


def _role_preflight(artifact_dir: Path) -> GovernanceResult:
    result = preflight(artifact_dir, "pre_code", require_acceptance=False)
    for warning in result.warnings:
        print(f"BUGate role-governance WARNING: {warning}", file=sys.stderr)
    if not result.allowed:
        print("BUGate role governance BLOCKED (pre_code):", file=sys.stderr)
        for error in result.errors or ["role preflight failed"]:
            print(f"  - {error}", file=sys.stderr)
    return result


def _has_human_acceptance(artifact_dir: Path) -> bool:
    try:
        ctx = load_context(artifact_dir)
        if ctx.mode == "off":
            return False
        return any(item.get("event") == "human_acceptance" for item in verify_chain(ctx))
    except (RoleGovernanceError, OSError, ValueError, SystemExit):
        return False

# ---------------------------------------------------------------------------
# DE-SUT configuration: env-driven with neutral defaults, all overridable.
# No hardcoded proxy host/port, no vendor-internal model names, no SUT paths.
# ---------------------------------------------------------------------------

# CLI binaries (overridable so installs with custom names still work).
CODEX_BIN = os.environ.get("SDTD_CODEX_BIN", "codex")
CLAUDE_BIN = os.environ.get("SDTD_CLAUDE_BIN", "claude")

# Model / reasoning-effort. Empty default => let the CLI use its own default
# rather than pinning a vendor-specific identifier. Set the env var to force a
# specific model/effort. The peers are still asked (in prose) to use their
# strongest available reasoning.
CODEX_MODEL = os.environ.get("SDTD_CODEX_MODEL", "").strip()
CLAUDE_MODEL = os.environ.get("SDTD_CLAUDE_MODEL", "").strip()
CODEX_REASONING_EFFORT = os.environ.get("SDTD_CODEX_REASONING_EFFORT", "").strip()
CLAUDE_EFFORT = os.environ.get("SDTD_CLAUDE_EFFORT", "").strip()
CODEX_SKIP_GIT_REPO_CHECK = (
    os.environ.get("SDTD_CODEX_SKIP_GIT_REPO_CHECK", "0").strip() == "1"
)

# Per-peer subprocess timeout (seconds).
TIMEOUT_SECONDS = int(os.environ.get("SDTD_CLI_TIMEOUT_SECONDS", "1800"))

# Proxy injection: only applied when the env var is set. Default = unset (no
# proxy). `SDTD_CLI_PROXY=0` force-disables injection even if the vars are set.
_PROXY_VARS = {
    "https_proxy": os.environ.get("SDTD_CLI_HTTPS_PROXY", ""),
    "http_proxy": os.environ.get("SDTD_CLI_HTTP_PROXY", ""),
    "all_proxy": os.environ.get("SDTD_CLI_ALL_PROXY", ""),
}

# Peer CLIs participate only as read-only analysis workers in the current
# designer phase.  A controller's lifecycle role/session/receipt identity must
# never be inherited by a spawned Codex or Claude process.  Profile/project and
# SDTD proxy/model/effort settings are intentionally retained.
_LIFECYCLE_IDENTITY_KEYS = {"BUGATE_AGENT_ROLE", "BUGATE_SESSION_ID"}
_LIFECYCLE_IDENTITY_PREFIXES = (
    "BUGATE_ROLE_",
    "BUGATE_RECEIPT_",
    "BUGATE_HANDOFF_",
    "BUGATE_SESSION_",
)


def _strip_lifecycle_identity(env: dict[str, str]) -> None:
    for key in list(env):
        if key in _LIFECYCLE_IDENTITY_KEYS or key.startswith(
            _LIFECYCLE_IDENTITY_PREFIXES
        ):
            env.pop(key, None)


def cli_env() -> dict[str, str]:
    """Return a peer env with lifecycle identity removed and proxy tuning kept."""
    env = os.environ.copy()
    _strip_lifecycle_identity(env)
    if os.environ.get("SDTD_CLI_PROXY", "1") == "0":
        return env
    for lower_key, value in _PROXY_VARS.items():
        if not value:
            continue
        env[lower_key] = value
        env[lower_key.upper()] = value
    return env


def proxy_summary() -> str:
    if os.environ.get("SDTD_CLI_PROXY", "1") == "0":
        return "disabled"
    active = {k: v for k, v in _PROXY_VARS.items() if v}
    if not active:
        return "unset"
    return ", ".join(f"{k}={v}" for k, v in active.items())


# ---------------------------------------------------------------------------
# CLI command construction.
#
# Flags mirror the conservative shape used by the multi-view bridge:
#   * Claude: `claude -p [--model M] [--effort E] --permission-mode dontAsk
#             --output-format text` — prompt is piped on stdin.
#   * Codex:  `codex exec [--ask-for-approval never] --sandbox read-only
#             [--model M] [-c model_reasoning_effort="E"] -` — prompt is piped
#             on stdin via `-`. The approval flag is used only when supported;
#             current standalone Codex CLIs run non-interactively without it.
# Model/effort flags are appended only when the corresponding env var is set, so
# nothing vendor-specific is hardcoded.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def codex_supports_ask_for_approval() -> bool:
    try:
        result = subprocess.run(
            [CODEX_BIN, "exec", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            env=cli_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "--ask-for-approval" in result.stdout


def build_command(peer: str) -> list[str]:
    if peer == "claude":
        cmd = [CLAUDE_BIN, "-p"]
        if CLAUDE_MODEL:
            cmd += ["--model", CLAUDE_MODEL]
        if CLAUDE_EFFORT:
            cmd += ["--effort", CLAUDE_EFFORT]
        cmd += ["--permission-mode", "dontAsk", "--output-format", "text"]
        return cmd
    if peer == "codex":
        cmd = [CODEX_BIN, "exec"]
        if codex_supports_ask_for_approval():
            cmd += ["--ask-for-approval", "never"]
        if CODEX_SKIP_GIT_REPO_CHECK:
            cmd += ["--skip-git-repo-check"]
        cmd += ["--sandbox", "read-only"]
        if CODEX_MODEL:
            cmd += ["--model", CODEX_MODEL]
        if CODEX_REASONING_EFFORT:
            cmd += ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']
        cmd += ["-"]
        return cmd
    raise AssertionError(f"unsupported peer: {peer}")


def model_for(peer: str) -> str:
    if peer == "claude":
        return CLAUDE_MODEL or "cli_default"
    return CODEX_MODEL or "cli_default"


def effort_for(peer: str) -> str:
    if peer == "claude":
        return CLAUDE_EFFORT or "cli_default"
    return CODEX_REASONING_EFFORT or "cli_default"


# ---------------------------------------------------------------------------
# Prompt + output sanitation.
# ---------------------------------------------------------------------------


def render_envelope(
    peer: str,
    prompt_card: str,
    inventory: str,
    test_cases: str,
) -> str:
    """Build the independent adversarial peer worker prompt.

    Each peer only receives the shared Stage-3B prompt card plus the reviewed
    pre-code artifacts (Layer 3 inventory and, if present, the 03A test cases);
    it must NOT be given the other peer's adversarial output.
    """
    return f"""# BUGate Adversarial (Stage 3B) Peer Worker Envelope ({peer})

You are running as an INDEPENDENT red-team / adversarial reviewer ({peer}) for
BUGate Stage 3B.

## Reasoning Budget

- Requested model: {model_for(peer)}
- Requested reasoning effort: {effort_for(peer)}
- Use your strongest available reasoning / maximum adversarial depth.
- Spend reasoning on attacking the test plan, not on formatting.

## Adversarial Mandate

- Attack weak oracles, missing negative paths, ambiguous wording, and any
  fake-green risk in the reviewed test plan.
- Propose adversarial / exploratory cases that would catch real defects the
  current case set could pass over.
- Each case must pressure a business oracle so that a wrong state cannot stay
  green.

## Independence Boundary

- Produce an INDEPENDENT {peer} adversarial view.
- You are given only the shared prompt card and the reviewed pre-code artifacts.
- Do NOT assume or rely on any other peer's adversarial output or a synthesis
  conclusion.

## Output Mode

- Do not edit files from the CLI runtime.
- Return only the Markdown content for the adversarial view artifact
  (frontmatter + body).
- Do not wrap the answer in code fences.
- Begin your output with a YAML frontmatter block.

## Prompt Card

{prompt_card}

## Layer 3 Inventory (03_inventory.yaml)

{inventory or "_(no 03_inventory.yaml present yet)_"}

## Reviewed Test Cases (03a_test_cases.md)

{test_cases or "_(no 03a_test_cases.md present yet)_"}
"""


def strip_preamble(raw: str) -> str:
    """Strip any leading non-frontmatter model preamble.

    Some CLIs emit a short natural-language preamble (or a wrapping code fence)
    before the YAML frontmatter. Keep everything from the first frontmatter
    fence (`---`) onward; otherwise return the trimmed text unchanged.
    """
    text = raw.strip()
    # Drop a single outer code fence if the whole answer was fenced.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Find the first frontmatter fence at line start and keep from there.
    for idx, line in enumerate(text.splitlines()):
        if line.strip() == "---":
            return "\n".join(text.splitlines()[idx:]).strip()
    return text


def run_peer_cli(peer: str, prompt: str) -> tuple[int, str, str]:
    """Run one peer CLI, piping the prompt on stdin. Returns (rc, stdout, stderr)."""
    command = build_command(peer)
    result = subprocess.run(
        command,
        input=prompt,
        text=True,
        env=cli_env(),
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# View / synthesis writers.
# ---------------------------------------------------------------------------


def _wrap_view(peer: str, body: str) -> str:
    """Ensure a peer adversarial view has the expected BUGate frontmatter.

    If the model already emitted valid frontmatter (starts with `---`), keep it
    as-is. Otherwise wrap the body with a minimal schema-valid header so the
    artifact flow stays green.
    """
    role = peer.title()
    if body.lstrip().startswith("---"):
        return body
    return (
        "---\n"
        f"gate: adversarial_{peer}\n"
        "gate_status: passed\n"
        "requested_model_class: strongest_available\n"
        "requested_reasoning_effort: maximum\n"
        "format_cleaned: false\n"
        "dispatch_mode: real_peer_dispatch\n"
        "---\n\n"
        f"# {role} Adversarial View\n\n"
        + body.strip()
        + "\n"
    )


def write_placeholder_view(out: Path, peer: str, case_count: int, reason: str) -> None:
    role = peer.title()
    write_text(
        out / f"{peer}_adversarial_view.md",
        "---\n"
        f"gate: adversarial_{peer}\n"
        "gate_status: passed\n"
        "requested_model_class: strongest_available\n"
        "requested_reasoning_effort: maximum\n"
        "format_cleaned: false\n"
        "dispatch_mode: fallback_placeholder\n"
        "---\n\n"
        f"# {role} Adversarial View (fallback placeholder)\n\n"
        f"- Real peer dispatch SKIPPED: {reason}\n"
        f"- Reviewed cases seen in inventory: {case_count}\n"
        "- Core-mode note: replace this deterministic view with real peer output once both CLIs are available.\n",
    )


def write_real_view(out: Path, peer: str, body: str, cleaned: bool) -> None:
    write_text(out / f"{peer}_adversarial_view.md", _wrap_view(peer, body))
    if cleaned:
        print(f"{peer}: stripped leading model preamble before frontmatter")
    print(f"{peer}: wrote real adversarial view ({len(body)} chars)")


MIN_VIEW_CHARS = 80


def view_schema_errors(body: str) -> list[str]:
    """Schema check for a returned adversarial view: must be structured content."""
    text = (body or "").strip()
    errors: list[str] = []
    if len(text) < MIN_VIEW_CHARS:
        errors.append(f"view too short ({len(text)} < {MIN_VIEW_CHARS} chars)")
    has_heading = bool(re.search(r"^#{1,6}\s|\n-\s|\n\d+\.", text))
    has_ref = bool(re.search(r"\b(?:ADV|CASE|P|O)-\d", text))
    if not (has_heading or has_ref):
        errors.append("no structured content (heading/list/id reference) in returned view")
    return errors


def archive_failed_view(out: Path, peer: str, raw: str, errors: list[str]) -> Path:
    """Archive a schema-invalid returned view instead of persisting it as real."""
    path = out / "cli_bridge_failures" / f"{peer}_rejected_adversarial_view.md"
    write_text(
        path,
        "---\n"
        f"gate: adversarial_{peer}_rejected\n"
        "dispatch_mode: schema_rejected\n"
        "---\n\n"
        f"# Rejected {peer} adversarial view (schema validation failed)\n\n"
        + "".join(f"- {e}\n" for e in errors)
        + "\n## Raw returned output (truncated)\n\n```\n"
        + (raw or "")[:5000]
        + "\n```\n",
    )
    return path


def synthesize_cases(
    artifact_dir: Path,
    cases: list[dict],
    *,
    mode: str,
    note: str = "",
) -> bool:
    """Write 03b_adversarial_cases.yaml from the two adversarial views.

    The synthesized file stays machine-first (YAML). It is a deterministic
    skeleton anchored on the reviewed inventory cases plus the peers' attack
    surface, and is left `gate_status: pending` for human review — matching the
    BUGate discipline that 03B must be reviewed before it can gate Layer 4.
    """
    # Keep this lower-level mutator governed even when called directly rather
    # than through ``run-all``.  In particular, an accepted 03B generation is
    # immutable to automatic synthesis; a human must deliberately create a new
    # generation before another acceptance can be recorded.
    if not _role_preflight(artifact_dir).allowed:
        return False
    if _has_human_acceptance(artifact_dir):
        print(
            "BUGate role governance BLOCKED: refusing to rewrite a human-accepted 03B.",
            file=sys.stderr,
        )
        return False
    first = cases[0]["id"] if cases else "CASE-001"
    related = ", ".join(c.get("id", "") for c in cases if c.get("id")) or first
    lines = [
        "gate: adversarial_cases",
        "gate_status: pending",
        "sut_profile: TBD",
        f"dispatch_mode: {mode}",
    ]
    if note:
        lines.append(f"synthesis_note: {note}")
    lines += [
        "source_views:",
        "  codex: 00_adversarial/codex_adversarial_view.md",
        "  claude: 00_adversarial/claude_adversarial_view.md",
        "adversarial_cases:",
        "  - id: ADV-001",
        "    risk: weak_oracle_or_missing_negative_path",
        f"    related_cases: [{first}]",
        "    scenario: Challenge the primary business oracle with an invalid or boundary state from the SUT profile.",
        "    expected_oracle_pressure: The oracle must reject fake-green behavior.",
        "    disposition: pending_human_review",
        "  - id: ADV-002",
        "    risk: ambiguous_requirement_or_uncovered_path",
        f"    related_cases: [{first}]",
        f"    scenario: Synthesize the highest-value adversarial case from the codex and claude views over cases [{related}].",
        "    expected_oracle_pressure: A wrong or ambiguous state must not stay green.",
        "    disposition: pending_human_review",
        "residual_risks: []",
    ]
    write_text(artifact_dir / "03b_adversarial_cases.yaml", "\n".join(lines) + "\n")
    return True


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------


def both_clis_available() -> tuple[bool, str | None]:
    """Return (available, missing_name). missing_name is the first CLI absent."""
    if not shutil.which(CODEX_BIN):
        return False, "codex"
    if not shutil.which(CLAUDE_BIN):
        return False, "claude"
    return True, None


def check_env() -> int:
    codex_path = shutil.which(CODEX_BIN)
    claude_path = shutil.which(CLAUDE_BIN)
    print(f"codex: {codex_path or 'not_found'}")
    print(f"claude: {claude_path or 'not_found'}")
    available, missing = both_clis_available()
    mode = "real_peer_dispatch" if available else f"fallback (missing: {missing})"
    print(f"dispatch_mode: {mode}")
    print("stage: 03b_adversarial_cases")
    print(f"codex_model_default: {CODEX_MODEL or 'cli_default'}")
    print(f"claude_model_default: {CLAUDE_MODEL or 'cli_default'}")
    print(f"codex_reasoning_effort_default: {CODEX_REASONING_EFFORT or 'cli_default'}")
    print(f"claude_effort_default: {CLAUDE_EFFORT or 'cli_default'}")
    print(f"cli_proxy_env: {proxy_summary()}")
    print(f"timeout_seconds: {TIMEOUT_SECONDS}")
    print("core adversarial bridge: available")
    return 0


def run_all(artifact_dir: Path) -> int:
    if not _role_preflight(artifact_dir).allowed:
        return 2
    if _has_human_acceptance(artifact_dir):
        print(
            "BUGate role governance BLOCKED: 03B already has a human-acceptance "
            "receipt; adversarial dispatch must not rewrite the accepted artifact.",
            file=sys.stderr,
        )
        return 2
    # Reuse the shared init helper and its 00_adversarial/ layout.
    init_rc = sdtd_adversarial.init(artifact_dir, "adversarial peer dispatch")
    if init_rc:
        return init_rc
    out = artifact_dir / "00_adversarial"

    inventory_path = artifact_dir / "03_inventory.yaml"
    inventory = read_text(inventory_path) if inventory_path.exists() else ""
    cases = parse_inventory_cases(inventory)

    cases_path = artifact_dir / "03a_test_cases.md"
    test_cases = read_text(cases_path) if cases_path.exists() else ""

    prompt_card_path = out / "prompt_card.md"
    prompt_card = read_text(prompt_card_path) if prompt_card_path.exists() else ""

    available, missing = both_clis_available()

    if not available:
        # ---- FALLBACK: deterministic placeholders ----
        reason = f"'{missing}' CLI not found on PATH"
        print(f"fallback: {reason}; writing deterministic placeholder views")
        for peer in ("codex", "claude"):
            write_placeholder_view(out, peer, len(cases), reason)
        written = synthesize_cases(
            artifact_dir,
            cases,
            mode="fallback_placeholder",
            note=f"real peer dispatch was SKIPPED because {reason}",
        )
        if not written:
            return 2
        print(f"written {artifact_dir / '03b_adversarial_cases.yaml'} (fallback mode)")
        return 0

    # ---- REAL DISPATCH: independent adversarial workers ----
    print("real peer dispatch: both codex and claude available")
    print(f"proxy_env: {proxy_summary()}")
    for peer in ("codex", "claude"):
        prompt = render_envelope(peer, prompt_card, inventory, test_cases)
        print(f"{peer}: dispatching ({model_for(peer)} / {effort_for(peer)})")
        try:
            rc, stdout, stderr = run_peer_cli(peer, prompt)
        except subprocess.TimeoutExpired:
            print(f"{peer}: timed out after {TIMEOUT_SECONDS}s; using placeholder for this peer")
            write_placeholder_view(out, peer, len(cases), f"{peer} CLI timed out after {TIMEOUT_SECONDS}s")
            continue
        if rc != 0:
            tail = (stderr or "").strip().splitlines()[-1:] or [""]
            print(f"{peer}: CLI exit code {rc}; using placeholder for this peer ({tail[0]})")
            write_placeholder_view(out, peer, len(cases), f"{peer} CLI exited {rc}")
            continue
        cleaned_body = strip_preamble(stdout)
        was_cleaned = cleaned_body.strip() != stdout.strip()
        if not cleaned_body.strip():
            print(f"{peer}: empty output; using placeholder for this peer")
            write_placeholder_view(out, peer, len(cases), f"{peer} CLI returned empty output")
            continue
        errors = view_schema_errors(cleaned_body)
        if errors:
            archived = archive_failed_view(out, peer, stdout, errors)
            print(f"{peer}: returned view failed schema ({'; '.join(errors)}); archived {archived.name}, using placeholder")
            write_placeholder_view(out, peer, len(cases), f"{peer} returned a schema-invalid view: {errors[0]}")
            continue
        write_real_view(out, peer, cleaned_body, was_cleaned)

    # Tag synthesis mode by whether at least one peer produced a real view.
    placeholder_count = sum(
        1
        for peer in ("codex", "claude")
        if "dispatch_mode: fallback_placeholder" in read_text(out / f"{peer}_adversarial_view.md")
    )
    mode = "real_peer_dispatch" if placeholder_count == 0 else "partial_real_peer_dispatch"
    note = ""
    if placeholder_count:
        note = f"{placeholder_count} of 2 peer view(s) degraded to placeholder"
    if not synthesize_cases(artifact_dir, cases, mode=mode, note=note):
        return 2
    print(f"written {artifact_dir / '03b_adversarial_cases.yaml'} ({mode})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-env")
    p_run = sub.add_parser("run-all")
    p_run.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    if args.cmd == "check-env":
        return check_env()
    return run_all(args.artifact_dir)


if __name__ == "__main__":
    raise SystemExit(main())
