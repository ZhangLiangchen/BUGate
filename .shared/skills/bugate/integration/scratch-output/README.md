# integration scratch output

This directory is the **engine-development (self-development) default output
location** for the `integrate-qa-methodology` workflow — a maintainer
convenience for running the onboarding workflow against BUGate itself, from
inside this engine repo. It is **not** the imported-mode target.

In imported mode the workflow writes its generated onboarding artifacts (the
current-scheme inventory, the methodology requirement list, the integration
plan, and the dry-run report) to the **imported SUT test repo's own docs area** —
`docs/qa-methodology-integration/`, resolved relative to the workspace root (the
nearest `bugate.config.yaml`) — so generated output lives with the host project
and never inside the vendored kit subtree.

This is an output directory only: nothing here is part of the BUGate engine or
an install prerequisite. Its generated contents are kept out of BUGate core by
a `.gitignore` exemption **that exists only in this engine repo**; that
exemption does not travel with the kit when it is vendored to
`<sut>/.bugate/.shared/...`, which is exactly why imported-mode runs must target
the workspace-local docs area above instead of this scratch directory. The canonical
workflow body lives in `../commands/integrate-qa-methodology.md`.
