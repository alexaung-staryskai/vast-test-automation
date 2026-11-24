"""Onboarded-tenant records, kept in tenants.yaml so they survive restarts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import ROOT, TemplateError, render, render_section

HEADER = "# Onboarded tenants - written by VAST Test Bench. Safe to edit; one entry per tenant.\n"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class TenantStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def from_cfg(cls, cfg: dict) -> "TenantStore":
        p = Path(str(cfg["app"].get("tenants_file", "tenants.yaml")))
        return cls(p if p.is_absolute() else Path(cfg.get("_dir", ROOT)) / p)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = yaml.safe_load(self.path.read_text()) or {}
        return [t for t in data.get("tenants") or [] if isinstance(t, dict)]

    def save(self, tenants: list[dict]) -> None:
        self.path.write_text(HEADER + yaml.safe_dump({"tenants": tenants}, sort_keys=False, width=200,
                                                     allow_unicode=True))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def key(rec: dict) -> str:
        return f"{rec.get('name')}@{rec.get('vms')}"

    def upsert(self, rec: dict) -> dict:
        tenants = self.load()
        for i, t in enumerate(tenants):
            if self.key(t) == self.key(rec):
                rec["onboarded_at"] = t.get("onboarded_at", rec.get("onboarded_at"))
                if "limits" in t and "limits" not in rec:  # QoS/quota switched off: keep the saved values
                    rec["limits"] = t["limits"]
                tenants[i] = rec
                break
        else:
            tenants.append(rec)
        self.save(tenants)
        return rec

    def update(self, key: str, **fields: Any) -> dict | None:
        tenants = self.load()
        for t in tenants:
            if self.key(t) == key:
                t.update(fields)
                self.save(tenants)
                return t
        return None

    def remove(self, key: str) -> bool:
        tenants = self.load()
        keep = [t for t in tenants if self.key(t) != key]
        if len(keep) == len(tenants):
            return False
        self.save(keep)
        SecretVault.for_store(self).forget(key)
        return True


class SecretVault:
    """Per-tenant secrets (admin password, S3 secret key) in a hidden file next to tenants.yaml.

    Kept apart from tenants.yaml so that file can be shared; mode 600, and hidden files are
    left out of the site zip. Survives 'Reset state' and later onboarding runs."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def for_store(cls, store: TenantStore) -> "SecretVault":
        return cls(store.path.parent / ".tenant-secrets.json")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        self.path.write_text(json.dumps(data, indent=1))

    def get(self, key: str) -> dict:
        return self._load().get(key) or {}

    def put(self, key: str, **secrets: Any) -> None:
        secrets = {k: v for k, v in secrets.items() if v}
        if not secrets:
            return
        data = self._load()
        if {**data.get(key, {}), **secrets} != data.get(key):
            data[key] = {**data.get(key, {}), **secrets}
            self._save(data)

    def forget(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)


def _get(outputs: dict, sid: str, key: str = "id") -> Any:
    v = outputs.get(sid)
    return v.get(key) if isinstance(v, dict) else None


def build_record(cfg: dict, prov, eff_vars: dict, store_secrets: bool) -> dict | None:
    """Summarise the current onboarding state (provisioner outputs + vars) as one tenant record."""
    o = prov.outputs
    if not isinstance(o.get("tenant"), dict):
        return None
    v = eff_vars
    vms_url = str(cfg["vms"].get("url", "")).rstrip("/")
    name = o["tenant"].get("name") or v.get("tenant_name")
    ips = prov.vip_ips() if "vippool" in o else []
    steps = [s for s in cfg["provisioning"]["steps"] if s.get("type") != "vip_check"]
    statuses = {s["id"]: prov.results.get(s["id"], {}).get("status", "pending") for s in steps}
    complete = all(st in ("passed", "reused") for st in statuses.values())

    ctx = prov.context(cfg, v)
    objects = []
    for s in steps:
        if s["id"] in o and s.get("verify"):
            try:
                objects.append({"step": s["id"], "name": s.get("name", s["id"]),
                                "endpoint": render(s["verify"]["endpoint"], ctx)})
            except TemplateError:
                pass

    view = o.get("view") if isinstance(o.get("view"), dict) else {}
    path = view.get("path") or v.get("view_path")
    mount_cmd = None
    if ips and cfg.get("mount"):
        try:
            mctx = {**ctx, "client": {"vip": ips[0], "name": "<client>", "host": "<client>"},
                    "tenant_vip": {"start": ips[0], "end": ips[-1]}}   # for multipath mounts (remoteports=start-end)
            if isinstance(o.get("view"), dict) and o["view"].get("path"):   # mount the view that was actually created
                mctx["vars"] = {**mctx.get("vars", {}), "view_path": o["view"]["path"]}
            mount_cmd = render_section(cfg["mount"], mctx, "mount").get("command")
        except TemplateError:
            pass
    keys = o.get("s3_keys") if isinstance(o.get("s3_keys"), dict) else {}

    rec: dict[str, Any] = {
        "name": name,
        "status": "active" if complete else "partial",
        "vms": vms_url,
        "onboarded_at": _now(),
        "updated_at": _now(),
        "tenant_id": _get(o, "tenant"),
        "admin": {
            "username": _get(o, "tenant_admin", "username") or v.get("tenant_admin_username"),
            "id": _get(o, "tenant_admin"),
            "role": _get(o, "admin_role", "name") or v.get("tenant_admin_role"),
            "login_url": f"{vms_url}/#/login/{name}",
        },
        "vip_pool": {
            "name": _get(o, "vippool", "name") or v.get("vip_pool_name"),
            "id": _get(o, "vippool"),
            "range": f"{ips[0]} - {ips[-1]}" if ips else None,
            "ip_count": len(ips) or cfg.get("vip_pool", {}).get("ip_count"),
            # what the pool was created with (falls back to the current settings)
            "subnet_cidr": _get(o, "vippool", "subnet_cidr") or cfg.get("vip_pool", {}).get("subnet_cidr"),
            "vlan": _get(o, "vippool", "vlan") or cfg.get("vip_pool", {}).get("vlan", 0),
            "gateway": _get(o, "vippool", "gw_ip") or cfg.get("vip_pool", {}).get("gateway") or None,
            "ips": ips,
        },
        "view": {
            "name": view.get("name") or v.get("view_name"),
            "id": view.get("id"),
            "path": path,
            "protocols": view.get("protocols") or v.get("view_protocols"),
            "bucket": view.get("bucket"),
        },
        "view_policy": {"name": _get(o, "view_policy", "name") or v.get("view_policy_name"),
                        "id": _get(o, "view_policy"), "flavor": _get(o, "view_policy", "flavor")},
        "qos": {"name": _get(o, "qos", "name") or v.get("qos_name"), "id": _get(o, "qos"),
                "read": v.get("qos_read_limit"), "write": v.get("qos_write_limit")},
        "quota": {"name": _get(o, "quota", "name") or v.get("quota_name"), "id": _get(o, "quota"),
                  "hard_limit": v.get("quota_hard_limit"), "soft_limit": v.get("quota_soft_limit")},
        "s3": {"user": _get(o, "s3_user", "name") or v.get("s3_user_name"), "user_id": _get(o, "s3_user"),
               "access_key": keys.get("access_key"), "endpoint": f"http://{ips[0]}" if ips else None,
               "bucket": view.get("bucket")},
        "nfs": {"export": f"{ips[0]}:{path}" if ips and path else None, "mount_example": mount_cmd},
        "steps": statuses,
        "objects": objects,
    }
    if store_secrets:
        rec["admin"]["password"] = v.get("tenant_admin_password")
        rec["s3"]["secret_key"] = keys.get("secret_key")
    return rec
