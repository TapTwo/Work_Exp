# fw-policy-as-code

Firewall policy intent kept as YAML in Git, validated on every pull request, rendered to PAN-OS.
Git is the system of record for *what the config should be*; the change-management system
owns *why* and *who approved it*; the pipeline is the only thing that writes to Panorama.

```
fw-policy-as-code/
  schemas/                 JSON Schema per file type
  shared/objects/          addresses, groups, services (value-derived names)
  device-groups/<dg>/
    pre-rules.yaml         order in file = order on device
    post-rules.yaml
    objects/               DG-scoped objects
  exceptions/              approved lint waivers: rule, check, CHG, approver, expiry
  tools/lint.py            gate 1 — static validation
  tools/render.py          gate 3 — render intent to `set` commands for review and lab validation
../.github/workflows/policy-ci.yml   runs both on every PR
```

## Run it

```bash
pip install -r ../panos-automation/requirements.txt
python tools/lint.py                  # 0 error(s), 0 warning(s)
python tools/render.py > build/panorama.set
```

## What the linter enforces

| Check | Rule |
| --- | --- |
| `schema` | every file validates against its JSON Schema (unknown keys fail) |
| `naming` | `H-<ip>`, `N-<net>_<len>`, `R-`, `F-` names must match the value; rule names start with their device group |
| `references` | every source / destination / service a rule names must exist |
| `duplicates` | no two objects with the same value; no two rules with the same name |
| `no-any-any` | no allow rule with source any **and** destination any |
| `no-any-application` | allow rules name real App-IDs |
| `profile-group` | allow rules carry a security profile group |
| `logging` | `log_end: true` on every rule |
| `chg-tag` | every rule carries a `CHGnnnnnnn` tag |
| `expiry` | `temporary` rules carry `expires:`; expired rules fail; < 7 days warns |
| `exceptions` | a waiver covers one check on one rule until its expiry; an expired waiver fails the build |

Add a rule like this and the build fails with six errors:

```yaml
  - name: dc-east-bad-rule
    from: [any]
    to: [any]
    source: [any]
    destination: [H-10.0.0.1]     # doesn't exist
    application: [any]            # no-any-application
    service: [any]
    action: allow                 # no profile group
    log_end: false                # logging
    tags: [temporary]             # no CHG tag, no expiry
    description: someone's shortcut
```

## Workflow

1. Branch per change: `CHG0012345-app-x-https`.
2. Edit YAML; the PR template asks for the CHG, the AlgoSec/risk result, expect-allow / expect-deny flows and a rollback note.
3. CI runs lint → render → uploads `panorama.set`. Reviewers read the rendered diff, not the YAML.
4. One reviewer who isn't the author (two for Shared, NAT and templates) via CODEOWNERS. Squash merge: one commit = one change = one clean revert.
5. Merge is technical approval only. Deployment (`../panos-automation/deploy.py`) also requires the CHG approved and the window open.
6. Drift detection (`../panos-automation/config_diff.py`) compares the rendered intent at the deployed commit with hourly Panorama exports; unauthorized differences open an incident.

## Why `set` commands and not Terraform/Ansible here

Any of the three works. This sample renders `set` commands because the output is the most readable
change preview for a reviewer and loads straight into a lab Panorama for commit validation. In a
production pipeline the renderer would target the XML API (see `../panos-automation/panos_api.py`),
`pan-os-python`, or Terraform's `panos` provider — the data model stays the same.
