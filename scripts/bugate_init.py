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
     ``.codex/skills/bugate`` → the vendored skill tree;
  3. merges the BUGate hook blocks into the repo's ``.claude/settings.json``
     and ``.codex/hooks.json`` (existing hooks are preserved; ours are appended
     only when absent);
  4. scaffolds a committed ``bugate.config.yaml`` (the workspace-root marker)
     and ``bugate.profile.yaml`` (inert until ``guarded_path_regex`` is filled);
  5. creates the ``docs/usecases/`` skeleton;
  6. prints the acceptance steps — including the Codex re-trust caveat (hooks
     stay silently inactive until the changed hook hash is re-trusted) and the
     R4 negative control.

Everything is stdlib-only and idempotent: re-running refreshes the vendored kit
and leaves existing config/profile/hooks untouched unless --force.
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

# Hook commands are templated on the vendor dir. ROOT is the governed WORKSPACE
# root, found via the committed config this installer scaffolds; the engine is
# then addressed at its known vendored location beneath it.
_ROOT_SNIPPET = (
    "ROOT=\"$(/usr/bin/env python3 -c 'import os; from pathlib import Path; "
    "p=Path.cwd(); print(os.environ.get(\"BUGATE_PROJECT_ROOT\") or "
    "next(str(c) for c in [p,*p.parents] if (c/\"bugate.config.yaml\").exists()))')\"; "
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
    """Append our hook entries to an existing hooks file, never rewriting theirs.

    Idempotency: an event block is skipped when any existing command in that
    event already calls a script under the vendor dir.
    """
    added: list[str] = []
    hooks = existing.setdefault("hooks", {})
    marker = f"{vendor_dir}/scripts/"
    for event, entries in blocks.items():
        current = hooks.setdefault(event, [])
        already = any(
            marker in (h.get("command") or "")
            for entry in current for h in (entry.get("hooks") or [])
        )
        if already:
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

PROFILE_SCAFFOLD = """\
# BUGate SUT profile — COMMIT this file beside the tests it governs.
# Schema: {vendor_dir}/.shared/skills/bugate/references/profile-schema.md
# Runnable miniature of this layout: the engine repo's examples/imported-demo/.

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

memory:
  namespace: project:{name}
"""


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


NEXT_STEPS = """\
Imported-mode setup written. Next steps (CHARTER §2.2):

  1. Fill bugate.profile.yaml: add `guarded_path_regex` for this repo's test
     layout (keep the (?P<uc>...) capture) — the write guard is inert until then.
  2. COMMIT: bugate.config.yaml, bugate.profile.yaml, {vendor_dir}/,
     .claude/ + .codex/ hook wiring, docs/usecases/ — the governance contract
     reviews and versions with the tests it guards.
  3. Codex only: RE-TRUST the changed hook hash in the Codex hook-management
     UI. Until then Codex hooks are SILENTLY inactive (known behavior).
  4. Acceptance — R4 negative control: pick a guarded test path whose UC has no
     passed pre-code artifacts and confirm the block:
       python3 {vendor_dir}/scripts/check_bugate.py <a-guarded-test>.py </dev/null
       # expect exit 2 and the missing-artifact list
  5. Per-UC flow from here on:
       python3 {vendor_dir}/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
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
    notes += wire_hooks(target, vendor_dir, args.dry_run)
    notes += scaffold(target, vendor_dir, args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    for note in notes:
        print(f"{prefix}{note}")
    print()
    print(NEXT_STEPS.format(vendor_dir=vendor_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
