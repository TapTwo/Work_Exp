# Work_Exp — Network Security Automation Portfolio

**Alexander Iglesias** · Zero Trust / SASE Network Security Engineer · Zscaler · Prisma Access · PAN-OS · Azure
PCNSE · CCNP Enterprise · [LinkedIn](https://www.linkedin.com/in/alexander-i-9261867b/)

Working samples and design documents from enterprise network security engineering:
firewall change automation, Zero Trust Network Access (ZTNA) migration tooling, and
policy-as-code for Palo Alto Networks estates.

Everything here is **sanitized** — no client names, production hostnames, IPs, rulebases
or credentials. Example data uses RFC 5737 ranges and `example.com`.

## Projects

| Folder | What it is | Stack |
| --- | --- | --- |
| [`panos-automation/`](panos-automation/) | PAN-OS / Panorama XML API tooling: config export, normalized drift diff, `test security-policy-match` runner, lock → partial-commit → push → poll deployment | Python, PAN-OS XML API |
| [`fw-policy-as-code/`](fw-policy-as-code/) | Firewall intent as YAML in Git: JSON-schema validation, policy lint (no any/any, logging, CHG tags, expiry), renderer to PAN-OS `set` commands, GitHub Actions CI | Python, JSON Schema, GitHub Actions |
| [`zscaler-ztna-toolkit/`](zscaler-ztna-toolkit/) | VPN → ZPA migration tooling: app-segment inventory built from VPN traffic logs, ZPA API client, cutover runbook, source-IP allowlist checklist | Python, ZPA API |
| [`docs/`](docs/) | Design notes and reference sheets: firewall change-automation operating model, NAC (ISE / ClearPass) reference | Markdown |

## Background these come from

- Managed 300–400+ Palo Alto firewalls across six Panorama servers (EMEA / AMER / APAC); PA-7050s with 100+ vsys
- Architected a Zscaler ZIA / ZPA / Client Connector stack integrated with Entra ID and Okta (SAML / SCIM), replacing legacy VPN with least-privilege application access
- GlobalProtect → Prisma Access migration; OCI and Azure PA-VM / Cloud NGFW deployments
- Multi-vendor policy orchestration (Tufin / AlgoSec) across Palo Alto, Fortinet, Check Point and Forcepoint

## Running the samples

Each folder has its own README with setup and usage. All Python samples target 3.10+ and
use only `requests`, `pyyaml` and `jsonschema`:

```bash
pip install -r panos-automation/requirements.txt
```

Credentials are read from environment variables or a local `.env` (git-ignored). Never commit keys.

## License

MIT — see [LICENSE](LICENSE).
