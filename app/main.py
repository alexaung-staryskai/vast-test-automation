"""FastAPI app: REST endpoints for the UI, WebSocket terminals, static frontend."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import httpx

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


VERSION_FILE = ROOT / "VERSION"


def _read_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()   # the version this server process is running

state = State()
app = FastAPI(title="VAST Testing Automation", version=APP_VERSION)


RESTART_EXIT_CODE = 75   # start.py relaunches the app when it exits with this code (used on Windows)


def _restart_blockers() -> list[str]:
    out = []
    prov = getattr(state, "provisioner", None)
    if state.site and prov is not None and getattr(prov, "running_all", False):
        out.append("Tenant onboarding is running")
    if any(r.get("status") == "running" for r in state.sessions.runs.values()):
        out.append("A test run is in progress")
    if _teardown_busy:
        out.append("A test site is being deleted")
    return out


@app.get("/api/restart")
def restart_info() -> dict:
    """What a restart would interrupt, for the confirm window."""
    return {"blockers": _restart_blockers(), "version": APP_VERSION, "on_disk": _read_version(),
            "site": state.site.name if state.site else None,
            "consoles": [{"name": s.info().get("name"), "status": s.info().get("status")}
                         for s in state.sessions.sessions.values()]}


@app.post("/api/restart")
async def restart_app() -> dict:
    """Restart the server process (loads new code and VERSION) and reopen the same test site.
    POSIX: re-exec in place (same PID, so start.py and the stop scripts keep working).
    Windows under start.py: exit with RESTART_EXIT_CODE and start.py starts it again."""
    import asyncio
    import sys
    blockers = _restart_blockers()
    if blockers:
        raise HTTPException(409, f"{blockers[0]} - wait for it to finish, then restart")
    if state.site:
        os.environ["VAST_TEST_SITE"] = str(state.site)
    else:
        os.environ.pop("VAST_TEST_SITE", None)

    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])   # keeps "-m uvicorn"

    def _go() -> None:
        print("[VAST Test Bench] Restarting ...", flush=True)
        if os.name == "nt":
            if os.environ.get("VAST_LAUNCHER") == "start.py":
                run_dir = ROOT / ".run"
                run_dir.mkdir(exist_ok=True)
                (run_dir / f"app-{os.environ.get('VAST_APP_PORT', '')}.site").write_text(os.environ.get("VAST_TEST_SITE", ""))
                os._exit(RESTART_EXIT_CODE)
            import subprocess
            subprocess.Popen(argv, cwd=os.getcwd(), env=os.environ.copy())
            os._exit(0)
        os.execv(sys.executable, argv)

    asyncio.get_running_loop().call_later(0.6, _go)   # let this response reach the browser first
    return {"ok": True, "site": state.site.name if state.site else None}


@app.get("/api/version")
def get_version() -> dict:
    """running: the code this process loaded. on_disk: VERSION now (differs after a pull until the app restarts)."""
    return {"running": APP_VERSION, "on_disk": _read_version()}


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
    if path.startswith("/api/") and not path.startswith(("/api/sites", "/api/version", "/api/restart")) and state.site is None:
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
        info = sites.info(Path(path).resolve())
        dest = sites.trash(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sites.audit(action="delete_site_local_only", site=info["name"], vms=info.get("vms"), moved_to=str(dest),
                tenants_left_on_vms=[t.get("name") for t in info.get("active_tenants") or []])
    return {"ok": True, "moved_to": str(dest)}


@app.get("/api/sites/delete-log")
def site_delete_log() -> dict:
    """App-wide log of site deletions (kept in sites/.delete-log.jsonl, outside the deleted folders)."""
    return {"path": str(sites.AUDIT_FILE), "entries": sites.read_audit()}


# ---- delete a site together with its tenant(s) on the VMS (🗑 Delete site → modal) ----
_teardown_busy: set[str] = set()


class TeardownIn(BaseModel):
    path: str
    site_name: str
    tenants: list[str] = []                     # tenant names as typed by the user
    plan: dict[str, dict[str, list]] = {}       # run only: tenant name → kind → reviewed object ids
    force: dict[str, str] = {}                  # run only: tenant name → typed "force delete <tenant name>"


def _teardown_site(body: TeardownIn) -> tuple[Path, dict, TenantStore]:
    """The site folder, its config and its tenants.yaml, after checking the typed site name."""
    try:
        path = sites.resolve(body.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if path.parent != sites.SITES_DIR.resolve():
        raise HTTPException(400, "Only test sites inside the sites folder can be deleted here")
    name = sites.info(path)["name"]
    if body.site_name.strip() != name:
        raise HTTPException(400, f"The site name doesn't match - type “{name}” exactly")
    if state.site and path == state.site.resolve():
        prov = state.provisioner
        if prov is not None and prov.running_all:
            raise HTTPException(409, "Tenant onboarding is still running on this site - wait for it to finish")
        cfg = state.cfg
    else:
        try:
            cfg, _ = load_config(path / "config.yaml")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Can't read this site's config.yaml: {e}")
    names = [t.strip() for t in body.tenants if t.strip()]
    if len(set(names)) != len(names):
        raise HTTPException(400, "The same tenant is typed twice")
    for n in names:
        if n.lower() == "default":
            raise HTTPException(400, "The VMS default tenant can't be deleted")
    return path, cfg, TenantStore.from_cfg(cfg)


def _record_for(store: TenantStore, vms_url: str, name: str) -> dict | None:
    return next((t for t in store.load() if t.get("name") == name and t.get("vms") == vms_url
                 and t.get("status") not in sites.GONE), None)


@app.post("/api/sites/teardown/scan")
async def teardown_scan(body: TeardownIn) -> dict:
    """Find the typed tenant(s) on the site's VMS and list everything under them, in deletion order."""
    from . import teardown
    path, cfg, store = _teardown_site(body)
    vms_url = str(cfg["vms"].get("url", "")).rstrip("/")
    client = VMSClient(cfg)
    out = []
    for name in [t.strip() for t in body.tenants if t.strip()]:
        rec = _record_for(store, vms_url, name)
        try:
            tenant = await teardown.find_tenant(client, name)
            if tenant is None:
                left = await teardown.orphan_providers(client, name)
                out.append({"name": name, "found": False, "recorded": bool(rec), "notes": [],
                            "groups": [{"kind": "local_provider", "label": teardown.LABEL["local_provider"],
                                        "items": left}] if left else []})
                continue
            if tenant.get("id") == 1:
                raise HTTPException(400, "The VMS default tenant can't be deleted")
            res = await teardown.scan(client, tenant, rec)
        except (teardown.TeardownError, httpx.HTTPError) as e:
            raise HTTPException(502, f"Couldn't read tenant “{name}” from the VMS {vms_url}: {e}")
        out.append({"name": name, "found": True, "id": tenant["id"], "recorded": bool(rec),
                    "recorded_id_matches": not rec or rec.get("tenant_id") in (None, tenant["id"]),
                    "groups": res["groups"], "notes": res["notes"]})
    return {"vms": vms_url, "site": sites.info(path)["name"], "tenants": out}


@app.post("/api/sites/teardown/run")
async def teardown_run(body: TeardownIn):
    """Delete the reviewed VMS objects in order, streaming progress as NDJSON, then move the site to the trash."""
    from fastapi.responses import StreamingResponse
    from . import teardown
    path, cfg, store = _teardown_site(body)
    key = str(path)
    if key in _teardown_busy:
        raise HTTPException(409, "This site is already being deleted")
    vms_url = str(cfg["vms"].get("url", "")).rstrip("/")
    client = VMSClient(cfg)
    site_name = sites.info(path)["name"]
    for name, phrase in body.force.items():
        if phrase.strip() != f"force delete {name}":
            raise HTTPException(400, f"To force delete, type “force delete {name}” exactly")
    log = lambda **e: sites.audit(site=site_name, vms=vms_url, **e)  # noqa: E731

    async def events():
        _teardown_busy.add(key)
        line = lambda d: json.dumps(d, default=str) + "\n"  # noqa: E731
        log(action="delete_site_started", tenants=[t.strip() for t in body.tenants if t.strip()],
            force=sorted(body.force))
        try:
            for name in [t.strip() for t in body.tenants if t.strip()]:
                rec = _record_for(store, vms_url, name)
                forced = name in body.force
                if forced:
                    log(action="force_delete_tenant_requested", tenant=name, typed=body.force[name])
                yield line({"t": "tenant", "name": name})
                try:
                    tenant = await teardown.find_tenant(client, name)
                except (teardown.TeardownError, httpx.HTTPError) as e:
                    yield line({"t": "stop", "msg": f"Couldn't read tenant “{name}” from the VMS: {e}"}); return
                if tenant is None:
                    log(action="tenant_not_on_vms", tenant=name)
                    yield line({"t": "tenant_gone", "name": name})
                    leftover = body.plan.get(name, {}).get("local_provider") or []
                    if leftover:   # the tenant's local provider was left behind by an earlier delete
                        stopped = False
                        try:
                            async for ev in teardown.run_orphans(client, name, leftover):
                                if ev["t"] == "item" and ev["s"] != "deleting":
                                    log(action="delete_object", tenant=name, kind=ev["kind"], id=ev["id"],
                                        name=ev["name"], result=ev["s"], msg=ev.get("msg") or "")
                                if ev["t"] == "stop":
                                    log(action="delete_site_stopped", tenant=name, msg=ev["msg"])
                                yield line({**ev, "tenant": name})
                                stopped = stopped or ev["t"] == "stop"
                        except (teardown.TeardownError, httpx.HTTPError) as e:
                            log(action="delete_site_stopped", tenant=name, msg=f"VMS error: {e}")
                            yield line({"t": "stop", "msg": f"VMS error: {e}"}); stopped = True
                        if stopped:
                            return
                elif tenant.get("id") == 1:
                    yield line({"t": "stop", "msg": "The VMS default tenant can't be deleted"}); return
                else:
                    stopped = False
                    try:
                        async for ev in teardown.run(client, tenant, rec, body.plan.get(name, {}), force_tenant=forced):
                            if ev["t"] == "item" and ev["s"] != "deleting":
                                log(action="force_delete_tenant" if ev.get("forced") else "delete_object",
                                    tenant=name, tenant_id=tenant["id"], kind=ev["kind"], id=ev["id"],
                                    name=ev["name"], result=ev["s"], msg=ev.get("msg") or "")
                            if ev["t"] == "stop":
                                log(action="delete_site_stopped", tenant=name, msg=ev["msg"])
                            yield line({**ev, "tenant": name})
                            stopped = stopped or ev["t"] == "stop"
                    except (teardown.TeardownError, httpx.HTTPError) as e:
                        log(action="delete_site_stopped", tenant=name, msg=f"VMS error: {e}")
                        yield line({"t": "stop", "msg": f"VMS error: {e}"}); stopped = True
                    if stopped:
                        if rec:
                            store.update(TenantStore.key(rec), status="partial", updated_at=results_now(),
                                         note="Delete site stopped part-way; some objects remain on the VMS")
                        return
                if rec:
                    store.update(TenantStore.key(rec), status=sites.VMS_DELETED, deleted_at=results_now(), objects=[], note=None)
            # VMS side is clean. The local records (config, tenants.yaml, test results) are KEPT; the site and its
            # tenants are only marked "vms record deleted".
            yield line({"t": "local", "s": "updating"})
            try:
                sites.mark_vms_deleted(path)
            except OSError as e:
                yield line({"t": "stop", "msg": f"VMS objects deleted, but updating site.yaml failed: {e}"}); return
            if state.site and path == state.site.resolve() and state.provisioner is not None:
                state.provisioner.reset()       # the onboarding run's objects no longer exist on the VMS
            log(action="delete_site_done", kept_local=True)
            yield line({"t": "local", "s": "updated"})
            yield line({"t": "done", "ok": True})
        finally:
            _teardown_busy.discard(key)

    return StreamingResponse(events(), media_type="application/x-ndjson")


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
                    "menu": g.get("menu"),                     # Presets dropdown it sits in (none -> Customs)
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


# ---- Custom Linux commands (⚙ Configuration) -> Presets → Customs ----
def _custom_view(g: dict) -> dict:
    """One custom preset as the editor shows it: one command per line, or one script."""
    cmds = g.get("commands") or []
    out = {"id": g.get("id"), "label": g.get("label", g.get("id")), "sudo": g.get("sudo", True) is not False,
           "confirm": g.get("confirm") or "", "results": bool(g.get("results")), "editable": True}
    if all(isinstance(c, str) and "\n" not in c.strip() for c in cmds):
        out.update(mode="lines", text="\n".join(cmds))
    elif len(cmds) == 1 and (isinstance(cmds[0], str) or (isinstance(cmds[0], dict) and set(cmds[0]) <= {"run", "display"})):
        out.update(mode="script", text=(cmds[0] if isinstance(cmds[0], str) else cmds[0].get("run", "")).rstrip("\n"))
    else:                                   # e.g. per-command display names or other keys: edit it as YAML
        import yaml
        out.update(mode="yaml", editable=False, text=yaml.safe_dump(cmds, sort_keys=False, allow_unicode=True))
    known = {"id", "label", "menu", "sudo", "confirm", "results", "commands"}
    if set(g) - known:                      # options the editor doesn't know about: keep the entry as it is
        out["editable"] = False
        out["extra"] = sorted(set(g) - known)
    return out


@app.get("/api/custom-commands")
def get_custom_commands() -> dict:
    raw = config_store.read_raw(config_path())
    groups = (raw["data"] or {}).get("command_groups") or []
    return {"mtime": raw["mtime"], "commands": [_custom_view(g) for g in groups if config_store.is_custom_group(g)]}


class CustomCmdIn(BaseModel):
    id: str | None = None
    label: str = ""
    mode: str = "lines"          # lines: every line is its own command · script: the whole text runs as one script
    text: str = ""
    sudo: bool = False
    confirm: str = ""            # non-empty: ask before running, with this message
    results: bool = False        # parse elbencho output and save it as a performance result
    keep: bool = False           # leave this entry exactly as it is in config.yaml


class CustomCmdsIn(BaseModel):
    commands: list[CustomCmdIn]
    base_mtime: float | None = None


def _slug(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "command"


@app.put("/api/custom-commands")
def put_custom_commands(body: CustomCmdsIn) -> dict:
    _not_stale(body.base_mtime)
    raw = config_store.read_raw(config_path())
    all_groups = (raw["data"] or {}).get("command_groups") or []
    taken = {g.get("id") for g in all_groups if not config_store.is_custom_group(g)}
    errors, groups, labels = [], [], set()
    for n, c in enumerate(body.commands, 1):
        if c.keep:
            if not c.id:
                errors.append(f"Command {n}: missing id")
            else:
                groups.append({"id": c.id, "keep": True}); taken.add(c.id)
            continue
        label = c.label.strip()
        lines = [ln.rstrip() for ln in c.text.replace("\r\n", "\n").split("\n")]
        if not label:
            errors.append(f"Command {n}: give it a name")
        elif label.lower() in labels:
            errors.append(f"Two commands are called \"{label}\" - names must be unique")
        labels.add(label.lower())
        if not any(ln.strip() for ln in lines):
            errors.append(f"\"{label or f'Command {n}'}\": enter at least one Linux command")
        if c.mode not in ("lines", "script"):
            errors.append(f"\"{label}\": unknown mode {c.mode}")
        gid = c.id if c.id and c.id not in taken else None
        if not gid:
            base = "custom_" + _slug(label)
            gid, k = base, 2
            while gid in taken:
                gid, k = f"{base}_{k}", k + 1
        taken.add(gid)
        if c.mode == "script":
            script = "\n".join(lines).strip("\n") + "\n"
            cmds = [{"display": label, "run": script}]
        else:
            cmds = [ln.strip() for ln in lines if ln.strip()]
        g = {"id": gid, "label": label, "menu": "Customs"}
        if not c.sudo:
            g["sudo"] = False
        if c.confirm.strip():
            g["confirm"] = c.confirm.strip()
        if c.results:
            g["results"] = True
        g["commands"] = cmds
        groups.append(g)
    if errors:
        raise HTTPException(400, {"errors": errors})
    res = _after_save(config_store.save_custom_groups(config_path(), groups))
    return {**res, **get_custom_commands()}


# ---- export / import of custom Linux commands (a small YAML file, readable and diff-able) ----
CC_FORMAT = "vast-test-bench/custom-commands"


class CustomExportIn(BaseModel):
    commands: list[CustomCmdIn]


@app.post("/api/custom-commands/export")
def export_custom_commands(body: CustomExportIn) -> dict:
    """The editor's current list (unsaved edits included) as a YAML file the Import button reads back."""
    import io
    from datetime import datetime
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.scalarstring import LiteralScalarString
    items = []
    for c in body.commands:
        if c.keep:
            continue                               # hand-written entries the editor can't show are not exported
        m = CommentedMap()
        m["name"] = c.label.strip()
        m["mode"] = "script" if c.mode == "script" else "lines"
        text = c.text.replace("\r\n", "\n").strip("\n")
        m["commands"] = LiteralScalarString(text + "\n") if "\n" in text else text
        m["sudo"] = bool(c.sudo)
        if c.confirm.strip():
            m["ask_before_running"] = c.confirm.strip()
        if c.results:
            m["save_elbencho_results"] = True
        items.append(m)
    site = state.site.name if state.site else "site"
    doc = CommentedMap()
    doc["format"] = CC_FORMAT
    doc["version"] = 1
    doc["exported_from"] = site
    doc["exported_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    doc["commands"] = items
    buf = io.StringIO()
    buf.write("# VAST Test Bench - custom Linux commands.\n"
              "# Import: ⚙ Configuration → Custom Linux commands → Import. They show under Presets → Customs.\n"
              "#   mode: lines  -> every line of commands is its own command, run in order\n"
              "#   mode: script -> commands runs as one bash script\n")
    config_store._rt().dump(doc, buf)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return {"filename": f"custom-commands-{_slug(site).replace('_', '-')}-{stamp}.yaml", "text": buf.getvalue(),
            "count": len(items), "skipped": sum(1 for c in body.commands if c.keep)}


class CustomImportIn(BaseModel):
    text: str


@app.post("/api/custom-commands/import")
def import_custom_commands(body: CustomImportIn) -> dict:
    """Parse an exported file (or a list of command_groups entries copied from a config.yaml) into editor items.
    Nothing is saved here: the page adds them to the editor and the user reviews them, then clicks Save."""
    import yaml
    if len(body.text) > 1_000_000:
        raise HTTPException(400, {"errors": ["The file is too large (over 1 MB) for a list of commands."]})
    try:
        data = yaml.safe_load(body.text)
    except yaml.YAMLError as e:
        raise HTTPException(400, {"errors": [f"Not a valid YAML/JSON file: {e}"]})
    if isinstance(data, dict):
        fmt = data.get("format")
        if fmt and fmt != CC_FORMAT:
            raise HTTPException(400, {"errors": [f"Unknown file format '{fmt}' - expected a custom commands export."]})
        raw = data.get("commands") if "commands" in data and fmt else data.get("command_groups")
    else:
        raw = data
    if not isinstance(raw, list):
        raise HTTPException(400, {"errors": ["No commands found. Expected a file exported from Custom Linux commands "
                                             "(format: vast-test-bench/custom-commands)."]})
    items, skipped = [], []
    for n, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            skipped.append(f"entry {n}: not a mapping"); continue
        if "name" in it:                               # our export format
            text = it.get("commands", "")
            if isinstance(text, list):
                text = "\n".join(str(x) for x in text)
            mode = it.get("mode", "lines")
            if mode not in ("lines", "script"):
                skipped.append(f"{it['name']}: unknown mode {mode}"); continue
            if not str(it["name"]).strip() or not str(text).strip():
                skipped.append(f"entry {n}: needs a name and commands"); continue
            items.append({"id": None, "label": str(it["name"]).strip()[:60], "mode": mode, "text": str(text).rstrip("\n"),
                          "sudo": bool(it.get("sudo", False)), "confirm": str(it.get("ask_before_running") or ""),
                          "results": bool(it.get("save_elbencho_results", False)), "editable": True})
        elif "commands" in it:                         # a command_groups entry copied from a config.yaml
            if it.get("id") in config_store.BUILTIN_GROUPS or it.get("section") == "packages":
                skipped.append(f"{it.get('label', it.get('id'))}: built-in preset"); continue
            v = _custom_view(it)
            if not v["editable"]:
                skipped.append(f"{v['label']}: uses options the editor can't show - add it in the YAML view"); continue
            items.append({**v, "id": None})
        else:
            skipped.append(f"entry {n}: no name/commands")
    if not items and not skipped:
        raise HTTPException(400, {"errors": ["The file has no commands."]})
    return {"commands": items, "skipped": skipped}


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
        pool = state.provisioner.outputs.get("vippool")
        return await state.provisioner.vip_check(state.cfg, map_only=isinstance(pool, dict) and pool.get("id") is not None)
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    p = state.provisioner
    previous = p.vip_choice
    p.vip_choice = body.start or None
    if body.start:  # picking a range on the map means manual selection for this session
        state.vip_overrides["auto_select"] = False
        state.cfg["vip_pool"]["auto_select"] = False
    res = await vip_check()
    if body.start:
        if res.get("proposal"):
            p.vip_choice = res["proposal"]["start"]   # the block actually chosen (a click may have been snapped)
        elif previous and previous != body.start:
            # the clicked IP can't start a block: keep the earlier choice instead of ending up with none
            p.vip_choice = previous
            res = {**await vip_check(), "rejected": body.start}
            res["warnings"] = [f"{body.start} is not inside a run of {res['ip_count']} free IPs - kept "
                               f"{previous}"] + [w for w in res.get("warnings", []) if body.start not in w]
    p._save_state()
    return res


@app.get("/api/vip/current")
async def vip_current() -> dict:
    """The VIP IPs for the VIP Pool card: the pool on the VMS once it exists (read live), otherwise the chosen range."""
    from . import ipam
    p = state.provisioner
    vp = state.cfg.get("vip_pool", {})
    count = max(ipam.MIN_IPS, int(vp.get("ip_count", ipam.MIN_IPS)))
    out: dict = {"auto": bool(vp.get("auto_select", True)), "count": count, "pool": None, "choice": None}
    if p.vip_choice:
        try:
            a = ipam.ip2int(p.vip_choice)
            out["choice"] = {"start": p.vip_choice, "end": ipam.int2ip(a + count - 1), "count": count}
        except ValueError:
            pass
    pool = p.outputs.get("vippool") if isinstance(p.outputs.get("vippool"), dict) else None
    if pool and pool.get("id") is not None:
        rec = {"id": pool["id"], "name": pool.get("name"), "ip_ranges": pool.get("ip_ranges"), "live": False}
        try:
            r = await VMSClient(state.cfg).request("GET", f"/vippools/{pool['id']}/")
            if r["ok"] and isinstance(r["body"], dict):
                b = r["body"]
                rec.update(name=b.get("name", rec["name"]), ip_ranges=b.get("ip_ranges"), live=True,
                           subnet_cidr=b.get("subnet_cidr"), vlan=b.get("vlan"), state=b.get("state"))
            elif r["status"] == 404:
                rec["error"] = "not found on the VMS any more"
            else:
                rec["error"] = f"VMS answered HTTP {r['status']}"
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
        rec["ips"] = ipam.expand(rec.get("ip_ranges"))
        out["pool"] = rec
    return out


class VipRangeIn(BaseModel):
    start: str
    end: str


@app.post("/api/vip/range")
async def vip_set_range(body: VipRangeIn) -> dict:
    """A typed VIP range. Before the VIP pool exists: the range step 1 will use (manual selection, the number of IPs
    follows the range). Once it exists: change the pool's ip_ranges on the VMS (PATCH /vippools/{id}/)."""
    from . import ipam
    try:
        a, b = ipam.ip2int(body.start), ipam.ip2int(body.end)
    except ValueError:
        raise HTTPException(400, "Start and end must be IPv4 addresses, e.g. 10.20.0.10")
    if b < a:
        raise HTTPException(400, "The end IP must not be before the start IP")
    count = b - a + 1
    if not ipam.MIN_IPS <= count <= 1024:
        raise HTTPException(400, f"A VIP pool needs {ipam.MIN_IPS} to 1024 IPs - this range has {count}")
    start, end = ipam.int2ip(a), ipam.int2ip(b)
    p = state.provisioner
    pool = p.outputs.get("vippool") if isinstance(p.outputs.get("vippool"), dict) else None

    if pool and pool.get("id") is not None:
        client = VMSClient(state.cfg)
        holders, _ = await ipam.list_holders(client)
        others = [h for h in holders if not (h["kind"] == "VIP pool" and h["name"] == pool.get("name"))]
        used = ipam.used_map(others)
        clash = next((n for n in range(a, b + 1) if n in used), None)
        if clash is not None:
            raise HTTPException(400, f"{ipam.int2ip(clash)} is already used by {used[clash]} - choose another range")
        r = await client.request("PATCH", f"/vippools/{pool['id']}/", body={"ip_ranges": [[start, end]]})
        if not r["ok"]:
            raise HTTPException(502, f"The VMS refused the change: HTTP {r['status']} {str(r['body'])[:300]}")
        p.outputs["vippool"] = {**pool, "ip_ranges": [[start, end]]}
        p.outputs["vip"] = {"start": start, "end": end, "count": count, "ips": ipam.expand([[start, end]])}
        state.vip_overrides["ip_count"] = count
        state.cfg["vip_pool"]["ip_count"] = count
        p._save_state()
        state.sync_tenant()
        return {"changed_on_vms": True, **await vip_current()}

    # not created yet: manual selection of exactly this range
    vp = state.cfg["vip_pool"]
    prev = (p.vip_choice, vp.get("ip_count"), vp.get("auto_select", True))
    vp.update(ip_count=count, auto_select=False)
    state.vip_overrides.update(ip_count=count, auto_select=False)
    p.vip_choice = start
    res = await vip_check()
    got = res.get("proposal")
    if not got or got["start"] != start:
        p.vip_choice, vp["ip_count"], vp["auto_select"] = prev
        state.vip_overrides.update(ip_count=prev[1], auto_select=prev[2])
        cand = ", ".join(f"{r['start']} – {r['end']}" for r in res.get("candidate_ranges", []))
        used = next((c["holder"] for c in res.get("cells", []) if a <= ipam.ip2int(c["ip"]) <= b and c.get("holder")), None)
        why = f"{used} holds part of it" if used else f"it must be free and inside the candidate ranges ({cand})"
        raise HTTPException(400, f"{start} – {end} can't be used: {why}")
    p._save_state()
    return {"changed_on_vms": False, "check": res, **await vip_current()}


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


@app.post("/api/tenants/verify/stream")
async def verify_tenant_stream(body: TenantKeyIn):
    """Same check as /api/tenants/verify, streamed as NDJSON so the UI can show each step as it happens:
    connect → one GET per recorded object → saved."""
    import time
    from fastapi.responses import StreamingResponse
    rec = next((t for t in state.tenants.load() if TenantStore.key(t) == body.key), None)
    if not rec:
        raise HTTPException(404, "Unknown tenant")
    vms_url = str(state.cfg["vms"].get("url", "")).rstrip("/")
    if rec.get("vms") != vms_url:
        raise HTTPException(400, f"This tenant is on {rec.get('vms')}, but the app is connected to {vms_url}")
    objects = [o for o in rec.get("objects") or [] if o.get("endpoint")]

    async def gen():
        def line(d: dict) -> bytes:
            return (json.dumps(d) + "\n").encode()
        client = VMSClient(state.cfg)
        yield line({"t": "start", "vms": vms_url, "objects": [{"step": o["step"], "name": o.get("name", o["step"]),
                                                               "endpoint": o["endpoint"]} for o in objects]})
        # 1. can we reach the VMS at all?
        yield line({"t": "connect", "s": "checking"})
        t0 = time.monotonic()
        try:
            r = await client.request("GET", state.cfg["vms"].get("health_endpoint", "/clusters/"))
            ok = r["ok"]
            info = r["body"]
            items = info.get("results") if isinstance(info, dict) and "results" in info else info
            c = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
            detail = " · ".join(str(x) for x in (c.get("name"), c.get("sw_version")) if x) if ok else f"HTTP {r['status']}"
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        yield line({"t": "connect", "s": "ok" if ok else "failed", "detail": detail,
                    "ms": round((time.monotonic() - t0) * 1000)})
        if not ok:
            yield line({"t": "done", "ok": False, "connect_failed": True})
            return
        # 2. every object onboarding created
        checks = {}
        for o in objects:
            yield line({"t": "check", "step": o["step"], "s": "checking"})
            t0 = time.monotonic()
            found = None
            try:
                r = await client.request("GET", o["endpoint"])
                res = "present" if r["ok"] else ("missing" if r["status"] == 404 else f"HTTP {r['status']}")
                if r["ok"] and isinstance(r["body"], dict):
                    b = r["body"]
                    found = {k: b.get(k) for k in ("id", "name", "username", "path", "state") if b.get(k) not in (None, "")}
            except Exception as e:  # noqa: BLE001
                res = f"error: {type(e).__name__}"
            checks[o["step"]] = res
            yield line({"t": "check", "step": o["step"], "s": res, "found": found,
                        "ms": round((time.monotonic() - t0) * 1000)})
        ok = bool(checks) and all(v == "present" for v in checks.values())
        state.tenants.update(body.key, verified_at=results_now(), verify=checks, verify_ok=ok)
        yield line({"t": "done", "ok": ok, "present": sum(v == "present" for v in checks.values()), "total": len(checks)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _tenant_on_this_vms(key: str) -> dict:
    rec = next((t for t in state.tenants.load() if TenantStore.key(t) == key), None)
    if not rec:
        raise HTTPException(404, "Unknown tenant")
    if rec.get("status") in sites.GONE:
        raise HTTPException(400, "This tenant was deleted from the VMS")
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


class ClientEditIn(BaseModel):
    name: str = ""
    host: str = ""
    port: str | int | None = None
    username: str = ""
    password: str = ""                 # empty = keep the saved one
    clear_password: bool = False       # forget the saved password (use the ssh: default)


@app.put("/api/clients/{old_name}")
def edit_client(old_name: str, body: ClientEditIn) -> dict:
    """Change a saved client (name, host, port, user, password). Open consoles keep their current connection."""
    ssh = state.cfg["ssh"]
    data = config_store.read_raw(config_path())["data"]
    clients = data.get("clients") or []
    idx = next((i for i, c in enumerate(clients) if c.get("name") == old_name), None)
    if idx is None:
        raise HTTPException(404, f"No saved client named {old_name}")
    host = body.host.strip()
    if not host:
        raise HTTPException(400, "Host is required")
    name = body.name.strip() or host
    if name != old_name and any(c.get("name") == name for c in clients):
        raise HTTPException(400, f"Another saved client is already called “{name}”")
    entry = {k: v for k, v in clients[idx].items() if k not in ("name", "host", "port", "username")}
    entry = {"name": name, "host": host, **entry}
    port = str(body.port or "").strip()
    if port and int(_int_field(port, "Port", 1, 65535)) != int(ssh.get("port", 22)):
        entry["port"] = int(port)
    user = body.username.strip()
    if user and user != ssh.get("username"):
        entry["username"] = user
    if body.clear_password:
        entry.pop("password", None)
    clients[idx] = entry
    data["clients"] = clients
    res = config_store.save_data(config_path(), data)
    if not res["ok"]:
        raise HTTPException(400, {"errors": res["errors"]})
    secrets = _client_secrets()
    old_secret = secrets.pop(old_name, None)
    if body.password:
        secrets[name] = body.password
    elif old_secret is not None and not body.clear_password:
        secrets[name] = old_secret
    _write_client_secrets(secrets)
    live = list(state.cfg["clients"])
    li = next((i for i, c in enumerate(live) if c["name"] == old_name), None)
    if li is None:
        live.append(dict(entry))
    else:
        live[li] = dict(entry)
    state.cfg["clients"] = live
    return {"client": entry, "clients": get_config()["clients"]}


class ClientTestIn(ClientEditIn):
    saved: str = ""                    # start from this saved client; the fields above override it


def _effective_client(body: ClientTestIn) -> dict:
    ssh = state.cfg["ssh"]
    base = next((c for c in state.cfg["clients"] if c["name"] == body.saved), None) if body.saved else None
    if body.saved and base is None:
        raise HTTPException(404, f"No saved client named {body.saved}")
    base = dict(base or {})
    host = body.host.strip() or base.get("host", "")
    if not host:
        raise HTTPException(400, "Host is required")
    port = str(body.port or "").strip() or base.get("port") or ssh.get("port", 22)
    if body.password:
        pw, src = body.password, "typed"
    elif not body.clear_password and body.saved and body.saved in _client_secrets():
        pw, src = _client_secrets()[body.saved], "saved"
    elif not body.clear_password and base.get("password"):
        pw, src = base["password"], "config"
    else:
        pw, src = ssh.get("password", ""), "config"
    return {"host": host, "port": int(_int_field(str(port), "Port", 1, 65535)),
            "username": body.username.strip() or base.get("username") or ssh.get("username", "root"),
            "password": pw or "", "password_source": src if pw else "keys"}


@app.post("/api/clients/test")
async def test_client(body: ClientTestIn):
    """Check a client step by step (NDJSON): name lookup → TCP port + SSH banner → login → a command → sudo.
    Uses the same SSH options as the consoles. Nothing is changed on the client."""
    import asyncio
    import socket
    import time
    import asyncssh
    from fastapi.responses import StreamingResponse
    cl = _effective_client(body)
    timeout = float(state.cfg["ssh"].get("connect_timeout_seconds", 15))

    def line(d: dict) -> bytes:
        return (json.dumps(d) + "\n").encode()

    def why(e: BaseException) -> str:
        if isinstance(e, asyncio.TimeoutError):
            return f"no answer within {timeout:g} s - host down, firewall, or VPN not connected?"
        if isinstance(e, socket.gaierror):
            return "host name not found (DNS)"
        if isinstance(e, ConnectionRefusedError):
            return f"connection refused - nothing is listening on port {cl['port']}"
        if isinstance(e, asyncssh.PermissionDenied):
            return "login refused - wrong username or password" if cl["password"] else \
                ("login refused - this client has no password (none saved, and the config's ssh password is empty) and "
                 "no SSH key was accepted. Use ✎ Edit to enter the password, then test again")
        return f"{type(e).__name__}: {e}"

    async def gen():
        ms = lambda t0: round((time.monotonic() - t0) * 1000)  # noqa: E731
        yield line({"t": "start", "host": cl["host"], "port": cl["port"], "username": cl["username"],
                    "auth": cl["password_source"]})
        # 1. name lookup
        yield line({"t": "step", "id": "dns", "s": "running"}); t0 = time.monotonic()
        try:
            infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                cl["host"], cl["port"], type=socket.SOCK_STREAM), timeout)
            ips = list(dict.fromkeys(i[4][0] for i in infos))
            yield line({"t": "step", "id": "dns", "s": "ok", "ms": ms(t0), "detail": ", ".join(ips[:4])})
        except Exception as e:  # noqa: BLE001
            yield line({"t": "step", "id": "dns", "s": "failed", "detail": why(e)}); yield line({"t": "done", "ok": False}); return
        # 2. TCP port + SSH banner
        yield line({"t": "step", "id": "tcp", "s": "running"}); t0 = time.monotonic()
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(cl["host"], cl["port"]), timeout)
            try:
                banner = (await asyncio.wait_for(r.readline(), 5)).decode(errors="replace").strip()
            except asyncio.TimeoutError:
                banner = ""
            w.close()
            if banner and not banner.startswith("SSH-"):
                yield line({"t": "step", "id": "tcp", "s": "failed", "ms": ms(t0),
                            "detail": f"port {cl['port']} is open but it is not SSH: {banner[:80]}"})
                yield line({"t": "done", "ok": False}); return
            yield line({"t": "step", "id": "tcp", "s": "ok", "ms": ms(t0),
                        "detail": f"port {cl['port']} open" + (f" · {banner[:80]}" if banner else "")})
        except Exception as e:  # noqa: BLE001
            yield line({"t": "step", "id": "tcp", "s": "failed", "detail": why(e)}); yield line({"t": "done", "ok": False}); return
        # 3. SSH login (same options as the consoles)
        yield line({"t": "step", "id": "auth", "s": "running"}); t0 = time.monotonic()
        try:
            conn = await asyncio.wait_for(asyncssh.connect(
                cl["host"], port=cl["port"], username=cl["username"], password=cl["password"] or None,
                known_hosts=None, **({"client_keys": (), "agent_path": None} if cl["password"] else {})), timeout)
            yield line({"t": "step", "id": "auth", "s": "ok", "ms": ms(t0),
                        "detail": f"logged in as {cl['username']} with " +
                                  ("an SSH key" if not cl["password"] else "the password")})
        except Exception as e:  # noqa: BLE001
            yield line({"t": "step", "id": "auth", "s": "failed", "detail": why(e)}); yield line({"t": "done", "ok": False}); return
        try:
            # 4. a harmless command
            yield line({"t": "step", "id": "cmd", "s": "running"}); t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(conn.run(
                    'hostname; uname -sr; (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || echo', check=False), 20)
                out = [x.strip() for x in str(res.stdout or "").splitlines()]
                detail = " · ".join(x for x in out if x) or "(no output)"
                yield line({"t": "step", "id": "cmd", "s": "ok" if res.exit_status == 0 else "failed", "ms": ms(t0),
                            "detail": detail if res.exit_status == 0 else f"exit {res.exit_status}: {str(res.stderr)[:120]}"})
            except Exception as e:  # noqa: BLE001
                yield line({"t": "step", "id": "cmd", "s": "failed", "detail": why(e)}); yield line({"t": "done", "ok": False}); return
            # 5. sudo (presets such as mount and package install need it) - a warning only
            yield line({"t": "step", "id": "sudo", "s": "running"}); t0 = time.monotonic()
            try:
                r1 = await asyncio.wait_for(conn.run("sudo -n true", check=False), 15)
                if r1.exit_status == 0:
                    yield line({"t": "step", "id": "sudo", "s": "ok", "ms": ms(t0), "detail": "sudo works without a password prompt"})
                else:
                    yield line({"t": "step", "id": "sudo", "s": "warn", "ms": ms(t0),
                                "detail": "sudo asks for a password - presets that use sudo will prompt in the console"})
            except Exception as e:  # noqa: BLE001
                yield line({"t": "step", "id": "sudo", "s": "warn", "detail": why(e)})
        finally:
            conn.close()
        yield line({"t": "done", "ok": True})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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


class PerfReportIn(BaseModel):
    runs: list[str] = []      # run ids (results/<id>.json); empty = every performance run of the site
    unit: str = "GB"


@app.post("/api/reports/performance")
def performance_report(body: PerfReportIn) -> dict:
    """Build a PowerPoint performance report from elbencho runs; it lands in the results folder / 🗒 Logs tab."""
    import re
    from . import perf_report
    perf = [e for e in results.list_logs(_perf_groups()) if e["performance"] and e["type"] != "report"
            and any(f["name"].endswith(".json") for f in e["files"])]
    ids = body.runs or [e["id"] for e in perf]
    known = {e["id"] for e in perf}
    bad = [i for i in ids if i not in known]
    if bad:
        raise HTTPException(400, f"Not a performance run of this site: {', '.join(bad[:3])}")
    if not ids:
        raise HTTPException(400, "No performance runs yet - run Elbencho Write / Read in tab ② first")
    runs = []
    for i in ids:
        try:
            runs.append(json.loads((results.RESULTS_DIR / f"{i}.json").read_text()))
        except (OSError, ValueError) as e:
            raise HTTPException(400, f"Can't read {i}.json: {e}")
    tenant = dict(runs[-1].get("vars") or {})
    tenant.update({k: v for k, v in (state.vars or {}).items() if k in ("tenant_name", "view_path") and v and not tenant.get(k)})
    mount = dict(state.cfg.get("mount") or {})
    ips = state.provisioner.vip_ips() if state.provisioner else []
    if not ips:
        rec = next((t for t in reversed(state.tenants.load()) if t.get("name") == tenant.get("tenant_name")), None) if state.tenants else None
        ips = list(((rec or {}).get("vip_pool") or {}).get("ips") or [])
    if mount.get("options") and ips:   # show the real VIPs instead of ${tenant_vip.start}-${tenant_vip.end}
        mount["options"] = re.sub(r"\$\{tenant_vip\.start\}", ips[0], re.sub(r"\$\{tenant_vip\.end\}", ips[-1], str(mount["options"])))
    clients = [{"name": c["name"], "host": c["host"], "username": c.get("username", state.cfg["ssh"].get("username"))}
               for c in state.cfg.get("clients", [])]
    out = perf_report.report_path(results.RESULTS_DIR)
    try:
        res = perf_report.build(runs, out, site=sites.info(state.site)["name"] if state.site else "Test site",
                                unit=body.unit.upper(), vms=str(state.cfg["vms"].get("url", "")).rstrip("/"),
                                tenant=tenant, clients=clients, mount=mount, version=APP_VERSION)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**res, "log_id": out.stem}


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
