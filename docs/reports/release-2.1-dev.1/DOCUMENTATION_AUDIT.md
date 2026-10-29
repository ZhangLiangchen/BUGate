# Publication documentation audit

English | [简体中文](DOCUMENTATION_AUDIT.zh-CN.md)

The audit corrects current instructions while preserving historical evidence.
It does not add product behavior, test categories or a new acceptance denominator.
The user authorized publication and removal of misleading obsolete documentation.
The starting documentation commit was `bb6153efc7a334172c36f069c2c160504df8f375`.

| Finding / scope | Impact | Treatment |
|---|---|---|
| `protocol/v2/README{,.zh-CN}.md` declared Protocol 2.0.0, Python 2.0.0a2 and native dev.2 | Contradicted actual manifest/package versions; stale text was inside the bundle | Corrected to Protocol/native 2.1.0-dev.1 and Python 2.1.0a1. Schema inventory now references the manifest instead of a fixed count. |
| The same protocol introduction omitted scope entities and advisory assessment identity | Could imply suggestions invalidate claims, or that all residual risks block completion | Documented ScopeRequest/ScopeContract/Suggestion, the inventory plan, Profile denominator, scope/plan/environment admission, and advisory_digest. In-scope unresolved findings remain blocking; typed out-of-scope residuals reference non-executable suggestions and are non-blocking. No rule changed. |
| `CONTRIBUTING`, `CAPABILITIES`, protocol architecture and DSH guide treated the scope-convergence delivery contract as current standing authority | Could make a one-change verification matrix govern unrelated future tasks | Pointed current readers to canonical protocol, methodology and the new bilingual docs index. Historical matrices remain linked as versioned evidence. |
| Development examples wrote to `dist/scope-convergence` | A later local build could overwrite the previously evidenced local artifact path | Changed generic development examples to `dist/local-build`. Publication assets use their separate release directory. |
| DSH adapter README said dev.1 was local-only and would not be published | Became obsolete after explicit publication authorization | Replaced with release-independent guidance and a link to current release/evidence navigation. Local build still does not itself publish. |
| Adapter conformance introduction described multiple first-class hosts | Suggested retired adapter support and left historical AC-33 dangling | Scoped introduction to DSH. Retained HC behavioral identifiers for traceability and linked retired decisions through the historical index. |
| `AGENTS`, `CHARTER`, `INIT`, METHOD/SOP, scope examples, usage guide, adapter/plugin/test READMEs and Skill/projection instructions | Reviewed for authority, host boundary, default matrix, provenance, completion and recovery consistency | Preserved accurate content. The Skill and plugin configuration were not changed. Normal explicit publication authorization rules remain applicable. |
| Historical `docs/reports/scope-convergence/`, `docs/reports/v2-dsh-only/`, old release notes and their hashes | Rewriting them would change the evidence of what actually ran | Preserved bytes. New `docs/README` distinguishes current rules, implementation snapshots and historical releases. |
| Local ignored `prd_health_report.md` and `oracle_falsification_result.md` | Old generated output, absent from tracked source and package inventory | Preserved local files; not treated as current documentation or packaged. No recovery of retired generator instructions. |

The root README pair, installation Prompt pair and release notes were assigned to
other writers; their changes are reviewed separately. No product source, Schema,
MethodSpec, rule, test or runtime Skill input was changed by this audit. The two
protocol README files are explicit packaged resources in `packaging/resources.json`.
Changing their bytes changes the protocol digest, Python wheel, native projection
and bundle, so the publication build receives new checksums and a fresh installed
DSH verification record. The prior `4279c20c…` package results are not relabeled.

Validation is intentionally limited to documentation links, bilingual pairing,
known stale-instruction scans and tracked-file/bundle-input differences. The
machine-readable [audit checks](documentation-checks.json) record commands and
results. Packaging and actual installed-runtime results are separate release
records; a documentation check is not runtime acceptance or live-model evidence.
