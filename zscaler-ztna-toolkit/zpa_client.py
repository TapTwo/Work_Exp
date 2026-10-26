"""
Minimal Zscaler Private Access (ZPA) API client — OAuth2 client credentials.

Covers the read/write calls used during a VPN -> ZPA migration:
list app segments, segment groups, server groups, app connectors and access policy;
create an app segment from the inventory tool's YAML stub.

Env: ZPA_BASE_URL (e.g. https://config.private.zscaler.com), ZPA_CLIENT_ID,
     ZPA_CLIENT_SECRET, ZPA_CUSTOMER_ID

    python zpa_client.py segments            # list app segments
    python zpa_client.py connectors          # app connector health
    python zpa_client.py policy              # access policy rules
    python zpa_client.py create-segments build/zpa-app-segments.yaml --dry-run

API reference: https://help.zscaler.com/zpa/zpa-api  (confirm paths for your cloud / version)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests
import yaml


class ZpaClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, customer_id: str):
        self.base = base_url.rstrip("/")
        self.customer = customer_id
        self.s = requests.Session()
        r = self.s.post(
            f"{self.base}/signin",
            data={"client_id": client_id, "client_secret": client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        r.raise_for_status()
        tok = r.json()
        self.s.headers["Authorization"] = f"{tok.get('token_type', 'Bearer')} {tok['access_token']}"
        self.s.headers["Content-Type"] = "application/json"

    @classmethod
    def from_env(cls) -> "ZpaClient":
        return cls(os.environ["ZPA_BASE_URL"], os.environ["ZPA_CLIENT_ID"],
                   os.environ["ZPA_CLIENT_SECRET"], os.environ["ZPA_CUSTOMER_ID"])

    # ---------------------------------------------------------------- helpers
    def _mgmt(self, path: str, version: str = "v1") -> str:
        return f"{self.base}/mgmtconfig/{version}/admin/customers/{self.customer}/{path.lstrip('/')}"

    def _paged(self, path: str, version: str = "v1", page_size: int = 500) -> list[dict]:
        out, page = [], 1
        while True:
            r = self.s.get(self._mgmt(path, version), params={"page": page, "pagesize": page_size}, timeout=60)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "2")))
                continue
            r.raise_for_status()
            body = r.json()
            items = body.get("list", [])
            out.extend(items)
            if page >= int(body.get("totalPages", 1)) or not items:
                return out
            page += 1

    # ---------------------------------------------------------------- reads
    def app_segments(self) -> list[dict]:
        return self._paged("application")

    def segment_groups(self) -> list[dict]:
        return self._paged("segmentGroup")

    def server_groups(self) -> list[dict]:
        return self._paged("serverGroup")

    def app_connectors(self) -> list[dict]:
        return self._paged("connector")

    def access_policy_rules(self) -> list[dict]:
        pol = self.s.get(self._mgmt("policySet/policyType/ACCESS_POLICY"), timeout=60)
        pol.raise_for_status()
        return self._paged(f"policySet/rules/policyType/ACCESS_POLICY")

    # ---------------------------------------------------------------- writes
    def create_app_segment(self, seg: dict, segment_group_id: str, server_group_ids: list[str]) -> dict:
        payload = {
            "name": seg["name"],
            "domainNames": seg["domain_names"],
            "tcpPortRange": [{"from": str(p), "to": str(p)} for p in seg.get("tcp_port_ranges", [])],
            "udpPortRange": [{"from": str(p), "to": str(p)} for p in seg.get("udp_port_ranges", [])],
            "segmentGroupId": segment_group_id,
            "serverGroups": [{"id": i} for i in server_group_ids],
            "enabled": True,
            "healthReporting": "ON_ACCESS",
            "bypassType": "NEVER",
            "description": f"created from traffic inventory; evidence {json.dumps(seg.get('evidence', {}))}",
        }
        r = self.s.post(self._mgmt("application"), json=payload, timeout=60)
        r.raise_for_status()
        return r.json()


# -------------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("segments")
    sub.add_parser("groups")
    sub.add_parser("connectors")
    sub.add_parser("policy")
    c = sub.add_parser("create-segments")
    c.add_argument("yaml_file")
    c.add_argument("--segment-group-id", required=False)
    c.add_argument("--server-group-id", action="append", default=[])
    c.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    z = ZpaClient.from_env()

    if args.cmd == "segments":
        for s in z.app_segments():
            print(f"{s.get('id'):>16}  {s.get('name'):40}  {','.join(s.get('domainNames', []))}")
    elif args.cmd == "groups":
        print("segment groups:")
        for g in z.segment_groups():
            print(f"  {g.get('id'):>16}  {g.get('name')}")
        print("server groups:")
        for g in z.server_groups():
            print(f"  {g.get('id'):>16}  {g.get('name')}")
    elif args.cmd == "connectors":
        for c_ in z.app_connectors():
            print(f"{c_.get('name'):30} {c_.get('currentVersion', ''):14} {c_.get('controlChannelStatus', '')}  {c_.get('privateIp', '')}")
    elif args.cmd == "policy":
        for r in z.access_policy_rules():
            print(f"{r.get('ruleOrder', ''):>4}  {r.get('action', ''):8}  {r.get('name')}")
    elif args.cmd == "create-segments":
        with open(args.yaml_file, encoding="utf-8") as f:
            segs = yaml.safe_load(f)["app_segments"]
        for seg in segs:
            if args.dry_run:
                print(f"would create {seg['name']}: {seg['domain_names']} tcp={seg.get('tcp_port_ranges')} udp={seg.get('udp_port_ranges')}")
                continue
            if not args.segment_group_id or not args.server_group_id:
                print("--segment-group-id and --server-group-id are required without --dry-run", file=sys.stderr)
                return 2
            res = z.create_app_segment(seg, args.segment_group_id, args.server_group_id)
            print(f"created {res.get('id')}  {res.get('name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
