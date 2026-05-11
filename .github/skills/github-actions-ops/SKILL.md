---
name: github-actions-ops
description: 'GitHub CI/CD and Actions troubleshooting workflow for live run monitoring, log triage, secret/variable management, and fix validation. Use for failed workflows, flaky runs, deployment breakages, Azure deploy errors, and when you need actionable handoff notes to the main agent.'
argument-hint: 'Workflow name/run URL, failure symptom, and desired outcome'
user-invocable: true
disable-model-invocation: false
---

# GitHub Actions Ops

## What This Skill Produces
- Fast diagnosis of failing or flaky GitHub Actions runs
- Root-cause hypotheses ranked by confidence
- Concrete remediation steps (workflow edits, config fixes, secret/variable updates)
- Re-run and validation guidance
- Concise handoff summary for the main agent/user

## When to Use
- A workflow run failed and logs are noisy or truncated
- Deployment succeeded partially but health checks fail
- Secrets/variables are missing, empty, or incorrectly scoped
- You need live monitoring of an in-progress run
- CI behavior differs between local and runner environments
- You need to automate repo settings updates from CLI

## Inputs
- GitHub repository and branch
- Workflow run URL or workflow name + run number
- Current failure signal (error line, step name, annotation, stack trace)
- Optional environment context (Azure subscription, resource group, app name)

## Procedure
1. Confirm target and scope.
2. Inspect the latest or specified workflow run and enumerate failed jobs/steps.
3. Extract high-signal errors from logs and annotations.
4. Classify failure type:
- Build/test failure
- Runtime/container startup failure
- Infra/deploy failure
- Auth/secret/variable failure
- Health-check false negative or race condition
5. Correlate with repository changes and workflow logic around the failing step.
6. Generate a minimal fix plan with expected impact and rollback option.
7. Apply fix(es) in smallest safe diff.
8. If needed, set or update repo secrets/variables and document which scope was used.
9. Re-run workflow and monitor progress until completion or new actionable failure appears.
10. Produce a handoff summary:
- What failed
- What changed
- Why it should work now
- What to watch in next run

## Decision Points
- If logs show empty/invalid secret values, prefer fixing secrets/vars before code edits.
- If deployment passes but health check fails, validate probe logic and worker-vs-web assumptions.
- If YouTube/3rd-party endpoints fail only in cloud, evaluate egress/IP reputation and runtime flags.
- If multiple failures appear, prioritize the earliest causal failure, not downstream noise.
- If failure is nondeterministic, add targeted diagnostics first, then retry.

## Quality Checks
- Root cause references exact job + step + log signal
- Fix is minimal and directly tied to root cause
- No unrelated refactors included
- Secrets are not logged or exposed in outputs
- Re-run evidence confirms improvement or narrows next hypothesis

## Live Monitoring Playbook (GH CLI)
- Authenticate and verify repo access
- View workflow runs and failed jobs
- Stream job logs while run is active
- Re-run failed jobs after fixes
- Capture run URL and status for handoff

## Secrets and Variables Playbook
- Detect missing/empty required values
- Update repository-level or environment-level secret/variable as needed
- Prefer least-privilege scope and avoid duplication across scopes
- Record only secret names and purpose (never secret values)

## Handoff Format
- Summary: one-paragraph diagnosis
- Findings: bullet list of concrete evidence
- Changes: files/settings updated
- Validation: run ID, job status, key passing checks
- Next action: single highest-value follow-up if still failing

## Example Prompts
- /github-actions-ops Investigate why CI/CD deploy failed in run 123456789 and patch the workflow.
- /github-actions-ops Monitor the currently running deploy workflow and report issues in real time.
- /github-actions-ops Validate if repo secrets/variables are causing this Azure deployment error.
- /github-actions-ops Produce a concise handoff note for the main agent after fixing this failed run.
