#!/usr/bin/env python3
"""BUGate de-SUT guard: keep one SUT's identity from seeping into the reusable kit.

Purpose (CHARTER Amendment A1, ADR-BUGATE-004): protect the kit's REUSABILITY.
The core subtree that gets vendored into — and later upgraded inside — a
governed SUT repo must not carry facts that are true for only one SUT. The
legislative intent in one line: **block seepage, not mention.** The discipline
is three-layered, and this guard mechanizes the machine-checkable slice:

  1. Behavioral SUT facts — defaults, endpoints, resources, credentials,
     environment names that would steer the engine or be inherited by the next
     SUT. Never in core, no exemption; they belong in a SUT profile
     (ADR-BUGATE-001 Promotion Rule, unchanged). The built-in GENERAL_HYGIENE
     patterns below catch the machine-detectable slice (machine-local user
     paths, credential shapes); the rest is review discipline.
  2. SUT identity terms — product/system/account names. Forbidden by default,
     but the term list is PROFILE-SUPPLIED (`sut_identity_terms`) or given via
     ``--terms-file``; the engine bakes in no product vocabulary. Narrative or
     provenance mentions are legitimate when explicitly marked (exemption
     channels below).
  3. Industry/domain vocabulary — deliberately NOT defended by core; a SUT
     profile that wants a domain word defended must list it itself.

Scan surface — anchored on the ENGINE root, never the governed workspace:

  - the kit subtree (``scripts/``, ``bin/``, ``.shared/skills/``): the fixed
    kit layout a ``bugate init`` vendors into a SUT repo, scanned in every
    layout;
  - upstream-only assets (docs/, examples/, .github/, root docs, config):
    scanned only when the engine root IS the upstream BUGate repo, detected by
    the ``CHARTER.md`` sentinel (the charter never ships in the vendored kit).

  A governed workspace's OWN files are never the scan surface: when the
  workspace root is a strict descendant of the engine root (workbench and demo
  layouts) its subtree is excluded, and in a vendored layout the SUT repo's
  files are simply not kit members. Files that legitimately DECLARE the terms
  (the active config/profile, ``--terms-file`` lists) are excluded likewise.

Exemption channels — explicit, per-site, auditable; no global kill switch:

  - inline marker ``bugate: allow-sut-term`` on the line (an HTML comment form
    ``<!-- bugate: allow-sut-term -->`` keeps rendered Markdown clean). Waives
    both scans for that line: marking a line is a signed, reviewable act.
  - file-level frontmatter ``desut: provenance-allowed`` — narrative Markdown
    OUTSIDE the kit subtree only (engine, templates, schema files never
    qualify). Waives the identity-term scan; general hygiene still runs.
  - the ``docs/case-studies/`` allowlisted directory (real import/migration
    stories). Waives the identity-term scan; general hygiene still runs.

  The marker legitimizes narrative/provenance MENTION only. Using any
  exemption to carry a behavioral fact is a violation — that verdict is owned
  by code review and the semantic gates, not by this grep (CHARTER A1 R9).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from bugate_core import (
    KIT_LAYOUT,
    find_engine_root,
    find_root,
    load_config,
    resolve_path,
    split_frontmatter,
)

# The fixed kit layout: what `bugate init` vendors into a governed repo, and
# therefore what must stay reusable across SUTs. Scanned in every layout.
# Single source of truth: bugate_core.KIT_LAYOUT (init's vendor list is
# asserted against it in tests/test_desut_guard.py).
KIT_SCAN_ROOTS = list(KIT_LAYOUT)

# Upstream-repo-only assets, scanned only when the engine root carries the
# upstream sentinel. These never ship in the vendored kit.
UPSTREAM_SENTINEL = "CHARTER.md"
UPSTREAM_SCAN_ROOTS = [
    "docs",
    "examples",
    ".github",
    "AGENTS.md",
    "CHARTER.md",
    "README.md",
    "INIT.md",
    "INIT.zh-CN.md",
    "CONTRIBUTING.md",
    "bugate.config.yaml",
]

# Narrative allowlist (relative to the engine root): identity-term scan lifted
# for the whole directory, general hygiene still enforced.
NARRATIVE_ALLOWLIST_DIRS = ["docs/case-studies"]

# Never descend into these (state, caches, vcs, vendored memory).
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".memory_bus",
    "memory",
    "progress",
    "memory-service-archive",
}

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".sh", ".txt", ".cfg", ".ini"}
ALLOW_MARKER = "bugate: allow-sut-term"
FRONTMATTER_EXEMPT_VALUE = "provenance-allowed"

# SUT-agnostic general hygiene: behavioral/environmental leakage that is never
# legitimate in a reusable kit, regardless of which SUT is mounted. Common
# placeholder usernames are tolerated so tutorials can show path shapes.
GENERAL_HYGIENE: list[tuple[str, str]] = [
    (
        r"(?:/Users|/home)/(?!(?:user|you|yourname|username|name|example|placeholder|runner|agent)/)"
        r"[A-Za-z][\w.-]+/",
        "machine-local user path",
    ),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key material"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bghp_[A-Za-z0-9]{36}\b", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{22,}\b", "GitHub fine-grained token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "Slack token"),
]


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    # Extensionless shell wrappers under bin/ are scanned too.
    return path.parent.name == "bin" and path.suffix == ""


def _scan_roots(engine_root: Path) -> list[str]:
    roots = list(KIT_SCAN_ROOTS)
    if (engine_root / UPSTREAM_SENTINEL).exists():
        roots += UPSTREAM_SCAN_ROOTS
    return roots


def _iter_files(engine_root: Path):
    for target in _scan_roots(engine_root):
        base = engine_root / target
        if base.is_symlink() or not base.exists():
            continue
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if _is_text_file(path):
                yield path


def _rel_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _in_kit_subtree(path: Path, engine_root: Path) -> bool:
    rel = _rel_to(path, engine_root)
    return any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in KIT_SCAN_ROOTS)


def _in_narrative_allowlist(path: Path, engine_root: Path) -> bool:
    rel = _rel_to(path, engine_root)
    return any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in NARRATIVE_ALLOWLIST_DIRS)


def _frontmatter_exempt(path: Path, text: str, engine_root: Path) -> bool:
    """File-level narrative exemption: Markdown outside the kit subtree only."""
    if path.suffix.lower() != ".md" or _in_kit_subtree(path, engine_root):
        return False
    fm, _ = split_frontmatter(text)
    return str(fm.get("desut") or "").strip().lower() == FRONTMATTER_EXEMPT_VALUE


def load_identity_terms(
    args: argparse.Namespace, workspace_root: Path | None, config: dict
) -> tuple[list[tuple[str, str]], set[Path]]:
    """Collect (pattern, provenance) identity terms + the files that declare them.

    Declaring files (active profile/config, --terms-file lists) legitimately
    contain the terms, so they are excluded from the scan surface.
    """
    terms: list[tuple[str, str]] = []
    declaring: set[Path] = set()

    raw = config.get("sut_identity_terms") or []
    if isinstance(raw, str):
        raw = [raw]
    for term in raw:
        text = str(term).strip()
        if text:
            terms.append((text, "profile sut_identity_terms"))
    if raw and workspace_root is not None:
        base = workspace_root / "bugate.config.yaml"
        if base.exists():
            declaring.add(base.resolve())
        profile_ref = (
            args.profile
            or os.environ.get("BUGATE_PROFILE")
            or config.get("profile")
            or config.get("active_profile")
        )
        if profile_ref:
            declaring.add(resolve_path(str(profile_ref), workspace_root).resolve())

    for terms_file in args.terms_file:
        path = Path(terms_file)
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"cannot read --terms-file {terms_file}: {exc}")
        declaring.add(path.resolve())
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append((line, str(terms_file)))

    return terms, declaring


def scan(
    engine_root: Path,
    workspace_root: Path | None,
    identity_terms: list[tuple[str, str]],
    excluded_files: set[Path],
    self_path: Path,
) -> list[str]:
    identity_patterns = [
        (re.compile(term, re.IGNORECASE), term, source) for term, source in identity_terms
    ]
    hygiene_patterns = [(re.compile(term), label) for term, label in GENERAL_HYGIENE]

    # A governed workspace nested under the engine root (workbench/demo
    # layouts) is the SUT's own territory, never the kit's scan surface.
    workspace_subtree: Path | None = None
    if workspace_root is not None:
        ws = workspace_root.resolve()
        er = engine_root.resolve()
        if ws != er and er in ws.parents:
            workspace_subtree = ws

    hits: list[str] = []
    for path in _iter_files(engine_root):
        resolved = path.resolve()
        if resolved == self_path or resolved in excluded_files:
            continue
        if workspace_subtree and (
            resolved == workspace_subtree or workspace_subtree in resolved.parents
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = _rel_to(path, engine_root)
        identity_lifted = _in_narrative_allowlist(path, engine_root) or _frontmatter_exempt(
            path, text, engine_root
        )
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue  # explicit, per-line, auditable exemption
            for regex, label in hygiene_patterns:
                match = regex.search(line)
                if match:
                    hits.append(f"{rel}:{lineno}: hygiene: {label} {match.group(0)!r}")
            if identity_lifted:
                continue
            for regex, term, source in identity_patterns:
                match = regex.search(line)
                if match:
                    hits.append(
                        f"{rel}:{lineno}: SUT identity term {match.group(0)!r} (list: {source})"
                    )
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--terms-file",
        action="append",
        default=[],
        metavar="PATH",
        help="identity-term list (one regex per line, # comments); repeatable. "
        "Used by upstream CI to regression-test against a legacy/fixture list.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="SUT profile whose sut_identity_terms to enforce (default: "
        "BUGATE_PROFILE env, then the workspace config's profile pointer)",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print on failure")
    args = parser.parse_args()

    # The guard audits the reusable KIT tree, so it anchors on the engine root —
    # never the governed workspace (whose files legitimately name its SUT).
    engine_root = find_engine_root()

    # The governed workspace supplies the identity-term list (and is excluded
    # from the surface). No workspace in sight -> hygiene-only run.
    workspace_root: Path | None = None
    config: dict = {}
    try:
        workspace_root = find_root()
        config = load_config(workspace_root, args.profile or os.environ.get("BUGATE_PROFILE"))
    except SystemExit:
        pass

    identity_terms, declaring_files = load_identity_terms(args, workspace_root, config)
    hits = scan(
        engine_root,
        workspace_root,
        identity_terms,
        declaring_files,
        Path(__file__).resolve(),
    )
    if hits:
        sys.stderr.write("BUGate de-SUT guard FAILED: SUT seepage found in the kit tree:\n")
        for hit in hits:
            sys.stderr.write(f"  - {hit}\n")
        sys.stderr.write(
            "Behavioral SUT facts belong in a SUT profile, never in core. For a "
            "legitimate narrative/provenance mention of an identity term, mark the "
            "line with 'bugate: allow-sut-term' (or '<!-- bugate: allow-sut-term -->' "
            "in Markdown), declare file-level 'desut: provenance-allowed' frontmatter "
            "on a narrative doc, or move the story under docs/case-studies/. "
            "General hygiene findings (paths, credentials) accept no file/dir "
            "exemption.\n"
        )
        return 1
    if not args.quiet:
        scope = "identity terms: " + (
            ", ".join(sorted({source for _, source in identity_terms})) or "none supplied"
        )
        print(f"BUGate de-SUT guard: PASS (kit tree clean; {scope}; general hygiene ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
