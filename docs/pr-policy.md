# Pull request scope and acceptance policy

[English](pr-policy.md) | [简体中文](pr-policy.zh-CN.md)

PanWatch has limited maintainer review capacity. A contribution should deliver one independently usable behavior with evidence that a maintainer can reproduce. Completing a large implementation does not guarantee that its product scope will be accepted.

## Agree on scope before implementation

Discuss new markets, authentication modes, external account synchronization, new dependencies and architectural changes in an issue before implementation. Describe the user problem, smallest useful version, defaults, compatibility, supported capabilities and who will maintain it. Maintainer agreement on a proposal does not replace review of the implementation.

Separate independent features and unrelated cleanup. Each smaller PR must work after merging; do not split an unusable feature across required PRs merely to fit the budget. Shared prerequisites can be delivered first if they have their own acceptance criteria. New optional markets and connectors start disabled; advanced capabilities are accepted separately.

## Scope budget

The initial automatic budget is **20 production files and 800 added + deleted production lines**. Both limits apply. Tests, Markdown documentation and known lockfiles are excluded from this budget, but remain part of review. Workflows, runtime configuration and translations count. Renaming production code into an excluded path does not remove it from the count.

Exceeding the budget requires a smaller independently usable change or an explicit maintainer exception. These limits measure review load; staying below them does not prove that a PR has one purpose or that its behavior is correct. The values live in `.github/pr-policy.json` and can be adjusted after experience with the first rollout.

A maintainer applies `pr:scope-approved` to allow a justified cross-cutting change for the current head. The exception expires on a new push and must be reapplied after reviewing the new scope. It waives only the size limit, not evidence, correctness or an explicit request to split.

## Acceptance report

Use the repository PR template. The body must contain meaningful `Background`, `Changes`, `Scope`, `Validation`, `Acceptance evidence`, `Boundaries and risks`, and `Follow-up` sections. Write the body in English; discussion can use either language. Use `type(scope): subject` for the English title. `None` is valid for Follow-up.

In Validation include `Tested commit:` with the current PR head (7–40 characters) and `Commands and results:`. After pushing, rerun checks appropriate to the change and update the report. Never mark unexecuted commands or author-provided claims as independently verified.

Acceptance evidence contains `Steps:`, `Expected:`, `Observed:` and `Evidence:`. Link a GitHub workflow/log, sanitized screenshot or recording, or include concise sanitized output. Evidence must be accessible from the PR; local-only paths are not a delivered report. Explain any check that is not applicable; bare placeholders, unchecked boxes, TODO, N/A or None do not count as evidence. The bot checks that material is supplied and tied to the head, not that its claims are true.

Evidence should match the change:

| Change | Relevant evidence |
| --- | --- |
| Focused bug fix | Before/after reproduction and relevant checks |
| UI behavior | Relevant tests and screenshots; light/dark and desktop/mobile where layout changes |
| Proxy or authentication | Real deployment/session flow, configured trust boundary, login/logout and existing access compatibility |
| Market extension | Enable/disable flow, small real-provider sample, symbol/currency/time validation and existing-market compatibility |
| Holdings import/sync | Preview/apply results, field ownership, failure preservation, unsupported records and currency handling |
| Documentation or tooling | Relevant preview/tool checks; explain why application tests or UI evidence do not apply |

Full-project tests and a browser report are not mandatory for every PR. Required checks depend on the affected behavior. Private positions, secrets and credentials must be removed from evidence.

## Automated notices and closure

The workflow initially runs in **audit** mode: it reads PR metadata/files and logs results without writing labels, comments, statuses or closing PRs. Maintainers activate enforcement separately.

In **enforce** mode, the bot:

1. Checks current-head metadata, report structure and size on PR changes and a daily sweep.
2. Posts one persistent notice listing unmet requirements, marks `pr:needs-work`, and reports `PR policy` against the PR head.
3. Gives **14 days from the first notice**, posts a separate reminder after 7 days, and closes a still-unresolved PR on a daily sweep after the deadline. Comments/pushes do not extend the clock. Becoming compliant clears it; reopening starts a fresh window.
4. Rechecks head/body/labels immediately before closing. An API failure or incomplete file list prevents that decision.

The same full notice period applies to old PRs. The PR creation date is never a closure deadline. Drafts also receive notices: use an issue for long-term planning and ask for a hold when an unfinished draft has an agreed purpose. Closure preserves the branch; the author can reopen after addressing the requests or submit smaller PRs.

A compliant PR awaiting maintainer review is not closed automatically. CI awaiting approval, unavailable infrastructure or an unrelated baseline failure alone do not trigger closure. Explain such limitations in the report; they may still prevent merge readiness. Correct code and real evidence require human review even with a green policy status.

Maintainer labels:

| Label | Effect |
| --- | --- |
| `pr:needs-work` | Bot-owned indicator of unmet requirements |
| `pr:needs-split` | Human scope decision; blocks readiness until a maintainer clears it |
| `pr:needs-evidence` | Human evidence-quality decision; blocks readiness until cleared |
| `pr:scope-approved` | Size exception for one reviewed head; not an evidence waiver |
| `pr:hold` | Pauses automatic closure, clears the deadline and keeps readiness pending; removing it starts a fresh window if still noncompliant |

Authors should ask for re-review after addressing human requests. Maintainers can use a hold while that re-review is pending; an unchanged blocking label remains an unmet requirement.

## Maintainer rollout

1. Merge the policy PR with `PR_POLICY_MODE` unset (read-only audit). Inspect a manual workflow run and adjust thresholds if necessary.
2. Publish tailored transition feedback to current contributors. For already-discussed large PRs, repeat the accepted minimum scope rather than sending an unexplained rejection.
3. Set repository Actions variable `PR_POLICY_MODE=enforce` and manually run **PR contribution policy**. It creates the five policy labels if needed, posts initial notices and starts fresh clocks.
4. After verifying the status on a real PR, require **PR policy** for `main` through branch protection/a ruleset. Without this repository setting, the workflow can report failures but cannot prevent a maintainer from merging. Keep existing required checks; do not replace them with this metadata check.
5. Verify repository Actions event policies allow this metadata-only `pull_request_target` workflow. GitHub's event-policy defaults can block it; inspect repository policy settings during activation.

The privileged workflow checks out only trusted default-branch policy code, never a contributor's head or artifacts. It executes no commands taken from a PR report. Policy regression tests run separately with read-only permissions. GitHub schedules can run late, so closure occurs on the first successful daily sweep after the deadline, not at an exact wall-clock time.

Related GitHub documentation: [trusted pull_request_target handling](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target), [commit statuses](https://docs.github.com/en/rest/commits/statuses), [required status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).
