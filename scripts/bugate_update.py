#!/usr/bin/env python3
"""Plan, apply, verify, and roll back an imported BUGate installation.

This is the release bootstrap entry point and the implementation behind the
vendored ``bin/bugate-update`` wrapper.  It deliberately does not scaffold a
SUT repository; fresh installation remains ``bugate_init.py``'s job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

sys.dont_write_bytecode = True

import bugate_install_contract as contract
import bugate_update_engine as engine
import bugate_update_source as source
import bugate_update_transaction as transaction


UPDATER_VERSION = "0.4.3"
RELEASE_BASE_URL = "https://github.com/ZhangLiangchen/BUGate/releases/download"
MAX_ARCHIVE_DOWNLOAD = 512 * 1024 * 1024
MAX_CHECKSUM_DOWNLOAD = 2 * 1024 * 1024


class CliError(RuntimeError):
    """A stable, operator-facing command failure."""


class CliArgumentError(CliError):
    """Argument parsing failed before a command could run."""


class UpdateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliArgumentError(message)


def _download(url: str, destination: Path, *, limit: int) -> None:
    """Download one public release asset with a strict byte limit."""

    # The caller owns the parent temporary directory, but a pre-existing leaf
    # is never ours to replace or clean up.  ``open('xb')`` closes the race;
    # ``created`` prevents its FileExistsError path from unlinking a competing
    # writer's file.
    if os.path.lexists(destination):
        raise CliError("release download destination already exists")
    created = False
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "bugate-update"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    declared = int(raw_length)
                except ValueError as exc:
                    raise CliError("release server returned an invalid Content-Length") from exc
                if declared < 0 or declared > limit:
                    raise CliError("release asset exceeds the updater download limit")
            total = 0
            with destination.open("xb") as output:
                created = True
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise CliError("release asset exceeds the updater download limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
    except CliError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as exc:
        if created:
            destination.unlink(missing_ok=True)
        raise CliError(f"release download failed: {type(exc).__name__}") from exc


def _download_release(version: str, directory: Path) -> tuple[Path, Path]:
    version = contract.validate_semver(version)
    prefix = f"bugate-{version}"
    base = f"{RELEASE_BASE_URL}/v{version}"
    archive = directory / f"{prefix}.tar.gz"
    checksums = directory / f"{prefix}.SHA256SUMS"
    if os.path.lexists(archive) or os.path.lexists(checksums):
        raise CliError("release download set collides with an existing path")
    archive_created = False
    checksums_created = False
    try:
        _download(f"{base}/{archive.name}", archive, limit=MAX_ARCHIVE_DOWNLOAD)
        archive_created = True
        _download(f"{base}/{checksums.name}", checksums, limit=MAX_CHECKSUM_DOWNLOAD)
        checksums_created = True
    except BaseException:
        # A two-asset source is indivisible.  Clean only leaves whose creation
        # completed in this call; an operator/competitor-owned leaf is never
        # removed.
        if archive_created:
            archive.unlink(missing_ok=True)
        if checksums_created:
            checksums.unlink(missing_ok=True)
        raise
    return archive, checksums


def _unpacked_release_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[1]
    manifest = candidate / contract.RELEASE_MANIFEST_PATH
    return candidate if manifest.is_file() and not manifest.is_symlink() else None


@contextmanager
def _prepared_release(args: argparse.Namespace) -> Iterator[source.PreparedRelease]:
    """Resolve one explicit release source without persisting target state."""

    archive = getattr(args, "archive", None)
    checksums = getattr(args, "checksums", None)
    target_version = getattr(args, "to", None)
    if target_version is not None:
        target_version = contract.validate_semver(target_version)
    if bool(archive) != bool(checksums):
        raise CliError("--archive and --checksums must be supplied together")

    try:
        target_root = engine._safe_root(Path(args.target))
        temp_base = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, engine.UpdateEngineError) as exc:
        raise CliError("cannot establish a safe temporary-source boundary") from exc
    if temp_base == target_root or target_root in temp_base.parents:
        raise CliError(
            "temporary source root must be outside the imported workspace"
        )

    with ExitStack() as stack:
        raw = stack.enter_context(
            tempfile.TemporaryDirectory(
                prefix="bugate-update-source-", dir=temp_base
            )
        )
        staging = Path(raw)
        if archive:
            prepared = source.prepare_archive(
                Path(archive).expanduser(),
                Path(checksums).expanduser(),
                staging,
                expected_version=target_version,
            )
        else:
            unpacked = _unpacked_release_root()
            if unpacked is not None:
                local_prepared = source.prepare_unpacked(unpacked)
            else:
                local_prepared = None
            if (
                local_prepared is not None
                and (
                    target_version is None
                    or local_prepared.manifest.get("bugate_version")
                    == target_version
                )
            ):
                prepared = local_prepared
            else:
                if target_version is None:
                    raise CliError(
                        "remote update requires explicit --to VERSION; there is no implicit latest"
                    )
                downloaded_archive, downloaded_sums = _download_release(
                    target_version, staging
                )
                # Extraction owns a separate top-level temporary directory.
                # Identity-safe source cleanup may quarantine a concurrently
                # exchanged child beside it; nesting extraction under the
                # download TemporaryDirectory would let its later recursive
                # cleanup erase that preserved operator entry.
                extract_root = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(
                            prefix="bugate-update-extract-",
                            dir=temp_base,
                        )
                    )
                )
                prepared = source.prepare_archive(
                    downloaded_archive,
                    downloaded_sums,
                    extract_root,
                    expected_version=target_version,
                )
                prepared = source.PreparedRelease(
                    prepared.root,
                    prepared.manifest,
                    prepared.archive_sha256,
                    "remote",
                    prepared.root_identity,
                )
        yield prepared


def _load_saved_plan(path: str) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("--plan does not contain a readable JSON plan") from exc
    if not isinstance(value, dict):
        raise CliError("--plan JSON must be an object")
    return value


def _human_plan(plan: dict[str, Any]) -> str:
    profile = plan.get("profile_compatibility") or {}
    profile_state = profile.get("migration") or profile.get("status", "unknown")
    lines = [
        "BUGate imported-mode update plan",
        f"From: {plan.get('from_version') or 'unrecognized'}",
        f"To: {plan.get('to_version') or 'unknown'}",
        f"Release digest: {plan.get('release_digest') or 'unavailable'}",
        f"Profile: {profile_state}",
        "Managed changes:",
    ]
    changes = plan.get("managed_changes") or []
    if not changes:
        lines.append("  (none)")
    for item in changes:
        lines.append(
            f"  [{item.get('classification', 'unknown')}] {item.get('target_path', item.get('id', '?'))}"
        )
    hook_changes = plan.get("hook_changes") or []
    if hook_changes:
        lines.append("Hook changes:")
        for item in hook_changes:
            lines.append(
                f"  [hook_refresh] {item.get('target_path', '?')} {item.get('event', '')}".rstrip()
            )
    for warning in plan.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    if plan.get("codex_hook_hash_changed"):
        lines.append("Codex hook hash changed: re-trust required")
    if plan.get("new_session_required"):
        lines.append("New Claude/Codex session required")
    lines.append(
        "Rollback: " + ("available after apply" if plan.get("rollback_available") else "not available")
    )
    lines.append(f"Decision: {plan.get('decision', 'NO-GO')}")
    return "\n".join(lines)


def _human_status(result: dict[str, Any]) -> str:
    lines = [
        "BUGate imported-mode update status",
        f"State: {result.get('kind', 'unknown')}",
        f"Installed version: {result.get('version') or 'unrecognized'}",
        f"Vendor dir: {result.get('vendor_dir', 'unknown')}",
    ]
    if result.get("recovery_required"):
        lines.append("Recovery required: yes")
    for warning in result.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    lines.append(f"Decision: {result.get('decision', 'NO-GO')}")
    return "\n".join(lines)


def _human_report(result: dict[str, Any], title: str) -> str:
    lines = [title]
    transaction_id = result.get("transaction_id")
    if transaction_id:
        lines.append(f"Transaction: {transaction_id}")
    if result.get("engine_updated") is not None:
        lines.append(f"Engine update: {'succeeded' if result['engine_updated'] else 'not applied'}")
    if result.get("memory_checked") is not None:
        lines.append(f"Memory health checked: {bool(result['memory_checked'])}")
    if result.get("role_governance_activated") is not None:
        lines.append(
            f"Role-governance activation: {bool(result['role_governance_activated'])}"
        )
    profile = result.get("profile_migration") or result.get("profile_compatibility")
    if isinstance(profile, dict):
        profile_state = profile.get("migration") or profile.get("status", "unknown")
        lines.append(f"Profile: {profile_state}")
    if result.get("codex_hook_hash_changed"):
        lines.append("Codex hook hash changed: re-trust required")
    if result.get("new_session_required"):
        lines.append("New Claude/Codex session required")
    if result.get("recovery_required"):
        lines.append("Recovery required: yes")
    for failure in result.get("failures") or []:
        if isinstance(failure, dict):
            detail = failure.get("error") or json.dumps(
                failure, ensure_ascii=False, sort_keys=True
            )
        else:
            detail = str(failure)
        lines.append(f"FAILURE: {detail}")
    for warning in result.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    lines.append(f"Decision: {result.get('decision', 'NO-GO')}")
    return "\n".join(lines)


def _emit(value: dict[str, Any], *, json_output: bool, human: str) -> None:
    if json_output:
        sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    else:
        print(human)


def _legacy_manifests(prepared: source.PreparedRelease | None) -> list[dict[str, Any]]:
    if prepared is None:
        unpacked = _unpacked_release_root()
        if unpacked is None:
            return []
        manifest = engine.load_release_manifest(
            unpacked / contract.RELEASE_MANIFEST_PATH,
            expected_version=UPDATER_VERSION,
        )
        return list(engine.load_legacy_manifests(unpacked, manifest))
    return list(engine.load_legacy_manifests(prepared.root, prepared.manifest))


def _run(args: argparse.Namespace) -> int:
    try:
        root = engine._safe_root(Path(args.target))
    except engine.UpdateEngineError as exc:
        raise CliError(str(exc)) from exc
    vendor_dir = contract.validate_vendor_dir(args.vendor_dir)
    json_output = bool(args.json)

    if args.command == "status":
        result = engine.get_status(
            root,
            vendor_dir,
            legacy_manifests=_legacy_manifests(None),
            recovery=transaction.recovery_status(root, vendor_dir),
        )
        _emit(result, json_output=json_output, human=_human_status(result))
        return 0 if result.get("decision") == "GO" else 1

    if args.command == "verify":
        result = engine.verify_installed(
            root,
            vendor_dir,
            legacy_manifests=_legacy_manifests(None),
            recovery=transaction.recovery_status(root, vendor_dir),
        )
        _emit(
            result,
            json_output=json_output,
            human=_human_report(result, "BUGate installed-state verification"),
        )
        return 0 if result.get("decision") == "GO" else 1

    if args.command == "rollback":
        result = transaction.rollback_transaction(
            root,
            vendor_dir,
            args.transaction,
            updater_version=UPDATER_VERSION,
            legacy_manifests=_legacy_manifests(None),
        )
        _emit(
            result,
            json_output=json_output,
            human=_human_report(result, "BUGate update rollback"),
        )
        return 0 if result.get("decision") == "GO" else 1

    with _prepared_release(args) as prepared:
        legacy = _legacy_manifests(prepared)
        recovery = transaction.recovery_status(root, vendor_dir)
        if (
            args.command == "apply"
            and not getattr(args, "dry_run", False)
            and recovery.get("recovery_required")
        ):
            # Read-only commands report interrupted state without repairing it.
            # A real apply is the mutating recovery entry point: the release
            # source has already been verified, recovery runs under the update
            # lock, and the plan is built only after the restored base exists.
            transaction.recover_pending(root, vendor_dir)
            recovery = transaction.recovery_status(root, vendor_dir)
            if recovery.get("recovery_required"):
                raise CliError("transaction recovery did not reach a stable base")
        plan = engine.build_update_plan(
            root,
            vendor_dir,
            prepared.manifest,
            legacy_manifests=legacy,
            archive_sha256=prepared.archive_sha256,
            source_kind=prepared.source_kind,
            updater_version=UPDATER_VERSION,
            recovery=recovery,
        )

        saved_plan_path = getattr(args, "plan", None)
        if saved_plan_path:
            saved = _load_saved_plan(saved_plan_path)
            if saved.get("plan_digest") != plan.get("plan_digest"):
                raise CliError("saved plan is stale or was built from different inputs")
            engine.validate_plan_base(root, vendor_dir, saved)
        if args.command == "plan" or getattr(args, "dry_run", False):
            _emit(plan, json_output=json_output, human=_human_plan(plan))
            return 0 if plan.get("decision") == "GO" else 1

        if plan.get("decision") != "GO":
            _emit(plan, json_output=json_output, human=_human_plan(plan))
            return 1
        result = transaction.apply_update(
            root,
            vendor_dir,
            prepared,
            plan,
            updater_version=UPDATER_VERSION,
        )
        _emit(
            result,
            json_output=json_output,
            human=_human_report(result, "BUGate imported-mode update"),
        )
        return 0 if result.get("decision") == "GO" else 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", nargs="?", default=".", help="imported SUT repo root")
    parser.add_argument(
        "--vendor-dir", default=".bugate", help="BUGate vendor directory (default: .bugate)"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--to", metavar="VERSION", help="explicit remote/target version")
    parser.add_argument("--archive", help="offline release .tar.gz or .zip")
    parser.add_argument("--checksums", help="matching release SHA256SUMS asset")


def build_parser() -> argparse.ArgumentParser:
    parser = UpdateArgumentParser(prog="bugate-update", description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {UPDATER_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show installed and recovery state")
    _add_common(status)

    plan = subparsers.add_parser("plan", help="build a zero-write update plan")
    _add_common(plan)
    _add_source(plan)

    apply = subparsers.add_parser("apply", help="apply one reviewed transaction")
    _add_common(apply)
    _add_source(apply)
    apply.add_argument("--plan", help="previous machine-readable plan to revalidate")
    apply.add_argument(
        "--dry-run", action="store_true", help="build the plan without persistent writes"
    )

    verify = subparsers.add_parser("verify", help="verify without repairing installed state")
    _add_common(verify)

    rollback = subparsers.add_parser("rollback", help="roll back one committed transaction")
    _add_common(rollback)
    rollback.add_argument("--transaction", required=True, help="32-hex transaction id")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except CliArgumentError as exc:
        command = next(
            (
                item
                for item in raw_argv
                if item in {"status", "plan", "apply", "verify", "rollback"}
            ),
            None,
        )
        if "--json" in raw_argv:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": command,
                        "decision": "NO-GO",
                        "errors": [str(exc)],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            parser.print_usage(sys.stderr)
            print(f"bugate-update: argument error: {exc}", file=sys.stderr)
        return 2
    try:
        return _run(args)
    except KeyboardInterrupt:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "command": args.command,
                        "decision": "NO-GO",
                        "errors": ["interrupted"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print("bugate-update: interrupted", file=sys.stderr)
        return 130
    except (
        CliError,
        contract.ContractError,
        source.UpdateSourceError,
        engine.UpdateError,
        transaction.TransactionError,
    ) as exc:
        if getattr(args, "json", False):
            payload = {
                "schema_version": 1,
                "command": args.command,
                "decision": "NO-GO",
                "errors": [str(exc)],
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"bugate-update: NO-GO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
