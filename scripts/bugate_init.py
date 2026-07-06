#!/usr/bin/env python3
"""bugate init — import BUGate into a SUT automation test repo (CHARTER §5.2).

Sets up the DEFAULT usage mode (imported governance layer): the SUT test repo
stays the project root; the engine is vendored into it; the governance contract
(config + profile) is created there to be COMMITTED with the tests it guards.

    python3 scripts/bugate_init.py <sut-repo> [--vendor-dir .bugate]
                                   [--dry-run] [--force]

What it does, in order:

  1. vendors the kit (``scripts/``, ``bin/``, ``.shared/skills/bugate/``) from
     this engine tree into ``<sut-repo>/<vendor-dir>/``;
  2. links runtime skill discovery: ``.claude/skills/bugate`` and
     ``.codex/skills/bugate`` → the vendored skill tree, and copies the Codex
     gate-review agents into ``.codex/agents/`` (Codex's agent channel — the
     Claude equivalents load via the plugin);
  3. merges the BUGate hook blocks into the repo's ``.claude/settings.json``
     and ``.codex/hooks.json`` (the repo's own hooks are preserved; ours are
     appended when absent and refreshed when their wired text has drifted from
     the current template — so a re-run also upgrades an older import's wiring);
  4. scaffolds a committed ``bugate.config.yaml`` (the workspace-root marker)
     and ``bugate.profile.yaml`` (inert until ``guarded_path_regex`` is filled);
  5. creates the ``docs/usecases/`` skeleton;
  6. appends a marked, idempotent ignore block to the repo's root
     ``.gitignore`` (creating it if absent) so the default scorer outputs and
     local agent/memory state don't litter the SUT repo's ``git status`` — the
     SUT's own lines and the committed governance contract are left untouched;
  7. probes the MACHINE-LEVEL memory bus (reuse-first, ADR-BUGATE-003): all
     governed repos on a machine share one running ``mcp-memory-service``
     instance, isolated by namespace tag — init never scaffolds or starts a
     per-repo service, it only reports whether the shared one is already up;
  8. prints the acceptance steps — including the Codex re-trust caveat (hooks
     stay silently inactive until the changed hook hash is re-trusted) and the
     R4 negative control.

Everything is stdlib-only and idempotent: re-running refreshes the vendored kit
and the BUGate hook wiring (a refreshed Codex hook needs its hash re-trusted
again) while config, profile, the repo's own hooks, and the repo's own
.gitignore lines stay untouched (the marked ignore block is appended once).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bugate_core import find_engine_root

KIT_DIRS = ["scripts", "bin", ".shared/skills/bugate"]
IGNORE_NAMES = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

# Codex plugins package skills/hooks/MCP but not custom agents, so the installer
# is Codex's channel for the gate-review agents (the Claude equivalents load via
# the plugin). The agent TOMLs travel inside the vendored kit and reference the
# skill through the .codex/skills/bugate symlink this installer also creates, so
# one file resolves in the engine repo and in any SUT repo regardless of vendor dir.
CODEX_AGENTS_KIT_REL = ".shared/skills/bugate/adapters/codex/agents"

# Hook commands are templated on the vendor dir. ROOT is the governed WORKSPACE
# root, found via the committed config this installer scaffolds; the engine is
# then addressed at its known vendored location beneath it. When no config
# marks a workspace above CWD, the hook exits 0 (inert) — the same lazy-guard
# contract as the plugin channel's hooks.json, so both channels degrade
# identically instead of hard-blocking every write with a resolver error.
_ROOT_SNIPPET = (
    "ROOT=\"$(/usr/bin/env python3 -c 'import os; from pathlib import Path; "
    "p=Path.cwd(); print(os.environ.get(\"BUGATE_PROJECT_ROOT\") or "
    "next((str(c) for c in [p,*p.parents] if (c/\"bugate.config.yaml\").exists()), \"\"))')\"; "
    "[ -n \"$ROOT\" ] || exit 0; "
)


def _cmd(vendor_dir: str, script: str, *args: str) -> str:
    tail = (" " + " ".join(args)) if args else ""
    return _ROOT_SNIPPET + f'/usr/bin/env python3 "$ROOT/{vendor_dir}/scripts/{script}"{tail}'


def hook_blocks(vendor_dir: str, runtime: str) -> dict:
    """The BUGate hook wiring for one runtime ('claude' or 'codex')."""
    guard_cmds = [
        _cmd(vendor_dir, "check_bugate.py"),
        _cmd(vendor_dir, "check_plan_lock.py"),
        _cmd(vendor_dir, "check_agent_role_paths.py"),
    ]
    reminder = [_cmd(vendor_dir, "bugate_prompt_reminder.py")]
    blocks = {
        "PreToolUse": [{
            "matcher": "Edit|Write" if runtime == "claude" else "apply_patch",
            "hooks": [{"type": "command", "command": c} for c in guard_cmds],
        }],
        "UserPromptSubmit": [{
            "hooks": [{"type": "command", "command": c} for c in reminder],
        }],
    }
    if runtime == "claude":
        # SUT-repo sessions record under the SUT's own namespace (no --core).
        blocks["SessionStart"] = [{
            "hooks": [{"type": "command",
                       "command": _cmd(vendor_dir, "memory_bus.py", "session-start", "--agent", "agent")}],
        }]
        blocks["Stop"] = [{
            "hooks": [{"type": "command",
                       "command": _cmd(vendor_dir, "memory_bus.py", "stop", "--agent", "agent")}],
        }]
    return blocks


def merge_hooks(existing: dict, blocks: dict, vendor_dir: str) -> tuple[dict, list[str]]:
    """Merge the BUGate hook entries into an existing hooks file.

    Refresh ours, never theirs. Ownership is decided per entry by the vendor
    marker: an entry is OURS when every command in it calls a script under the
    vendor dir. The repo's own entries are never rewritten. Our entries are
    appended when absent and REWRITTEN in place when their text has drifted
    from the current template — the upgrade path: re-running init brings an
    older import's wiring (e.g. a pre-lazy-guard hook shape) up to the current
    contract. An entry that mixes a vendor call into the repo's own hooks is
    treated as wired-but-theirs and left untouched.
    """
    added: list[str] = []
    hooks = existing.setdefault("hooks", {})
    marker = f"{vendor_dir}/scripts/"

    def commands(entry: dict) -> list[str]:
        return [h.get("command") or "" for h in (entry.get("hooks") or [])]

    def is_ours(entry: dict) -> bool:
        cmds = commands(entry)
        return bool(cmds) and all(marker in c for c in cmds)

    def is_marked(entry: dict) -> bool:
        return any(marker in c for c in commands(entry))

    for event, entries in blocks.items():
        current = hooks.setdefault(event, [])
        ours = [e for e in current if is_ours(e)]
        if any(is_marked(e) and not is_ours(e) for e in current) and not ours:
            continue  # wired through the repo's own mixed entry — not ours to touch
        if ours:
            if ours == entries:
                continue  # already wired at the current contract
            current[:] = [e for e in current if not is_ours(e)]
            current.extend(entries)
            added.append(f"{event} (refreshed)")
            continue
        current.extend(entries)
        added.append(event)
    return existing, added


CONFIG_SCAFFOLD = """\
# BUGate governed-workspace config — COMMIT this file (CHARTER §2.2 R2).
# It marks the workspace root: the gate engine (vendored at {vendor_dir}/)
# finds this repo by walking up from CWD to the nearest bugate.config.yaml.
profile: bugate.profile.yaml
"""

# Raw string: the sut_identity_terms example must reach the scaffolded file as
# a literal backslash-b (the simple YAML parser does not unescape), never as a
# 0x08 control character.
PROFILE_SCAFFOLD = r"""# BUGate SUT profile — COMMIT this file beside the tests it governs.
# Schema: {vendor_dir}/.shared/skills/bugate/references/profile-schema.md

# Per-UC fail-closed binding: each guarded test file maps to its own
# docs/usecases/<uc>/ artifact dir via the {{uc}} capture below.
artifact_dir_template: docs/usecases/{{uc}}/

# INERT until filled: add regexes (with a (?P<uc>...) capture) matching this
# repo's test layout to turn the physical write guard on. Example:
#   - "(^|/)tests/(?P<uc>[^/]+)/[^/]+[.]py$"
guarded_path_regex: []

required_precode_artifacts:
  - 01_business_brief.md
  - 02_testability.md
  - 03_inventory.yaml
  - 03a_test_cases.md
  - 03b_adversarial_cases.yaml

# De-SUT identity defense (CHARTER A1): list THIS SUT's identity terms
# (product / internal-system / account names, as case-insensitive regexes) so
# the guard keeps them from seeping into the reusable vendored kit at
# {vendor_dir}/. This repo's own files are not the scan surface. The simple
# YAML parser does not unescape, so write \b literally (single backslash):
# sut_identity_terms:
#   - "\bmy-product-name\b"

# Memory-bus namespace on the MACHINE-LEVEL shared service (ADR-BUGATE-003):
# every governed repo on this machine shares one mcp-memory-service instance
# (data home ~/.bugate/memory-bus), isolated by this tag. Declaring the
# namespace is ALL the memory setup this repo needs — never scaffold or start
# a per-repo service dir.
memory:
  namespace: project:{name}
"""

# A clearly-marked, textually-idempotent .gitignore block. The markers make
# idempotency a substring test (never a fuzzy diff): re-running init appends the
# block only when BEGIN is absent, so the SUT's own lines are never rewritten or
# doubled. Scorer defaults are anchored to the repo root with a leading `/` —
# exactly where the scorers drop them when run without an explicit --*-output
# (see the scorer argparse defaults) — so a same-named committed artifact deeper
# in the tree is not swept up. The governance contract (bugate.config.yaml /
# bugate.profile.yaml) is deliberately NOT ignored: it must stay committable.
GITIGNORE_BEGIN = "# >>> BUGate imported-mode ignores (managed by bugate_init.py) >>>"
GITIGNORE_END = "# <<< BUGate imported-mode ignores <<<"
GITIGNORE_BLOCK = """\
{begin}
# Default scorer outputs written to the repo root when run without --*-output
# (oracle_falsification.py / check_prd_health.py /
# generate_assertion_coverage_matrix.py / self_healing_mvp.py).
/oracle_falsification_result.json
/oracle_falsification_result.md
/prd_health_result.json
/prd_health_report.md
/assertion_coverage_matrix.md
/self_healing.json
/self_healing.md
/self_healing_repair_plan.md
# Local agent + memory state — machine-local, never committed.
/{vendor_dir}/plan.lock
/.memory_bus/
/.claude/memory/
/.codex/memories/
{end}
"""


def scaffold_gitignore(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    """Append the marked BUGate ignore block to the SUT repo's root .gitignore.

    Create it if absent; otherwise PRESERVE the SUT's own lines and append our
    block only when the BEGIN marker is not already present, so a re-run neither
    duplicates the block nor rewrites the repo's own entries.
    """
    path = target / ".gitignore"
    block = GITIGNORE_BLOCK.format(begin=GITIGNORE_BEGIN, end=GITIGNORE_END, vendor_dir=vendor_dir)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if GITIGNORE_BEGIN in existing:
        return ["keep existing .gitignore (BUGate block already present)"]
    if not existing:
        note = "scaffold .gitignore (BUGate ignore block)"
        new_text = block
    else:
        note = "append BUGate ignore block to existing .gitignore"
        sep = "" if existing.endswith("\n") else "\n"
        new_text = existing + sep + "\n" + block
    if not dry:
        path.write_text(new_text, encoding="utf-8")
    return [note]


def vendor_kit(engine_root: Path, target: Path, vendor_dir: str, dry: bool) -> list[str]:
    notes = []
    for rel in KIT_DIRS:
        src = engine_root / rel
        dst = target / vendor_dir / rel
        if not src.is_dir():
            raise SystemExit(f"engine tree incomplete: missing {src}")
        if dst.exists() and dst.resolve() == src.resolve():
            # Re-running from the vendored engine against its own repo: the
            # source IS the destination — never rmtree it out from under us.
            notes.append(f"vendor {rel}/ — already in place (running from the vendored kit)")
            continue
        notes.append(f"vendor {rel}/ -> {vendor_dir}/{rel}/")
        if dry:
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=IGNORE_NAMES, symlinks=False)
    return notes


def link_skills(target: Path, vendor_dir: str, dry: bool, force: bool) -> list[str]:
    notes = []
    rel_target = Path("..") / ".." / vendor_dir / ".shared" / "skills" / "bugate"
    for runtime in (".claude", ".codex"):
        link = target / runtime / "skills" / "bugate"
        notes.append(f"link {runtime}/skills/bugate -> {rel_target}")
        if dry:
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            if not force and link.is_symlink() and link.readlink() == rel_target:
                continue
            if not force and not link.is_symlink():
                raise SystemExit(f"{link} exists and is not the expected symlink (use --force)")
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
        link.symlink_to(rel_target)
    return notes


def install_codex_agents(engine_root: Path, target: Path, vendor_dir: str, dry: bool) -> list[str]:
    """Copy the Codex gate-review agents into the SUT repo's .codex/agents/.

    Codex discovers project-local agents from .codex/agents/, and its plugins
    cannot bundle agents, so the installer copies our gate agents there as
    committed files.
    Refresh-ours: our own named TOMLs are (re)written each run so an upgrade
    reaches them; any other agent the SUT owns in that dir is left untouched.
    The kit source is read from the vendored location, so this works whether
    init runs from the engine repo or an already-vendored kit.
    """
    notes: list[str] = []
    src_dir = engine_root / CODEX_AGENTS_KIT_REL
    if not src_dir.is_dir():
        notes.append(f"codex agents: source {CODEX_AGENTS_KIT_REL}/ missing — skipped")
        return notes
    dst_dir = target / ".codex" / "agents"
    for src in sorted(src_dir.glob("*.toml")):
        dst = dst_dir / src.name
        notes.append(f"{'refresh' if dst.exists() else 'install'} .codex/agents/{src.name}")
        if not dry:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    return notes


def wire_hooks(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    notes = []
    for runtime, path in (("claude", target / ".claude" / "settings.json"),
                          ("codex", target / ".codex" / "hooks.json")):
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path} is not valid JSON — fix or remove it first: {exc}")
        merged, added = merge_hooks(existing, hook_blocks(vendor_dir, runtime), vendor_dir)
        if added:
            notes.append(f"wire {path.relative_to(target)}: +{', +'.join(added)}")
            if not dry:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        else:
            notes.append(f"wire {path.relative_to(target)}: already wired")
    return notes


def scaffold(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    notes = []
    files = {
        target / "bugate.config.yaml": CONFIG_SCAFFOLD.format(vendor_dir=vendor_dir),
        target / "bugate.profile.yaml": PROFILE_SCAFFOLD.format(
            vendor_dir=vendor_dir, name=target.resolve().name),
    }
    for path, body in files.items():
        if path.exists():
            notes.append(f"keep existing {path.name}")
            continue
        notes.append(f"scaffold {path.name}")
        if not dry:
            path.write_text(body, encoding="utf-8")
    skeleton = target / "docs" / "usecases"
    notes.append("mkdir docs/usecases/")
    if not dry:
        skeleton.mkdir(parents=True, exist_ok=True)
        keep = skeleton / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    return notes


def bus_probe() -> list[str]:
    """Reuse-first machine-level memory-bus check (ADR-BUGATE-003).

    The bus is ONE service per machine shared by every governed repo
    (namespace-tag isolation), so init never scaffolds or starts a per-repo
    service — it only reports whether the shared instance is already running.
    Non-fatal by design: the memory runtime is optional and its absence must
    never block an import.
    """
    try:
        import memory_bus  # sibling module; loads client.env system-home-first

        memory_bus.load_local_env()
        url = memory_bus.base_url()
        home = memory_bus.memory_home()
        if memory_bus.service_available():
            return [
                f"memory-bus: RUNNING at {url} (data home {home}) — reusing the "
                "machine-level shared instance; this repo only declares "
                "memory.namespace in its profile"
            ]
        return [
            f"memory-bus: no service detected at {url} — OPTIONAL. One install "
            "serves every repo on this machine; see the engine repo's "
            "docs/SETUP-OPTIONAL.md §2, then start with bin/memory-bus-ensure. "
            "Hooks no-op gracefully until then"
        ]
    except Exception as exc:  # probe must never block an import
        return [f"memory-bus: probe skipped ({exc.__class__.__name__}: {exc}) — optional runtime, hooks degrade gracefully"]


NEXT_STEPS = """\
Imported-mode setup written. Next steps (CHARTER §2.2):

  1. Fill bugate.profile.yaml: add `guarded_path_regex` for this repo's test
     layout (keep the (?P<uc>...) capture) — the write guard is inert until then.
  2. COMMIT: bugate.config.yaml, bugate.profile.yaml, {vendor_dir}/,
     .claude/ + .codex/ hook wiring, .codex/agents/ (the Codex gate agents),
     docs/usecases/, and the updated .gitignore (a marked block backstops the
     default scorer outputs + local agent/memory state out of git status) — the
     governance contract reviews and versions with the tests it guards.
  3. Codex only: RE-TRUST the changed hook hash in the Codex hook-management
     UI. Until then Codex hooks are SILENTLY inactive (known behavior). The
     .codex/agents/ gate agents are picked up on the next Codex session (no
     re-trust needed — they are agents, not hooks).
  4. Acceptance — R4 negative control: pick a guarded test path whose UC has no
     passed pre-code artifacts and confirm the block:
       python3 {vendor_dir}/scripts/check_bugate.py <a-guarded-test>.py </dev/null
       # expect exit 2 and the missing-artifact list
  5. Per-UC flow from here on:
       python3 {vendor_dir}/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
  6. Memory bus (optional, machine-level): this repo REUSES the one shared
     mcp-memory-service instance per machine under its own profile namespace —
     check with {vendor_dir}/bin/memory-bus-status (start: …/memory-bus-ensure);
     install once per machine ONLY if the probe above says none is running.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="path to the SUT automation test repo")
    parser.add_argument("--vendor-dir", default=".bugate",
                        help="directory inside the SUT repo receiving the kit (default: .bugate)")
    parser.add_argument("--dry-run", action="store_true", help="print actions without writing")
    parser.add_argument("--force", action="store_true",
                        help="replace non-matching skill links")
    args = parser.parse_args(argv)

    engine_root = find_engine_root()
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        raise SystemExit(f"target is not a directory: {target}")
    if target == engine_root.resolve():
        raise SystemExit("target is the engine tree itself; run against the SUT repo")
    vendor_dir = args.vendor_dir.strip("/")
    if not vendor_dir or vendor_dir.startswith(".."):
        raise SystemExit(f"invalid --vendor-dir: {args.vendor_dir!r}")

    notes = []
    notes += vendor_kit(engine_root, target, vendor_dir, args.dry_run)
    notes += link_skills(target, vendor_dir, args.dry_run, args.force)
    notes += install_codex_agents(engine_root, target, vendor_dir, args.dry_run)
    notes += wire_hooks(target, vendor_dir, args.dry_run)
    notes += scaffold(target, vendor_dir, args.dry_run)
    notes += scaffold_gitignore(target, vendor_dir, args.dry_run)
    notes += bus_probe()

    prefix = "[dry-run] " if args.dry_run else ""
    for note in notes:
        print(f"{prefix}{note}")
    print()
    print(NEXT_STEPS.format(vendor_dir=vendor_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
