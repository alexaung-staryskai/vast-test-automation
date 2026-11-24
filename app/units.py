"""Human-friendly sizes and bandwidths in vars.

A var written as "500 MB/s" / "2 GB/s" gets a derived <name>_mbps (MB/s, what VMS QoS expects);
a var written as "500 MB" / "10 GB" / "2 TB" gets a derived <name>_bytes.
Decimal units, like VMS and the Tenant onboarding app (1 TB = 10^12 bytes, 1 GB/s = 1000 MB/s).
"""
from __future__ import annotations

import re
from typing import Any

BW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(MB/s|GB/s)\s*$", re.I)
SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(MB|GB|TB)\s*$", re.I)
BW_FACTOR = {"mb/s": 1, "gb/s": 1000}
SIZE_FACTOR = {"mb": 10**6, "gb": 10**9, "tb": 10**12}


def to_mbps(value: Any) -> int | None:
    m = BW_RE.match(str(value)) if isinstance(value, str) else None
    return round(float(m.group(1)) * BW_FACTOR[m.group(2).lower()]) if m else None


def to_bytes(value: Any) -> int | None:
    m = SIZE_RE.match(str(value)) if isinstance(value, str) else None
    return round(float(m.group(1)) * SIZE_FACTOR[m.group(2).lower()]) if m else None


def derive(vars_: dict) -> dict:
    """Return vars plus <name>_mbps / <name>_bytes for every unit-valued var.
    Use 0 for 'no soft limit' ("0 TB") or 'unlimited' QoS ("0 MB/s")."""
    out = dict(vars_)
    for k, v in vars_.items():
        mbps = to_mbps(v)
        if mbps is not None:
            out[f"{k}_mbps"] = mbps
            continue
        b = to_bytes(v)
        if b is not None:
            out[f"{k}_bytes"] = b
    return out


def fmt_bytes(n: Any) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    for unit, f in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6)):
        if n >= f:
            v = n / f
            return f"{v:g} {unit}"
    return f"{n} B"


def fmt_mbps(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n == 0:
        return "unlimited"
    return f"{n / 1000:g} GB/s" if n >= 1000 and n % 100 == 0 else f"{n:g} MB/s"
