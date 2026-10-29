# Integration and independent review

English | [简体中文](REVIEW.zh-CN.md)

Review scope is the formal F-01–F-08 matrix, not a mandatory SC checklist.
The main agent reviewed source relationships, source/protocol changes, CLI/core/
adapter boundaries, report consistency and package evidence. A separate reviewer
reproduced a named-claim regression against the pre-change behavior. The core
implementer independently checked DSH's actual installed lifecycle types and
adapter forwarding. Packaging review checked advisory draft snapshot behavior.

Resolved findings with direct impact on the real objective:

- **Profile authority:** an early draft let scope revisions replace Profile
  denominators. Baseline re-verification and the user's correction restored
  locked Profile requirements/risks; changing them requires explicit new
  Profile/task initialization, preserving the original task and failures.
- **User provenance:** actual DSH tags model-created child prompts as user-source
  messages. Capturing those would invent user consent. The adapter also checks
  session origin, excludes fork-inherited messages and gives its own rework a
  distinct source. Actual subagent regression exercises this boundary.
- **Auxiliary and diagnostic admission:** evidence gathering must not require the
  evidence it is trying to obtain. Auxiliary admission uses its declared policy;
  diagnostic admission requires an applicable managed failure and consumes a
  durable bounded budget. Auxiliary observations are not acceptance cases.
- **Result integrity:** an omitted applicable failure cannot make the functional
  summary pass; a nonzero process without a failed business Oracle remains an
  execution problem. Reports and structured results are checked against managed
  observations rather than model labels.
- **Advisory persistence:** actual DSH found that newly recorded suggestions made
  a repeated assessment conflict at the same immutable key. The request now binds
  its advisory view separately; the claim, execution manifest and authorization
  snapshot remain unchanged. The original failure and final real-runtime
  reproduction are preserved in [runtime findings](dsh/RUNTIME_FINDINGS.md).
- **Named requirement versus whole-scope closure:** the independent reviewer
  reproduced R1 verification being blocked by unobserved R2. The fix restores
  named-claim coverage, leaves whole-scope execution incomplete, and reports the
  claim's actual scope separately. The targeted test failed before the fix and
  passed after it; the final full core run passed 123 tests. See
  [independent evidence](evidence/independent-review.json).

No unresolved review finding remains. This is not a proof of arbitrary business
semantics or hostile same-user authenticity. Source-backed semantic judgment
remains distinct from deterministic references and managed execution observation.
The adapter enforces its observable managed tools; it does not classify arbitrary
shell/business semantics or promise protection from every native filesystem
writer. Registered authoring uses atomic/CAS history, while independently changed
facts invalidate evaluation. Exact runtime/package validation is indexed in the
final delivery report rather than inferred from this review.
