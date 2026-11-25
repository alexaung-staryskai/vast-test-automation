"""Delete a test site together with its tenant on the VMS.

The scan lists everything that belongs to the tenant. Most VMS list endpoints don't filter by tenant, so every
object is checked against its own tenant field here. Anything we can't prove belongs to the tenant is left alone.

Deletion order (each step depends on the next ones still existing):
    view → view policy → S3 user → QoS policy → quota → VIP pool → tenant admin → admin role → tenant
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import httpx

from .vms import VMSClient

ORDER: list[tuple[str, str, str]] = [
    ("view", "View", "/views/"),
    ("view_policy", "View policy", "/viewpolicies/"),
    ("s3_user", "S3 user", "/users/"),
    ("qos", "QoS policy", "/qospolicies/"),
    ("quota", "Quota", "/quotas/"),
    ("vippool", "VIP pool", "/vippools/"),
    ("tenant_admin", "Tenant admin", "/managers/"),
    ("admin_role", "Admin role", "/roles/"),
    ("tenant", "Tenant", "/tenants/"),
]
LABEL = {k: lbl for k, lbl, _ in ORDER}
ENDPOINT = {k: ep for k, _, ep in ORDER}
GONE_TIMEOUT_S = 90          # some deletes (tenant, VIP pool) finish asynchronously on the VMS


class TeardownError(RuntimeError):
    pass


def _items(body: Any) -> tuple[list, str | None]:
    if isinstance(body, list):
        return body, None
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        return body["results"], body.get("next")
    raise TeardownError(f"unexpected answer from the VMS: {str(body)[:160]}")


async def list_all(client: VMSClient, endpoint: str, params: dict | None = None) -> list[dict]:
    out: list[dict] = []
    url, p = endpoint, {"page_size": 1000, **(params or {})}
    for _ in range(50):
        r = await client.request("GET", url, params=p)
        if not r["ok"]:
            raise TeardownError(f"GET {endpoint} → HTTP {r['status']}: {str(r['body'])[:200]}")
        items, nxt = _items(r["body"])
        out += [i for i in items if isinstance(i, dict)]
        if not nxt:
            break
        url, p = nxt, None
    return out


def _tid(obj: dict) -> Any:
    t = obj.get("tenant_id")
    if t is None and isinstance(obj.get("tenant"), dict):
        t = obj["tenant"].get("id")
    return t


def _provider_id(user: dict) -> Any:
    lp = user.get("local_provider")
    if isinstance(lp, dict):
        return lp.get("id")
    return user.get("local_provider_id", lp if isinstance(lp, int) else None)


def belongs(kind: str, obj: dict, tenant: dict, ctx: dict) -> bool:
    """True only when the object provably belongs to this tenant."""
    tid = tenant["id"]
    if kind == "tenant":
        return obj.get("id") == tid and obj.get("name") == tenant["name"]
    if kind == "s3_user":
        if ctx.get("provider_shared"):
            return obj.get("id") in ctx.get("recorded_users", set())
        return ctx.get("provider_id") is not None and _provider_id(obj) == ctx["provider_id"]
    if kind == "tenant_admin":
        return _tid(obj) == tid and obj.get("user_type", "TENANT_ADMIN") == "TENANT_ADMIN"
    if kind == "admin_role":
        tenants = obj.get("tenants")
        if isinstance(tenants, list) and tenants:
            return tenants == [tid]
        return _tid(obj) == tid
    return _tid(obj) == tid


def _detail(kind: str, o: dict) -> str:
    if kind == "view":
        protos = o.get("protocols")
        return " · ".join(x for x in [o.get("path"), ", ".join(protos) if isinstance(protos, list) else None] if x)
    if kind == "quota":
        return o.get("path") or ""
    if kind == "vippool":
        rng = o.get("ip_ranges")
        if isinstance(rng, list) and rng:
            return ", ".join("-".join(map(str, r)) if isinstance(r, list) else str(r) for r in rng[:3])
        return ""
    return ""


def _name(kind: str, o: dict) -> str:
    return str(o.get("name") or o.get("username") or o.get("title") or f"id {o.get('id')}")


async def find_tenant(client: VMSClient, name: str) -> dict | None:
    ts = await list_all(client, "/tenants/", {"name__icontains": name})
    exact = [t for t in ts if t.get("name") == name]
    return exact[0] if exact else None


async def _context(client: VMSClient, tenant: dict, record: dict | None) -> dict:
    """Whether the tenant's local provider (which holds the S3 users) is shared with other tenants."""
    pid = tenant.get("local_provider_id")
    if pid is None and isinstance(tenant.get("local_provider"), dict):
        pid = tenant["local_provider"].get("id")
    shared = True
    if pid is not None:
        others = [t for t in await list_all(client, "/tenants/") if t.get("id") != tenant["id"]
                  and (t.get("local_provider_id") == pid
                       or (isinstance(t.get("local_provider"), dict) and t["local_provider"].get("id") == pid))]
        shared = bool(others)
    recorded = set()
    uid = ((record or {}).get("s3") or {}).get("user_id")
    if uid is not None:
        recorded.add(uid)
    return {"provider_id": pid, "provider_shared": shared, "recorded_users": recorded}


async def scan(client: VMSClient, tenant: dict, record: dict | None) -> dict:
    """Everything under the tenant, grouped in deletion order."""
    ctx = await _context(client, tenant, record)
    groups, notes = [], []
    for kind, label, ep in ORDER:
        if kind == "tenant":
            objs = [tenant]
        else:
            params = {"tenant_id": tenant["id"]}
            if kind == "s3_user" and ctx["provider_id"] is not None:
                params = {"local_provider_id": ctx["provider_id"]}
            objs = await list_all(client, ep, params)
        mine = [o for o in objs if belongs(kind, o, tenant, ctx)]
        if kind == "admin_role":
            shared = [o for o in objs if isinstance(o.get("tenants"), list) and tenant["id"] in o["tenants"]
                      and len(o["tenants"]) > 1]
            for o in shared:
                notes.append(f"Admin role “{_name(kind, o)}” is shared with other tenants - left in place.")
        groups.append({"kind": kind, "label": label,
                       "items": [{"id": o.get("id"), "name": _name(kind, o), "detail": _detail(kind, o)}
                                 for o in mine if o.get("id") is not None]})
    if ctx["provider_shared"]:
        notes.append("The tenant's local user provider is shared with other tenants, so only the S3 user this "
                     "site created is deleted.")
    return {"groups": groups, "notes": notes, "ctx": ctx}


async def _wait_gone(client: VMSClient, endpoint: str) -> bool:
    end = time.monotonic() + GONE_TIMEOUT_S
    delay = 0.5
    while time.monotonic() < end:
        r = await client.request("GET", endpoint)
        if r["status"] == 404:
            return True
        await asyncio.sleep(delay)
        delay = min(delay * 1.6, 4)
    return False


async def delete_one(client: VMSClient, kind: str, oid: Any, tenant: dict, ctx: dict,
                     force: bool = False) -> tuple[str, str]:
    """Re-check ownership, DELETE, then wait until the object is really gone. Returns (status, message).
    force (tenant only): DELETE ?force_remove=true - the VMS also deletes files left in the tenant's directory."""
    ep = f"{ENDPOINT[kind]}{oid}/"
    r = await client.request("GET", ep)
    if r["status"] == 404:
        return "gone", "already gone"
    if not r["ok"] or not isinstance(r["body"], dict):
        return "failed", f"could not read it: HTTP {r['status']}"
    if not belongs(kind, r["body"], tenant, ctx):
        return "failed", "no longer belongs to this tenant - not deleted"
    d = await client.request("DELETE", ep, params={"force_remove": "true"} if force and kind == "tenant" else None)
    if d["status"] == 404:
        return "deleted", ""
    if not d["ok"]:
        msg = d["body"]
        while isinstance(msg, dict) and (msg.get("detail") or msg.get("error") or msg.get("message")):
            msg = msg.get("detail") or msg.get("error") or msg.get("message")
        return "failed", f"HTTP {d['status']}: {str(msg)[:300]}"
    if await _wait_gone(client, ep):
        return "deleted", ""
    return "failed", f"the VMS accepted the delete but it still exists after {GONE_TIMEOUT_S}s"


async def run(client: VMSClient, tenant: dict, record: dict | None, plan: dict[str, list],
              force_tenant: bool = False) -> AsyncIterator[dict]:
    """Delete the reviewed objects (plan: kind → ids) in ORDER. Stops at the first failure.
    force_tenant: delete the tenant with force_remove, but only once nothing else is left under it."""
    fresh = await scan(client, tenant, record)
    ctx = fresh["ctx"]
    now = {g["kind"]: {i["id"]: i for i in g["items"]} for g in fresh["groups"]}
    for kind, label, _ in ORDER:
        wanted = [i for i in plan.get(kind, []) if i in now.get(kind, {})]
        new = [i for i in now.get(kind, {}) if i not in plan.get(kind, [])]
        yield {"t": "kind", "kind": kind, "count": len(wanted),
               "new": [now[kind][i]["name"] for i in new]}
        if kind == "tenant" and force_tenant and wanted:
            left = [f"{g['label']} “{i['name']}”" for g in (await scan(client, tenant, record))["groups"]
                    if g["kind"] != "tenant" for i in g["items"]]
            if left:
                yield {"t": "stop", "kind": kind, "msg": "Force delete is only allowed when nothing else is left "
                       f"under the tenant. Still there: {', '.join(left[:6])}{' …' if len(left) > 6 else ''}"}
                return
        for oid in wanted:
            item = now[kind][oid]
            yield {"t": "item", "kind": kind, "id": oid, "name": item["name"], "s": "deleting"}
            try:
                status, msg = await delete_one(client, kind, oid, tenant, ctx, force=force_tenant)
            except httpx.HTTPError as e:
                status, msg = "failed", f"{type(e).__name__}: {e}"
            if status == "failed" and kind == "view_policy" and item["name"] == "default":
                status, msg = "skipped", "the tenant's default policy - the VMS removes it with the tenant"
            forced = force_tenant and kind == "tenant"
            yield {"t": "item", "kind": kind, "id": oid, "name": item["name"], "s": status, "msg": msg, "forced": forced}
            if status == "failed":
                yield {"t": "stop", "kind": kind, "msg": f"{label} “{item['name']}”: {msg}",
                       "can_force": kind == "tenant" and not forced, "tenant_name": tenant["name"]}
                return
        yield {"t": "kind_done", "kind": kind}
