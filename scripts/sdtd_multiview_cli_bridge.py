#!/usr/bin/env python3
"""BUGate multi-view bridge: real Codex/Claude peer dispatch with fallback.

The bridge keeps the orchestrating runtime as the controller while using two CLI
agents as *independent* peer workers. Each peer extracts propositions, oracles,
gaps, and risks on its own from the shared prompt card plus the Layer 1 brief
draft; neither peer sees the other's output. A divergence report is then
synthesized by diffing the two views' proposition-id sets.

Dispatch decision:
  * If BOTH `codex` and `claude` are on PATH  -> real peer dispatch.
  * If EITHER CLI is missing                  -> deterministic placeholder
    fallback, with a written note that real dispatch was skipped.

This module is SUT-neutral and stdlib-only. Model names, reasoning effort, and
proxy settings are read from environment variables with neutral defaults and are
fully overridable; nothing here is tied to a specific vendor's internal naming or
to any single SUT/project. Proxy injection is OFF unless the relevant env vars
are explicitly set.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import os
import re
import shutil
import subprocess
from pathlib import Path

import sdtd_multiview
from bugate_core import (
    PROPOSITION_PATTERN,
    load_config,
    proposition_ids,
    read_text,
    resolve_schema_name,
    semantic_schema,
    write_text,
)


def proposition_pattern_for(artifact_dir: Path) -> str | None:
    """Resolve the active dialect's proposition-id pattern for this UC dir.

    Mirrors the semantic gates' schema resolution: a SUT profile in scope selects
    its dialect, so a prose-assertion dialect (no P-/O- ids) returns ``None`` and
    the bridge stops requiring/diffing proposition ids. Defaults to the canonical
    v1.3 pattern when no profile applies or config can't be read.
    """
    try:
        config = load_config(profile=os.environ.get("BUGATE_PROFILE"))
        return semantic_schema(resolve_schema_name(artifact_dir, config)).get("proposition_pattern")
    except Exception:
        return PROPOSITION_PATTERN

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

# Per-peer subprocess timeout (seconds).
TIMEOUT_SECONDS = int(os.environ.get("SDTD_CLI_TIMEOUT_SECONDS", "1800"))

# Proxy injection: only applied when the env var is set. Default = unset (no
# proxy). `SDTD_CLI_PROXY=0` force-disables injection even if the vars are set.
_PROXY_VARS = {
    "https_proxy": os.environ.get("SDTD_CLI_HTTPS_PROXY", ""),
    "http_proxy": os.environ.get("SDTD_CLI_HTTP_PROXY", ""),
    "all_proxy": os.environ.get("SDTD_CLI_ALL_PROXY", ""),
}


def cli_env() -> dict[str, str]:
    """Child-process env with optional proxy injection (only if vars are set)."""
    env = os.environ.copy()
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
# Flags mirror the conservative shape used by the reference dispatcher:
#   * Claude: `claude -p [--model M] [--effort E] --permission-mode dontAsk
#             --output-format text` — prompt is piped on stdin.
#   * Codex:  `codex exec [--ask-for-approval never] --sandbox read-only
#             [--model M] [-c model_reasoning_effort="E"] -` — prompt is piped
#             on stdin via `-`. The approval flag is used only when supported;
#             current standalone Codex CLIs run non-interactively without it.
# Model/effort flags are appended only when the corresponding env var is set, so
# nothing vendor-specific is hardcoded. If you are unsure about flag support for
# your CLI build, override the binary or unset the model/effort env vars to fall
# back to the CLI's own defaults.
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
        # Non-interactive, plain-text output suitable for capturing on stdout.
        cmd += ["--permission-mode", "dontAsk", "--output-format", "text"]
        return cmd
    if peer == "codex":
        cmd = [CODEX_BIN, "exec"]
        if codex_supports_ask_for_approval():
            cmd += ["--ask-for-approval", "never"]
        cmd += ["--sandbox", "read-only"]
        if CODEX_MODEL:
            cmd += ["--model", CODEX_MODEL]
        if CODEX_REASONING_EFFORT:
            cmd += ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']
        # Trailing "-" makes codex exec read the prompt from stdin.
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


def render_envelope(peer: str, prompt_card: str, brief: str) -> str:
    """Build the independent peer worker prompt.

    Each peer only receives the shared prompt card and the Layer 1 brief draft;
    it must NOT be given the other peer's output.
    """
    return f"""# BUGate Multi-View Peer Worker Envelope ({peer})

You are running as an INDEPENDENT peer reviewer ({peer}) for BUGate Wave 1.

## Reasoning Budget

- Requested model: {model_for(peer)}
- Requested reasoning effort: {effort_for(peer)}
- Use your strongest available reasoning / maximum effort.
- Spend reasoning on independent requirement understanding, not on formatting.

## Independence Boundary

- Produce an INDEPENDENT {peer} view.
- You are given only the shared prompt card and the Layer 1 business brief draft.
- Do NOT assume or rely on any other peer's output or a divergence conclusion.

## Output Mode

- Do not edit files from the CLI runtime.
- Return only the Markdown content for the view artifact (frontmatter + body).
- Do not wrap the answer in code fences.
- Begin your output with a YAML frontmatter block.

## Prompt Card

{prompt_card}

## Layer 1 Business Brief Draft

{brief or "_(no 01_business_brief.md draft present yet)_"}
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
# View / divergence writers.
# ---------------------------------------------------------------------------


def _wrap_view(peer: str, body: str) -> str:
    """Ensure a peer view has the expected BUGate frontmatter.

    If the model already emitted valid frontmatter (starts with `---`), keep it
    as-is. Otherwise wrap the body with a minimal schema-valid header so the
    artifact flow and `sdtd_multiview.check` stay green.
    """
    role = peer.title()
    if body.lstrip().startswith("---"):
        return body
    return (
        "---\n"
        f"gate: multiview_{peer}\n"
        "gate_status: passed\n"
        "requested_model_class: strongest_available\n"
        "requested_reasoning_effort: maximum\n"
        "format_cleaned: false\n"
        "---\n\n"
        f"# {role} View\n\n"
        + body.strip()
        + "\n"
    )


def write_placeholder_view(out: Path, peer: str, propositions: list[str], reason: str) -> None:
    role = peer.title()
    write_text(
        out / f"{peer}_view.md",
        "---\n"
        f"gate: multiview_{peer}\n"
        "gate_status: passed\n"
        "requested_model_class: strongest_available\n"
        "requested_reasoning_effort: maximum\n"
        "format_cleaned: false\n"
        "dispatch_mode: fallback_placeholder\n"
        "---\n\n"
        f"# {role} View (fallback placeholder)\n\n"
        f"- Real peer dispatch SKIPPED: {reason}\n"
        f"- Proposition count seen in brief draft: {len(propositions)}\n"
        f"- Propositions: {', '.join(propositions) if propositions else '(none yet)'}\n"
        "- Core-mode note: replace this deterministic view with real peer output once both CLIs are available.\n",
    )


def write_real_view(out: Path, peer: str, body: str, cleaned: bool) -> None:
    write_text(out / f"{peer}_view.md", _wrap_view(peer, body))
    if cleaned:
        print(f"{peer}: stripped leading model preamble before frontmatter")
    print(f"{peer}: wrote real peer view ({len(body)} chars)")


MIN_VIEW_CHARS = 40


def view_schema_errors(body: str, pattern: str | None) -> list[str]:
    """Schema check for a returned Wave 1 peer view.

    Always requires substantive content. Requires proposition ids only when the
    active dialect defines a proposition-id scheme (``pattern``); a prose-assertion
    dialect (``pattern is None``) is validated on length + structure instead, so a
    real peer view is no longer rejected just for lacking P-xxx ids.
    """
    text = (body or "").strip()
    errors: list[str] = []
    if len(text) < MIN_VIEW_CHARS:
        errors.append(f"view too short ({len(text)} < {MIN_VIEW_CHARS} chars)")
    if pattern:
        if not proposition_ids(text, pattern):
            errors.append("no proposition ids found in returned view")
    elif not re.search(r"^#{1,6}\s|\n[-*]\s|\n\d+\.", text):
        errors.append("no structured content (heading/list) in returned view")
    return errors


def archive_failed_view(out: Path, peer: str, raw: str, errors: list[str]) -> Path:
    """Archive a schema-invalid returned view instead of persisting it as real."""
    path = out / "cli_bridge_failures" / f"{peer}_rejected_view.md"
    write_text(
        path,
        "---\n"
        f"gate: multiview_{peer}_rejected\n"
        "dispatch_mode: schema_rejected\n"
        "---\n\n"
        f"# Rejected {peer} peer view (schema validation failed)\n\n"
        + "".join(f"- {e}\n" for e in errors)
        + "\n## Raw returned output (truncated)\n\n```\n"
        + (raw or "")[:5000]
        + "\n```\n",
    )
    return path


def synthesize_divergence(
    out: Path,
    codex_view: str,
    claude_view: str,
    *,
    mode: str,
    note: str = "",
    pattern: str | None = PROPOSITION_PATTERN,
) -> None:
    """Diff the two views' proposition-id sets and write divergence_report.md.

    When the active dialect has no proposition-id scheme (``pattern is None``),
    id-set diffing is not applicable: capture both views and defer to human review.
    """
    if not pattern:
        lines = [
            "---",
            "gate: multiview_divergence",
            "gate_status: passed",
            "layer1_update_required: review",
            "layer1_updated: not_required",
            "format_cleaned: false",
            f"dispatch_mode: {mode}",
            "id_scheme: none",
            "---",
            "",
            "# Divergence Report",
            "",
        ]
        if note:
            lines += [f"> {note}", ""]
        lines += [
            "This SUT dialect has no proposition-id (P-xxx) scheme, so automated "
            "id-set divergence detection is not applicable. Both independent peer "
            "views were captured (`codex_view.md`, `claude_view.md`); compare them "
            "by hand and absorb any missing propositions into 01_business_brief.md "
            "before Layer 2.",
            "",
        ]
        write_text(out / "divergence_report.md", "\n".join(lines))
        return
    codex_props = proposition_ids(codex_view, pattern)
    claude_props = proposition_ids(claude_view, pattern)
    agreed = sorted(codex_props & claude_props)
    only_codex = sorted(codex_props - claude_props)
    only_claude = sorted(claude_props - codex_props)
    divergent = bool(only_codex or only_claude)

    layer1_update_required = "yes" if divergent else "no"
    lines = [
        "---",
        "gate: multiview_divergence",
        "gate_status: passed",
        f"layer1_update_required: {layer1_update_required}",
        "layer1_updated: not_required",
        "format_cleaned: false",
        f"dispatch_mode: {mode}",
        "---",
        "",
        "# Divergence Report",
        "",
    ]
    if note:
        lines += [f"> {note}", ""]
    lines += [
        "## Proposition Coverage",
        "",
        f"- Codex propositions: {', '.join(sorted(codex_props)) or '(none)'}",
        f"- Claude propositions: {', '.join(sorted(claude_props)) or '(none)'}",
        "",
        "## Agreements",
        "",
        f"- Shared by both views: {', '.join(agreed) or '(none)'}",
        "",
        "## Divergences",
        "",
        f"- Only in Codex view: {', '.join(only_codex) or '(none)'}",
        f"- Only in Claude view: {', '.join(only_claude) or '(none)'}",
        "",
    ]
    if divergent:
        lines += [
            "Proposition-id sets differ between the two independent views. "
            "layer1_update_required=yes: review the divergent propositions and "
            "absorb the missing ones into 01_business_brief.md before Layer 2.",
            "",
        ]
    else:
        lines += [
            "Both independent views agree on the proposition-id set; no "
            "machine-detected divergence. Human review may still supersede this.",
            "",
        ]
    write_text(out / "divergence_report.md", "\n".join(lines))


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
    print(f"codex_model_default: {CODEX_MODEL or 'cli_default'}")
    print(f"claude_model_default: {CLAUDE_MODEL or 'cli_default'}")
    print(f"codex_reasoning_effort_default: {CODEX_REASONING_EFFORT or 'cli_default'}")
    print(f"claude_effort_default: {CLAUDE_EFFORT or 'cli_default'}")
    print(f"cli_proxy_env: {proxy_summary()}")
    print(f"timeout_seconds: {TIMEOUT_SECONDS}")
    print("core bridge: available")
    return 0


def run_all(artifact_dir: Path) -> int:
    # Reuse the shared init/check helper and its 00_multiview/ layout.
    sdtd_multiview.init(artifact_dir, "multiview peer dispatch")
    out = artifact_dir / "00_multiview"

    pattern = proposition_pattern_for(artifact_dir)
    brief_path = artifact_dir / "01_business_brief.md"
    brief = read_text(brief_path) if brief_path.exists() else ""
    propositions = sorted(proposition_ids(brief, pattern))

    prompt_card_path = out / "prompt_card.md"
    prompt_card = read_text(prompt_card_path) if prompt_card_path.exists() else ""

    available, missing = both_clis_available()

    if not available:
        # ---- FALLBACK: deterministic placeholders ----
        reason = f"'{missing}' CLI not found on PATH"
        print(f"fallback: {reason}; writing deterministic placeholder views")
        for peer in ("codex", "claude"):
            write_placeholder_view(out, peer, propositions, reason)
        synthesize_divergence(
            out,
            read_text(out / "codex_view.md"),
            read_text(out / "claude_view.md"),
            mode="fallback_placeholder",
            note=f"Real peer dispatch was SKIPPED because {reason}. "
            "This divergence report reflects deterministic placeholder views only.",
            pattern=pattern,
        )
        print(f"written {out} (fallback mode)")
        return 0

    # ---- REAL DISPATCH: independent peer workers ----
    print("real peer dispatch: both codex and claude available")
    print(f"proxy_env: {proxy_summary()}")
    views: dict[str, str] = {}
    for peer in ("codex", "claude"):
        prompt = render_envelope(peer, prompt_card, brief)
        print(f"{peer}: dispatching ({model_for(peer)} / {effort_for(peer)})")
        try:
            rc, stdout, stderr = run_peer_cli(peer, prompt)
        except subprocess.TimeoutExpired:
            print(f"{peer}: timed out after {TIMEOUT_SECONDS}s; using placeholder for this peer")
            write_placeholder_view(out, peer, propositions, f"{peer} CLI timed out after {TIMEOUT_SECONDS}s")
            views[peer] = read_text(out / f"{peer}_view.md")
            continue
        if rc != 0:
            tail = (stderr or "").strip().splitlines()[-1:] or [""]
            print(f"{peer}: CLI exit code {rc}; using placeholder for this peer ({tail[0]})")
            write_placeholder_view(out, peer, propositions, f"{peer} CLI exited {rc}")
            views[peer] = read_text(out / f"{peer}_view.md")
            continue
        cleaned_body = strip_preamble(stdout)
        was_cleaned = cleaned_body.strip() != stdout.strip()
        if not cleaned_body.strip():
            print(f"{peer}: empty output; using placeholder for this peer")
            write_placeholder_view(out, peer, propositions, f"{peer} CLI returned empty output")
            views[peer] = read_text(out / f"{peer}_view.md")
            continue
        errors = view_schema_errors(cleaned_body, pattern)
        if errors:
            archived = archive_failed_view(out, peer, stdout, errors)
            print(f"{peer}: returned view failed schema ({'; '.join(errors)}); archived {archived.name}, using placeholder")
            write_placeholder_view(out, peer, propositions, f"{peer} returned a schema-invalid view: {errors[0]}")
            views[peer] = read_text(out / f"{peer}_view.md")
            continue
        write_real_view(out, peer, cleaned_body, was_cleaned)
        views[peer] = read_text(out / f"{peer}_view.md")

    synthesize_divergence(
        out,
        views["codex"],
        views["claude"],
        mode="real_peer_dispatch",
        pattern=pattern,
    )
    print(f"written {out} (real peer dispatch)")
    return 0


def run_divergence(artifact_dir: Path, force: bool = False) -> int:
    """Re-synthesize divergence_report.md from the two existing peer views."""
    out = artifact_dir / "00_multiview"
    codex_path = out / "codex_view.md"
    claude_path = out / "claude_view.md"
    missing = [p.name for p in (codex_path, claude_path) if not p.exists()]
    if missing:
        for name in missing:
            print(f"FAIL: missing 00_multiview/{name}; run run-all first")
        return 1
    report = out / "divergence_report.md"
    if report.exists() and not force:
        print(f"refusing to overwrite existing {report}; pass --force")
        return 1
    codex_view = read_text(codex_path)
    claude_view = read_text(claude_path)
    # Tag the mode by whether the views were real or placeholder.
    placeholder = "dispatch_mode: fallback_placeholder" in codex_view or "dispatch_mode: fallback_placeholder" in claude_view
    synthesize_divergence(
        out,
        codex_view,
        claude_view,
        mode="fallback_placeholder" if placeholder else "real_peer_dispatch",
        pattern=proposition_pattern_for(artifact_dir),
    )
    print(f"written {report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-env")
    p_run = sub.add_parser("run-all")
    p_run.add_argument("artifact_dir", type=Path)
    p_div = sub.add_parser("run-divergence")
    p_div.add_argument("artifact_dir", type=Path)
    p_div.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.cmd == "check-env":
        return check_env()
    if args.cmd == "run-divergence":
        return run_divergence(args.artifact_dir, force=args.force)
    return run_all(args.artifact_dir)


if __name__ == "__main__":
    raise SystemExit(main())
