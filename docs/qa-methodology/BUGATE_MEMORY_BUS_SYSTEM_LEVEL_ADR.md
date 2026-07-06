---
type: ADR
id: ADR-BUGATE-003
title: System-level memory bus — one machine-wide service, namespace-tag isolation
status: accepted
created_at: 2026-07-03
authority: ADR-BUGATE-001
---

# ADR-BUGATE-003: System-Level Memory Bus

## Context

BUGate depends on an optional memory service (`mcp-memory-service`) as its
long-term truth layer, but the service should not belong to any single
project. Until this decision the live database physically lived inside ONE
BUGate checkout's working tree (`<checkout>/.memory_bus/`), while every
wrapper (`bin/memory-bus-*`) defaulted its data dir to "*own repo*
`/.memory_bus`".

That layout had a failure mode built into its construction — **split brain on
restart**: after a
service crash or machine reboot, whichever repo's `ensure` hook fired first
would host the port with *its own* `.memory_bus` as the database. A client-only
workspace (whose `.memory_bus/` holds just `client.env`) would lazily create an
EMPTY database, bind the port, and make the entire history invisible to every
client. The hazard was real: workspace SessionStart hooks run `ensure`
automatically.

Meanwhile the isolation model was already correct and needed zero server-side
change: `memory_bus.py` reads/writes are namespace-tag-filtered
(`project:<name>`), so N workspaces can share one database without
cross-pollution. What had to change was only (a) who owns the data home and
(b) how clients resolve credentials.

## Decision

1. **The service data home is machine-level, not repo-level.** Default
   `~/.bugate/memory-bus/`; the new env `BUGATE_MEMORY_HOME` overrides it; the
   service's own `MCP_MEMORY_BASE_DIR` stays highest-priority. Every wrapper
   (`bin/memory-bus-{start,stop,status}`), the stdlib client
   (`scripts/memory_bus.py memory_home()`), and the full-check resolve through
   the same order — so a restart triggered from ANY repo lands on the same
   directory by construction. Split brain is eliminated structurally, not by
   discipline.
2. **Client resolution order:** `MEMORY_BUS_URL`/`MCP_API_KEY*` env vars win;
   otherwise `client.env` is loaded from the system home; a legacy per-repo
   `.memory_bus/client.env` still works as a fallback but prints a deprecation
   hint. Keys are 0600, never in git, auto-generated on first start from the
   system home (logic lives in `bin/memory-bus-start`).
3. **Isolation = namespace tag** (`project:<name>`). In imported mode the
   governed repo's committed profile declares `memory.namespace`; BUGate core
   uses `project:bugate`. Single-database multi-tenancy is a feature (default
   views are per-project, cross-namespace reads are explicit via
   `--namespace`/`--core`). Per-project databases are explicitly rejected.
4. **Init flow:** a governed repo does NOT scaffold a local service dir; it
   only writes its namespace into the profile and points at the shared bus.
5. **Optional hardening:** `bin/memory-bus-install-launchd` registers a
   user-level macOS LaunchAgent (`RunAtLoad` + `KeepAlive`, foreground child
   via `memory launch --foreground`); `--uninstall` removes it. Its absence
   changes nothing — `bin/memory-bus-ensure` still starts the bus on demand
   and everything degrades gracefully when the service is down.
6. **Companion rule for client-only workspaces:** a workspace that is a pure
   client must not self-host. Its `memory-bus-start` delegates to the shared
   host checkout's starter (override `BUGATE_MEMORY_BUS_HOME`, which names the
   HOST CHECKOUT — distinct from `BUGATE_MEMORY_HOME`, which names the DATA
   home) and refuses to start when the host is missing; its `ensure` therefore
   safely restarts the shared service.

## Non-goals

- No remote / multi-user service; the bus stays loopback-only.
- No per-project database split.
- No modification of `mcp-memory-service` itself (its built-in daily backup
  scheduler and multi-tenant tag filtering are used as-is).

## Migration record (2026-07-03)

- Downtime ≈ 10 s: `memory-bus-stop` → SQLite `wal_checkpoint(TRUNCATE)` =
  `(0,0,0)` → moved `sqlite_vec.db` + `client.env` to `~/.bugate/memory-bus/`
  (dir 700, key 600) → restarted via `memory-bus-ensure`.
- Ledger reconciliation: total 1537 memories, 38 in the core namespace, 1499
  in the legacy SUT namespace — identical before/after; latest-entry content
  hashes preserved and retrievable through the API.
- Backups follow the home automatically (the service scheduler derives its
  backup dir from the base dir): `/api/backup/status` reports
  `<home>/backups/`, and a manual `/api/backup/now` plus the startup daily
  backup both landed there. Pre-migration `.db` snapshots stay in the old
  directory as a read-only archive with a pointer README.
- Split-brain reproduction test GREEN both ways: with the service killed,
  `ensure` from the legacy workspace and from the core checkout each brought
  the SAME historical database up from the system home.
- LaunchAgent verified: install (RunAtLoad), `kill -9` respawn (KeepAlive),
  uninstall (label + plist removed, port freed), then normal on-demand mode
  restored.
- Recorded as a `transition-gap` ledger closure (`bucket:c`,
  `resolution:routed`) per TRANSITION_PROTOCOL §4.

## Rollback

Stop the service; move `sqlite_vec.db` + `client.env` from
`~/.bugate/memory-bus/` back into a checkout's `.memory_bus/`; either export
`MCP_MEMORY_BASE_DIR=<that dir>` (highest-priority override, no code change)
or revert the wrapper commit; restart. The legacy client fallback keeps old
clients working throughout any interim state.

## Consequences

- One database to back up, one service to babysit; project growth adds a
  namespace, not an instance.
- The engine tree carries no runtime data at all — `git clean`/re-clone of any
  checkout can no longer touch the memory history.
- The deprecation path (legacy `client.env` fallback + stderr hint) means
  un-migrated setups keep working while announcing what to move.

## References

- `docs/SETUP-OPTIONAL.md` §2 (install/wire/verify for the system-level bus)
- `CAPABILITIES.md` — memory-bus command table
- `.shared/skills/bugate/references/profile-schema.md` — `memory.namespace`,
  `BUGATE_MEMORY_HOME`
- `docs/qa-methodology/TRANSITION_PROTOCOL.md` §4 — the ledger entry format
  used for the closure record
- ADR-BUGATE-001 — the four-part split this runtime layout serves
