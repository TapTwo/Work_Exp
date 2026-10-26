# Runbook — Legacy VPN to Zscaler ZPA

Pattern used to move remote access from "on the VPN = on the network" to per-application,
identity-based access. Written for a Palo Alto / Entra ID / Okta estate; adapt the names.

## 0. Principles

- **Inventory from traffic, not from memory.** The app catalog is what someone remembers; 30+ days of VPN-pool traffic logs are what the apps actually do. Build the segment list from the logs (`segment_inventory.py`), then confirm it with owners.
- **Apps are rarely one host.** Thick clients sign in on one host and pull reports or hit a database on another. The co-access report exists to catch the second host before users do.
- **Pilot, then waves. VPN stays up as the rollback until the last wave signs off.**
- **Source-IP assumptions break silently.** Anything that trusted the VPN pool (server firewalls, NSGs, app allowlists, SaaS tenants) now sees App Connector addresses instead. See `checklists/source-ip-allowlist.md`.

## 1. Discovery (weeks 1–3)

1. Export ≥30 days of traffic logs for the VPN client pool(s): Panorama/Splunk, both directions. Include month-end so batch jobs appear.
2. Run `segment_inventory.py` → `destinations.csv`, `zpa-app-segments.yaml`, `co-access.json`.
3. Walk `destinations.csv` with each app owner: confirm, justify, or remove. Record owner and business function per segment.
4. Identify multi-host apps from co-access; add every host and port to the segment.
5. Map identity: which Entra/Okta group should reach each segment. Clean up group sprawl now; policies are only as least-privilege as the groups.
6. Find source-IP dependencies (checklist). Fix ahead of each wave.

**Exit criteria:** every segment has an owner, a group, confirmed hosts/ports, and a test plan covering each business function (not just sign-in).

## 2. Build (weeks 2–4, overlaps discovery)

- **Identity:** SAML to Entra/Okta for Client Connector and ZPA admin; SCIM provisioning for groups.
- **App Connectors:** ≥2 per site/VNet, next to the apps, outbound TCP/443 to the Zscaler cloud only. Connector groups per location; server groups mapped to connector groups.
- **Firewall:** scoped rules from the connector subnet to each segment's hosts/ports, replacing the broad VPN-pool rules (disable, don't delete, the old rules until stable).
- **Segments and policy:** create from `zpa-app-segments.yaml` (`zpa_client.py create-segments`), segment groups by business area, access policy = group → segment group. Default deny.
- **Client Connector:** deploy via MDM to the pilot ring first; ZIA forwarding profile if ZIA is in scope.
- **DNS:** internal domains in ZPA search domains; verify split-DNS for anything that must resolve locally.

## 3. Pilot (1–2 weeks)

- IT plus a few users from each department.
- Test plan per segment covers each business function. Capture: ZPA diagnostics, App Connector logs, firewall session logs for every failure.
- Expect to find: missing second hosts, source-IP allowlists, certificate-pinned apps, non-standard ports.
- Fix forward; re-run the plan. Pilot ends when the plan passes two consecutive days with no new findings.

## 4. Waves

- Order: lowest-risk app groups first; business-critical last; never a finance wave near month-end.
- Per wave: comms → Client Connector push → monitor 48h → owner sign-off → disable that wave's VPN group.
- Rollback per wave: re-enable VPN group membership; nothing else changes.
- Watch: ZPA app health (`health reporting`), connector CPU/sessions, firewall denies from the connector subnet, helpdesk tickets tagged to the wave.

## 5. Decommission

- VPN gateways to monitor-only for 2 weeks after the last wave; then shut down, then remove the VPN-pool firewall rules and routes.
- Final drift pass: search firewall rules, NSGs, app configs and SaaS tenants for the old VPN pool range; nothing should remain.
- Hand over: segment inventory with owners becomes the living catalog; new-app intake form asks "hosts, ports, group, does anything trust your source IP?"

## Troubleshooting quick map

| Symptom | Check |
| --- | --- |
| User can't see the app at all | Group membership (SCIM lag), access policy order, Client Connector enrolled and ZPA enabled |
| App in list, connection fails | ZPA diagnostics: segment matched? connector selected? server reachable? then connector-to-server firewall logs |
| Sign-in works, a module fails | Second host / port not in the segment — check co-access and client config |
| Works on-site, fails remote | Source-IP allowlist on the server or SaaS tenant |
| Certificate errors | ZIA SSL inspection on a cert-pinning app: add a bypass |
| Intermittent | Connector health / capacity, DNS resolution at the connector, asymmetric paths through the internal firewall |
