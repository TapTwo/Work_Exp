#!/usr/bin/env python3
"""
Deployment step of a firewall change pipeline, Panorama edition.

    lock -> validate -> partial commit (service account only) -> push device groups in waves
         -> poll every job -> per-device results -> unlock

Stops at the first failed wave so the rollback scope is "completed waves only".
Idempotent re-run: pass --resume-job <id> to pick up a push that was in flight
when the previous run died, instead of re-applying blind.

    python deploy.py --chg CHG0012345 --admin svc-pipeline --waves "dg-canary" "dg-east,dg-west" "dg-apac"
    python deploy.py --chg CHG0012345 --resume-job 1842

Everything this script prints is meant to be captured as evidence on the change record.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from panos_api import PanosApiError, PanosClient


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def push_wave(pano: PanosClient, dgs: list[str], chg: str) -> dict:
    """Push each device group in the wave; return per-DG, per-device results."""
    results = {}
    for dg in dgs:
        job = pano.commit_all_device_group(dg, description=chg)
        log(f"push {dg}: job {job}")
        try:
            jr = pano.wait_for_job(job)
            results[dg] = {"job": job, "result": "OK", "devices": jr.devices or {}}
        except PanosApiError as e:
            jr = pano.job_status(job)
            results[dg] = {"job": job, "result": "FAIL", "error": str(e), "devices": jr.devices or {}}
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chg", required=True, help="change number, used as lock comment and commit description")
    ap.add_argument("--admin", default="svc-pipeline", help="service account whose changes get committed")
    ap.add_argument("--waves", nargs="*", default=[], help='each wave is a comma-separated list of device groups')
    ap.add_argument("--resume-job", help="resume by polling an existing job id instead of committing")
    ap.add_argument("--no-unlock", action="store_true", help="keep the config lock after a failure for investigation")
    args = ap.parse_args()

    pano = PanosClient.from_env()
    evidence = {"chg": args.chg, "started": datetime.now(timezone.utc).isoformat(), "waves": []}

    if args.resume_job:
        log(f"resuming: polling job {args.resume_job}")
        jr = pano.wait_for_job(args.resume_job)
        print(json.dumps({"job": args.resume_job, "devices": jr.devices}, indent=2))
        return

    try:
        log(f"taking config lock ({args.chg})")
        pano.config_lock(comment=args.chg)

        log("validating candidate")
        pano.wait_for_job(pano.validate())

        log(f"partial commit as {args.admin}")
        jr = pano.wait_for_job(pano.commit_partial(admin=args.admin, description=args.chg))
        evidence["panorama_commit_job"] = jr.job_id

        for i, wave in enumerate(args.waves, 1):
            dgs = [d.strip() for d in wave.split(",") if d.strip()]
            log(f"wave {i}: {dgs}")
            res = push_wave(pano, dgs, args.chg)
            evidence["waves"].append({"wave": i, "device_groups": res})
            failed = {dg: r for dg, r in res.items() if r["result"] != "OK"}
            if failed:
                log(f"wave {i} FAILED on {list(failed)} — stopping. Rollback scope: waves 1..{i}")
                evidence["status"] = "FAILED"
                break
        else:
            evidence["status"] = "OK"
    finally:
        if evidence.get("status") == "OK" or not args.no_unlock:
            pano.config_unlock()
            log("config lock released")

    evidence["finished"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(evidence, indent=2))
    sys.exit(0 if evidence.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
