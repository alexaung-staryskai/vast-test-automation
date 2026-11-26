"""VMS REST client and the Step 1 tenant-onboarding / API-test runner."""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import ipam, units
from .config import TemplateError, lookup, redact, render, resolve_vars, to_list


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class VMSClient:
    _bearer_cache: dict[str, str] = {}

    def __init__(self, cfg: dict):
        vms = cfg["vms"]
        self.url = str(vms.get("url", "")).rstrip("/")
        self.api_base = "/" + str(vms.get("api_base", "/api")).strip("/")
        self.auth_type = vms.get("auth_type", "api_token")
        self.vms = vms
        self.verify = bool(vms.get("verify_ssl", False))
        self.timeout = float(vms.get("timeout_seconds", 30))

    def full_url(self, endpoint: str) -> str:
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        if endpoint.startswith("/api/"):
            return self.url + endpoint
        return self.url + self.api_base + "/" + endpoint.lstrip("/")

    async def _headers(self, client: httpx.AsyncClient, refresh: bool = False) -> dict:
        h = {"Accept": "application/json"}
        if self.auth_type == "api_token":
            h["Authorization"] = f"Api-Token {self.vms.get('api_token', '')}"
        elif self.auth_type == "bearer":
            key = f"{self.url}|{self.vms.get('username')}"
            if refresh or key not in self._bearer_cache:
                r = await client.post(
                    self.full_url(self.vms.get("token_endpoint", "/token/")),
                    json={"username": self.vms.get("username"), "password": self.vms.get("password")},
                )
                r.raise_for_status()
                self._bearer_cache[key] = r.json().get("access")
            h["Authorization"] = f"Bearer {self._bearer_cache[key]}"
        return h

    async def request(self, method: str, endpoint: str, body: Any = None, params: dict | None = None) -> dict:
        url = self.full_url(endpoint)
        auth = None
        if self.auth_type == "basic":
            auth = (self.vms.get("username", ""), self.vms.get("password", ""))
        started = time.monotonic()
        async with httpx.AsyncClient(verify=self.verify, timeout=self.timeout, auth=auth) as client:
            kwargs: dict = {"headers": await self._headers(client), "params": params}
            if body is not None and method.upper() not in ("GET", "DELETE"):
                kwargs["json"] = body
            r = await client.request(method.upper(), url, **kwargs)
            if r.status_code == 401 and self.auth_type == "bearer":  # expired JWT
                kwargs["headers"] = await self._headers(client, refresh=True)
                r = await client.request(method.upper(), url, **kwargs)
        try:
            data: Any = r.json()
        except ValueError:
            data = r.text
        return {
            "url": str(r.request.url),
            "status": r.status_code,
            "ok": 200 <= r.status_code < 300,
            "body": data,
            "duration_s": round(time.monotonic() - started, 3),
        }


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (list, dict)) or isinstance(actual, (list, dict)):
        return expected == actual
    return str(expected) == str(actual)


def generate_password(length: int = 16) -> str:
    """VMS requires upper, lower, digit and a non-alphanumeric character."""
    classes = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnopqrstuvwxyz", "23456789", "!@#%^*-_"]
    chars = [secrets.choice(c) for c in classes]
    allc = "".join(classes)
    chars += [secrets.choice(allc) for _ in range(max(length, 8) - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class Provisioner:
    """Runs the provisioning steps, keeps responses for ${step.field} references and cleanup."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.results: dict[str, dict] = {}   # step id -> last result
        self.running_all = False
        self.outputs: dict[str, Any] = {}    # step id -> response body (template context)
        self.vip_choice: str | None = None   # start IP picked in the UI
        self.generated: dict[str, str] = {}  # generated secrets (e.g. tenant admin password)
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.outputs = data.get("outputs", {})
                self.results = redact(data.get("results", {}))  # step results never carry secrets
                self.vip_choice = data.get("vip_choice")
                self.generated = data.get("generated", {})
            except (ValueError, OSError):
                pass

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(
            {"outputs": self.outputs, "results": self.results, "vip_choice": self.vip_choice,
             "generated": self.generated}, indent=2))
        try:
            self.state_file.chmod(0o600)  # may hold the generated admin password / S3 secret
        except OSError:
            pass

    def effective_vars(self, cfg: dict, vars_: dict) -> dict:
        v = units.derive(resolve_vars(vars_))
        if "tenant_admin_password" in v and not v["tenant_admin_password"]:
            if "tenant_admin_password" not in self.generated:
                self.generated["tenant_admin_password"] = generate_password(
                    int(cfg.get("tenant_admin", {}).get("password_length", 16)))
            v["tenant_admin_password"] = self.generated["tenant_admin_password"]
        return v

    def context(self, cfg: dict, vars_: dict) -> dict:
        public_cfg = {k: v for k, v in cfg.items() if not k.startswith("_") and k not in ("vms", "ssh")}
        return {**self.outputs, "vars": self.effective_vars(cfg, vars_), "cfg": public_cfg}

    # ---------------------------------------------------------------- VIP check

    async def vip_check(self, cfg: dict, map_only: bool = False) -> dict:
        """map_only: just the IP map (the tenant's VIP pool already exists - its IPs are taken by that pool, so the
        range chosen before it was created must not be checked again)."""
        vp = cfg.get("vip_pool", {})
        auto = bool(vp.get("auto_select", True))
        if map_only:
            return await ipam.check(VMSClient(cfg), vp, None, auto=False)
        return await ipam.check(VMSClient(cfg), vp, None if auto else self.vip_choice, auto=auto)

    def vip_ips(self) -> list[str]:
        """IPs of the provisioned VIP pool (or the chosen range if the pool step has not run)."""
        pool = self.outputs.get("vippool")
        if isinstance(pool, dict) and pool.get("ip_ranges"):
            return ipam.expand(pool["ip_ranges"])
        vip = self.outputs.get("vip")
        return list(vip.get("ips", [])) if isinstance(vip, dict) else []

    async def _run_vip_check(self, cfg: dict, result: dict) -> None:
        res = await self.vip_check(cfg)
        result["vip_check"] = {k: res[k] for k in ("candidate_ranges", "free_blocks", "proposal", "warnings",
                                                    "used_in_candidates", "candidates")}
        if not res["proposal"]:
            result["status"] = "failed"
            if not res["auto"] and not res["choice"]:
                result["error"] = ("Auto VIP pool selection is off and no range is chosen - "
                                   "click 🗺 Choose VIP range in the VIP Pool box, or turn auto selection on")
            elif not res["auto"]:
                result["error"] = (f"The chosen range starting at {res['choice']} no longer has {res['ip_count']} "
                                   "free consecutive IPs - choose another range")
            else:
                result["error"] = f"No {res['ip_count']} consecutive free IPs in the candidate ranges"
            return
        self.outputs["vip"] = res["proposal"]
        result["status"] = "passed"
        result["detail"] = (f"{res['proposal']['start']} – {res['proposal']['end']} ({res['ip_count']} IPs, "
                            f"{'auto-selected' if res['auto'] else 'chosen manually'})")

    # ---------------------------------------------------------------- steps

    async def run_step(self, cfg: dict, vars_: dict, step: dict) -> dict:
        client = VMSClient(cfg)
        sid = step["id"]
        result: dict = {"id": sid, "name": step.get("name", sid), "started_at": now_iso(), "status": "running"}
        self.results[sid] = result  # visible to /api/provision/state while the step runs
        try:
            if step.get("type") == "vip_check":
                await self._run_vip_check(cfg, result)
            else:
                await self._run_rest(client, cfg, vars_, step, result)
        except (TemplateError, ValueError, RuntimeError) as e:
            result["status"] = "failed"
            result["error"] = str(e) + (" (did an earlier step run?)" if isinstance(e, TemplateError) else "")
        except httpx.HTTPError as e:
            result["status"] = "failed"
            result["error"] = f"{type(e).__name__}: {e}"
        result["finished_at"] = now_iso()
        self.results[sid] = result
        self._save_state()
        return result

    async def _run_rest(self, client: VMSClient, cfg: dict, vars_: dict, step: dict, result: dict) -> None:
        sid = step["id"]
        ctx = self.context(cfg, vars_)
        for k, val in ctx["vars"].items():  # VMS rejects static QoS limits between 1 and 99 MB/s
            if k.endswith("_mbps") and isinstance(val, int) and 0 < val < 100 and f"vars.{k}" in json.dumps(step):
                raise ValueError(f"{k[:-5]} = {val} MB/s is below the VMS QoS minimum of 100 MB/s (use 0 for unlimited)")
        method = step.get("method", "POST").upper()
        endpoint = render(step["endpoint"], ctx)
        body = render(step.get("body"), ctx) if "body" in step else None
        if isinstance(body, dict):
            for key in step.get("omit_if_empty", []):
                if body.get(key) in (None, "", 0, [], {}):
                    body.pop(key, None)
        result["request"] = {"method": method, "url": client.full_url(endpoint), "body": redact(body)}

        # Same-name object already there?
        field = step.get("exists_by")
        if field and isinstance(body, dict) and body.get(field) not in (None, ""):
            look = await client.request("GET", endpoint, params={field: body[field]})
            match = next((o for o in to_list(look["body"]) if isinstance(o, dict) and o.get(field) == body[field]), None)
            if match:
                if cfg["provisioning"].get("on_existing", "fail") == "reuse":
                    self.outputs[sid] = match
                    result["status"] = "reused"
                    result["detail"] = f"{field}={body[field]!r} already exists (id {match.get('id')}) - reused"
                    result["response"] = {"status": look["status"], "body": redact(match), "duration_s": look["duration_s"]}
                    return
                result["status"] = "failed"
                result["error"] = f"{field}={body[field]!r} already exists (id {match.get('id')}). " \
                                  "Change the name, run Cleanup, or set provisioning.on_existing: reuse"
                return

        resp = await client.request(method, endpoint, body)
        result["response"] = {"status": resp["status"], "body": redact(resp["body"]), "duration_s": resp["duration_s"]}
        if not resp["ok"]:
            result["status"] = "failed"
            result["error"] = f"HTTP {resp['status']}"
            return
        self.outputs[sid] = resp["body"]
        result["status"] = "passed"
        if isinstance(resp["body"], dict) and "id" in resp["body"]:
            result["detail"] = f"id {resp['body']['id']}"
        if step.get("verify"):
            result["verify"] = await self._verify(client, step["verify"], self.context(cfg, vars_))
            got = result["verify"].pop("_body", None)
            if isinstance(got, dict) and isinstance(self.outputs[sid], dict):
                self.outputs[sid] = {**self.outputs[sid], **got}  # e.g. tenant.local_provider_id
            if not result["verify"]["passed"]:
                result["status"] = "failed"
                result["error"] = "Verification failed"

    async def _verify(self, client: VMSClient, verify: dict, ctx: dict) -> dict:
        endpoint = render(verify["endpoint"], ctx)
        resp = await client.request(verify.get("method", "GET"), endpoint)
        out: dict = {"url": resp["url"], "status": resp["status"], "checks": [], "passed": resp["ok"]}
        if not resp["ok"]:
            out["body"] = resp["body"]
            return out
        out["_body"] = resp["body"]
        for key, expected_tpl in (verify.get("expect") or {}).items():
            expected = render(expected_tpl, ctx)
            try:
                actual = lookup(resp["body"], key) if isinstance(resp["body"], (dict, list)) else None
            except TemplateError:
                actual = "<missing>"
            ok = _values_match(expected, actual)
            out["checks"].append({"field": key, "expected": expected, "actual": actual, "passed": ok})
            out["passed"] = out["passed"] and ok
        return out

    async def run_all(self, cfg: dict, vars_: dict) -> list[dict]:
        cont = bool(cfg["provisioning"].get("continue_on_error", False))
        steps = cfg["provisioning"]["steps"]
        for step in steps:  # show the whole run as queued, so progress can be followed step by step
            self.results[step["id"]] = {"id": step["id"], "name": step.get("name", step["id"]), "status": "queued"}
        self.running_all = True
        try:
            return await self._run_all(cfg, vars_, steps, cont)
        finally:
            self.running_all = False

    async def _run_all(self, cfg: dict, vars_: dict, steps: list, cont: bool) -> list[dict]:
        out = []
        failed = False
        for step in steps:
            if failed and not cont:
                r = {"id": step["id"], "name": step.get("name", step["id"]), "status": "skipped"}
                self.results[step["id"]] = r
                out.append(r)
                continue
            r = await self.run_step(cfg, vars_, step)
            out.append(r)
            failed = failed or r["status"] == "failed"
        self._save_state()
        return out

    async def cleanup(self, cfg: dict, vars_: dict) -> list[dict]:
        client = VMSClient(cfg)
        out = []
        for step in reversed(cfg["provisioning"]["steps"]):
            sid = step["id"]
            cl = step.get("cleanup")
            if not cl or sid not in self.outputs:
                continue
            if self.results.get(sid, {}).get("status") == "reused":
                out.append({"id": sid, "name": f"Cleanup: {step.get('name', sid)}", "status": "skipped",
                            "error": "object was reused, not created by this run - left in place"})
                self.outputs.pop(sid, None)
                continue
            item: dict = {"id": sid, "name": f"Cleanup: {step.get('name', sid)}"}
            try:
                ctx = self.context(cfg, vars_)
                endpoint = render(cl["endpoint"], ctx)
                body = render(cl.get("body"), ctx) if "body" in cl else None
                resp = await client.request(cl.get("method", "DELETE"), endpoint, body)
                item.update(url=resp["url"], status_code=resp["status"], body=resp["body"])
                item["status"] = "passed" if resp["ok"] or resp["status"] == 404 else "failed"
            except (TemplateError, httpx.HTTPError) as e:
                item.update(status="failed", error=str(e))
            if item["status"] == "passed":
                self.outputs.pop(sid, None)
                self.results.pop(sid, None)
            out.append(item)
        if all(i["status"] != "failed" for i in out):
            self.outputs.clear()
            self.results.clear()
            self.generated.clear()
        self._save_state()
        return out

    def reset(self) -> None:
        self.outputs.clear()
        self.results.clear()
        self.generated.clear()
        self.vip_choice = None
        self._save_state()
