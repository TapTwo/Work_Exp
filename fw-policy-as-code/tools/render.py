#!/usr/bin/env python3
"""
Render YAML intent to PAN-OS `set` commands for Panorama.

Output is deterministic and ordered, so `git diff` on the rendered file is the change preview
a reviewer reads, and the same file can be loaded on a lab Panorama for commit validation.

    python tools/render.py > build/panorama.set
    python tools/render.py --device-group dc-east

Rendering to `set` commands rather than XML keeps the review diff human-readable. A production
version of this would emit XML API calls (see ../panos-automation/panos_api.py) or drive
pan-os-python; the data model is the same either way.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def q(s: str) -> str:
    return f'"{s}"' if " " in s else s


def members(xs: list[str]) -> str:
    return "[ " + " ".join(q(x) for x in xs) + " ]" if len(xs) > 1 else q(xs[0])


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def render_objects(prefix: str, data: dict, out: list[str]) -> None:
    for a in data.get("addresses", []):
        kind = a.get("type", "ip-netmask")
        out.append(f"set {prefix} address {a['name']} {kind} {a['value']}")
        if a.get("description"):
            out.append(f"set {prefix} address {a['name']} description {q(a['description'])}")
        for t in a.get("tags", []):
            out.append(f"set {prefix} address {a['name']} tag {t}")
    for g in data.get("address_groups", []):
        out.append(f"set {prefix} address-group {g['name']} static {members(g['members'])}")
        for t in g.get("tags", []):
            out.append(f"set {prefix} address-group {g['name']} tag {t}")
    for s in data.get("services", []):
        out.append(f"set {prefix} service {s['name']} protocol {s['protocol']} port {s['port']}")
        if s.get("description"):
            out.append(f"set {prefix} service {s['name']} description {q(s['description'])}")


def render_rules(prefix: str, rulebase: str, data: dict, out: list[str]) -> None:
    base = f"set {prefix} {rulebase} security rules"
    for r in data.get("rules", []):
        n = r["name"]
        out.append(f"{base} {n} from {members(r['from'])}")
        out.append(f"{base} {n} to {members(r['to'])}")
        out.append(f"{base} {n} source {members(r['source'])}")
        out.append(f"{base} {n} destination {members(r['destination'])}")
        out.append(f"{base} {n} application {members(r['application'])}")
        out.append(f"{base} {n} service {members(r['service'])}")
        out.append(f"{base} {n} action {r['action']}")
        if r.get("profile_group"):
            out.append(f"{base} {n} profile-setting group {r['profile_group']}")
        out.append(f"{base} {n} log-end {'yes' if r.get('log_end') else 'no'}")
        if r.get("log_start"):
            out.append(f"{base} {n} log-start yes")
        if r.get("disabled"):
            out.append(f"{base} {n} disabled yes")
        for t in r.get("tags", []):
            out.append(f"{base} {n} tag {t}")
        desc = r["description"]
        if r.get("expires"):
            desc += f" [expires {r['expires']}]"
        out.append(f"{base} {n} description {q(desc)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-group", help="render only this device group (shared objects always included)")
    args = ap.parse_args()

    out: list[str] = ["# rendered by tools/render.py — do not edit by hand"]

    # shared objects, in a fixed file order
    for p in sorted((ROOT / "shared" / "objects").glob("*.yaml")):
        render_objects("shared", load(p), out)

    # shared tags used anywhere (so references resolve)
    tags: set[str] = set()
    for p in sorted(ROOT.glob("**/*.yaml")):
        d = load(p)
        for coll in ("addresses", "address_groups", "rules"):
            for item in d.get(coll, []):
                tags.update(item.get("tags", []))
    for t in sorted(tags):
        out.append(f"set shared tag {t}")

    for dg_dir in sorted((ROOT / "device-groups").iterdir()):
        if not dg_dir.is_dir() or (args.device_group and dg_dir.name != args.device_group):
            continue
        dg = dg_dir.name
        prefix = f"device-group {dg}"
        for p in sorted((dg_dir / "objects").glob("*.yaml")) if (dg_dir / "objects").exists() else []:
            render_objects(prefix, load(p), out)
        if (dg_dir / "pre-rules.yaml").exists():
            render_rules(prefix, "pre-rulebase", load(dg_dir / "pre-rules.yaml"), out)
        if (dg_dir / "post-rules.yaml").exists():
            render_rules(prefix, "post-rulebase", load(dg_dir / "post-rules.yaml"), out)

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
