#!/usr/bin/env python3
"""
Run a set of expected flows through `test security-policy-match` on a firewall and
report pass/fail. Used three ways:

  - before a change, to prove the flow is NOT already allowed (change is needed)
  - after a change, to prove expect-allow flows hit the new rule and expect-deny still deny
  - during a cutover, as the app-dependency test plan

Flows live in a YAML file (see examples/flows.yaml). Exit 1 on any failure.

    PANORAMA_HOST=fw-dc-east-01.example.com python policy_match.py examples/flows.yaml
    python policy_match.py examples/flows.yaml --expect-rule CHG0012345-app-x-https
"""
import argparse
import sys

import yaml

from panos_api import PanosClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("flows", help="YAML file with a list of flows")
    ap.add_argument("--vsys", default="vsys1")
    ap.add_argument("--expect-rule", help="for expect-allow flows, also require this rule name")
    args = ap.parse_args()

    with open(args.flows, encoding="utf-8") as f:
        flows = yaml.safe_load(f)["flows"]

    fw = PanosClient.from_env()
    failures = 0
    print(f"{'result':6} {'expect':6} {'got':6} {'rule':40} flow")
    for fl in flows:
        got = fw.test_security_policy_match(
            fl["from"], fl["to"], fl["src"], fl["dst"], fl.get("proto", 6), fl["port"],
            application=fl.get("app"), vsys=args.vsys,
        )
        action = got["action"] or "no-match"
        expect = fl["expect"]
        ok = (expect == "allow" and action == "allow") or (expect == "deny" and action in ("deny", "drop", "reset-client", "reset-server", "reset-both", "no-match"))
        if ok and expect == "allow" and args.expect_rule and got["rule"] != args.expect_rule:
            ok = False
        failures += 0 if ok else 1
        desc = f"{fl['src']} -> {fl['dst']}:{fl['port']} ({fl['from']}->{fl['to']})"
        print(f"{'PASS' if ok else 'FAIL':6} {expect:6} {action:6} {str(got['rule']):40} {desc}  {fl.get('name','')}")

    print(f"\n{len(flows) - failures}/{len(flows)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
