# panos-automation

PAN-OS / Panorama XML API tooling for the deploy-and-verify half of a firewall change pipeline.
Each script is one pipeline stage; together they cover **capture → validate → commit → push → verify → drift**.

| Script | Pipeline stage | What it does |
| --- | --- | --- |
| `panos_api.py` | — | Thin XML API client: keygen, config get/show/set, export, config lock, validate, **partial commit scoped to a service account**, commit-all to device groups / template stacks, job polling with per-device results, `test security-policy-match` |
| `export_config.py` | Pre/post-change capture | Exports the running config, writes it with a SHA-256 — the evidence attached to the change record |
| `config_diff.py` | Drift detection · post-change verify | **Semantic** diff of two config exports: strips volatile attributes, ignores member/object reordering, keeps rule order, groups findings by scope (shared / device-group / template). Exit 1 on drift |
| `policy_match.py` | Pre-check · post-check · cutover test plan | Runs a YAML list of expected flows through `test security-policy-match`; expect-allow / expect-deny with optional "must hit rule X" |
| `deploy.py` | Deploy | Lock → validate → partial commit as `svc-pipeline` → push device groups in waves → poll jobs → stop at first failed wave → emit JSON evidence. `--resume-job` for idempotent recovery after a crash |

## Try the drift diff (no firewall needed)

```bash
pip install -r requirements.txt
python config_diff.py examples/intended.xml examples/running-with-drift.xml
```

Output:

```
== device-group/dc-east  (3 differences)
  [added_on_device]   .../rules/entry[@name='dc-east-app-x-db'][1]/application/member[any]
  [missing_on_device] .../rules/entry[@name='dc-east-app-x-db'][1]/application/member[mssql-db]
  [changed]           .../rules/entry[@name='dc-east-app-x-db'][1]/log-end
      intended: 'yes'
      running:  'no'

== shared  (1 differences)
  [added_on_device]   /config/shared/address/entry[@name='temp-vendor-host']/ip-netmask
```

The running config in that example also has objects and tag members in a different order and
carries `admin=`/`time=` attributes from a GUI save — none of that is reported, because none of
it is drift.

## Against a real Panorama

```bash
cp ../.env.example .env   # fill in PANORAMA_HOST / PANORAMA_API_KEY
set -a; source .env; set +a

python export_config.py --label CHG0012345-pre
python deploy.py --chg CHG0012345 --admin svc-pipeline --waves "dg-canary" "dg-east,dg-west"
python export_config.py --label CHG0012345-post
python config_diff.py exports/*CHG0012345-pre*.xml exports/*CHG0012345-post*.xml   # should equal the approved diff, nothing else
PANORAMA_HOST=fw-dc-east-01.example.com python policy_match.py examples/flows.yaml --expect-rule dc-east-app-x-web
```

## Design notes

- **Partial commit, not full commit.** `type=commit&action=partial` with `<admin><member>svc-pipeline</member></admin>`
  commits only the pipeline's own changes, so a colleague's half-finished GUI edits are never swept into a change.
- **Waves + stop on first failure.** Rollback scope is always "the waves that completed", never "somewhere in 50 devices".
- **Locks carry the CHG number.** Anyone looking at Panorama sees which change holds the lock; a dead run is recoverable with `--resume-job`.
- **Diff by scope, not per firewall.** One Shared change is one finding, not one finding per device that inherits it.
- **Everything prints evidence.** Config hashes, job IDs and per-device results are what goes on the change record and what an auditor asks for.
- Generate the API key once with `PanosClient.keygen()` for a dedicated service account, store it in a secret store
  (Azure Key Vault, etc.), and set an API key lifetime on the Panorama admin.

Tested against PAN-OS / Panorama 10.2 and 11.1 API semantics. Confirm exact XML for your version in the API browser at `https://<panorama>/api`.
