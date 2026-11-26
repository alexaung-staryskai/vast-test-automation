"""In-memory stand-in for the VMS REST API (v8 paths), for trying the app without the VPN.
Run:  .venv/bin/uvicorn dev.mock_vms:app --port 9443
"""
import itertools
import secrets

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
IDS = itertools.count(2)   # id 1 is the VMS default tenant / provider, as on a real cluster
DB: dict[str, dict[int, dict]] = {
    # an existing pool so the VIP map shows used IPs
    "vippools": {900: {"id": 900, "name": "existing-pool", "ip_ranges": [["10.20.0.10", "10.20.0.19"]]}},
    # like a real VMS: the default tenant and its local provider
    "tenants": {1: {"id": 1, "name": "default", "local_provider_id": 1}},
    "localproviders": {1: {"id": 1, "name": "default", "assigned_tenants_preview": "default"}},
}
BASE = "/api/v8"


def _auth(req: Request) -> None:
    if not req.headers.get("authorization"):
        raise HTTPException(401, {"detail": "Authentication credentials were not provided."})


@app.get(BASE + "/clusters/")
def clusters(req: Request):
    _auth(req)
    return [{"id": 1, "name": "mock-cluster", "sw_version": "5.5.0-mock", "state": "ONLINE"}]


@app.get(BASE + "/computeclusters/")
def computeclusters(req: Request):
    _auth(req)
    return [{"name": "cc1", "static_ip_ranges": [["10.20.0.40", "10.20.0.44"]], "server_kube_vip_address": "10.20.0.60"}]


@app.post(BASE + "/users/{uid}/access_keys/")
def access_keys(uid: int, req: Request):
    _auth(req)
    if uid not in DB.get("users", {}):
        raise HTTPException(404, {"detail": "Not found."})
    return {"access_key": secrets.token_hex(10).upper(), "secret_key": secrets.token_urlsafe(30)}


@app.get(BASE + "/{coll}/")
def list_(coll: str, req: Request):
    _auth(req)
    items = list(DB.get(coll, {}).values())
    for k, v in req.query_params.items():
        if k in ("page", "page_size"):
            continue
        if k.endswith("__icontains"):
            f = k[: -len("__icontains")]
            items = [o for o in items if v.lower() in str(o.get(f, "")).lower()]
        else:
            items = [o for o in items if str(o.get(k)) == v]
    return items


@app.post(BASE + "/{coll}/")
async def create(coll: str, req: Request):
    _auth(req)
    body = await req.json()
    obj = {"id": next(IDS), **body}
    if coll == "tenants":
        obj["local_provider_id"] = 1000 + obj["id"]
        # like the VMS: the tenant gets its own local provider, which is NOT deleted with the tenant
        DB.setdefault("localproviders", {})[obj["local_provider_id"]] = {
            "id": obj["local_provider_id"], "name": f"provider-{obj.get('name')}", "assigned_tenants_preview": obj.get("name")}
    obj.pop("password", None)
    DB.setdefault(coll, {})[obj["id"]] = obj
    return obj


@app.get(BASE + "/{coll}/{oid}/")
def get(coll: str, oid: int, req: Request):
    _auth(req)
    if oid not in DB.get(coll, {}):
        raise HTTPException(404, {"detail": "Not found."})
    return DB[coll][oid]


@app.patch(BASE + "/{coll}/{oid}/")
async def patch(coll: str, oid: int, req: Request):
    _auth(req)
    if oid not in DB.get(coll, {}):
        raise HTTPException(404, {"detail": "Not found."})
    DB[coll][oid].update(await req.json())
    return DB[coll][oid]


@app.delete(BASE + "/{coll}/{oid}/", status_code=204)
def delete(coll: str, oid: int, req: Request):
    _auth(req)
    obj = DB.get(coll, {}).pop(oid, None)
    if obj is None:
        raise HTTPException(404, {"detail": "Not found."})
    if coll == "tenants" and obj.get("local_provider_id") in DB.get("localproviders", {}):
        DB["localproviders"][obj["local_provider_id"]]["assigned_tenants_preview"] = ""   # left behind, unassigned
