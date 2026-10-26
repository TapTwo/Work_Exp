# NAC Reference — 802.1X, Cisco ISE, Aruba ClearPass

## NAC fundamentals

- NAC = identity + posture + context → an access decision, enforced at the switchport / AP / VPN headend.
- Closes the "open switchport" problem: no access by default; only authenticated, authorized, compliant endpoints get on.
- The switch/WLC **enforces**; it never decides policy. The RADIUS server (ISE/ClearPass) makes the decision.
- **AAA**: Authentication (prove identity) · Authorization (what you may do — VLAN/ACL/role/SGT) · Accounting (log the session).
- Use cases: authenticated access, dynamic segmentation, posture/compliance, guest, BYOD, IoT/OT visibility, rapid threat containment, Zero Trust at the access layer.

## Protocol stack

- **802.1X roles**: Supplicant (endpoint) · Authenticator (switch/WLC = NAS) · Authentication Server (RADIUS = ISE/ClearPass).
- Pre-auth port is **unauthorized** — only EAPOL passes. On Access-Accept → **authorized**.
- **MAB**: no supplicant (printers/cameras/IoT) → switch sends MAC as user/pass; pair with profiling; MAC is spoofable.
- **RADIUS** (UDP 1812/1813): network access, authn+authz combined, only the password is encrypted. **TACACS+** (TCP 49): device admin, AAA separated, whole payload encrypted.
- **EAP methods**: EAP-TLS (mutual cert, needs PKI — gold standard) · PEAP-MSCHAPv2 (server cert + password in tunnel, easy with AD) · EAP-TTLS (Aruba-common) · EAP-FAST (Cisco PAC) · **TEAP** (RFC 7170, EAP chaining = machine AND user) · EAP-MD5 (deprecated).
- **CoA** (RFC 5176, UDP 3799): server pushes a change into a live session — re-auth, port bounce, disconnect/quarantine. Enables posture remediation and threat containment.
- **Enforcement**: dynamic VLAN · dACL · named ACL/Filter-ID · URL redirect · SGT (TrustSec) · Aruba downloadable user role.

## Cisco ISE

- **Personas**: PAN (admin/config) · MnT (monitoring/Live Logs) · PSN (workhorse — RADIUS/TACACS+, portals, scale out) · pxGrid (context sharing).
- **Deployment**: standalone vs distributed (2× PAN, 2× MnT, many PSNs — enterprise standard).
- **Policy flow**: Policy Set (first match) → Authentication Policy (protocols + identity source) → Authorization Policy (conditions → result) → CoA.
- **Features**: Profiling (RADIUS/DHCP/HTTP/SNMP/NMAP/NetFlow/DNS/CDP-LLDP probes) · Posture (Cisco Secure Client) · Guest · BYOD (SCEP certs) · TrustSec/SGT + SGACL · pxGrid + ANC (Rapid Threat Containment).
- Strength = depth inside the Cisco fabric.

## Aruba ClearPass (CPPM)

- **Architecture**: Publisher (read/write + standby) + Subscribers (read-only, process auth). Flatter than ISE personas.
- **Modules**: Policy Manager (base AAA) · Guest · OnGuard (posture) · OnBoard (BYOD + built-in CA) · Insight (reporting) · Profiling/Device Insight.
- **Request flow**: Service (ordered match) → Authentication (method + source) → Role mapping → Posture (optional) → Enforcement (policy → profile: VLAN/ACL/Aruba role/CoA).
- Strength = **vendor-neutral / multi-vendor** reach; ClearPass Exchange for third-party integration.

## Choosing

- All-Cisco + SGT micro-segmentation → **ISE**.
- Mixed estate (Cisco + Aruba + Juniper) or Aruba wireless core → **ClearPass**.
- Both are RADIUS servers at heart — fundamentals transfer. ISE = Policy Sets / Authorization result; ClearPass = Services / Roles / Enforcement profiles.

## Troubleshooting

- **802.1X auth fails**: server live logs (ISE Live Logs / ClearPass Access Tracker) → supplicant config (EAP/cert/creds) → switchport dot1x + RADIUS client shared secret → reachability UDP 1812/1813 + cert trust.
- **IoT onboarding**: MAB + profiling → restricted segment scoped to device type.
- **802.1X rollout**: monitor → low-impact → closed mode; critical-auth / inaccessible-auth VLAN fallback if RADIUS is down.
- **Containment**: SIEM/EDR flags host → pxGrid+ANC (ISE) / ClearPass Exchange → CoA quarantine or disconnect.

## Ports

RADIUS auth UDP 1812 · acct UDP 1813 (legacy 1645/46) · TACACS+ TCP 49 · CoA UDP 3799 (Cisco legacy 1700) · EAPOL L2 0x888E.
