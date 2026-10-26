"""
Minimal PAN-OS / Panorama XML API client.

Deliberately thin: one class, one method per API request type, no magic.
The point is to make every call the pipeline needs readable in one file.

Usage:
    from panos_api import PanosClient
    pano = PanosClient.from_env()          # PANORAMA_HOST / PANORAMA_API_KEY
    cfg  = pano.export_running_config()
    job  = pano.commit_partial(admin="svc-pipeline", description="CHG0012345")
    pano.wait_for_job(job)

Reference: https://docs.paloaltonetworks.com/pan-os/11-0/pan-os-panorama-api
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import requests


class PanosApiError(RuntimeError):
    pass


@dataclass
class JobResult:
    job_id: str
    status: str          # ACT | FIN | PEND
    result: str          # OK | FAIL | PEND
    details: str = ""
    devices: dict | None = None   # per-device result for commit-all jobs


class PanosClient:
    def __init__(self, host: str, api_key: str, verify_tls: bool = True, timeout: int = 60):
        self.base = f"https://{host}/api/"
        self.key = api_key
        self.verify = verify_tls
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_env(cls) -> "PanosClient":
        host = os.environ["PANORAMA_HOST"]
        key = os.environ.get("PANORAMA_API_KEY")
        verify = os.environ.get("PANOS_VERIFY_TLS", "true").lower() != "false"
        if not key:
            raise PanosApiError("PANORAMA_API_KEY not set (use keygen() once and store it in a secret store)")
        return cls(host, key, verify_tls=verify)

    @staticmethod
    def keygen(host: str, username: str, password: str, verify_tls: bool = True) -> str:
        """One-time: generate an API key for a service account. Store it in Key Vault, not in code."""
        r = requests.post(
            f"https://{host}/api/",
            data={"type": "keygen", "user": username, "password": password},
            verify=verify_tls,
            timeout=60,
        )
        root = _parse(r)
        return root.findtext(".//key")

    # ------------------------------------------------------------------ core
    def _call(self, params: dict, data: dict | None = None) -> ET.Element:
        params = {**params, "key": self.key}
        if data:
            r = self.session.post(self.base, params=params, data=data, verify=self.verify, timeout=self.timeout)
        else:
            r = self.session.get(self.base, params=params, verify=self.verify, timeout=self.timeout)
        return _parse(r)

    def op(self, cmd_xml: str) -> ET.Element:
        """Operational command, XML form. Example: '<show><system><info></info></system></show>'"""
        return self._call({"type": "op", "cmd": cmd_xml})

    # ------------------------------------------------------------------ config
    def config_get(self, xpath: str) -> ET.Element:
        """Candidate config at xpath."""
        return self._call({"type": "config", "action": "get", "xpath": xpath})

    def config_show(self, xpath: str) -> ET.Element:
        """Running (committed) config at xpath."""
        return self._call({"type": "config", "action": "show", "xpath": xpath})

    def config_set(self, xpath: str, element_xml: str) -> ET.Element:
        return self._call({"type": "config", "action": "set", "xpath": xpath, "element": element_xml})

    def config_delete(self, xpath: str) -> ET.Element:
        return self._call({"type": "config", "action": "delete", "xpath": xpath})

    def export_running_config(self) -> str:
        """Full running config as an XML string (type=export&category=configuration)."""
        r = self.session.get(
            self.base,
            params={"type": "export", "category": "configuration", "key": self.key},
            verify=self.verify,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.text

    # ------------------------------------------------------------------ locks
    def config_lock(self, comment: str, location: str = "shared") -> ET.Element:
        return self.op(f"<request><config-lock><add><comment>{_esc(comment)}</comment></add></config-lock></request>")

    def config_unlock(self) -> ET.Element:
        return self.op("<request><config-lock><remove></remove></config-lock></request>")

    # ------------------------------------------------------------------ commits
    def validate(self) -> str:
        """Validate the full candidate config. Returns a job id."""
        root = self._call({"type": "op", "cmd": "<validate><full></full></validate>"})
        return _job_id(root)

    def commit_partial(self, admin: str, description: str = "") -> str:
        """
        Commit only the changes made by `admin` (the pipeline service account),
        leaving other admins' uncommitted work alone.
        """
        cmd = (
            "<commit><partial>"
            f"<admin><member>{_esc(admin)}</member></admin>"
            + (f"<description>{_esc(description)}</description>" if description else "")
            + "</partial></commit>"
        )
        root = self._call({"type": "commit", "action": "partial", "cmd": cmd})
        return _job_id(root)

    def commit_all_device_group(self, device_group: str, description: str = "") -> str:
        """Push policy to a device group (Panorama commit-all)."""
        cmd = (
            "<commit-all><shared-policy>"
            f"<device-group><entry name=\"{_esc(device_group)}\"/></device-group>"
            + (f"<description>{_esc(description)}</description>" if description else "")
            + "</shared-policy></commit-all>"
        )
        root = self._call({"type": "commit", "action": "all", "cmd": cmd})
        return _job_id(root)

    def commit_all_template_stack(self, stack: str, description: str = "") -> str:
        cmd = (
            "<commit-all><template-stack>"
            f"<name>{_esc(stack)}</name>"
            + (f"<description>{_esc(description)}</description>" if description else "")
            + "</template-stack></commit-all>"
        )
        root = self._call({"type": "commit", "action": "all", "cmd": cmd})
        return _job_id(root)

    # ------------------------------------------------------------------ jobs
    def job_status(self, job_id: str) -> JobResult:
        root = self.op(f"<show><jobs><id>{_esc(job_id)}</id></jobs></show>")
        job = root.find(".//job")
        if job is None:
            raise PanosApiError(f"job {job_id} not found")
        devices = None
        if job.find("devices") is not None:
            devices = {
                d.findtext("devicename") or d.findtext("serial-no"): {
                    "result": d.findtext("result"),
                    "status": d.findtext("status"),
                    "details": " ".join(x.text or "" for x in d.findall(".//details/msg")),
                }
                for d in job.findall("devices/entry")
            }
        return JobResult(
            job_id=job_id,
            status=job.findtext("status") or "",
            result=job.findtext("result") or "",
            details=" ".join(x.text or "" for x in job.findall(".//details/line")),
            devices=devices,
        )

    def wait_for_job(self, job_id: str, poll: int = 5, timeout: int = 1800) -> JobResult:
        """Poll until FIN. Raises on FAIL so callers can branch to rollback."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            jr = self.job_status(job_id)
            if jr.status == "FIN":
                if jr.result != "OK":
                    raise PanosApiError(f"job {job_id} finished {jr.result}: {jr.details}")
                return jr
            time.sleep(poll)
        raise PanosApiError(f"job {job_id} did not finish within {timeout}s")

    # ------------------------------------------------------------------ tests
    def test_security_policy_match(
        self,
        from_zone: str,
        to_zone: str,
        source: str,
        destination: str,
        protocol: int,
        port: int,
        application: Optional[str] = None,
        vsys: str = "vsys1",
    ) -> dict:
        """
        Run on a firewall (not Panorama). Returns {"rule": name|None, "action": allow|deny|None}.
        """
        cmd = (
            "<test><security-policy-match>"
            f"<from>{_esc(from_zone)}</from><to>{_esc(to_zone)}</to>"
            f"<source>{_esc(source)}</source><destination>{_esc(destination)}</destination>"
            f"<protocol>{int(protocol)}</protocol><destination-port>{int(port)}</destination-port>"
            + (f"<application>{_esc(application)}</application>" if application else "")
            + "</security-policy-match></test>"
        )
        root = self._call({"type": "op", "cmd": cmd, "vsys": vsys})
        rule = root.find(".//rules/entry")
        if rule is None:
            return {"rule": None, "action": None}
        return {"rule": rule.get("name"), "action": rule.findtext("action")}


# ---------------------------------------------------------------------- helpers
def _parse(r: requests.Response) -> ET.Element:
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        raise PanosApiError(f"non-XML response (HTTP {r.status_code}): {r.text[:200]}") from e
    if root.get("status") != "success":
        msg = root.findtext(".//msg") or root.findtext(".//line") or r.text[:300]
        raise PanosApiError(f"API error {root.get('code', '')}: {msg}")
    return root


def _job_id(root: ET.Element) -> str:
    jid = root.findtext(".//job")
    if not jid:
        raise PanosApiError("no job id in response")
    return jid


def _esc(s: str) -> str:
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
