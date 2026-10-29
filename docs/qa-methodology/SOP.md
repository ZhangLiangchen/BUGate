# Working with a bound test task

English | [简体中文](SOP.zh-CN.md)

1. Install the native package into an explicit external project and DSH profile,
   then run doctor. See the [installation guide](../DEEPSEEK_HARNESS.md).
2. Read the requirements and create a source-backed Profile against its canonical
   Schema. Initialize a task with `bugate init`; do not invent resource identities
   or copy stale task bindings.
3. Use `bugate protocol prepare` / `bugate_prepare` to obtain the current exact
   binding, MethodSpec constraints and actionable gaps.
4. Capture the actual user request and record a versioned scope contract before
   deriving required cases. Link finite goals, flows/branches and Oracles; keep
   unconfirmed assumptions visible and extra ideas as non-blocking suggestions.
   See [scope and examples](../ACCEPTANCE_SCOPE.md).
5. Create JSON artifact drafts, then register them with `artifact record` /
   `bugate_record_artifact`. The core validates Schema and computes digests. Updates
   require the expected current digest; a digest is not semantic approval.
6. Establish business understanding, testability, Oracle/case mapping and the
   required semantic review. Assess a current `test_design_complete` claim before
   governed implementation. Native optional modeling follows Profile requirements.
7. Execute configured tests through `bugate_run_test`. Record exploratory material
   with its true provenance. Wait for durable evidence publication; preserve
   product failures and infrastructure distinctions.
8. Diagnose failures and produce execution/knowledge artifacts as required by
   the Profile. Submit a scoped claim, run assess and report its actual verdict.
   Repair only in-scope actionable gaps within host limits; blocked or cancelled work is not
   silently marked complete.

Do not edit canonical registered facts via arbitrary shell writes. Do not infer
that a saved assessment remains applicable after changes. Resume checks exact
Protocol/Profile/evaluator bindings; mismatches require explicit resolution, never
automatic conversion. Normal reads and analysis remain available while governed
implementation is blocked.

User-authorized changes within the locked Profile denominator create a new
contract revision and fresh plan/claim assessment. A denominator change requires
an explicit new Profile version and a new task, preserving the old task and
failures. User receipts do not replace Profile-declared domain evidence. Never
shrink scope, remove failures or lower assertions to obtain a passing result.
Report execution closure, functional acceptance, blockers and suggestions
separately, with “This conclusion covers only the agreed scope” and the actual
covered goals.
