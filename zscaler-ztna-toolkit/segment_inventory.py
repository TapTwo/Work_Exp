#!/usr/bin/env python3
"""
Build a ZPA application-segment inventory from VPN traffic logs.

The problem this solves: nobody has a complete list of what remote users actually reach
through the VPN. The app catalog is what someone remembers; the firewall log is what the
apps actually do. So we build the inventory from 30+ days of VPN-pool traffic and group
destinations into proposed app segments — then take that list to the app owners.

Input: a CSV of firewall/VPN traffic logs with at least these columns (names configurable):
    src_ip, dst_ip, dst_port, proto, [dst_fqdn], [app], [user], [bytes]
Exports from Panorama, Splunk, or `show log traffic` all map onto this.

Output:
    - per-destination summary: who talks to it, on which ports, how often
    - proposed segments: destinations grouped by FQDN domain / app tag / subnet
    - a CSV you hand to app owners to confirm, plus a YAML stub of ZPA app segments
    - the "second host" finder: ports/hosts a client opens *after* the primary one
      (thick clients that sign in on one host and pull reports from another)

    python segment_inventory.py examples/vpn-traffic-sample.csv --vpn-pool 192.0.2.0/24 \
        --min-users 2 --out build/
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
from collections import defaultdict
from pathlib import Path

import yaml

ALWAYS_SKIP_PORTS = {53, 123, 137, 138, 5355}   # DNS/NTP/NetBIOS noise, handled separately
AUTH_PORTS = {88, 389, 636, 464, 3268, 3269}     # AD — becomes one "domain services" segment
DC_PREFIXES = ("dc", "ad", "ldap", "gc")         # hostnames that are domain controllers regardless of port


def load_rows(path: Path, cols: dict[str, str]) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = []
        for raw in csv.DictReader(f):
            try:
                rows.append({
                    "src": raw[cols["src_ip"]].strip(),
                    "dst": raw[cols["dst_ip"]].strip(),
                    "port": int(raw[cols["dst_port"]]),
                    "proto": raw.get(cols["proto"], "tcp").strip().lower() or "tcp",
                    "fqdn": (raw.get(cols["dst_fqdn"]) or "").strip().lower(),
                    "app": (raw.get(cols["app"]) or "").strip(),
                    "user": (raw.get(cols["user"]) or "").strip(),
                    "bytes": int(raw.get(cols["bytes"]) or 0),
                })
            except (KeyError, ValueError):
                continue
        return rows


def in_pool(ip: str, pools: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in p for p in pools)


def host_stem(fqdn: str) -> str:
    """portal.corp.example.com -> portal ; erp-db.corp.example.com -> erp ; dc01 -> dc"""
    host = fqdn.split(".")[0]
    host = host.split("-")[0]
    return host.rstrip("0123456789") or host


def segment_key(r: dict) -> str:
    """
    Group destinations into a proposed segment:
      AD ports or DC-named hosts -> domain-services
      FQDN                       -> hostname stem (erp-app + erp-db -> 'erp'), so one app = one segment
      App-ID only                -> app:<name>
      bare IP                    -> subnet:/24
    """
    stem = host_stem(r["fqdn"]) if r["fqdn"] else ""
    if r["port"] in AUTH_PORTS or stem in DC_PREFIXES:
        return "domain-services"
    if r["fqdn"]:
        return stem
    if r["app"] and r["app"] not in ("ssl", "web-browsing", "unknown-tcp", "unknown-udp", "incomplete"):
        return f"app:{r['app']}"
    net = ipaddress.ip_network(r["dst"] + "/24", strict=False) if ":" not in r["dst"] else ipaddress.ip_network(r["dst"] + "/64", strict=False)
    return f"subnet:{net}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--vpn-pool", action="append", required=True, help="VPN client pool CIDR (repeatable)")
    ap.add_argument("--min-users", type=int, default=1, help="drop destinations seen from fewer distinct users/sources")
    ap.add_argument("--out", type=Path, default=Path("build"))
    ap.add_argument("--col", action="append", default=[], help="column mapping, e.g. --col dst_ip=destination_ip")
    args = ap.parse_args()

    cols = {"src_ip": "src_ip", "dst_ip": "dst_ip", "dst_port": "dst_port", "proto": "proto",
            "dst_fqdn": "dst_fqdn", "app": "app", "user": "user", "bytes": "bytes"}
    for m in args.col:
        k, v = m.split("=", 1)
        cols[k] = v

    pools = [ipaddress.ip_network(p) for p in args.vpn_pool]
    rows = [r for r in load_rows(args.csv, cols) if in_pool(r["src"], pools) and r["port"] not in ALWAYS_SKIP_PORTS]

    # ---- per destination
    dest: dict[tuple[str, int, str], dict] = {}
    for r in rows:
        k = (r["dst"], r["port"], r["proto"])
        d = dest.setdefault(k, {"dst": r["dst"], "fqdn": r["fqdn"], "port": r["port"], "proto": r["proto"],
                                "app": r["app"], "sessions": 0, "bytes": 0, "users": set(), "sources": set()})
        d["sessions"] += 1
        d["bytes"] += r["bytes"]
        d["sources"].add(r["src"])
        if r["user"]:
            d["users"].add(r["user"])
        if r["fqdn"] and not d["fqdn"]:
            d["fqdn"] = r["fqdn"]

    kept = [d for d in dest.values() if max(len(d["users"]), len(d["sources"])) >= args.min_users]
    dropped = len(dest) - len(kept)

    # ---- proposed segments
    segments: dict[str, dict] = defaultdict(lambda: {"hosts": set(), "ports": defaultdict(set), "users": set(), "sessions": 0})
    for d in kept:
        key = segment_key({"port": d["port"], "fqdn": d["fqdn"], "app": d["app"], "dst": d["dst"]})
        s = segments[key]
        s["hosts"].add(d["fqdn"] or d["dst"])
        s["ports"][d["proto"]].add(d["port"])
        s["users"] |= d["users"] or d["sources"]
        s["sessions"] += d["sessions"]

    # ---- second-host finder: for each source, hosts contacted within the same log set on >1 distinct host of a segment
    by_src: dict[str, set[str]] = defaultdict(set)
    for d in kept:
        host = d["fqdn"] or d["dst"]
        if segment_key({"port": d["port"], "fqdn": d["fqdn"], "app": d["app"], "dst": d["dst"]}) == "domain-services":
            continue  # everyone talks to the DCs; not a signal
        for s in d["sources"]:
            by_src[s].add(host)
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for hosts in by_src.values():
        hs = sorted(hosts)
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                pair_counts[(hs[i], hs[j])] += 1
    co_access = sorted(((n, a, b) for (a, b), n in pair_counts.items() if n >= max(2, args.min_users)), reverse=True)[:50]

    # ---- write outputs
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "destinations.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["destination", "fqdn", "proto", "port", "app", "sessions", "bytes", "distinct_users", "distinct_sources", "proposed_segment", "owner", "confirmed"])
        for d in sorted(kept, key=lambda x: -x["sessions"]):
            w.writerow([d["dst"], d["fqdn"], d["proto"], d["port"], d["app"], d["sessions"], d["bytes"],
                        len(d["users"]), len(d["sources"]),
                        segment_key({"port": d["port"], "fqdn": d["fqdn"], "app": d["app"], "dst": d["dst"]}), "", ""])

    zpa_stub = {"app_segments": []}
    for name, s in sorted(segments.items(), key=lambda kv: -kv[1]["sessions"]):
        zpa_stub["app_segments"].append({
            "name": f"seg-{name.replace(':', '-').replace('/', '_')}",
            "domain_names": sorted(s["hosts"]),
            "tcp_port_ranges": sorted(s["ports"].get("tcp", [])),
            "udp_port_ranges": sorted(s["ports"].get("udp", [])),
            "segment_group": "TBD",
            "server_groups": ["TBD"],
            "evidence": {"sessions": s["sessions"], "distinct_users": len(s["users"])},
        })
    (args.out / "zpa-app-segments.yaml").write_text(yaml.safe_dump(zpa_stub, sort_keys=False), encoding="utf-8")

    (args.out / "co-access.json").write_text(json.dumps(
        [{"sources": n, "host_a": a, "host_b": b} for n, a, b in co_access], indent=2), encoding="utf-8")

    # ---- summary
    print(f"rows from VPN pool: {len(rows)}   destinations: {len(dest)} (dropped {dropped} below --min-users {args.min_users})")
    print(f"\nproposed segments ({len(segments)}):")
    for name, s in sorted(segments.items(), key=lambda kv: -kv[1]['sessions'])[:25]:
        ports = ",".join(f"{p}/{','.join(map(str, sorted(ps)))}" for p, ps in s["ports"].items())
        print(f"  {name:40} hosts={len(s['hosts']):3}  users={len(s['users']):4}  sessions={s['sessions']:6}  {ports}")
    if co_access:
        print("\nco-accessed host pairs (same client, both hosts) — check for multi-host apps:")
        for n, a, b in co_access[:10]:
            print(f"  {n:4} clients  {a}  <->  {b}")
    print(f"\nwrote {args.out}/destinations.csv, zpa-app-segments.yaml, co-access.json")


if __name__ == "__main__":
    main()
