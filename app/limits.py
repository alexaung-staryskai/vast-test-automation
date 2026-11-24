"""Switch a tenant's QoS and quota limits off and on again on the VMS.

QoS:   the tenant's QoS policy is attached to its view. Off = detach it (view qos_policy_id -> null),
       on = attach it again. The policy itself, with its limits, is left untouched. (A QoS policy can't be
       made "unlimited": the VMS refuses a policy whose limits are all 0.)
Quota: off = the quota's limits are set to null (how the VMS stores "no limit"), after saving their values in
       the tenant's record (tenants.yaml, limits.quota.saved); on = the saved values are PATCHed back.
The state shown is always read live from the VMS, so changes made in the VMS UI are reflected too.
"""
from __future__ import annotations

import time
from typing import Any

QUOTA_FIELDS = ("hard_limit", "soft_limit", "hard_limit_inodes", "soft_limit_inodes")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _id(rec: dict, part: str) -> Any:
    obj = rec.get(part) or {}
    if not obj.get("id"):
        raise ValueError(f"This tenant has no {part.replace('_', ' ')} id recorded in tenants.yaml")
    return obj["id"]


async def _get(client, ep: str) -> dict:
    r = await client.request("GET", ep)
    if not r["ok"]:
        raise RuntimeError(f"GET {r['url']} -> HTTP {r['status']}: {r['body']}")
    return r["body"]


async def _patch(client, ep: str, body: dict) -> None:
    r = await client.request("PATCH", ep, body)
    if not r["ok"]:
        raise RuntimeError(f"PATCH {r['url']} -> HTTP {r['status']}: {r['body']}")


def _quota_on(q: dict) -> bool:
    return any(q.get(f) not in (None, 0) for f in QUOTA_FIELDS)


async def _qos_state(client, rec: dict) -> dict:
    view_id, pol_id = _id(rec, "view"), _id(rec, "qos")
    view = await _get(client, f"/views/{view_id}/")
    pol = await _get(client, f"/qospolicies/{pol_id}/")
    st = pol.get("static_limits") or {}
    attached = view.get("qos_policy_id")
    return {"enabled": attached is not None, "attached_policy_id": attached, "attached_policy": view.get("qos_policy"),
            "name": pol.get("name"), "view_id": view_id, "policy_id": pol_id,
            "read_mbps": st.get("max_reads_bw_mbps"), "write_mbps": st.get("max_writes_bw_mbps"),
            "read_iops": st.get("max_reads_iops"), "write_iops": st.get("max_writes_iops")}


async def _quota_state(client, rec: dict) -> dict:
    q = await _get(client, f"/quotas/{_id(rec, 'quota')}/")
    return {"enabled": _quota_on(q), "name": q.get("name"), "path": q.get("path"),
            **{f: q.get(f) for f in QUOTA_FIELDS}, "used_capacity": q.get("used_capacity")}


async def read(client, rec: dict) -> dict:
    out: dict[str, Any] = {}
    for kind, fn in (("qos", _qos_state), ("quota", _quota_state)):
        try:
            out[kind] = {**await fn(client, rec), "saved": ((rec.get("limits") or {}).get(kind) or {}).get("saved")}
        except (ValueError, RuntimeError) as e:
            out[kind] = {"error": str(e)}
    return out


async def set_enabled(client, rec: dict, kind: str, enabled: bool) -> tuple[dict, dict]:
    """Returns (new state, updated `limits` dict for the tenant record)."""
    limits = dict(rec.get("limits") or {})
    entry = dict(limits.get(kind) or {})

    if kind == "qos":
        cur = await _qos_state(client, rec)
        if cur["enabled"] == enabled:
            return cur, limits
        view_ep = f"/views/{cur['view_id']}/"
        if not enabled:
            entry.update(saved={"qos_policy_id": cur["attached_policy_id"]}, disabled_at=_now())
            await _patch(client, view_ep, {"qos_policy_id": None})
        else:
            pid = (entry.get("saved") or {}).get("qos_policy_id") or cur["policy_id"]
            await _patch(client, view_ep, {"qos_policy_id": pid})
            entry.update(enabled_at=_now())
        after = await _qos_state(client, rec)
    else:
        ep = f"/quotas/{_id(rec, 'quota')}/"
        cur = await _quota_state(client, rec)
        if cur["enabled"] == enabled:
            return cur, limits
        if not enabled:
            entry.update(saved={f: cur.get(f) for f in QUOTA_FIELDS}, disabled_at=_now())
            await _patch(client, ep, {f: None for f in QUOTA_FIELDS if cur.get(f) is not None})
        else:
            saved = entry.get("saved")
            if not saved:
                raise RuntimeError("No saved quota limits to restore (they were removed outside this app). "
                                   "Set them in the VMS, or re-run the quota onboarding step.")
            await _patch(client, ep, saved)
            entry.update(enabled_at=_now())
        after = await _quota_state(client, rec)

    if after["enabled"] != enabled:
        raise RuntimeError(f"The VMS accepted the change but the {kind} limits are still "
                           f"{'on' if after['enabled'] else 'off'}")
    entry["enabled"] = enabled
    if enabled:
        entry.pop("saved", None)  # back on: nothing pending
    limits[kind] = entry
    return after, limits
