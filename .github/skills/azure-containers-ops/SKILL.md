---
name: azure-containers-ops
description: 'Azure containers troubleshooting and operations workflow for Container Apps, Container Instances, and containerized workloads. Use for startup failures, crash loops, unhealthy revisions, image pull errors, auth issues, runtime diagnostics, live log analysis, and CLI-based recovery with clear handoff notes.'
argument-hint: 'Resource type, app or container name, resource group, failure symptom, and desired outcome'
user-invocable: true
disable-model-invocation: false
---

# Azure Containers Ops

## What This Skill Produces
- Fast triage of Azure container failures using platform and app logs
- Root cause hypotheses ranked by confidence
- Concrete remediation steps using Azure CLI and safe rollout practices
- Validation checks for health, readiness, and runtime behavior
- Concise handoff summary to the main agent or user

## When to Use
- Azure Container Apps revision fails to become healthy
- Azure Container Instances container exits, restarts, or fails image pull
- App starts but requests fail, time out, or return unexpected status
- Managed identity, registry auth, or secret references fail
- Deployment appears successful but runtime health gates fail
- You need live log streaming during incident investigation

## Inputs
- Resource type: Container Apps or Container Instances
- Subscription, resource group, and resource name
- Failure signal: error text, health state, failing step, or incident timestamp
- Optional deployment context: image tag, revision name, identity, ingress mode

## Procedure
1. Confirm scope and blast radius.
2. Verify Azure context and target resource identity.
3. Collect current state snapshot:
- Provisioning state
- Running state
- Active revision or active container instance state
- Recent events and diagnostics
4. Stream live logs and capture first causal error.
5. Classify failure category:
- Startup or crash loop
- Image pull and registry auth
- Secret or environment reference
- Identity or RBAC permissions
- Networking and ingress
- App-level exception or dependency timeout
6. Correlate with most recent deployment or config change.
7. Apply minimal corrective action with rollback path.
8. Validate health and runtime behavior after change.
9. Record findings and remaining risks.
10. Provide structured handoff summary.

## Platform Branching Logic

### Container Apps path
- Check app status, latest revision, latest ready revision, running state, and health state.
- If revision is inactive or unhealthy, inspect revision-level logs and events first.
- If image pull fails, verify registry identity binding, registry server settings, and role assignment.
- If worker app has ingress disabled, avoid web-probe assumptions and validate worker-appropriate health criteria.
- If secrets are referenced, verify secret names exist and are non-empty, then confirm env var mapping.

### Container Instances path
- Check container group provisioning state, instance view, and per-container current state.
- If waiting reason points to image pull/auth, validate credentials and image path.
- If terminated with non-zero code, inspect logs and restart policy behavior.
- If networking fails, verify DNS, outbound access, and dependency reachability.

## Decision Points
- If logs show auth or permission failures, fix identity or role assignment before app code changes.
- If health gate fails but runtime appears functional, adjust validation to workload type and ingress model.
- If multiple errors appear, prioritize the earliest causal error over downstream stack traces.
- If failure is intermittent, add targeted diagnostics and capture correlation IDs before retry.
- If rollback is lower risk than forward fix, revert first then patch safely.

## CLI and Monitoring Playbook
- Verify account and subscription context.
- Query resource state and revision or container status.
- Stream logs during reproduction.
- Pull events and diagnostics for failed startup windows.
- Recheck state after each fix, not only at end.

## Quality Criteria
- Root cause tied to exact log signal and resource state transition
- Minimal fix directly addresses root cause
- No unrelated configuration drift introduced
- Sensitive values are never echoed in logs or reports
- Validation includes both platform health and app behavior checks

## Completion Checks
- Resource reports healthy or expected running state
- Latest intended revision or container instance is active and stable
- Critical runtime path succeeds in smoke test
- Live logs no longer show prior causal error
- Handoff includes evidence, actions taken, and next watch items

## Handoff Format
- Summary: one-paragraph diagnosis
- Evidence: key log lines and state observations
- Actions: exact changes or commands executed
- Validation: what passed and what remains unverified
- Next step: highest-value follow-up if residual risk remains

## Example Prompts
- /azure-containers-ops Diagnose why this Container App revision stays unhealthy and fix it.
- /azure-containers-ops Stream live logs for this container instance and identify startup failure root cause.
- /azure-containers-ops Investigate registry auth and managed identity issues blocking image pulls.
- /azure-containers-ops Produce a handoff summary for main agent after runtime recovery.
