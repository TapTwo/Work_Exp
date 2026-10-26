# zscaler-ztna-toolkit

Tooling and method for migrating remote access from a legacy VPN to Zscaler Private Access (ZPA):
build the application inventory from what users actually reach, turn it into app segments,
and cut over in waves with the VPN as the rollback.

| File | Purpose |
| --- | --- |
| `segment_inventory.py` | VPN traffic logs (CSV) → per-destination summary, **proposed ZPA app segments**, and a co-access report that finds multi-host apps (sign-in on one host, reports on another) |
| `zpa_client.py` | ZPA API client (OAuth client credentials): list segments, groups, connectors, access policy; create segments from the inventory's YAML stub |
| `runbook/vpn-to-zpa-migration.md` | Discovery → build → pilot → waves → decommission, with exit criteria and a troubleshooting map |
| `checklists/source-ip-allowlist.md` | The dependency that breaks silently after ZTNA: everything that trusted the VPN pool or the old egress IP |
| `examples/vpn-traffic-sample.csv` | 7,300 synthetic log rows (RFC 5737 addresses) to try the inventory tool |

## Try it

```bash
pip install -r ../panos-automation/requirements.txt
python segment_inventory.py examples/vpn-traffic-sample.csv --vpn-pool 192.0.2.0/24 --min-users 2 --out build/
```

```
rows from VPN pool: 5800   destinations: 18 (dropped 1 below --min-users 2)

proposed segments (8):
  domain-services      hosts=  2  users=  60  sessions=  2000  tcp/88,389,445,udp/88
  portal               hosts=  1  users=  60  sessions=  1000  tcp/443
  erp                  hosts=  2  users=  30  sessions=   925  tcp/443,1433,8000
  files                hosts=  1  users=  55  sessions=   750  tcp/445
  reports              hosts=  1  users=  45  sessions=   450  tcp/8443
  git                  hosts=  1  users=  20  sessions=   375  tcp/22,443
  jump                 hosts=  1  users=   6  sessions=   200  tcp/22,3389
  subnet:203.0.113.0/24 hosts= 1  users=   2  sessions=    75  tcp/9100

co-accessed host pairs (same client, both hosts) — check for multi-host apps:
    45 clients  portal.corp.example.com  <->  reports.corp.example.com
    30 clients  erp-app.corp.example.com <->  erp-db.corp.example.com
```

Three things that output tells you before anyone opens a ticket:

1. The ERP thick client talks to the database on 1433 directly — the segment needs both hosts, not just the web front end.
2. `portal` users also hit `reports` on 8443 — that's one application, two hosts, and the second one isn't on the diagram.
3. NTP, DNS and the single-user one-offs were filtered out so the list is reviewable.

Column names differ by log source; map them with `--col dst_ip=destination_ip` etc.

## Against a live ZPA tenant

```bash
cp ../.env.example .env   # ZPA_BASE_URL, ZPA_CLIENT_ID, ZPA_CLIENT_SECRET, ZPA_CUSTOMER_ID
set -a; source .env; set +a

python zpa_client.py connectors
python zpa_client.py groups
python zpa_client.py create-segments build/zpa-app-segments.yaml --dry-run
python zpa_client.py create-segments build/zpa-app-segments.yaml --segment-group-id <id> --server-group-id <id>
```

The API client is deliberately small; for a full SDK use the official `zscaler-sdk-python`, or the
`zscaler/zpa` Terraform provider once segments are stable and should live in code.

## Method in one paragraph

Pull 30+ days of VPN-pool traffic. Build the segment list from it, not from the app catalog. Confirm
every destination with an owner. Catch the second host with the co-access report. Search everything for
the VPN pool range before each wave (the allowlist checklist). Pilot with IT plus a slice of each
department, then waves by business area, VPN group disabled per wave, VPN kept as rollback until the
last wave signs off. Decommission only after a final search finds no reference to the old pool.
