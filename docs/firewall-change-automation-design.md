# Firewall Change Automation — Target Design

A reference design for turning firewall change management into code: ServiceNow for requests
and approvals, AlgoSec for path and risk analysis, Git for intent, and a pipeline that is the only
thing that writes to Panorama — with evidence on every change and continuous drift detection.

The working samples for the pipeline half of this design live in
[`../panos-automation/`](../panos-automation/) and [`../fw-policy-as-code/`](../fw-policy-as-code/).

## The target flow

Every change follows one path. One CHG number links the request, the PR, the pipeline run and the evidence.

| # | System | Step | If it fails |
| --- | --- | --- | --- |
| 1 | ServiceNow | Request — catalog item: flow, owner, reason, expiry | |
| 2 | AlgoSec FireFlow | Analyze — path, already allowed?, risk, work order | Already allowed → auto-close, notify requester |
| 3 | Git | Pull request — intent + CHG; CI checks; peer review | Check fails → back to the author |
| 4 | ServiceNow | Approve change — risk-based approval; CHG in Implement | |
| 5 | Pipeline → Panorama | Deploy — capture, lock, partial commit, push in waves | |
| 6 | Pipeline | Verify — capture again, compare, run flow tests | Post-check fails → roll back |
| 7 | ServiceNow, FireFlow | Evidence and close — artifacts on CHG; FireFlow validates | |
| 8 | Pipeline | Drift watch — Panorama vs Git hourly; classify each diff | Unauthorized drift → incident; backport or revert |

## Systems of record

ServiceNow owns the request and approval state, Git owns configuration intent, and Panorama only holds deployed state.

| System | System of record for | Not the owner of | Hands off |
| --- | --- | --- | --- |
| ServiceNow | Request (RITM), change record (CHG), approvals, window, closure | The configuration | CHG number and approval state to the pipeline gate; request fields to AlgoSec |
| AlgoSec FireFlow + Security Analyzer | Requirement analysis: devices in path, already-allowed check, risk, recommended change | Deployment — keep ActiveChange off for pipeline-managed devices | Recommended change to a PR; risk result to the CHG |
| Git repository | Configuration intent, peer review, change history | Permission to deploy in a window | Merged commit SHA to the pipeline |
| Pipeline (Azure DevOps / GitHub Actions) | Validation results, deployment runs, pre/post artifacts | Business or risk approval | Evidence and status to the CHG; drift reports |
| Panorama and firewalls | Deployed (running) state | Intent — a direct edit here is drift | Config exports to drift detection and evidence |

Pipeline runtime: self-hosted agents with reach to Panorama, a secret store (Key Vault) for API keys, immutable blob storage for evidence. The CHG number goes in the branch name, PR title, commit message, pipeline run, evidence path and FireFlow request.

## Operating model

People approve only what a machine cannot check: business intent, risk acceptance, technical correctness and timing. Syntax, naming, references and policy rules move into the pipeline.

| Role | Does | Control point owned |
| --- | --- | --- |
| Requester / app owner | Submits a complete request; confirms the outcome | Business justification |
| Firewall engineer | Turns the AlgoSec recommendation into a PR; owns the change to closure | Technical design |
| Peer reviewer | Reviews the rendered diff, test flows and rollback note | Technical approval (author ≠ approver) |
| Security architect | Decides high-risk results and exceptions | Risk acceptance |
| Change manager / CAB | Schedules, checks conflicts, approves normal changes by risk | Change authorization |
| Pipeline service account | Validates, deploys, collects evidence; cannot approve | Automated gates |
| Operations | Watches the post-change window; calls rollback | Post-change verification |
| Platform owner | Runs the pipeline, data model, integrations and drift tooling | Tooling change control |

**Change types**

- **Standard** — pre-approved patterns with full automated validation (host added to an existing group, EDL entry, object update). No CAB; still a PR and the pipeline.
- **Normal** — new access, rule or NAT changes. Risk sets the approval: low → peer + change manager; high → adds CAB and the security architect.
- **Emergency** — break-glass under an emergency CHG, backported to Git within an agreed SLA (e.g. 24 h), enforced by drift detection.

**Separation of duties:** the author can't approve, the pipeline can't approve, and merging a PR is not permission to deploy.

## Repository-based configuration

Intent is a YAML data model that mirrors Panorama's hierarchy, rendered to PAN-OS. Git says what the config should be; ServiceNow says why, who approved it and when.

| Option | Strong for | Watch for |
| --- | --- | --- |
| YAML data model + renderer (pan-os-python or XML API) | Rules, objects, groups, NAT; clean diffs; schema validation | You own the renderer and its tests |
| Terraform `panos` provider | Declarative CRUD; `plan` doubles as a change preview | State drift, brownfield import, rule ordering, slow plans on large rulebases |
| Ansible `paloaltonetworks.panos` | Procedural tasks, check/diff mode, quick wins | Not full-state by default |
| Raw XML or `set` snippets | Anything PAN-OS supports | Noisy diffs, hard reviews |

- Trunk-based; short-lived branches named for the change (`CHG0012345-app-x-https`).
- Branch protection: no direct pushes, required checks green, squash merge — one commit = one change = one clean revert.
- CODEOWNERS per device group; one reviewer who isn't the author, two for Shared, NAT and templates. Reviewers judge the rendered diff and the test results, not the YAML.
- Build the deploy artifact once at merge (rendered config, diff, SHA) and promote that same artifact: lab → canary → waves. Environment differences live in variables, never in long-lived branches.
- Brownfield: export Panorama, convert to the data model, prove a round trip (rendered = normalized running) before calling a device group managed. One device group at a time.

## Validation and evidence pipeline

| Stage | Runs | Checks | Fails when |
| --- | --- | --- | --- |
| 1. Static | Every PR push | Schema, naming, references, duplicates by value, policy rules (no any/any allow, profile group and logging set, CHG tag, expiry on temporary rules) | Any violation without an approved exception |
| 2. Semantic | Every PR push | Shadowed/redundant rules, placement, zone validity, AlgoSec risk check | Shadowing warns; high risk goes to the security gate |
| 3. Render and diff | Every PR push | Render intent, diff against Panorama running config, post the readable diff on the PR | Diff touches anything outside declared scope |
| 4. Device validation | Before the window | Load into a lab/twin Panorama and validate the commit; baseline `test security-policy-match` on production to prove the change is needed | Validation errors |
| 5. Approval gate | Deploy start | CHG approved and in Implement, window open, no freeze, artifact SHA = merged SHA | Any condition false |
| 6. Pre-change capture | Deploy start | Artifact set below; config lock taken | Capture fails — no baseline, no deploy |
| 7. Deploy | In the window | Apply under lock → partial commit as the service account → push device groups/templates in waves → poll each job | Device fails — partial-failure rules |
| 8. Post-change verify | After each wave | Same artifact set vs pre and vs expected diff; flow tests | Threshold breached — rollback trigger |
| 9. Evidence and close | End of run | Upload artifacts with hashes, attach summary to the CHG, update states, release lock | Evidence incomplete — CHG can't close |

**Pre/post artifacts**

| Artifact | Source | Pass rule |
| --- | --- | --- |
| Running config | `type=export&category=configuration` | Diff = the approved diff, nothing else |
| Push job results | `show jobs id <n>` | Every device succeeded |
| DG / template sync | Panorama managed-devices status | All targets in sync |
| System and HA state | `show system info`, `show high-availability state` | Same versions and HA roles; peer up |
| Interfaces | `show interface all` | No up-to-down changes |
| Routing | `show routing route`, `show routing protocol bgp peer` | Route count within threshold; peers Established |
| Sessions and drops | `show session info`, `show counter global filter delta yes severity drop` | Within threshold; no new drop spikes |
| Flow tests | `test security-policy-match …` per expected flow | Allows hit the new rule; denies still deny |
| Rule hits | `show rule-hit-count …` | New rule gets hits; removed rules had none |

Raw artifacts go to immutable storage under `CHG/<run>/<device>/pre|post` with SHA-256 hashes; a summary is attached to the CHG with the commit SHA, run ID and Panorama job IDs. Re-runs are idempotent: read job IDs and the lock owner, verify device state, never re-apply blind.

## Drift detection

Drift = any difference between the rendered intent at the last deployed commit (plus approved exceptions) and what Panorama and the firewalls run. Only *unauthorized* drift raises an incident.

1. Export hourly and on Panorama config-log events.
2. Normalize: drop volatile attributes, sort unordered collections, keep rule order.
3. Diff semantically by path (device group → rulebase → rule → field), not as text. See [`config_diff.py`](../panos-automation/config_diff.py).
4. Classify: out of scope · approved exception (unexpired) · emergency change pending backport (within SLA) · unauthorized.
5. Act: unauthorized → incident for the owning team; pending backport → reminders until the PR merges.

Compare at source scope (Shared / device group / template), or one Shared change shows up as hundreds of device diffs. Renderer and drift tool must apply the same object-precedence rule (descendant wins by default) or they report false drift. Phase it: detect and report → auto-PR → auto-revert only for low-risk classes once trusted. Never silently revert a rule someone added during an incident.

## Rollback

Decided before the change: triggers, recovery method and consistency rules per change, each path rehearsed with evidence.

| Situation | Method |
| --- | --- |
| Failed before commit | Revert the candidate; release the lock |
| Committed on Panorama, push failed | Load the previous Panorama config version and commit (hold the lock — loading replaces the whole candidate) |
| Pushed, bad outcome (default) | `git revert`; pipeline redeploys previous intent through expedited gates |
| Pushed, fastest restore needed | Load previous Panorama version, commit, push to affected DGs/templates; merge the revert PR right after |
| Firewall lost Panorama after a push | Automated commit recovery (per template) reverts that firewall |

PAN-OS has no Junos-style commit-confirmed; the pipeline's watch window and triggers fill the gap. Define consistency groups (HA pairs, both ends of a path, sites serving one app): all-or-nothing inside a group, best-effort across independent sites. Deploy in waves and stop at the first failure — rollback scope is the completed waves.

## AlgoSec and ServiceNow handoffs

AlgoSec produces an artifact (work order + risk), not a push. ActiveChange stays off for pipeline-managed devices or it becomes a drift source.

| Step | From → to | Data |
| --- | --- | --- |
| Request | Requester → ServiceNow catalog item | Source, destination, service/app, justification, app CI, owner, expiry |
| Analysis | ServiceNow → FireFlow | Request fields + RITM/CHG |
| Already allowed? | FireFlow → ServiceNow | Auto-close with the result |
| Plan and risk | FireFlow → ServiceNow | Devices in path, risk severity, work order |
| Proposal to code | FireFlow → Git | Work order converted to intent; PR opened with CHG and FireFlow IDs |
| Approval | ServiceNow ↔ pipeline | CHG state, window, approvers (Azure Pipelines ServiceNow gate / DevOps Change Velocity) |
| Deploy and evidence | Pipeline → ServiceNow + FireFlow | Run ID, commit SHA, job IDs, artifacts, pass/fail |
| Validate and close | FireFlow → ServiceNow | Validation result; CHG closed, RITM fulfilled |

## Automation-ready Panorama design

Fix what code can't express cleanly — per-device templates, local overrides, ambiguous names, undocumented exceptions — before automation copies them at scale.

| Element | Automation-ready | Revise if you see |
| --- | --- | --- |
| Templates | Modular by function; device values in template variables | One template per firewall; hard-coded IPs |
| Template stacks | Consistent order and naming; ≤ 8 templates; priority intentional | Same setting in several templates; stack-level overrides |
| Device groups | Hierarchy follows policy ownership; ≤ 4 levels | One-off groups; a hierarchy that copies the org chart |
| Rules | Global controls in pre-rules, cleanup in post-rules, no local rules; unique names, tags, CHG | Local firewall rules; untagged rules; duplicate names |
| Objects | Lowest scope that covers every user; names derived from value; no duplicates by value | One name, different values across DGs; free-text names |
| Exceptions | Recorded with scope, CHG, approver and expiry | Exceptions only people know about |

```text
H-10.20.30.40              host
N-10.20.0.0_16             network
R-10.20.30.10-10.20.30.20  range
F-api.example.com          FQDN
SVC-TCP-8443               service
AG-<app>-<role>            address group
<dg>-<app>-<purpose>       rule (tags: app, owner, CHG)
TPL-<scope>-<function>     template
TS-<site>-<role>           template stack
```

Rule evaluation order: Shared pre → DG pre (ancestors first) → local → DG post (descendants first) → Shared post → default; first match wins. Track rule UUIDs so hit counts and audit history survive renames.

## PAN-OS API quick reference

| Task | Call |
| --- | --- |
| API key | `type=keygen` (POST; store in a secret store; set a key lifetime) |
| Read candidate / running | `type=config&action=get` / `action=show` + `xpath` |
| Change config | `type=config&action=set|edit|delete|move|rename` |
| Config lock | `type=op` · `<request><config-lock><add><comment>CHG…</comment></add></config-lock></request>` |
| Validate | `type=op` · `<validate><full></full></validate>` → job |
| Partial commit (service account only) | `type=commit&action=partial` · `<commit><partial><admin><member>svc-pipeline</member></admin></partial></commit>` |
| Push device group | `type=commit&action=all` · `<commit-all><shared-policy><device-group><entry name="DG"/></device-group></shared-policy></commit-all>` |
| Push template stack | `type=commit&action=all` · `<commit-all><template-stack><name>TS</name></template-stack></commit-all>` |
| Job status | `type=op` · `<show><jobs><id>N</id></jobs></show>` |
| Export config | `type=export&category=configuration` |
| Flow test | `test security-policy-match from <z> to <z> source <ip> destination <ip> protocol 6 destination-port 443` |
| XML behind any CLI command | `debug cli on` |

## References

- [AlgoSec FireFlow — traffic change workflow](https://techdocs.algosec.com/en/asms/a32.10/asms-help/content/ff-ug/traffic-change-request-lifecycle.htm)
- [Palo Alto Networks — Commit all (XML API)](https://docs.paloaltonetworks.com/pan-os/11-0/pan-os-panorama-api/pan-os-xml-api-request-types/commit-configuration-api/commit-all)
- [Palo Alto Networks — Device Group Hierarchy](https://docs.paloaltonetworks.com/panorama/11-1/panorama-admin/panorama-overview/centralized-firewall-configuration-and-update-management/device-groups/device-group-hierarchy)
- [Palo Alto Networks — Configure a Template Stack](https://docs.paloaltonetworks.com/panorama/11-1/panorama-admin/manage-firewalls/manage-templates-and-template-stacks/configure-a-template-stack)
- [Palo Alto Networks — Enable Automated Commit Recovery](https://docs.paloaltonetworks.com/panorama/10-2/panorama-admin/administer-panorama/enable-automated-commit-recovery)
- [Microsoft Learn — Azure Pipelines and ServiceNow change management](https://learn.microsoft.com/en-au/azure/devops/pipelines/release/approvals/servicenow?view=azure-devops)
- [ServiceNow — DevOps Change Velocity](https://www.servicenow.com/docs/r/washingtondc/release-notes/devops-rn.html)
