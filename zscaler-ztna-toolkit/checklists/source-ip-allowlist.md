# Checklist — "Does anything trust our source IP?"

After a move to ZPA, connections to internal apps come **from the App Connector subnet**, not from
the VPN client pool. After a move to ZIA, internet egress comes **from Zscaler's IP ranges**, not
the corporate egress address. Anything that allowlisted the old addresses breaks silently —
the user authenticates fine, and the connection just never completes.

Run this before every wave. Search for the **old VPN pool CIDR(s)** and the **old egress IP(s)** in:

## Internal (ZPA side)

- [ ] Panorama / firewall rules — source = VPN pool → add App Connector subnet (scoped per app, not everywhere)
- [ ] Host firewalls (Windows Firewall, iptables, nftables) on app servers
- [ ] Cloud NSGs / security groups (Azure, AWS, OCI) in front of app subnets
- [ ] Load balancer / WAF allowlists and client-IP restrictions
- [ ] Application-level IP restrictions (IIS IP restrictions, Apache `Require ip`, nginx `allow`, app config)
- [ ] Database listeners / firewalls (SQL Server, Oracle `TCP.INVITED_NODES`, Postgres `pg_hba.conf`)
- [ ] Jump hosts / bastions and privileged-access tools
- [ ] RADIUS / NAC policies keyed on the VPN pool
- [ ] Monitoring, backup and management agents that expect a specific source
- [ ] Internal DNS views / split-horizon that answer differently by source

## External (ZIA side)

- [ ] SaaS tenant IP allowlists (M365 Conditional Access named locations, Salesforce, Workday, GitHub, Atlassian…)
- [ ] Partner VPN / B2B integrations with source-IP ACLs
- [ ] Vendor portals and payment gateways
- [ ] Identity provider conditional-access rules keyed on "trusted network"
- [ ] Geo/IP-based fraud or risk controls

## For each hit

| Where | Old value | New value | Owner | CHG | Done |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |

Scope the replacement to what needs it (the connector subnet on *that* server), not a blanket allow.
Add "does anything trust your source IP?" to the new-app intake form so this is never a surprise again.
