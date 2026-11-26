"""VIP pool IP planning: find IPs already held on the cluster and propose a free block.

Same rules as the Tenant onboarding web application: every VIP pool range and every
compute-cluster IP (static_ip_ranges + kube VIP) counts as used, because VMS rejects
overlapping ranges on POST /vippools/ ("ip_ranges_overlap").
"""
from __future__ import annotations

import asyncio
import ipaddress
import platform
from typing import Any

from .config import to_list

MIN_IPS = 5
MAX_CELLS = 2048  # cap for the visual map


def ip2int(ip: str) -> int:
    return int(ipaddress.IPv4Address(ip.strip()))


def int2ip(n: int) -> str:
    return str(ipaddress.IPv4Address(n))


def _range_pair(r: Any) -> tuple[str, str] | None:
    if isinstance(r, (list, tuple)) and r:
        return str(r[0]), str(r[-1] if len(r) > 1 else r[0])
    if isinstance(r, dict):
        start = r.get("start_ip") or r.get("start")
        end = r.get("end_ip") or r.get("end") or start
        if start:
            return str(start), str(end)
    if isinstance(r, str) and r:
        return r, r
    return None


async def list_holders(client) -> tuple[list[dict], list[str]]:
    """Return ([{name, kind, ranges:[(start,end)]}], warnings)."""
    holders: list[dict] = []
    warnings: list[str] = []
    r = await client.request("GET", "/vippools/")
    if not r["ok"]:
        raise RuntimeError(f"GET /vippools/ failed: HTTP {r['status']} {str(r['body'])[:200]}")
    for p in to_list(r["body"]):
        pairs = [x for x in (_range_pair(rr) for rr in p.get("ip_ranges") or []) if x]
        holders.append({"name": p.get("name", f"pool {p.get('id')}"), "kind": "VIP pool", "ranges": pairs})
    r = await client.request("GET", "/computeclusters/")
    if r["ok"]:
        for cc in to_list(r["body"]):
            pairs = [x for x in (_range_pair(rr) for rr in cc.get("static_ip_ranges") or []) if x]
            if cc.get("server_kube_vip_address"):
                pairs.append((cc["server_kube_vip_address"], cc["server_kube_vip_address"]))
            holders.append({"name": cc.get("name", "compute cluster"), "kind": "compute cluster", "ranges": pairs})
    elif r["status"] != 404:  # older VAST versions have no compute clusters
        warnings.append(f"GET /computeclusters/ returned HTTP {r['status']} - compute-cluster IPs not checked")
    return holders, warnings


def used_map(holders: list[dict]) -> dict[int, str]:
    used: dict[int, str] = {}
    for h in holders:
        for start, end in h["ranges"]:
            try:
                a, b = ip2int(start), ip2int(end)
            except ValueError:
                continue
            for i in range(a, b + 1):
                used.setdefault(i, f"{h['kind']}: {h['name']}")
    return used


async def ping_used(candidates: list[int], concurrency: int = 64) -> set[int]:
    """Ping candidates from this machine; IPs that answer are treated as used."""
    import subprocess
    sem = asyncio.Semaphore(concurrency)
    system = platform.system()
    if system == "Windows":   # -n count, -w timeout in ms; exit code 0 can also mean "unreachable", so check for TTL=
        args = lambda ip: ["ping", "-n", "1", "-w", "1000", ip]  # noqa: E731
    else:
        args = lambda ip: ["ping", "-c", "1", *(["-t", "1"] if system == "Darwin" else ["-W", "1"]), ip]  # noqa: E731
    alive: set[int] = set()

    def ping_once(ip: str) -> bool:
        # a thread + subprocess.run works with every event loop (asyncio subprocesses don't on all Windows loops)
        r = subprocess.run(args(ip), capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and (system != "Windows" or "TTL=" in r.stdout.upper())

    async def one(n: int) -> None:
        async with sem:
            try:
                if await asyncio.to_thread(ping_once, int2ip(n)):
                    alive.add(n)
            except (OSError, subprocess.SubprocessError):
                pass

    await asyncio.gather(*(one(n) for n in candidates))
    return alive


def free_blocks(candidates: list[int], used: dict[int, str]) -> list[tuple[int, int]]:
    """Consecutive free runs [(start, end)] within the candidate list (sorted ints)."""
    blocks: list[tuple[int, int]] = []
    run_start = prev = None
    for n in candidates:
        if n in used:
            if run_start is not None:
                blocks.append((run_start, prev))
            run_start = None
        elif run_start is None or n != prev + 1:
            if run_start is not None:
                blocks.append((run_start, prev))
            run_start = n
        prev = n
    if run_start is not None:
        blocks.append((run_start, prev))
    return blocks


async def check(client, vip_cfg: dict, choice_start: str | None = None, auto: bool = True) -> dict:
    """auto=True proposes the first free block (or choice_start if it fits).
    auto=False (manual) only ever proposes choice_start; no choice, or a choice that no longer fits, gives no proposal."""
    count = max(MIN_IPS, int(vip_cfg.get("ip_count", MIN_IPS)))
    cand_ranges = []
    candidates: list[int] = []
    from .config import vip_range_problems
    problems = vip_range_problems(vip_cfg)
    if problems:
        raise ValueError("; ".join(problems) + ". Fix it in ⚙ Configuration (vip_pool → ranges), then check again.")
    for r in vip_cfg.get("ranges") or []:
        a, b = ip2int(str(r["start"])), ip2int(str(r["end"]))
        if b < a:
            a, b = b, a
        cand_ranges.append((a, b))
        candidates.extend(range(a, b + 1))
    if not candidates:
        raise ValueError("vip_pool.ranges is empty - add at least one candidate range to the config")
    candidates = sorted(set(candidates))

    holders, warnings = await list_holders(client)
    used = used_map(holders)
    if vip_cfg.get("ping_check"):
        for n in await ping_used([c for c in candidates if c not in used]):
            used[n] = "answers ping"

    blocks = [b for b in free_blocks(candidates, used) if b[1] - b[0] + 1 >= count]
    proposal = None
    if choice_start:
        s = ip2int(choice_start)
        # a click inside a free run that is big enough picks the block containing it (e.g. near the end of
        # the run the block starts earlier), instead of requiring the exact first IP of the block
        run = next(((a, b) for a, b in blocks if a <= s <= b), None)
        if run:
            s = min(s, run[1] - count + 1)
        e = s + count - 1
        if all(n in candidates and n not in used for n in range(s, e + 1)):
            proposal = (s, e)
        else:
            warnings.append(f"Chosen start {choice_start} does not have {count} free consecutive IPs"
                            + (" - using the first free block" if auto else " - choose another range"))
    if proposal is None and blocks and auto:
        proposal = (blocks[0][0], blocks[0][0] + count - 1)

    # The map shows every /24 the candidate ranges touch (x.x.x.0 - x.x.x.255), so the whole
    # neighbourhood is visible; IPs outside the candidate ranges are shown but cannot be chosen.
    cand_set = set(candidates)
    subnets = sorted({n >> 8 for a, b in cand_ranges for n in (a, b)} |
                     {n >> 8 for a, b in cand_ranges for n in range(a, b + 1, 256)})
    cells = []
    for net in subnets:
        for n in range(net << 8, (net << 8) + 256):
            if len(cells) >= MAX_CELLS:
                break
            if n in used:
                state = "used"
            elif n not in cand_set:
                state = "outside"
            else:
                state = "free"
            if proposal and proposal[0] <= n <= proposal[1]:
                state = "proposed"
            cells.append({"ip": int2ip(n), "state": state, "holder": used.get(n), "in_range": n in cand_set})

    return {
        "ip_count": count,
        "candidate_ranges": [{"start": int2ip(a), "end": int2ip(b), "size": b - a + 1} for a, b in cand_ranges],
        "candidates": len(candidates),
        "used_in_candidates": sum(1 for n in candidates if n in used),
        "free_blocks": [{"start": int2ip(a), "end": int2ip(b), "size": b - a + 1} for a, b in blocks],
        "auto": auto,
        "choice": choice_start,
        "proposal": None if not proposal else {
            "start": int2ip(proposal[0]), "end": int2ip(proposal[1]), "count": count,
            "ips": [int2ip(n) for n in range(proposal[0], proposal[1] + 1)],
        },
        "holders": [{"name": h["name"], "kind": h["kind"], "ranges": [list(x) for x in h["ranges"]]} for h in holders],
        "cells": cells,
        "truncated": len(subnets) * 256 > MAX_CELLS,
        "warnings": warnings,
    }


def expand(ip_ranges: Any) -> list[str]:
    out: list[str] = []
    for r in ip_ranges or []:
        pair = _range_pair(r)
        if pair:
            out.extend(int2ip(n) for n in range(ip2int(pair[0]), ip2int(pair[1]) + 1))
    return out
