"""Config loading and ${...} template rendering."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"

ENV_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
# Only dotted references are templated (${vars.x}, ${tenant.id}); shell's ${HOME} is left alone.
REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\}")

# Masked in request bodies shown in the UI / reports. (Responses are shown as-is so the
# generated S3 access/secret key is visible to the tester.)
SECRET_KEYS = {"password", "api_token", "secret_key"}   # masked in step results, reports and logs


class TemplateError(Exception):
    pass


def _expand_env(node: Any, missing: set[str]) -> Any:
    if isinstance(node, dict):
        return {k: _expand_env(v, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env(v, missing) for v in node]
    if isinstance(node, str):
        def sub(m: re.Match) -> str:
            val = os.environ.get(m.group(1))
            if val is None:
                missing.add(m.group(1))
                return ""
            return val
        return ENV_RE.sub(sub, node)
    return node


CURRENT_CONFIG: Path | None = None  # config.yaml of the open test site


class NoSiteError(RuntimeError):
    pass


def config_path() -> Path:
    if CURRENT_CONFIG is None:
        raise NoSiteError("No test site is open")
    return CURRENT_CONFIG


def load_config(path: Path | None = None) -> tuple[dict, list[str]]:
    """Return (config, warnings)."""
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.yaml to config.yaml and edit it."
        )
    raw = yaml.safe_load(path.read_text()) or {}
    missing: set[str] = set()
    cfg = _expand_env(raw, missing)
    warnings = [f"Environment variable {name} is not set (used in config)" for name in sorted(missing)]

    cfg.setdefault("app", {})
    cfg.setdefault("vms", {})
    cfg.setdefault("vars", {})
    cfg.setdefault("provisioning", {}).setdefault("steps", [])
    cfg.setdefault("ssh", {})
    cfg.setdefault("clients", [])
    cfg.setdefault("execution", {})
    cfg.setdefault("command_groups", [])
    cfg.setdefault("vip_pool", {}).setdefault("ranges", [])
    warnings += [f"{w}. Fix it in ⚙ Configuration (vip_pool → ranges)." for w in vip_range_problems(cfg["vip_pool"])]
    cfg.setdefault("mount", {})
    cfg.setdefault("packages", {}).setdefault("required", [])
    cfg["packages"].setdefault("package_managers", {})
    cfg["packages"].setdefault("system", [])
    if not isinstance(cfg["packages"]["system"], list):
        warnings.append("packages.system must be a list of package names. Fix it in ⚙ Configuration (packages → system).")
        cfg["packages"]["system"] = []
    cfg.setdefault("tenant_admin", {})
    cfg["_path"] = str(path)
    cfg["_dir"] = str(path.parent)  # the test site folder; relative paths resolve against it
    return cfg, warnings


def vip_range_problems(vip: Any) -> list[str]:
    """Plain-language problems with vip_pool.ranges (each start/end must be a full IPv4 address, start <= end)."""
    import ipaddress
    out: list[str] = []
    if not isinstance(vip, dict):
        return out
    for i, r in enumerate(vip.get("ranges") or []):
        if not isinstance(r, dict):
            continue
        vals = {}
        for k in ("start", "end"):
            v = str(r.get(k) or "").strip()
            if not v or v.startswith("${"):
                continue
            try:
                vals[k] = int(ipaddress.IPv4Address(v))
            except ValueError:
                parts = len(v.split("."))
                hint = f" - it has {parts} number{'s' if parts != 1 else ''}, an IPv4 address needs 4 (e.g. 10.20.0.250)" \
                    if v.replace(".", "").isdigit() else " - expected an IPv4 address such as 10.20.0.250"
                out.append(f"vip_pool.ranges #{i + 1} {k} “{v}” is not a valid IP address{hint}")
        if len(vals) == 2 and vals["end"] < vals["start"]:
            out.append(f"vip_pool.ranges #{i + 1}: end {r.get('end')} is before start {r.get('start')}")
    return out


def lookup(ctx: dict, dotted: str) -> Any:
    cur: Any = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise TemplateError(f"Unresolved reference ${{{dotted}}}")
    return cur


def render(node: Any, ctx: dict) -> Any:
    """Recursively substitute ${a.b} references. A string that is exactly one reference
    keeps the referenced value's type (so ints stay ints in JSON bodies)."""
    if isinstance(node, dict):
        return {(str(render(k, ctx)) if isinstance(k, str) else k): render(v, ctx) for k, v in node.items()}
    if isinstance(node, list):
        return [render(v, ctx) for v in node]
    if isinstance(node, str):
        full = REF_RE.fullmatch(node)
        if full:
            return copy.deepcopy(lookup(ctx, full.group(1)))
        return REF_RE.sub(lambda m: str(lookup(ctx, m.group(1))), node)
    return node


def render_section(section: Any, ctx: dict, name: str, passes: int = 5) -> Any:
    """Render a config section that may reference itself (e.g. ${mount.options} inside mount.command)
    or values that themselves contain references. Repeats until stable."""
    cur = section
    for _ in range(passes):
        nxt = render(cur, {**ctx, name: cur})
        if nxt == cur:
            break
        cur = nxt
    return cur


def resolve_vars(vars_: dict) -> dict:
    """vars may reference other vars (quota_name: "quota-${vars.tenant_name}")."""
    cur = dict(vars_)
    for _ in range(5):
        nxt = {}
        for k, v in cur.items():
            try:
                nxt[k] = render(v, {"vars": cur})
            except TemplateError:
                nxt[k] = v
        if nxt == cur:
            break
        cur = nxt
    return cur


def to_list(data: Any) -> list:
    """VMS list endpoints return a bare array or a paginated {results: [...]}."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return []


def redact(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: ("********" if str(k).lower() in SECRET_KEYS and v else redact(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v) for v in node]
    return node


def coerce_var(value: Any, original: Any) -> Any:
    """Convert a value edited in the UI back to the type it had in the config."""
    if isinstance(original, (list, dict)):
        if isinstance(value, (list, dict)):
            return value
        try:
            import json
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    if isinstance(original, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(original, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if isinstance(original, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value
