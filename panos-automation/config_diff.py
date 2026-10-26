#!/usr/bin/env python3
"""
Semantic diff of two PAN-OS / Panorama config exports — the core of drift detection.

Why not plain `diff`? Panorama exports reorder unordered collections, carry volatile
attributes (uuid, timestamps, admin names) and wrap everything in whitespace, so a text
diff is pure noise. This tool:

  1. parses both XML files,
  2. drops volatile attributes/nodes,
  3. flattens the tree to  xpath -> value  leaves (keeping member/rule ORDER where it matters),
  4. reports added / removed / changed leaves grouped by the config scope they live in
     (shared, device-group X, template Y) so one Shared change shows once, not per firewall.

    python config_diff.py intended.xml running.xml
    python config_diff.py intended.xml running.xml --json > drift.json
    python config_diff.py intended.xml running.xml --scope "device-group/dc-east"

Exit code 0 = no drift, 1 = drift found (so CI/pipelines can gate on it).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

# Attributes that change on every save and never mean "drift".
VOLATILE_ATTRS = {"uuid", "time", "dirtyId", "admin", "loc", "overrides"}
# Subtrees we ignore entirely (runtime / Panorama bookkeeping).
IGNORED_PATHS = (
    "/config/mgt-config/users",          # admin passwords / last-login
    "/config/shared/content-preview",
    "/config/devices/entry/deviceconfig/system/update-schedule",
    "/config/panorama/log-settings",     # noisy, rarely intent
)
# Paths where child ORDER is part of the meaning (rule order = policy).
ORDERED_PARENTS = re.compile(r"/(pre-rulebase|post-rulebase|rulebase)/[^/]+/rules$")


def flatten(root: ET.Element) -> dict[str, str]:
    """Return {xpath: value}. 'entry' nodes are keyed by name; ordered lists carry an index."""
    leaves: dict[str, str] = {}

    def walk(el: ET.Element, path: str) -> None:
        if any(path.startswith(p) for p in IGNORED_PATHS):
            return
        ordered = bool(ORDERED_PARENTS.search(path))
        for i, child in enumerate(el):
            tag = child.tag
            if tag == "entry":
                seg = f"entry[@name='{child.get('name')}']"
                if ordered:
                    seg += f"[{i}]"
            else:
                seg = tag
            cpath = f"{path}/{seg}"
            for a, v in child.attrib.items():
                if a not in VOLATILE_ATTRS and a != "name":
                    leaves[f"{cpath}/@{a}"] = v
            if len(child) == 0:
                text = (child.text or "").strip()
                if tag == "member":
                    # unordered set: key by value so reordering isn't drift
                    leaves[f"{cpath}[{text}]"] = text
                else:
                    leaves[cpath] = text
            else:
                walk(child, cpath)

    walk(root, "/" + root.tag)
    return leaves


SCOPE_RE = re.compile(
    r"^/config/(?:"
    r"(?P<shared>shared)|"
    r"devices/entry\[@name='[^']+'\]/(?P<kind>device-group|template|template-stack|vsys)/entry\[@name='(?P<name>[^']+)'\]"
    r")"
)


def scope_of(path: str) -> str:
    m = SCOPE_RE.match(path)
    if not m:
        return "other"
    if m.group("shared"):
        return "shared"
    return f"{m.group('kind')}/{m.group('name')}"


def diff(intended: dict[str, str], running: dict[str, str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for p in sorted(set(intended) | set(running)):
        a, b = intended.get(p), running.get(p)
        if a == b:
            continue
        if a is None:
            kind = "added_on_device"       # exists in running, not in intent -> unauthorized unless excepted
        elif b is None:
            kind = "missing_on_device"     # in intent, not deployed -> push pending or deleted out of band
        else:
            kind = "changed"
        out[scope_of(p)].append({"type": kind, "path": p, "intended": a, "running": b})
    return dict(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("intended", help="rendered/approved config XML (what Git says)")
    ap.add_argument("running", help="exported running config XML (what the device says)")
    ap.add_argument("--scope", help="only report this scope, e.g. shared or device-group/dc-east")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    a = flatten(ET.parse(args.intended).getroot())
    b = flatten(ET.parse(args.running).getroot())
    result = diff(a, b)
    if args.scope:
        result = {k: v for k, v in result.items() if k == args.scope}

    total = sum(len(v) for v in result.values())
    if args.json:
        print(json.dumps({"drift_count": total, "scopes": result}, indent=2))
    else:
        if not total:
            print("no drift")
        for scope, items in result.items():
            print(f"\n== {scope}  ({len(items)} differences)")
            for it in items:
                print(f"  [{it['type']}] {it['path']}")
                if it["type"] == "changed":
                    print(f"      intended: {it['intended']!r}\n      running:  {it['running']!r}")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
