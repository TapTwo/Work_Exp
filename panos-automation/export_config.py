#!/usr/bin/env python3
"""
Export the running configuration from Panorama (or a firewall) to a timestamped file
and record its SHA-256. This is the "pre-change capture" / "post-change capture" step:
the hash goes on the change record as evidence.

    python export_config.py --out exports/ --label CHG0012345-pre
"""
import argparse
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from panos_api import PanosClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="exports", help="output directory")
    ap.add_argument("--label", default="", help="tag for the filename, e.g. CHG0012345-pre")
    args = ap.parse_args()

    pano = PanosClient.from_env()
    xml = pano.export_running_config()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    host = os.environ["PANORAMA_HOST"].split(".")[0]
    name = f"{host}_{args.label + '_' if args.label else ''}{ts}.running.xml"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(xml, encoding="utf-8")

    digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    (out / (name + ".sha256")).write_text(f"{digest}  {name}\n")
    print(f"wrote {path}\nsha256 {digest}")


if __name__ == "__main__":
    main()
