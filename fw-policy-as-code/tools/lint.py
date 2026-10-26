#!/usr/bin/env python3
"""
Static validation of firewall intent — the first gate of the pipeline, run on every PR push.

Checks, in order:
  schema          every YAML file validates against its JSON Schema
  naming          object names match the naming standard (value-derived)
  references      every member a rule names exists (address, group, service) or is a PAN-OS builtin
  duplicates      no two objects with the same value; no two rules with the same name
  no-any-any      no allow rule with source any AND destination any
  no-any-application   allow rules name real applications (not 'any')
  profile-group   every allow rule has a security profile group
  logging         every rule logs at session end
  chg-tag         every rule carries a CHGnnnnnnn tag
  expiry          'temporary' rules carry an expiry; nothing is expired; nothing expires in < 7 days without a warning
  exceptions      an exception can waive one check for one rule until its expiry — expired exceptions fail

Exit 1 on any ERROR. WARNs don't fail the build but are posted on the PR.

    python tools/lint.py            # run from the repo root
"""
from __future__ import annotations

import ipaddress
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
BUILTIN_SERVICES = {"any", "application-default", "service-http", "service-https"}
BUILTIN_MEMBERS = {"any"}
CHG_RE = re.compile(r"^CHG\d{7}$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, check: str, where: str, msg: str) -> None:
        self.errors.append(f"ERROR [{check}] {where}: {msg}")

    def warn(self, check: str, where: str, msg: str) -> None:
        self.warnings.append(f"WARN  [{check}] {where}: {msg}")


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_schema(path: Path, schema_name: str, rep: Report) -> dict:
    data = load_yaml(path)
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    for err in sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        rep.error("schema", f"{path.relative_to(ROOT)}:{loc}", err.message)
    return data


def main() -> int:
    rep = Report()
    today = date.today()

    # ---- objects
    addresses: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    services: dict[str, str] = {}
    for p in sorted((ROOT / "shared" / "objects").glob("*.yaml")) + sorted((ROOT / "device-groups").glob("*/objects/*.yaml")):
        if "address" in p.name:
            data = validate_schema(p, "addresses.schema.json", rep)
            for a in data.get("addresses", []):
                # naming: name must be derived from value
                expected = derived_name(a["value"], a.get("type", "ip-netmask"))
                if expected and a["name"] != expected:
                    rep.error("naming", f"{p.name}:{a['name']}", f"expected name {expected} for value {a['value']}")
                if a["value"] in addresses.values():
                    rep.error("duplicates", f"{p.name}:{a['name']}", f"another object already has value {a['value']}")
                addresses[a["name"]] = a["value"]
            for g in data.get("address_groups", []):
                groups[g["name"]] = g["members"]
        elif "service" in p.name:
            data = validate_schema(p, "services.schema.json", rep)
            for s in data.get("services", []):
                key = f"{s['protocol']}/{s['port']}"
                if key in services.values():
                    rep.error("duplicates", f"{p.name}:{s['name']}", f"another service already has {key}")
                services[s["name"]] = key

    for g, members in groups.items():
        for m in members:
            if m not in addresses and m not in groups:
                rep.error("references", f"address-group {g}", f"member {m} does not exist")

    # ---- exceptions
    exceptions: dict[tuple[str, str], dict] = {}
    exc_path = ROOT / "exceptions" / "exceptions.yaml"
    if exc_path.exists():
        for e in validate_schema(exc_path, "exceptions.schema.json", rep).get("exceptions", []):
            if date.fromisoformat(e["expires"]) < today:
                rep.error("exceptions", f"{e['rule']}/{e['check']}", f"exception expired {e['expires']} ({e['chg']})")
            else:
                exceptions[(e["rule"], e["check"])] = e

    def waived(rule: str, check: str) -> bool:
        return (rule, check) in exceptions

    # ---- rules
    seen_rules: set[str] = set()
    for p in sorted((ROOT / "device-groups").glob("*/*-rules.yaml")) + sorted((ROOT / "shared").glob("*-rules.yaml")):
        data = validate_schema(p, "rules.schema.json", rep)
        dg = p.parent.name if p.parent.name != "shared" else "shared"
        for r in data.get("rules", []):
            where = f"{dg}/{r['name']}"
            if r["name"] in seen_rules:
                rep.error("duplicates", where, "rule name already used")
            seen_rules.add(r["name"])
            if dg != "shared" and not r["name"].startswith(dg + "-"):
                rep.error("naming", where, f"rule name must start with '{dg}-'")

            for field in ("source", "destination"):
                for m in r[field]:
                    if m not in addresses and m not in groups and m not in BUILTIN_MEMBERS and not is_literal_ip(m):
                        rep.error("references", where, f"{field} member {m} does not exist")
            for s in r["service"]:
                if s not in services and s not in BUILTIN_SERVICES:
                    rep.error("references", where, f"service {s} does not exist")

            is_allow = r["action"] == "allow"
            if is_allow and r["source"] == ["any"] and r["destination"] == ["any"] and not waived(r["name"], "no-any-any"):
                rep.error("no-any-any", where, "allow rule with source any and destination any")
            if is_allow and "any" in r["application"] and not waived(r["name"], "no-any-application"):
                rep.error("no-any-application", where, "allow rule with application any")
            if is_allow and not r.get("profile_group") and not waived(r["name"], "profile-group"):
                rep.error("profile-group", where, "allow rule without a security profile group")
            if not r.get("log_end", False) and not waived(r["name"], "logging"):
                rep.error("logging", where, "log_end must be true")
            if not any(CHG_RE.match(t) for t in r["tags"]):
                rep.error("chg-tag", where, "no CHGnnnnnnn tag")
            if not any(t.startswith("owner-") for t in r["tags"]):
                rep.warn("owner-tag", where, "no owner-* tag")

            exp = r.get("expires")
            if "temporary" in r["tags"] and not exp:
                rep.error("expiry", where, "temporary rule without an expiry")
            if exp:
                d = date.fromisoformat(exp)
                if d < today:
                    rep.error("expiry", where, f"rule expired {exp}")
                elif d < today + timedelta(days=7):
                    rep.warn("expiry", where, f"rule expires {exp}")

    # ---- report
    for w in rep.warnings:
        print(w)
    for e in rep.errors:
        print(e)
    print(f"\n{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    return 1 if rep.errors else 0


def derived_name(value: str, kind: str) -> str | None:
    try:
        if kind == "fqdn":
            return f"F-{value}"
        if kind == "ip-range":
            return f"R-{value}"
        net = ipaddress.ip_network(value, strict=False)
        if net.prefixlen == net.max_prefixlen:
            return f"H-{net.network_address}"
        return f"N-{net.network_address}_{net.prefixlen}"
    except ValueError:
        return None


def is_literal_ip(s: str) -> bool:
    try:
        ipaddress.ip_network(s, strict=False)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
