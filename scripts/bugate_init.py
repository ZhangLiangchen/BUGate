#!/usr/bin/env python3
"""bugate init — import BUGate into a SUT automation test repo (CHARTER §5.2).

Sets up the DEFAULT usage mode (imported governance layer): the SUT test repo
stays the project root; the engine is vendored into it; the governance contract
(config + profile) is created there to be COMMITTED with the tests it guards.

    python3 scripts/bugate_init.py <sut-repo> [--vendor-dir .bugate]
                                   [--dry-run] [--force]

What it does, in order:

  1. vendors the kit (``scripts/``, ``bin/``, ``.shared/skills/bugate/``, and
     ``.shared/skills/bugate-full-check/``) from this engine tree into
     ``<sut-repo>/<vendor-dir>/``;
  2. links runtime skill discovery: ``.claude/skills/<skill>`` and
     ``.agents/skills/<skill>`` → the vendored skill trees, keeps
     ``.codex/skills/<skill>`` as a legacy Codex compatibility bridge, and
     copies the Codex gate-review agents into ``.codex/agents/``;
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
  7. ensures the MACHINE-LEVEL memory bus (reuse-first, ADR-BUGATE-003): all
     governed repos on a machine share one running ``mcp-memory-service``
     instance, isolated by namespace tag — init never scaffolds a per-repo
     service, but it does reuse/restart/install-once the shared service through
     ``bin/memory-bus-ensure`` when needed;
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
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from bugate_core import find_engine_root

KIT_DIRS = ["scripts", "bin", ".shared/skills/bugate", ".shared/skills/bugate-full-check"]
# Single files vendored alongside the kit subtrees: the imported-mode field
# guide is the post-import operator manual (lessons + activation recipes) and
# must live INSIDE the governed repo so later sessions can read it without the
# engine checkout.
KIT_FILES = ["docs/IMPORT-FIELD-GUIDE.md"]
IGNORE_NAMES = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")

# Codex plugins package the shared skills/hooks/MCP surface. BUGate still wires
# the Codex gate-review agents through the project-local installer channel so
# each governed SUT repo can review and commit the exact agent cards that govern
# its tests. The agent TOMLs travel inside the vendored kit and reference the
# skill through the official .agents/skills/bugate symlink this installer also
# creates, so one file resolves in the engine repo and in any SUT repo
# regardless of vendor dir.
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
    """The BUGate hook wiring for one runtime ('claude' or 'codex').

    The write-shaped guards (check_bugate, check_plan_lock) must see ONLY
    write tools: check_bugate does not inspect tool_name and fail-closes on
    any payload naming a guarded path, so matching it on Read would block
    reading guarded tests. The Wave-7 role guard (check_agent_role_paths)
    distinguishes read/write buckets itself and needs Read in its matcher or
    profile `agent_roles` read-isolation is silently unenforced.
    """
    write_guard_cmds = [
        _cmd(vendor_dir, "check_bugate.py"),
        _cmd(vendor_dir, "check_plan_lock.py"),
    ]
    role_guard_cmds = [
        _cmd(vendor_dir, "check_agent_role_paths.py"),
    ]
    reminder = [_cmd(vendor_dir, "bugate_prompt_reminder.py")]
    if runtime == "claude":
        pre_tool_use = [
            {
                "matcher": "Edit|Write",
                "hooks": [{"type": "command", "command": c} for c in write_guard_cmds],
            },
            {
                "matcher": "Read|Edit|Write",
                "hooks": [{"type": "command", "command": c} for c in role_guard_cmds],
            },
        ]
    else:
        # Codex has no hookable Read tool; apply_patch carries all writes.
        pre_tool_use = [{
            "matcher": "apply_patch",
            "hooks": [
                {"type": "command", "command": c}
                for c in write_guard_cmds + role_guard_cmds
            ],
        }]
    blocks = {
        "PreToolUse": pre_tool_use,
        "UserPromptSubmit": [{
            "hooks": [{"type": "command", "command": c} for c in reminder],
        }],
    }
    # SUT-repo sessions record under the SUT's own namespace (no --core) in
    # both runtimes. Codex supports the same lifecycle events, so memory/liveness
    # hooks stay symmetric with Claude Code.
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

# --- Optional waves (dormant until configured; recipes: IMPORT_PROMPT
# --- appendix and {vendor_dir}/docs/IMPORT-FIELD-GUIDE.md) ---------------
# Wave 7 role isolation: uncomment and adapt, then run with
# BUGATE_AGENT_ROLE=<role>. Bare list = forbidden for read AND write;
# read:/write: sub-lists scope each side. Role names lowercase.
# agent_roles:
#   implementer:
#     - "^docs/raw/source_code/.*"
#   designer:
#     write:
#       - "^tests/.*"
# Wave 8 oracle falsification: point at a real spec once captured evidence
# exists (evidence paths inside the spec resolve relative to the spec file).
# falsification_spec: <path/to/falsification_spec.yaml>
# falsification_threshold: 0.7
# wave8_evidence_glob: <workspace-relative glob>
# wave8_reports_dir: <workspace-relative dir, prefer gitignored>
# wave8_artifact_root: <inventory scan root>

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
    for rel in KIT_FILES:
        src = engine_root / rel
        dst = target / vendor_dir / rel
        if not src.is_file():
            raise SystemExit(f"engine tree incomplete: missing {src}")
        if dst.exists() and dst.resolve() == src.resolve():
            notes.append(f"vendor {rel} — already in place (running from the vendored kit)")
            continue
        notes.append(f"vendor {rel} -> {vendor_dir}/{rel}")
        if dry:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return notes


def link_skills(target: Path, vendor_dir: str, dry: bool, force: bool) -> list[str]:
    notes = []
    skill_names = ("bugate", "bugate-full-check")
    runtimes = (
        (".claude", "project skill discovery"),
        (".agents", "official Codex skill discovery"),
        (".codex", "legacy Codex compatibility"),
    )
    for skill in skill_names:
        rel_target = Path("..") / ".." / vendor_dir / ".shared" / "skills" / skill
        for runtime, label in runtimes:
            link = target / runtime / "skills" / skill
            notes.append(f"link {runtime}/skills/{skill} -> {rel_target} ({label})")
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

    BUGate discovers project-local gate agents from .codex/agents/ for Codex
    sessions, so the installer copies our gate agents there as committed files.
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


NAMESPACE_REGISTRY = Path.home() / ".bugate" / "namespaces.tsv"


def _read_namespace_registry() -> dict[str, str]:
    entries: dict[str, str] = {}
    if NAMESPACE_REGISTRY.exists():
        for line in NAMESPACE_REGISTRY.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                ns, path = line.split("\t", 1)
                entries[ns.strip()] = path.strip()
    return entries


def _memory_namespace(target: Path) -> tuple[str, bool]:
    """Collision-guarded default namespace for the machine-level shared bus.

    The bus is ONE service per machine isolated only by namespace tags, so two
    governed repos whose directories share a basename (e.g. two checkouts both
    named `backend`) would silently share `project:backend` and cross-pollute
    each other's memory. A tiny machine-local registry maps namespace -> repo
    path: the first repo keeps the plain name; a DIFFERENT repo hitting a taken
    name gets a short path-hash suffix. Deterministic and offline; re-running
    init on the same repo is idempotent. Returns (namespace, was_suffixed).
    """
    me = str(target.resolve())
    base = f"project:{target.resolve().name}"
    entries = _read_namespace_registry()
    if entries.get(base) in (None, me):
        return base, False
    suffix = hashlib.sha1(me.encode("utf-8")).hexdigest()[:4]
    return f"{base}-{suffix}", True


def _register_namespace(namespace: str, target: Path) -> None:
    entries = _read_namespace_registry()
    me = str(target.resolve())
    if entries.get(namespace) == me:
        return
    entries[namespace] = me
    NAMESPACE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    NAMESPACE_REGISTRY.write_text(
        "".join(f"{ns}\t{path}\n" for ns, path in sorted(entries.items())),
        encoding="utf-8",
    )


def scaffold(target: Path, vendor_dir: str, dry: bool) -> list[str]:
    notes = []
    namespace, suffixed = _memory_namespace(target)
    if suffixed:
        notes.append(
            f"memory.namespace: `{namespace}` (basename already claimed by another repo "
            f"in {NAMESPACE_REGISTRY} — path-hash suffix added to prevent cross-repo "
            "memory pollution; edit the profile if you prefer another tag)")
    files = {
        target / "bugate.config.yaml": CONFIG_SCAFFOLD.format(vendor_dir=vendor_dir),
        target / "bugate.profile.yaml": PROFILE_SCAFFOLD.format(
            vendor_dir=vendor_dir, name=namespace.removeprefix("project:")),
    }
    for path, body in files.items():
        if path.exists():
            notes.append(f"keep existing {path.name}")
            continue
        notes.append(f"scaffold {path.name}")
        if not dry:
            path.write_text(body, encoding="utf-8")
            if path.name == "bugate.profile.yaml":
                _register_namespace(namespace, target)
    skeleton = target / "docs" / "usecases"
    notes.append("mkdir docs/usecases/")
    if not dry:
        skeleton.mkdir(parents=True, exist_ok=True)
        keep = skeleton / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    return notes


def bus_ensure(engine_root: Path, dry: bool) -> list[str]:
    """Ensure the REQUIRED machine-level memory bus is up (ADR-BUGATE-003).

    The memory bus is a CORE BUGate component (long-term memory, dual-agent
    progress sync + relay, memory promotion) — a BUGate setup is incomplete
    without it, so init treats it as a first-class step, not an optional probe.
    It is ONE service per machine shared by every governed repo (namespace-tag
    isolation): if it is already running, reuse it; if not, bring it up —
    ``bin/memory-bus-ensure`` reuses/restarts it, or installs it once
    (machine-level) on a first run. Still never blocks the import: install/start
    proceeds in the background and a slow first-time setup is reported, not fatal.
    """
    try:
        import memory_bus  # sibling module; loads client.env system-home-first

        memory_bus.load_local_env()
        url = memory_bus.base_url()
        home = memory_bus.memory_home()
        if memory_bus.service_available():
            return [
                f"memory-bus: RUNNING at {url} (data home {home}) — reusing the "
                "required machine-level shared instance; this repo only declares "
                "memory.namespace in its profile"
            ]
        if dry:
            return [f"memory-bus: not running at {url} — would install/start the required service via bin/memory-bus-ensure (machine-level, once)"]
        ensure = engine_root / "bin" / "memory-bus-ensure"
        if not ensure.exists():
            return [f"memory-bus: not running and {ensure} missing — engine tree incomplete; install per docs/SETUP-OPTIONAL.md §2"]
        try:
            proc = subprocess.run([str(ensure)], capture_output=True, text=True, timeout=120)
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = tail[-1] if tail else "(no output)"
        except Exception as exc:  # ensure must never block an import
            detail = f"{exc.__class__.__name__}: {exc}"
        if memory_bus.service_available():
            return [f"memory-bus: brought up the required machine-level service at {url}"]
        return [
            "memory-bus: required service not up yet — first-time install/start is "
            f"running in the background ({detail}). Watch with bin/memory-bus-status; "
            "it self-heals on the next session. BUGate is incomplete until it is up"
        ]
    except Exception as exc:  # never block an import
        return [f"memory-bus: ensure step had an issue ({exc.__class__.__name__}: {exc}) — required component; run bin/memory-bus-ensure and see docs/SETUP-OPTIONAL.md §2"]


NEXT_STEPS = """\
Imported-mode setup written. Next steps (CHARTER §2.2):

  1. Fill bugate.profile.yaml: add `guarded_path_regex` for this repo's test
     layout (keep the (?P<uc>...) capture) — the write guard is inert until then.
  2. COMMIT: bugate.config.yaml, bugate.profile.yaml, {vendor_dir}/,
     .claude/ + .codex/ hook wiring, .claude/skills/, .agents/skills/,
     .codex/skills/ (legacy compatibility), .codex/agents/ (the Codex gate
     agents), docs/usecases/, and the updated .gitignore (a marked block backstops the
     default scorer outputs + local agent/memory state out of git status) — the
     governance contract reviews and versions with the tests it guards.
  3. Codex only: RE-TRUST the changed hook hash in the Codex hook-management
     UI. Until then Codex hooks are SILENTLY inactive (known behavior). The
     .agents/skills/ skills and .codex/agents/ gate agents are picked up on the
     next Codex session (no re-trust needed — they are skills/agents, not hooks).
  4. Acceptance — R4 negative control: pick a guarded test path whose UC has no
     passed pre-code artifacts and confirm the block:
       python3 {vendor_dir}/scripts/check_bugate.py <a-guarded-test>.py </dev/null
       # expect exit 2 and the missing-artifact list
  5. Per-UC flow from here on:
       python3 {vendor_dir}/scripts/sdtd_orchestrator.py docs/usecases/<UC> --init
  6. Memory bus (REQUIRED, machine-level): a BUGate setup is incomplete without
     it (long-term memory, dual-agent progress sync + relay, memory promotion).
     Init already ensured it above — ONE shared mcp-memory-service per machine,
     auto-installed once if it was absent; this repo just declares its profile
     namespace. Check with {vendor_dir}/bin/memory-bus-status; if a first-time
     install is still finishing it self-heals on the next session
     ({vendor_dir}/bin/memory-bus-ensure re-checks). Offline/locked-down machine:
     BUGATE_MEMORY_NO_INSTALL=1 skips auto-install (then install manually per
     docs/SETUP-OPTIONAL.md §2).
  7. Read the vendored field guide — {vendor_dir}/docs/IMPORT-FIELD-GUIDE.md —
     before operating the orchestrator: it carries the real-SUT lessons
     (dual-agent dispatch diagnosis/proxy surface, --auto 03b overwrite
     semantics, post-run 04/05 clobber SOP, copy hygiene) and the Wave 7/8
     activation recipes. Optional one-shot self-check from the repo root:
       python3 {vendor_dir}/.shared/skills/bugate-full-check/scripts/run_full_check.py --mode smoke
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
    notes += bus_ensure(engine_root, args.dry_run)

    prefix = "[dry-run] " if args.dry_run else ""
    for note in notes:
        print(f"{prefix}{note}")
    print()
    print(NEXT_STEPS.format(vendor_dir=vendor_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
