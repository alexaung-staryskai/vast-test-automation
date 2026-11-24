"""FastAPI app: REST endpoints for the UI, WebSocket terminals, static frontend."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, config_store, packages, results, sites
from .tenants import SecretVault, TenantStore, build_record
from .config import ROOT, NoSiteError, TemplateError, _expand_env, coerce_var, config_path, load_config, render
from .ssh import Session, SessionManager
from .vms import Provisioner, VMSClient


class State:
    def __init__(self) -> None:
        self.cfg: dict = {}
        self.warnings: list[str] = []
        self.vars: dict = {}
        self.sessions = SessionManager()
        self.provisioner: Provisioner | None = None
        self.tenants: TenantStore | None = None
        self.site: Path | None = None
        self.vip_overrides: dict = {}  # VIP count / CIDR / VLAN typed in Tab 1 for this session
        start = os.environ.get("VAST_TEST_SITE")
        if start:
            self.open_site(sites.resolve(start))

    def open_site(self, path: Path) -> None:
        prev = (config.CURRENT_CONFIG, self.site)
        config.CURRENT_CONFIG = path / "config.yaml"
        self.site = path
        self.vars = {}
        self.vip_overrides = {}
        try:
            self.load()
        except Exception:
            config.CURRENT_CONFIG, self.site = prev
            raise
        sites.remember(path)

    def close_site(self) -> None:
        config.CURRENT_CONFIG = None
        self.site = None
        self.cfg, self.vars = {}, {}

    def load(self, keep_vars: bool = False) -> None:
        old_vars = self.vars
        self.cfg, self.warnings = load_config()
        self.vars = copy.deepcopy(self.cfg["vars"])
        if keep_vars:  # keep edits made in Tab 1 when only other settings changed
            self.vars.update({k: v for k, v in old_vars.items() if k in self.vars})
        results.set_results_dir(self.cfg["app"].get("results_dir", "results"), Path(self.cfg["_dir"]))
        self.provisioner = Provisioner(results.RESULTS_DIR / ".provision_state.json")
        self.tenants = TenantStore.from_cfg(self.cfg)
        self.cfg["vip_pool"].update(self.vip_overrides)

    def sync_tenant(self) -> dict | None:
        """Write the current onboarding state to tenants.yaml."""
        eff = self.provisioner.effective_vars(self.cfg, self.vars)
        rec = build_record(self.cfg, self.provisioner, eff, bool(self.cfg["app"].get("store_secrets", False)))
        if not rec:
            return None
        # secrets go to the hidden per-site vault (mode 600), never to tenants.yaml unless store_secrets
        keys = self.provisioner.outputs.get("s3_keys")
        keys = keys if isinstance(keys, dict) else {}
        SecretVault.for_store(self.tenants).put(
            TenantStore.key(rec), admin_password=eff.get("tenant_admin_password") if "tenant_admin" in self.provisioner.outputs else None,
            s3_access_key=keys.get("access_key"), s3_secret_key=keys.get("secret_key"))
        return self.tenants.upsert(rec)


state = State()
app = FastAPI(title="VAST Testing Automation")


async def _idle_console_reaper() -> None:
    """Consoles stay open when their page is closed or reloaded. Close the ones no page has shown for
    ssh.idle_close_minutes (default 60, 0 = never) that are not running anything."""
    import asyncio
    while True:
        await asyncio.sleep(60)
        try:
            if state.site is None:
                continue
            minutes = float((state.cfg.get("ssh") or {}).get("idle_close_minutes", 60) or 0)
            for label in await state.sessions.reap_idle(minutes * 60):
                print(f"Closed idle console {label} (no page attached for {minutes:g} min)", flush=True)
        except Exception as e:  # noqa: BLE001 - never let the loop die
            print(f"idle console reaper: {type(e).__name__}: {e}", flush=True)


@app.on_event("startup")
async def _start_background_tasks() -> None:
    import asyncio
    asyncio.create_task(_idle_console_reaper())
STATIC = ROOT / "static"


@app.middleware("http")
async def require_site(request, call_next):
    """Everything except the test-site endpoints needs an open test site."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/sites") and state.site is None:
        return JSONResponse({"detail": "No test site is open", "code": "no_site"}, status_code=409)
    return await call_next(request)


@app.middleware("http")
async def no_cache_ui(request, call_next):
    """Always serve the current UI files (no stale app.js after an update)."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _step(step_id: str) -> dict:
    for s in state.cfg["provisioning"]["steps"]:
        if s["id"] == step_id:
            return s
    raise HTTPException(404, f"Unknown step {step_id}")


def _group(group_id: str) -> dict:
    for g in state.cfg["command_groups"]:
        if g["id"] == group_id:
            return g
    raise HTTPException(404, f"Unknown command group {group_id}")


def _masked_vars(v: dict) -> dict:
    return {k: ("********" if "password" in k.lower() and val else val) for k, val in v.items()}


# ---------------------------------------------------------------- test sites

def _site_info() -> dict | None:
    return sites.info(state.site) if state.site else None


@app.get("/api/sites")
def list_sites() -> dict:
    return {"current": _site_info(), "sites": sites.list_sites(), "sites_dir": str(sites.SITES_DIR.resolve()),
            "last": sites.last(), "templates": sites.list_templates()}


class SiteCreateIn(BaseModel):
    name: str
    description: str = ""
    template: str | None = None   # path of an existing site to copy the configuration from


async def _switch(path: Path) -> dict:
    for sid in list(state.sessions.sessions):  # consoles belong to the previous site's clients
        await state.sessions.remove(sid)
    try:
        state.open_site(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not open the test site: {e}")
    return {"current": _site_info()}


@app.post("/api/sites")
async def create_site(body: SiteCreateIn) -> dict:
    try:
        path = sites.create(body.name, body.description, body.template)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _switch(path)


class SiteOpenIn(BaseModel):
    path: str


@app.post("/api/sites/open")
async def open_site(body: SiteOpenIn) -> dict:
    try:
        path = sites.resolve(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await _switch(path)


class SiteMetaIn(BaseModel):
    name: str
    description: str = ""


@app.put("/api/sites/meta")
def update_site_meta(body: SiteMetaIn) -> dict:
    if not state.site:
        raise HTTPException(409, "No test site is open")
    try:
        sites.update_meta(state.site, body.name, body.description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"current": _site_info()}


@app.delete("/api/sites")
async def delete_site(path: str) -> dict:
    """Move a test site folder to sites/.trash/ (can be restored by moving it back)."""
    if state.site and Path(path).resolve() == state.site.resolve():
        for sid in list(state.sessions.sessions):
            await state.sessions.remove(sid)
        state.close_site()
    try:
        dest = sites.trash(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "moved_to": str(dest)}


@app.post("/api/sites/close")
async def close_site() -> dict:
    prov = getattr(state, "provisioner", None)
    if state.site and prov is not None and prov.running_all:
        raise HTTPException(409, "Tenant onboarding is still running - wait for it to finish before closing the site")
    for sid in list(state.sessions.sessions):
        await state.sessions.remove(sid)
    state.close_site()
    return {"ok": True}


@app.get("/api/sites/export")
def export_site(include_results: bool = True) -> Response:
    if not state.site:
        raise HTTPException(409, "No test site is open")
    data = sites.export_zip(state.site, include_results=include_results)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{state.site.name}.zip"'})


# ---------------------------------------------------------------- config

@app.get("/api/config")
def get_config() -> dict:
    c = state.cfg
    eff = state.provisioner.effective_vars(c, state.vars)
    return {
        "path": c["_path"],
        "site": _site_info(),
        "warnings": state.warnings,
        "vms": {k: c["vms"].get(k) for k in ("url", "api_base", "auth_type", "verify_ssl")},
        "vars": state.vars,
        "resolved_vars": _masked_vars(eff),
        "generated_password": bool(state.provisioner.generated.get("tenant_admin_password")),
        "steps": [
            {"id": s["id"], "name": s.get("name", s["id"]), "type": s.get("type", "rest"),
             "method": s.get("method", "POST"), "endpoint": s.get("endpoint", ""),
             "has_verify": bool(s.get("verify")), "has_cleanup": bool(s.get("cleanup"))}
            for s in c["provisioning"]["steps"]
        ],
        "vip_pool": c.get("vip_pool", {}),
        "mount": c.get("mount", {}),
        "elbencho": c.get("elbencho", {}),
        "packages": [p["name"] for p in c["packages"].get("required", [])],
        "clients": [{"name": cl["name"], "host": cl["host"], "username": cl.get("username", c["ssh"].get("username")),
                     "port": int(cl.get("port", c["ssh"].get("port", 22))),
                     "password_saved": cl["name"] in _client_secrets()}
                    for cl in c["clients"]],
        "groups": [{"id": g["id"], "label": g.get("label", g["id"]), "results": bool(g.get("results")),
                    "section": g.get("section", "presets"),   # "packages" puts the button in Required packages
                    "confirm": g.get("confirm"),               # ask before running (e.g. deletes)
                    "commands": g.get("commands", []),
                    "delay": g.get("delay_between_commands_seconds",
                                   c["execution"].get("delay_between_commands_seconds", 0))}
                   for g in c["command_groups"]],
        "execution": c["execution"],
    }


@app.post("/api/config/reload")
def reload_config() -> dict:
    try:
        state.load()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not load config: {e}")
    return get_config()


@app.get("/api/config/raw")
def get_config_raw() -> dict:
    return config_store.read_raw(config_path())


class RawIn(BaseModel):
    text: str
    base_mtime: float | None = None   # mtime of config.yaml when the editor loaded it


class DataIn(BaseModel):
    data: dict
    base_mtime: float | None = None


def _not_stale(base_mtime: float | None) -> None:
    """The ⚙ Configuration editor saves its whole copy of the file. Refuse when config.yaml changed on disk
    after the editor loaded it (another tab, another save, a hand edit), instead of silently overwriting it."""
    if base_mtime is None:
        return
    p = config_path()
    if p.exists() and abs(p.stat().st_mtime - base_mtime) > 0.001:
        raise HTTPException(409, {"errors": ["config.yaml was changed after you opened it (another tab, another save "
                                             "or an edit outside the app). Nothing was saved. Click Revert to load "
                                             "the current file, then make your change again."], "code": "stale"})


def _after_save(res: dict) -> dict:
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})
    try:
        state.load()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, {"errors": [f"Saved, but could not load: {e}"]})
    return {"ok": True, "backup": res.get("backup"), "warnings": state.warnings}


@app.put("/api/config/raw")
def put_config_raw(body: RawIn) -> dict:
    _not_stale(body.base_mtime)
    return _after_save(config_store.save_text(config_path(), body.text))


@app.put("/api/config/data")
def put_config_data(body: DataIn) -> dict:
    _not_stale(body.base_mtime)
    return _after_save(config_store.save_data(config_path(), body.data))


@app.post("/api/config/validate")
def validate_config(body: RawIn) -> dict:
    import yaml
    try:
        data = yaml.safe_load(body.text) or {}
    except yaml.YAMLError as e:
        return {"ok": False, "errors": [f"YAML syntax error: {e}"]}
    errs = config_store.validate(data)
    return {"ok": not errs, "errors": errs}


class VarsIn(BaseModel):
    vars: dict


@app.put("/api/vars")
def put_vars(body: VarsIn) -> dict:
    for k, v in body.vars.items():
        state.vars[k] = coerce_var(v, state.cfg["vars"].get(k, ""))
    return {"vars": state.vars, "resolved_vars": _masked_vars(state.provisioner.effective_vars(state.cfg, state.vars))}


@app.post("/api/vars/save")
def save_vars() -> dict:
    """Write the current Tab 1 values to the test site's config.yaml as its defaults.
    A value that still equals an ${env:...} reference's value keeps the reference (no secrets written)."""
    data = config_store.read_raw(config_path())["data"]
    raw_vars = data.setdefault("vars", {})
    changed = []
    for k, v in state.vars.items():
        raw = raw_vars.get(k)
        if isinstance(raw, str) and "${env:" in raw and state.cfg["vars"].get(k) == v:
            continue
        if raw != v:
            raw_vars[k] = v
            changed.append(k)
    if not changed:
        return {"ok": True, "changed": [], "backup": None}
    res = config_store.save_data(config_path(), data)
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})
    state.load(keep_vars=True)
    return {"ok": True, "changed": changed, "backup": res.get("backup")}


# ---------------------------------------------------------------- step 1: VMS

VMS_FIELDS = ("url", "api_base", "auth_type", "api_token", "username", "password", "token_endpoint",
              "verify_ssl", "timeout_seconds", "health_endpoint")


@app.get("/api/vms/settings")
def vms_settings() -> dict:
    """Raw values from the config file (an ${env:...} reference stays a reference)."""
    raw = config_store.read_raw(config_path())["data"].get("vms") or {}
    return {k: raw.get(k) for k in VMS_FIELDS}


class VmsIn(BaseModel):
    vms: dict | None = None


def _clean_vms(d: dict) -> dict:
    out = {k: v for k, v in d.items() if k in VMS_FIELDS}
    if "verify_ssl" in out:
        out["verify_ssl"] = out["verify_ssl"] in (True, "true", "on", 1, "1")
    if "timeout_seconds" in out:
        try:
            out["timeout_seconds"] = float(out["timeout_seconds"])
            if out["timeout_seconds"].is_integer():
                out["timeout_seconds"] = int(out["timeout_seconds"])
        except (TypeError, ValueError):
            raise HTTPException(400, "timeout_seconds must be a number")
    if "url" in out:
        out["url"] = str(out["url"]).strip().rstrip("/")
        if not out["url"].startswith(("http://", "https://")):
            raise HTTPException(400, "URL must start with https:// (or http://)")
    return out


@app.put("/api/vms/settings")
def put_vms_settings(body: VmsIn) -> dict:
    data = config_store.read_raw(config_path())["data"]
    data.setdefault("vms", {}).update(_clean_vms(body.vms or {}))
    res = config_store.save_data(config_path(), data)
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})
    state.load(keep_vars=True)
    return {"ok": True, "backup": res.get("backup"), "warnings": state.warnings, "settings": vms_settings()}


@app.post("/api/vms/test")
async def vms_test(body: VmsIn | None = None) -> dict:
    cfg = state.cfg
    if body and body.vms:  # test values typed in the form before saving them
        missing: set = set()
        cfg = {**state.cfg, "vms": {**state.cfg["vms"], **_expand_env(_clean_vms(body.vms), missing)}}
    client = VMSClient(cfg)
    try:
        r = await client.request("GET", cfg["vms"].get("health_endpoint", "/clusters/"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": client.full_url("")}
    summary = r["body"]
    items = summary.get("results") if isinstance(summary, dict) and "results" in summary else summary
    if isinstance(items, list) and items and isinstance(items[0], dict):
        summary = {k: items[0].get(k) for k in ("name", "sw_version", "state", "guid") if k in items[0]}
    return {"ok": r["ok"], "status": r["status"], "url": r["url"], "summary": summary}


@app.post("/api/vip/check")
async def vip_check() -> dict:
    try:
        return await state.provisioner.vip_check(state.cfg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}")


VIP_DEFAULTS = {"ip_count": 5, "subnet_cidr": 16, "vlan": 0, "auto_select": True}


class VipSettingsIn(BaseModel):
    ip_count: str | int = 5
    subnet_cidr: str | int = 16
    vlan: str | int = 0
    auto_select: bool = True   # True: step 1 picks the first free block; False: use the range chosen on the IP map
    save: bool = False   # also write them to the site's config.yaml as the new defaults


def _int_field(v, name: str, lo: int, hi: int) -> int:
    try:
        n = int(str(v).strip().lstrip("/"))
    except ValueError:
        raise HTTPException(400, f"{name} must be a whole number")
    if not lo <= n <= hi:
        raise HTTPException(400, f"{name} must be between {lo} and {hi}")
    return n


@app.get("/api/vip/settings")
def get_vip_settings() -> dict:
    vp = state.cfg.get("vip_pool", {})
    return {k: vp.get(k, d) for k, d in VIP_DEFAULTS.items()}


@app.put("/api/vip/settings")
def put_vip_settings(body: VipSettingsIn) -> dict:
    vals = {
        "ip_count": _int_field(body.ip_count, "Number of IPs", 5, 1024),
        "subnet_cidr": _int_field(body.subnet_cidr, "Subnet CIDR", 1, 32),
        "vlan": _int_field(body.vlan, "VLAN", 0, 4094),
        "auto_select": bool(body.auto_select),
    }
    state.vip_overrides = vals
    state.cfg["vip_pool"].update(vals)
    backup = None
    if body.save:
        data = config_store.read_raw(config_path())["data"]
        data.setdefault("vip_pool", {}).update(vals)
        res = config_store.save_data(config_path(), data)
        if not res["ok"]:
            raise HTTPException(400, {"errors": res["errors"]})
        backup = res.get("backup")
    state.sync_tenant()  # keep tenants.yaml in step if a tenant is being onboarded
    return {**vals, "saved": body.save, "backup": backup}


class VipSelectIn(BaseModel):
    start: str | None = None


@app.post("/api/vip/select")
async def vip_select(body: VipSelectIn) -> dict:
    state.provisioner.vip_choice = body.start or None
    state.provisioner._save_state()
    if body.start:  # picking a range on the map means manual selection for this session
        state.vip_overrides["auto_select"] = False
        state.cfg["vip_pool"]["auto_select"] = False
    return await vip_check()


@app.get("/api/provision/state")
async def provision_state() -> dict:
    # async on purpose: runs on the event loop, so it never reads a result while a step is mutating it
    p = state.provisioner
    return {"results": p.results, "running_all": p.running_all, "created": list(p.outputs.keys()), "vip_choice": p.vip_choice,
            "vip_ips": p.vip_ips()}


@app.post("/api/provision/run-all")
async def provision_run_all() -> dict:
    res = await state.provisioner.run_all(state.cfg, state.vars)
    rec = state.sync_tenant()
    return {"results": res, "tenant": rec and TenantStore.key(rec)}


@app.post("/api/provision/step/{step_id}")
async def provision_step(step_id: str) -> dict:
    res = await state.provisioner.run_step(state.cfg, state.vars, _step(step_id))
    state.sync_tenant()
    return res


@app.post("/api/provision/cleanup")
async def provision_cleanup() -> dict:
    before = state.sync_tenant()  # make sure the record is current before objects disappear
    res = await state.provisioner.cleanup(state.cfg, state.vars)
    if before:
        removed = [r["id"] for r in res if r["status"] == "passed"]
        if "tenant" in removed:
            state.tenants.update(TenantStore.key(before), status="deleted", deleted_at=results_now(),
                                 objects=[], steps={k: "deleted" for k in before.get("steps", {})})
        elif removed:
            state.tenants.update(TenantStore.key(before), status="partial",
                                 note=f"cleanup removed: {', '.join(removed)}; some objects remain")
    return {"results": res}


def results_now() -> str:
    from .vms import now_iso
    return now_iso()


# ---------------------------------------------------------------- tenants

@app.get("/api/tenants")
def list_tenants() -> dict:
    ts = state.tenants.load()
    return {"path": str(state.tenants.path), "tenants": [{**t, "key": TenantStore.key(t)} for t in ts]}


class TenantKeyIn(BaseModel):
    key: str


@app.post("/api/tenants/verify")
async def verify_tenant(body: TenantKeyIn) -> dict:
    rec = next((t for t in state.tenants.load() if TenantStore.key(t) == body.key), None)
    if not rec:
        raise HTTPException(404, "Unknown tenant")
    if rec.get("vms") != str(state.cfg["vms"].get("url", "")).rstrip("/"):
        raise HTTPException(400, f"This tenant is on {rec.get('vms')}, but the app is connected to "
                                 f"{state.cfg['vms'].get('url')}")
    client = VMSClient(state.cfg)
    checks = {}
    for obj in rec.get("objects") or []:
        try:
            r = await client.request("GET", obj["endpoint"])
            checks[obj["step"]] = "present" if r["ok"] else ("missing" if r["status"] == 404 else f"HTTP {r['status']}")
        except Exception as e:  # noqa: BLE001
            checks[obj["step"]] = f"error: {type(e).__name__}"
    ok = bool(checks) and all(v == "present" for v in checks.values())
    return state.tenants.update(body.key, verified_at=results_now(), verify=checks,
                                verify_ok=ok) or {}


def _tenant_on_this_vms(key: str) -> dict:
    rec = next((t for t in state.tenants.load() if TenantStore.key(t) == key), None)
    if not rec:
        raise HTTPException(404, "Unknown tenant")
    if rec.get("status") == "deleted":
        raise HTTPException(400, "This tenant was deleted")
    if rec.get("vms") != str(state.cfg["vms"].get("url", "")).rstrip("/"):
        raise HTTPException(400, f"This tenant is on {rec.get('vms')}, but the app is connected to "
                                 f"{state.cfg['vms'].get('url')}")
    return rec


@app.get("/api/tenants/limits")
async def tenant_limits(key: str) -> dict:
    """Live state of the tenant's QoS and quota limits on the VMS."""
    from . import limits
    return await limits.read(VMSClient(state.cfg), _tenant_on_this_vms(key))


class LimitToggleIn(BaseModel):
    key: str
    kind: str          # "qos" | "quota"
    enabled: bool


@app.post("/api/tenants/limits")
async def toggle_tenant_limits(body: LimitToggleIn) -> dict:
    """Switch the tenant's QoS or quota limits off (values saved in tenants.yaml) or back on (saved values restored)."""
    from . import limits
    if body.kind not in ("qos", "quota"):
        raise HTTPException(400, "kind must be qos or quota")
    rec = _tenant_on_this_vms(body.key)
    try:
        st, new_limits = await limits.set_enabled(VMSClient(state.cfg), rec, body.kind, body.enabled)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))
    state.tenants.update(body.key, limits=new_limits)
    return {"kind": body.kind, **st, "saved": (new_limits.get(body.kind) or {}).get("saved")}


@app.get("/api/tenants/secrets")
def tenant_secrets(key: str) -> dict:
    """Admin password + S3 keys for one tenant, fetched only when the user clicks Show / Copy."""
    rec = next((t for t in state.tenants.load() if TenantStore.key(t) == key), None)
    if not rec:
        raise HTTPException(404, "Unknown tenant")
    vault = SecretVault.for_store(state.tenants)
    s = vault.get(key)
    p = state.provisioner
    if not s and (p.outputs.get("tenant") or {}).get("id") == rec.get("tenant_id") \
            and rec.get("vms") == str(state.cfg["vms"].get("url", "")).rstrip("/"):
        # tenant from the current run, onboarded before the vault existed: take the secrets from the run state
        state.sync_tenant()
        s = vault.get(key)
    return {"admin_password": s.get("admin_password") or (rec.get("admin") or {}).get("password"),
            "s3_access_key": s.get("s3_access_key") or (rec.get("s3") or {}).get("access_key"),
            "s3_secret_key": s.get("s3_secret_key") or (rec.get("s3") or {}).get("secret_key")}


@app.delete("/api/tenants")
def remove_tenant(key: str) -> dict:
    if not state.tenants.remove(key):
        raise HTTPException(404, "Unknown tenant")
    return {"ok": True}


@app.post("/api/provision/reset")
def provision_reset() -> dict:
    state.provisioner.reset()
    return {"ok": True}


@app.get("/api/provision/secrets")
def provision_secrets() -> dict:
    """Shown on demand in Tab 1: generated admin password and the S3 key pair."""
    p = state.provisioner
    keys = p.outputs.get("s3_keys") if isinstance(p.outputs.get("s3_keys"), dict) else {}
    return {"tenant_admin_password": p.effective_vars(state.cfg, state.vars).get("tenant_admin_password"),
            "s3_access_key": keys.get("access_key"), "s3_secret_key": keys.get("secret_key")}


@app.post("/api/provision/export")
def provision_export() -> dict:
    order = [s["id"] for s in state.cfg["provisioning"]["steps"]]
    res = [state.provisioner.results[i] for i in order if i in state.provisioner.results]
    if not res:
        raise HTTPException(400, "Nothing to export yet - run the steps first")
    eff = _masked_vars(state.provisioner.effective_vars(state.cfg, state.vars))
    return {"files": results.export_provisioning(res, eff, state.cfg["vms"].get("url", ""))}


# ---------------------------------------------------------------- step 2/3: SSH

class SessionIn(BaseModel):
    client: str | None = None          # name of a client from the config
    custom: dict | None = None         # {name, host, port, username, password} typed in the UI
    save: bool = False                 # also add the custom host to the site's client list


# ---- saved SSH clients: the entry goes to config.yaml `clients:`, its password to a hidden file (mode 600)

def _client_secrets_path() -> Path:
    return config_path().parent / ".client-secrets.json"


def _client_secrets() -> dict[str, str]:
    try:
        return json.loads(_client_secrets_path().read_text())
    except (FileNotFoundError, ValueError, NoSiteError):
        return {}


def _write_client_secrets(data: dict[str, str]) -> None:
    p = _client_secrets_path()
    p.touch(mode=0o600, exist_ok=True)
    p.chmod(0o600)
    p.write_text(json.dumps(data, indent=2))


def save_client(custom: dict) -> dict:
    """Add (or update, matched by name) a client in config.yaml. Returns the stored entry."""
    ssh = state.cfg["ssh"]
    host = str(custom.get("host") or "").strip()
    if not host:
        raise HTTPException(400, "Host is required")
    name = str(custom.get("name") or "").strip() or host
    entry: dict = {"name": name, "host": host}
    port = str(custom.get("port") or "").strip()
    if port and int(_int_field(port, "Port", 1, 65535)) != int(ssh.get("port", 22)):
        entry["port"] = int(port)
    user = str(custom.get("username") or "").strip()
    if user and user != ssh.get("username"):
        entry["username"] = user

    data = config_store.read_raw(config_path())["data"]
    clients = data.get("clients") or []
    idx = next((i for i, c in enumerate(clients) if c.get("name") == name), None)
    if idx is None:
        clients.append(entry)
    else:
        clients[idx] = {**{k: v for k, v in clients[idx].items() if k not in ("port", "username")}, **entry}
    data["clients"] = clients
    res = config_store.save_data(config_path(), data)
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})

    secrets = _client_secrets()
    if custom.get("password"):
        secrets[name] = custom["password"]
        _write_client_secrets(secrets)

    # update the loaded config in place (a full reload would also reset the onboarding state)
    live = [c for c in state.cfg["clients"] if c["name"] != name]
    stored = clients[idx] if idx is not None else entry
    live.insert(idx if idx is not None else len(live), dict(stored))
    state.cfg["clients"] = live
    return stored


@app.post("/api/clients")
def add_client(body: dict) -> dict:
    return {"client": save_client(body), "clients": get_config()["clients"]}


@app.delete("/api/clients/{name}")
def delete_client(name: str) -> dict:
    data = config_store.read_raw(config_path())["data"]
    clients = data.get("clients") or []
    if not any(c.get("name") == name for c in clients):
        raise HTTPException(404, f"No saved client named {name}")
    data["clients"] = [c for c in clients if c.get("name") != name]
    res = config_store.save_data(config_path(), data)
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})
    secrets = _client_secrets()
    if secrets.pop(name, None) is not None:
        _write_client_secrets(secrets)
    state.cfg["clients"] = [c for c in state.cfg["clients"] if c["name"] != name]
    return {"ok": True, "clients": get_config()["clients"]}


@app.post("/api/sessions")
def create_session(body: SessionIn) -> dict:
    ssh = state.cfg["ssh"]
    if body.custom and body.save:
        body = SessionIn(client=save_client(body.custom)["name"])
    if body.client:
        cl = next((c for c in state.cfg["clients"] if c["name"] == body.client), None)
        if not cl:
            raise HTTPException(404, f"Unknown client {body.client}")
        base = dict(cl)
        if not base.get("password") and body.client in _client_secrets():
            base["password"] = _client_secrets()[body.client]
    elif body.custom and body.custom.get("host"):
        base = {k: v for k, v in body.custom.items() if v not in (None, "")}
        base.setdefault("name", base["host"])
    else:
        raise HTTPException(400, "client or custom.host is required")
    client = {
        "name": base["name"],
        "host": base["host"],
        "port": int(base.get("port", ssh.get("port", 22))),
        "username": base.get("username", ssh.get("username", "root")),
        "password": base.get("password", ssh.get("password", "")),
    }
    for k in ("use_sudo", "vip"):
        if k in base:
            client[k] = base[k]
    sess = state.sessions.create(client, ssh)
    sess.origin = body.client or None  # lets a page reopen it after a server restart
    return sess.info()


@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return [s.info() for s in state.sessions.sessions.values()]


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str) -> dict:
    await state.sessions.remove(sid)
    return {"ok": True}


def _session(sid: str) -> Session:
    s = state.sessions.sessions.get(sid)
    if not s:
        raise HTTPException(404, "Unknown session (it was closed, or the server restarted)")
    return s


@app.post("/api/sessions/{sid}/disconnect")
async def disconnect_session(sid: str) -> dict:
    s = _session(sid)
    if s.busy:
        raise HTTPException(409, "A preset is running on this console - wait for it to finish")
    await s.disconnect()
    return s.info()


@app.post("/api/sessions/{sid}/reconnect")
async def reconnect_session(sid: str) -> dict:
    s = _session(sid)
    await s.ensure_connected()
    return s.info()


class SessionUpdateIn(BaseModel):
    selected: bool


@app.put("/api/sessions/{sid}")
def update_session(sid: str, body: SessionUpdateIn) -> dict:
    s = _session(sid)
    s.selected = body.selected
    return s.info()


@app.websocket("/ws/terminal/{sid}")
async def terminal(ws: WebSocket, sid: str) -> None:
    await ws.accept()
    s = state.sessions.sessions.get(sid)
    if not s:
        await ws.send_json({"type": "gone"})
        await ws.close()
        return
    await state.sessions.attach(s, ws)


def _ctx_for(s: Session, index: int) -> dict:
    """Template context for one client: provisioning outputs, vars, cfg, client (incl. its VIP)."""
    base = state.provisioner.context(state.cfg, state.vars)
    # Once onboarding has created the view, mount that view. vars.view_path follows the tenant name typed in
    # Tab 1, which is session-only: after a restart it falls back to config.yaml and no longer matches.
    view = base.get("view")
    if isinstance(view, dict) and view.get("path"):
        base["vars"] = {**base["vars"], "view_path": view["path"]}
    # ${tenant_vip.start} / ${tenant_vip.end}: the VIP pool this site's onboarding created
    # (falls back to the VIP check's choice before the pool exists)
    pool = base.get("vippool")
    rng = pool.get("ip_ranges") if isinstance(pool, dict) else None
    if rng and isinstance(rng[0], (list, tuple)) and len(rng[0]) == 2:
        base["tenant_vip"] = {"start": rng[0][0], "end": rng[-1][1], "name": pool.get("name", "")}
    elif isinstance(base.get("vip"), dict) and base["vip"].get("start"):
        base["tenant_vip"] = {"start": base["vip"]["start"], "end": base["vip"]["end"], "name": ""}
    client = {k: v for k, v in s.client.items() if k != "password"}
    if "vip" not in client:
        vip = state.cfg.get("mount", {}).get("vip", "auto")
        if str(vip).lower() == "auto":
            ips = state.provisioner.vip_ips()
            if ips:
                client["vip"] = ips[index % len(ips)]  # spread clients over the pool
        else:
            try:
                client["vip"] = render(vip, base)
            except TemplateError:
                pass
    base["client"] = client
    return base


def _start(group: dict, session_ids: list[str]) -> dict:
    try:
        run = state.sessions.start_run(state.cfg, group, session_ids, _ctx_for,
                                       _masked_vars(state.provisioner.effective_vars(state.cfg, state.vars)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"run_id": run["run_id"]}


class RunIn(BaseModel):
    group: str
    session_ids: list[str]


@app.post("/api/runs")
async def start_run(body: RunIn) -> dict:
    return _start(_group(body.group), body.session_ids)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    """Runs still in progress (a reloaded page resumes their status lines)."""
    return [{"run_id": r["run_id"], "group": r["group"], "label": r["label"], "status": r["status"]}
            for r in state.sessions.runs.values() if r["status"] == "running"]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = state.sessions.runs.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    return run


# ---------------------------------------------------------------- packages

class PkgCheckIn(BaseModel):
    session_ids: list[str]


@app.post("/api/packages/check")
async def packages_check(body: PkgCheckIn) -> dict:
    pkg_cfg = state.cfg["packages"]
    group = {"id": "packages-check", "label": "Check packages", "sudo": False, "parse": "packages",
             "pkg_cfg": pkg_cfg, "delay_between_commands_seconds": 0,
             "commands": [{"run": packages.check_script(pkg_cfg),
                           "display": "check: " + ", ".join(p["name"] for p in pkg_cfg.get("required", []))}]}
    return _start(group, body.session_ids)


@app.get("/api/packages/status")
def packages_status() -> dict:
    return {sid: {"client": state.sessions.sessions[sid].client["name"], **st}
            for sid, st in state.sessions.pkg_status.items() if sid in state.sessions.sessions}


class PkgInstallIn(BaseModel):
    session_ids: list[str]
    mode: str = "missing"            # missing | update
    packages: list[str] | None = None


@app.post("/api/packages/install")
async def packages_install(body: PkgInstallIn) -> dict:
    if body.mode not in ("missing", "update"):
        raise HTTPException(400, "mode must be 'missing' or 'update'")
    pkg_cfg = state.cfg["packages"]
    per_session: dict[str, list] = {}
    errors = []
    for sid in body.session_ids:
        st = state.sessions.pkg_status.get(sid)
        if not st:
            continue
        try:
            cmds = packages.build_commands(pkg_cfg, st, body.mode, body.packages)
        except ValueError as e:
            errors.append(f"{state.sessions.sessions[sid].client['name']}: {e}")
            continue
        if cmds:
            per_session[sid] = cmds
    if not per_session:
        raise HTTPException(400, "; ".join(errors) or
                            ("Nothing to install - run 'Check packages' first" if body.mode == "missing"
                             else "Nothing to update"))
    label = "Install missing packages" if body.mode == "missing" else "Update packages"
    group = {"id": f"packages-{body.mode}", "label": label, "per_session": per_session}
    return _start(group, list(per_session))


# ---------------------------------------------------------------- results

class ConsoleExportIn(BaseModel):
    name: str
    host: str
    username: str = ""
    text: str            # plain text of the console (ANSI codes already stripped by xterm)


@app.post("/api/consoles/export")
def export_console(body: ConsoleExportIn) -> dict:
    import re as _re
    from datetime import datetime
    now = datetime.now().astimezone()
    slug = _re.sub(r"[^A-Za-z0-9._-]+", "-", body.name).strip("-") or "console"
    name = f"{now:%Y%m%d-%H%M%S}-console-{slug}.md"
    text = body.text
    for s in state.sessions.sessions.values():  # never write a known SSH password into the file
        text = s.mask(text)
    for pw in _client_secrets().values():
        if pw and len(pw) >= 3:
            text = text.replace(pw, "********")
    fence = "````" if "```" in text else "```"
    md = (f"# Console: {body.name}\n\n"
          f"| | |\n|---|---|\n| Host | `{body.username + '@' if body.username else ''}{body.host}` |\n"
          f"| Exported | {now:%Y-%m-%d %H:%M:%S %Z} |\n| Test site | {state.site.name if state.site else '-'} |\n\n"
          f"{fence}text\n{text.rstrip()}\n{fence}\n")
    results.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (results.RESULTS_DIR / name).write_text(md)
    return {"file": name}


def _perf_groups() -> set[str]:
    return {g["id"] for g in state.cfg.get("command_groups", []) if g.get("results")}


class LogsActionIn(BaseModel):
    include_performance: bool = False   # also archive/purge elbencho (performance) results


@app.get("/api/logs")
def get_logs() -> dict:
    logs = results.list_logs(_perf_groups())
    return {"logs": logs, "archives": results.list_archives(), "dir": str(results.RESULTS_DIR)}


def _no_running_runs() -> None:
    if any(r.get("status") == "running" for r in state.sessions.runs.values()):
        raise HTTPException(409, "A run is still in progress - wait for it to finish first")


@app.post("/api/logs/archive")
def archive_logs(body: LogsActionIn) -> dict:
    _no_running_runs()
    return results.archive_logs(_perf_groups(), body.include_performance, state.site.name if state.site else "site")


@app.post("/api/logs/purge")
def purge_logs(body: LogsActionIn) -> dict:
    _no_running_runs()
    return results.purge_logs(_perf_groups(), body.include_performance)


@app.get("/api/logs/archives/{name}")
def download_archive(name: str) -> FileResponse:
    adir = (results.RESULTS_DIR / results.ARCHIVE_DIRNAME).resolve()
    p = (adir / name).resolve()
    if p.parent != adir or not p.is_file() or not name.startswith("logs-") or not name.endswith(".zip"):
        raise HTTPException(404, "Not found")
    return FileResponse(p, filename=p.name, media_type="application/zip")


@app.get("/api/results")
def get_results() -> list[dict]:
    return results.list_files()


@app.get("/api/results/{name}")
def download_result(name: str) -> FileResponse:
    p = (results.RESULTS_DIR / name).resolve()
    if p.parent != results.RESULTS_DIR.resolve() or not p.is_file() or name.startswith("."):
        raise HTTPException(404, "Not found")
    return FileResponse(p, filename=p.name)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=Path(STATIC)), name="static")
